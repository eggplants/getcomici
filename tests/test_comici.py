from __future__ import annotations

import json

import pytest
from PIL import Image
from requests import Session

from getcomici import __version__
from getcomici.comici import (
    COLUMNS,
    ROWS,
    TILES,
    Comici,
    ComiciError,
    NotAComiciPageError,
    descramble,
    parse_scramble,
)

SCRAMBLE = [1, 5, 13, 8, 4, 14, 3, 2, 10, 0, 11, 12, 7, 6, 9, 15]

EPISODE_HTML = """
<html><head>
<title>IRUKA・prologue | MANGABU!</title>
<meta property="og:title" content="IRUKA・prologue | MANGABU!(マンガ部!)"/>
</head><body>
<div id="comici-viewer" data-comici-viewer-id="abc123" data-api-domain="/api"
     data-share-text="IRUKA" data-next-episode-id="def456"></div>
</body></html>
"""


class FakeResponse:
    def __init__(self, content=b"", payload=None):
        self.content = content
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeSession(Session):
    """Answers by substring match on the requested URL."""

    def __init__(self, routes):
        super().__init__()
        self.routes = routes
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs.get("params")))
        for needle, response in self.routes.items():
            if needle in url:
                return response
        raise AssertionError(url)


def tiled_image(order):
    """A 4x4 grid of tiles, tile k painted with a colour derived from order[k]."""
    image = Image.new("RGB", (COLUMNS * 8, ROWS * 8))
    for position, tile in enumerate(order):
        col, row = divmod(position, ROWS)
        colour = (tile * 16, 255 - tile * 16, (tile * 37) % 256)
        image.paste(Image.new("RGB", (8, 8), colour), (col * 8, row * 8))
    return image


def test_version_is_available():
    assert __version__


def test_parse_scramble_reads_the_api_format():
    assert parse_scramble("[1, 5, 13, 8, 4, 14, 3, 2, 10, 0, 11, 12, 7, 6, 9, 15]") == SCRAMBLE


@pytest.mark.parametrize("bad", ["[0, 1, 2]", "[0, 0, " + "1, " * 13 + "1]"])
def test_parse_scramble_rejects_non_permutations(bad):
    with pytest.raises(ComiciError):
        parse_scramble(bad)


def test_descramble_restores_the_original():
    original = tiled_image(range(TILES))
    # The viewer copies source tile SCRAMBLE[f] into slot f, so the scrambled
    # image must hold original tile f at slot SCRAMBLE[f].
    inverse = [0] * TILES
    for slot, source in enumerate(SCRAMBLE):
        inverse[source] = slot
    scrambled = tiled_image(inverse)

    assert scrambled.tobytes() != original.tobytes()
    assert descramble(scrambled, SCRAMBLE).tobytes() == original.tobytes()


def test_descramble_leaves_the_uneven_edge_alone():
    # 34 = 4 * 8 + 2, so a two pixel strip falls outside the shuffled grid.
    image = Image.new("RGB", (34, 34), (10, 20, 30))
    image.putpixel((33, 33), (200, 100, 50))
    assert descramble(image, list(range(TILES))).getpixel((33, 33)) == (200, 100, 50)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://mangabu.jp/episodes/71f48a2c352ed", True),
        ("https://younganimal.com/episodes/006a5e6131753", True),
        ("http://mangabu.jp/episodes/71f48a2c352ed", False),
        ("https://example.com/episodes/1", False),
    ],
)
def test_is_valid_uri(url, expected):
    assert Comici.is_valid_uri(url) is expected


def test_episode_info_reads_the_viewer_element():
    session = FakeSession({"/episodes/": FakeResponse(content=EPISODE_HTML.encode())})
    episode = Comici(session).episode_info("https://mangabu.jp/episodes/71f48a2c352ed")

    assert episode.viewer_id == "abc123"
    assert episode.api_base == "https://mangabu.jp/api"
    assert episode.series_title == "IRUKA"
    assert episode.episode_title == "prologue"
    assert episode.next_url == "https://mangabu.jp/episodes/def456"


