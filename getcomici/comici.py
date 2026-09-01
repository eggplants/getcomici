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
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Linux"',
}

DOCUMENT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}
API_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}
IMAGE_HEADERS = {
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Sec-Fetch-Dest": "image",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "cross-site",
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

# Sites that hand their images over unscrambled still go through `descramble`,
# with a permutation that puts every tile back where it already was.
_IDENTITY_SCRAMBLE = json.dumps(list(range(TILES)))


class ComiciError(Exception):
    """Base class for every error this module raises."""


class NotAComiciPageError(ComiciError):
    """The fetched page carries no Comici+ viewer."""


class LoginError(ComiciError):
    """The site refused the credentials."""


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
    member_jwt: str = ""
    # Pages the episode JSON carried itself, on sites that render the viewer
    # client-side. None when the page had a viewer and `pages` has to ask
    # `contentsInfo` for them.
    inline_pages: tuple[Page, ...] | None = None


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
        self._id_tokens: dict[str, str] = {}

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

    def login(self, url: str, user_id: str, password: str) -> None:
        """Sign in, so episodes the account may read become readable.

        Sites run NextAuth behind `/api/auth`, with a credentials provider that
        takes a Comici ID or an email address. The session cookie lands on the
        shared session and the returned id token is sent with later API calls.
        This grants nothing the account does not already own.

        Args:
            url: Any URL on the site to sign in to.
            user_id: A Comici ID or the email address the account uses.
            password: The account's password.

        Raises:
            LoginError: The site refused the credentials.
        """
        origin = self._origin(url)
        auth = f"{origin}/api/auth"
        csrf = self._session.get(f"{auth}/csrf", headers=self._headers(url, API_HEADERS), timeout=30)
        csrf.raise_for_status()

        res = self._session.post(
            f"{auth}/callback/credentials",
            data={
                "id": user_id,
                "password": password,
                "csrfToken": csrf.json()["csrfToken"],
                "callbackUrl": f"{origin}/",
                "json": "true",
            },
            headers={**self._headers(url, API_HEADERS), "Origin": origin, "Referer": f"{origin}/"},
            timeout=30,
        )
        res.raise_for_status()

        session = self._session.get(f"{auth}/session", headers=self._headers(url, API_HEADERS), timeout=30)
        session.raise_for_status()
        token = (session.json() or {}).get("idToken")
        if not token:
            msg = f"{origin} refused the credentials for {user_id!r}."
            raise LoginError(msg)
        self._id_tokens[origin] = str(token)

    @staticmethod
    def _origin(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _headers(self, url: str, kind: dict[str, str] | None = None) -> dict[str, str]:
        """Headers for `url`, carrying the id token once its site is signed in.

        Args:
            url: The URL the headers are for.
            kind: `DOCUMENT_HEADERS`, `API_HEADERS` or `IMAGE_HEADERS`, saying
                what the request is fetching.

        Returns:
            The headers to send.
        """
        headers = {**HEADERS, **(kind or {})}
        token = self._id_tokens.get(self._origin(url))
        if token:
            headers["Authorization"] = token
        return headers

    def episode_info(self, url: str) -> Episode:
        """Read the viewer parameters off an episode page.

        Args:
            url: The episode URL.

        Returns:
            The parsed episode.

        Raises:
            NotAComiciPageError: The page carries no Comici+ viewer.
        """
        res = self._session.get(url, headers=self._headers(url, DOCUMENT_HEADERS), timeout=30)
        res.raise_for_status()
        soup = BeautifulSoup(res.content, "html.parser")

        viewer = soup.find(id=_VIEWER_ID)
        if not isinstance(viewer, Tag):
            return self._episode_from_api(url)

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
            member_jwt=str(viewer.attrs.get("data-member-jwt", "")),
            api_base=api_base,
            series_title=series_title,
            episode_title=episode_title,
            next_url=next_url,
        )

    def pages(self, episode: Episode, member_jwt: str | None = None) -> list[Page]:
        """List every page of an episode.

        `contentsInfo` refuses a range wider than the episode, so the total is
        asked for first and the real range fetched second.

        Args:
            episode: The episode to list.
            member_jwt: Overrides the token the episode page carried, if any.

        Returns:
            The pages, in reading order. Empty when the episode is not readable.
        """
        if episode.inline_pages is not None:
            return list(episode.inline_pages)
        if member_jwt is None:
            member_jwt = episode.member_jwt
        total = int(self._contents_info(episode, 0, 0, member_jwt).get("totalPages") or 0)
        if total <= 0:
            return []
        body = self._contents_info(episode, 0, total - 1, member_jwt)
        pages: list[Page] = list(body.get("result") or [])
        return sorted(pages, key=lambda page: page["sort"])

    def _episode_from_api(self, url: str) -> Episode:
        """Read an episode that renders its viewer only after hydration.

        Newer sites (ebookstore.corkagency.com) ship an episode page with no
        `#comici-viewer` element on it, and their `/api/episodes/{id}` hands the
        page images over directly, already unscrambled, instead of a viewer id
        to look up with `contentsInfo`.

        Args:
            url: The episode URL.

        Returns:
            The parsed episode, carrying its pages.

        Raises:
            NotAComiciPageError: The API describes no episode either.
        """
        parsed = urlparse(url)
        episode_id = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        api_base = f"{parsed.scheme}://{parsed.netloc}/api"
        res = self._session.get(
            f"{api_base}/episodes/{episode_id}",
            headers={**self._headers(url, API_HEADERS), "Referer": url},
            timeout=30,
        )
        body = res.json() if res.ok else None
        episode = body.get("episode") if isinstance(body, dict) else None
        if not isinstance(episode, dict):
            msg = f"no '#{_VIEWER_ID}' element on {url}, and its episode API describes none either."
            raise NotAComiciPageError(msg)

        series = episode.get("series") or {}
        summary = episode.get("summary") or {}
        next_id = str(episode.get("nextEpisodeId") or "")
        return Episode(
            url=url,
            viewer_id=str(episode.get("id") or episode_id),
            api_base=api_base,
            series_title=str(series.get("name") or "").strip() or episode_id,
            episode_title=str(summary.get("title") or "").strip() or episode_id,
            next_url=urljoin(url, next_id) if next_id else None,
            inline_pages=self._inline_pages(episode),
        )

    @staticmethod
    def _inline_pages(episode: dict[str, Any]) -> tuple[Page, ...]:
        """Turn the `content` blocks of an episode JSON into pages."""
        return tuple(
            Page(
                imageUrl=str(node["url"]),
                scramble=_IDENTITY_SCRAMBLE,
                sort=index,
                width=int(node.get("width") or 0),
                height=int(node.get("height") or 0),
                expiresOn=0,
            )
            for index, node in enumerate(
                node
                for node in episode.get("content") or []
                if isinstance(node, dict) and node.get("type") == "image" and node.get("url")
            )
        )

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
            # Some sites (studio.booklista.co.jp) answer 403 without a site Referer.
            headers={**self._headers(episode.url, API_HEADERS), "Referer": episode.url},
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
            headers={**self._headers(referer, IMAGE_HEADERS), "Referer": referer},
            timeout=60,
        )
        res.raise_for_status()
        image = Image.open(BytesIO(res.content))
        return descramble(image, parse_scramble(page["scramble"]))
