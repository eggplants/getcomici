"""Download and unscramble manga pages from sites running the Comici+ viewer."""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag
from pathvalidate import sanitize_filename
from PIL import Image
from requests import Session
from requests.adapters import HTTPAdapter, Retry
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
}

# https://comici.jp/cooperation-site
VALID_HOSTS = (
    "asacomi.jp",
    "bibibi-comic.com",
    "championcross.jp",
    "comic-growl.com",
    "comic-room-base.com",
    "comic.j-nbooks.jp",
    "comicpash.jp",
    "comicride.jp",
    "comics.manga-bang.com",
    "comirela.com",
    "ebookstore.corkagency.com",
    "g-comi.jp",
    "hanayume.com",
    "hayacomic.jp",
    "heros-web.com",
    "kansai.mag-garden.co.jp",
    "kimicomi.com",
    "manga-zegra.com",
    "mangabu.jp",
    "mangalt.jp",
    "mangaspa.nikkan-spa.jp",
    "namicomic.jp",
    "piacomic.jp",
    "studio.booklista.co.jp",
    "takecomic.jp",
    "younganimal.com",
    "youngchampion.jp",
)

# The viewer slices every page into a COLUMNS x ROWS grid and shuffles the tiles.
COLUMNS = 4
ROWS = 4
TILES = COLUMNS * ROWS

_VIEWER_ID = "comici-viewer"


class ComiciError(Exception):
    """Base class for every error this module raises."""


class NotAComiciPageError(ComiciError):
    """The fetched page carries no Comici+ viewer."""


class NeedPurchase(Warning):
    """The episode is not readable without buying it or logging in."""


class Page(TypedDict):
    """One page as `book/contentsInfo` describes it."""

    imageUrl: str  # noqa: N815 (mirrors the API's own spelling)
    scramble: str
    sort: int
    width: int
    height: int
    expiresOn: int  # noqa: N815 (mirrors the API's own spelling)


@dataclass(frozen=True)
class Episode:
    """What the episode page says about itself."""

    url: str
    viewer_id: str
    api_base: str
    series_title: str
    episode_title: str
    next_url: str | None


def parse_scramble(scramble: str) -> list[int]:
    """Turn the API's `"[1, 5, 13, ...]"` into a list of tile indices.

    Args:
        scramble: The `scramble` field of a page, as the API returns it.

    Returns:
        One source tile index per destination tile, in column-major order.

    Raises:
        ComiciError: The string is not a permutation of `range(TILES)`.
    """
    indices = [int(part) for part in scramble.strip().strip("[]").split(",")]
    if sorted(indices) != list(range(TILES)):
        msg = f"{scramble!r} is not a permutation of 0..{TILES - 1}."
        raise ComiciError(msg)
    return indices


def descramble(image: Image.Image, scramble: Sequence[int]) -> Image.Image:
    """Put a scrambled page back together.

    The viewer walks the destination grid column by column and copies
    `scramble[n]`-th source tile into the n-th destination slot. Tile size is
    floored, so any leftover strip on the right and bottom edge is never
    shuffled and is kept as-is.

    Args:
        image: The page exactly as the CDN serves it.
        scramble: Source tile index per destination tile, from `parse_scramble`.

    Returns:
        A new image with the tiles back in reading order.
    """
    width, height = image.size
    tile_width, tile_height = width // COLUMNS, height // ROWS
    out = image.copy()
    for dest, src in enumerate(scramble):
        dest_col, dest_row = divmod(dest, ROWS)
        src_col, src_row = divmod(src, ROWS)
        tile = image.crop(
            (
                tile_width * src_col,
                tile_height * src_row,
                tile_width * (src_col + 1),
                tile_height * (src_row + 1),
            ),
        )
        out.paste(tile, (tile_width * dest_col, tile_height * dest_row))
    return out