def test_episode_info_rejects_a_page_without_a_viewer():
    session = FakeSession({"/episodes/": FakeResponse(content=b"<html><body>nope</body></html>")})
    with pytest.raises(NotAComiciPageError):
        Comici(session).episode_info("https://example.com/episodes/1")


def test_episode_info_without_a_next_episode():
    html = EPISODE_HTML.replace('data-next-episode-id="def456"', 'data-next-episode-id=""')
    session = FakeSession({"/episodes/": FakeResponse(content=html.encode())})
    assert Comici(session).episode_info("https://mangabu.jp/episodes/x").next_url is None


def make_episode(session):
    return Comici(session).episode_info("https://mangabu.jp/episodes/71f48a2c352ed")


def test_pages_asks_for_the_total_before_the_range():
    pages = [
        {"imageUrl": "u2", "scramble": "[]", "sort": 1, "width": 8, "height": 8, "expiresOn": 0},
        {"imageUrl": "u1", "scramble": "[]", "sort": 0, "width": 8, "height": 8, "expiresOn": 0},
    ]
    session = FakeSession(
        {
            "/episodes/": FakeResponse(content=EPISODE_HTML.encode()),
            "contentsInfo": FakeResponse(payload={"totalPages": 2, "result": pages}),
        },
    )
    comici = Comici(session)
    result = comici.pages(make_episode(session))

    assert [page["sort"] for page in result] == [0, 1]
    ranges = [(params["page-from"], params["page-to"]) for _, params in session.calls[1:]]
    assert ranges == [(0, 0), (0, 1)]


def test_pages_is_empty_when_nothing_is_readable():
    session = FakeSession(
        {
            "/episodes/": FakeResponse(content=EPISODE_HTML.encode()),
            "contentsInfo": FakeResponse(payload={"totalPages": 0, "result": []}),
        },
    )
    assert Comici(session).pages(make_episode(session)) == []


def test_contents_info_raises_on_an_error_body():
    session = FakeSession(
        {
            "/episodes/": FakeResponse(content=EPISODE_HTML.encode()),
            "contentsInfo": FakeResponse(payload={"message": "something went wrong"}),
        },
    )
    with pytest.raises(ComiciError, match="refused"):
        Comici(session).pages(make_episode(session))


def test_get_writes_descrambled_pages_and_metadata(tmp_path):
    inverse = [0] * TILES
    for slot, source in enumerate(SCRAMBLE):
        inverse[source] = slot
    buffer = tmp_path / "src.png"
    tiled_image(inverse).save(buffer)

    page = {
        "imageUrl": "https://viewer.mangabu.jp/book/abc123/master-01.jpg",
        "scramble": json.dumps(SCRAMBLE),
        "sort": 0,
        "width": 32,
        "height": 32,
        "expiresOn": 0,
    }
    session = FakeSession(
        {
            "/episodes/": FakeResponse(content=EPISODE_HTML.encode()),
            "contentsInfo": FakeResponse(payload={"totalPages": 1, "result": [page]}),
            "viewer.mangabu.jp": FakeResponse(content=buffer.read_bytes()),
        },
    )

    next_url, save_dir, saved = Comici(session).get(
        "https://mangabu.jp/episodes/71f48a2c352ed",
        tmp_path,
        save_metadata=True,
    )

    assert saved is True
    assert next_url == "https://mangabu.jp/episodes/def456"
    assert save_dir == tmp_path / "IRUKA" / "prologue"
    assert (save_dir / "0.jpg").exists()
    assert json.loads((save_dir / "metadata.json").read_text(encoding="utf-8"))[0]["sort"] == 0


def test_get_skips_an_existing_directory(tmp_path):
    (tmp_path / "IRUKA" / "prologue").mkdir(parents=True)
    session = FakeSession({"/episodes/": FakeResponse(content=EPISODE_HTML.encode())})

    _, _, saved = Comici(session).get("https://mangabu.jp/episodes/71f48a2c352ed", tmp_path)

    assert saved is False