class Comici:
    """Fetch episodes from a site running the Comici+ viewer."""

    def __init__(self, session: Session | None = None) -> None:
        """Build a client.

        Args:
            session: A session to reuse. A retrying one is made when omitted.
        """
        if session is None:
            session = Session()
            adapter = HTTPAdapter(max_retries=Retry(total=10, backoff_factor=1))
            session.mount("https://", adapter)
            session.mount("http://", adapter)
        self._session = session

    @staticmethod
    def is_valid_uri(url: str) -> bool:
        """Report whether `url` is on a site known to run Comici+.

        A `False` here is only a hint: any page carrying a Comici+ viewer works,
        so `get` decides for itself once it has the HTML.

        Args:
            url: The URL to check.

        Returns:
            True when the URL is https and its host is in `VALID_HOSTS`.
        """
        parsed = urlparse(url)
        return parsed.scheme == "https" and parsed.hostname in VALID_HOSTS

    def get(  # noqa: PLR0913
        self,
        url: str,
        save_path: str | Path = ".",
        *,
        overwrite: bool = False,
        only_first: bool = False,
        save_metadata: bool = False,
        print_log: bool = False,
    ) -> tuple[str | None, Path, bool]:
        """Download one episode and unscramble every page of it.

        Args:
            url: The episode URL.
            save_path: Directory to build `<series>/<episode>/` under.
            overwrite: Download again even if the directory already exists.
            only_first: Stop after the first page.
            save_metadata: Also write the raw `contentsInfo` response.
            print_log: Draw a progress bar.

        Returns:
            The next episode's URL (or None), the directory written, and
            whether anything was actually downloaded.
        """
        episode = self.episode_info(url)
        save_dir = Path(save_path) / sanitize_filename(episode.series_title) / sanitize_filename(episode.episode_title)
        if save_dir.exists() and not overwrite:
            return episode.next_url, save_dir, False

        pages = self.pages(episode)
        if not pages:
            warnings.warn(episode.episode_title, NeedPurchase, stacklevel=2)
            return episode.next_url, save_dir, False

        save_dir.mkdir(parents=True, exist_ok=True)
        if save_metadata:
            (save_dir / "metadata.json").write_text(
                json.dumps(pages, indent=4, ensure_ascii=False),
                encoding="utf-8",
            )
        self._save_pages(
            pages,
            save_dir,
            referer=episode.url,
            only_first=only_first,
            print_log=print_log,
        )
        return episode.next_url, save_dir, True

    def episode_info(self, url: str) -> Episode:
        """Read the viewer parameters off an episode page.

        Args:
            url: The episode URL.

        Returns:
            The parsed episode.

        Raises:
            NotAComiciPageError: The page carries no Comici+ viewer.
        """
        res = self._session.get(url, headers=HEADERS, timeout=30)
        res.raise_for_status()
        soup = BeautifulSoup(res.content, "html.parser")

        viewer = soup.find(id=_VIEWER_ID)
        if not isinstance(viewer, Tag):
            msg = f"no '#{_VIEWER_ID}' element on {url}; is it a Comici+ episode?"
            raise NotAComiciPageError(msg)

        viewer_id = str(viewer.attrs.get("data-comici-viewer-id", "")) or None
        if viewer_id is None:
            msg = f"'#{_VIEWER_ID}' on {url} carries no data-comici-viewer-id."
            raise NotAComiciPageError(msg)

        api_domain = str(viewer.attrs.get("data-api-domain", "/api"))
        parsed = urlparse(url)
        api_base = (
            f"{parsed.scheme}://{parsed.netloc}{api_domain}" if api_domain.startswith("/") else f"https://{api_domain}"
        )

        series_title, episode_title = self._titles(soup, viewer, viewer_id)

        next_id = str(viewer.attrs.get("data-next-episode-id", ""))
        next_url = urljoin(url, next_id) if next_id else None

        return Episode(
            url=url,
            viewer_id=viewer_id,
            api_base=api_base,
            series_title=series_title,
            episode_title=episode_title,
            next_url=next_url,
        )

    def pages(self, episode: Episode, member_jwt: str = "") -> list[Page]:
        """List every page of an episode.

        `contentsInfo` refuses a range wider than the episode, so the total is
        asked for first and the real range fetched second.

        Args:
            episode: The episode to list.
            member_jwt: A logged-in member token, for episodes that need one.

        Returns:
            The pages, in reading order. Empty when the episode is not readable.
        """
        total = int(self._contents_info(episode, 0, 0, member_jwt).get("totalPages") or 0)
        if total <= 0:
            return []
        body = self._contents_info(episode, 0, total - 1, member_jwt)
        pages: list[Page] = list(body.get("result") or [])
        return sorted(pages, key=lambda page: page["sort"])

    def _contents_info(
        self,
        episode: Episode,
        page_from: int,
        page_to: int,
        member_jwt: str = "",
    ) -> dict[str, Any]:
        res = self._session.get(
            f"{episode.api_base}/book/contentsInfo",
            params={
                "user-id": member_jwt,
                "comici-viewer-id": episode.viewer_id,
                "page-from": page_from,
                "page-to": page_to,
            },
            headers=HEADERS,
            timeout=30,
        )
        res.raise_for_status()
        body = res.json()
        if not isinstance(body, dict) or "result" not in body:
            msg = f"contentsInfo refused the request: {body}"
            raise ComiciError(msg)
        return body

    @staticmethod
    def _titles(soup: BeautifulSoup, viewer: Tag, viewer_id: str) -> tuple[str, str]:
        """Split `og:title` -- `"<series>・<episode> | <site>"` -- into its parts."""
        og = soup.find("meta", property="og:title")
        heading = str(og.attrs.get("content", "")) if isinstance(og, Tag) else ""
        if not heading and soup.title:
            heading = soup.title.get_text()
        heading = heading.rsplit(" | ", 1)[0].strip()

        series, _, episode = heading.partition("・")
        if not episode:
            series, episode = str(viewer.attrs.get("data-share-text", "")), heading
        return (series.lstrip("#").strip() or viewer_id, episode.strip() or viewer_id)

    def _save_pages(
        self,
        pages: list[Page],
        save_dir: Path,
        *,
        referer: str,
        only_first: bool = False,
        print_log: bool = False,
    ) -> None:
        wanted = pages[:1] if only_first else pages
        width = len(str(len(wanted)))
        progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("("),
            MofNCompleteColumn(),
            TextColumn("pages )"),
            TextColumn("remain:"),
            TimeRemainingColumn(),
            TextColumn("spent:"),
            TimeElapsedColumn(),
            disable=not print_log,
        )
        with progress:
            task = progress.add_task("[red]Downloading...", total=len(wanted))
            for index, page in enumerate(wanted):
                image = self._image(page, referer)
                image.save(save_dir / f"{index:0{width}d}.jpg", quality=95)
                progress.update(task, advance=1)

    def _image(self, page: Page, referer: str) -> Image.Image:
        res = self._session.get(
            page["imageUrl"],
            headers={**HEADERS, "Referer": referer},
            timeout=60,
        )
        res.raise_for_status()
        image = Image.open(BytesIO(res.content))
        return descramble(image, parse_scramble(page["scramble"]))
