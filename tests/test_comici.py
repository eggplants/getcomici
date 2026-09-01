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
    VALID_HOSTS,
    Comici,
    ComiciError,
    LoginError,
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
    def __init__(self, content=b"", payload=None, ok=True):
        self.content = content
        self._payload = payload
        self.ok = ok

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
        self.posts = []
        self.headers_seen = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs.get("params")))
        self.headers_seen.append(kwargs.get("headers") or {})
        return self._route(url)

    def post(self, url, data=None, json=None, **_kwargs):
        self.posts.append((url, data if data is not None else json))
        return self._route(url)

    def _route(self, url):
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
    session = FakeSession(
        {
            "/api/episodes/": FakeResponse(payload={}),
            "/episodes/": FakeResponse(content=b"<html><body>nope</body></html>"),
        },
    )
    with pytest.raises(NotAComiciPageError):
        Comici(session).episode_info("https://example.com/episodes/1")


EPISODE_API = {
    "episode": {
        "id": "ad51b31190681",
        "nextEpisodeId": "d05c9cd20ca35",
        "series": {"name": "IRUKA"},
        "summary": {"title": "1話"},
        "content": [
            {"type": "image", "url": "https://cdn.comici.jp/a.jpg", "width": 800, "height": 2560},
            {"type": "html", "html": ""},
            {"type": "image", "url": "https://cdn.comici.jp/b.jpg", "width": 800, "height": 1760},
        ],
    },
}


def hydrated_session():
    """A site whose episode HTML carries no viewer, as corkagency's does not."""
    return FakeSession(
        {
            "/api/episodes/": FakeResponse(payload=EPISODE_API),
            "/episodes/": FakeResponse(content=b"<html><body>hydrated later</body></html>"),
        },
    )


def test_episode_info_falls_back_to_the_episode_api():
    session = hydrated_session()
    episode = Comici(session).episode_info("https://ebookstore.corkagency.com/episodes/ad51b31190681")

    assert episode.api_base == "https://ebookstore.corkagency.com/api"
    assert episode.series_title == "IRUKA"
    assert episode.episode_title == "1話"
    assert episode.next_url == "https://ebookstore.corkagency.com/episodes/d05c9cd20ca35"


def test_inline_pages_skip_non_image_blocks_and_never_scramble():
    session = hydrated_session()
    comici = Comici(session)
    pages = comici.pages(comici.episode_info("https://ebookstore.corkagency.com/episodes/ad51b31190681"))

    assert [page["imageUrl"] for page in pages] == ["https://cdn.comici.jp/a.jpg", "https://cdn.comici.jp/b.jpg"]
    assert [page["sort"] for page in pages] == [0, 1]
    # The identity permutation leaves every tile where it already was.
    assert parse_scramble(pages[0]["scramble"]) == list(range(TILES))
    # No contentsInfo round trip is needed: the episode JSON already had them.
    assert not [url for url, _ in session.calls if "contentsInfo" in url]


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


def test_contents_info_sends_the_site_referer():
    # studio.booklista.co.jp answers 403 without one.
    session = FakeSession(
        {
            "/episodes/": FakeResponse(content=EPISODE_HTML.encode()),
            "contentsInfo": FakeResponse(payload={"totalPages": 0, "result": []}),
        },
    )
    Comici(session).pages(make_episode(session))

    assert session.headers_seen[-1]["Referer"] == "https://mangabu.jp/episodes/71f48a2c352ed"


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


AUTH_ROUTES = {
    "/api/auth/csrf": FakeResponse(payload={"csrfToken": "tok"}),
    "/api/auth/callback/credentials": FakeResponse(payload={"url": "https://mangabu.jp/"}),
    "/api/auth/session": FakeResponse(payload={"idToken": "id-token-value", "user": 1}),
}


def test_login_posts_the_credentials_with_the_csrf_token():
    session = FakeSession(dict(AUTH_ROUTES))
    Comici(session).login("https://mangabu.jp/episodes/1", "comici-id", "pw")

    url, data = session.posts[0]
    assert url == "https://mangabu.jp/api/auth/callback/credentials"
    assert data["id"] == "comici-id"
    assert data["password"] == "pw"
    assert data["csrfToken"] == "tok"


def test_login_raises_when_no_token_comes_back():
    routes = dict(AUTH_ROUTES) | {"/api/auth/session": FakeResponse(payload={})}
    with pytest.raises(LoginError, match="refused the credentials"):
        Comici(FakeSession(routes)).login("https://mangabu.jp/episodes/1", "who", "pw")


def test_a_signed_in_site_sends_the_token():
    routes = dict(AUTH_ROUTES) | {
        "/episodes/": FakeResponse(content=EPISODE_HTML.encode()),
        "contentsInfo": FakeResponse(payload={"totalPages": 1, "result": []}),
    }
    session = FakeSession(routes)
    comici = Comici(session)
    comici.login("https://mangabu.jp/episodes/1", "comici-id", "pw")
    comici.pages(comici.episode_info("https://mangabu.jp/episodes/71f48a2c352ed"))

    assert session.headers_seen[-1]["Authorization"] == "id-token-value"


def test_another_site_does_not_get_the_token():
    routes = dict(AUTH_ROUTES) | {
        "/episodes/": FakeResponse(content=EPISODE_HTML.encode()),
        "contentsInfo": FakeResponse(payload={"totalPages": 1, "result": []}),
    }
    session = FakeSession(routes)
    comici = Comici(session)
    comici.login("https://mangabu.jp/episodes/1", "comici-id", "pw")
    comici.episode_info("https://younganimal.com/episodes/2")

    assert "Authorization" not in session.headers_seen[-1]


def test_pages_uses_the_token_the_page_rendered():
    html = EPISODE_HTML.replace(
        'data-api-domain="/api"',
        'data-api-domain="/api" data-member-jwt="page-jwt"',
    )
    session = FakeSession(
        {
            "/episodes/": FakeResponse(content=html.encode()),
            "contentsInfo": FakeResponse(payload={"totalPages": 1, "result": []}),
        },
    )
    comici = Comici(session)
    comici.pages(comici.episode_info("https://mangabu.jp/episodes/71f48a2c352ed"))

    assert session.calls[-1][1]["user-id"] == "page-jwt"


# One episode per known site that is free to read without an account.
TEST_URLS: dict[str, str] = {
    "asacomi.jp": "https://asacomi.jp/episodes/d689876764c05",
    "bibibi-comic.com": "https://bibibi-comic.com/episodes/3fc263ee98e51",
    "championcross.jp": "https://championcross.jp/episodes/f79c98b6ede83",
    "comic-growl.com": "https://comic-growl.com/episodes/ae67f63a142b8",
    "comic-room-base.com": "https://comic-room-base.com/episodes/49e48489486b7",
    "comic.j-nbooks.jp": "https://comic.j-nbooks.jp/episodes/4c9428882f24a",
    "comicpash.jp": "https://comicpash.jp/episodes/3e051ee5500c3",
    "comicride.jp": "https://comicride.jp/episodes/e7137bf1e8b27",
    "comics.manga-bang.com": "https://comics.manga-bang.com/episodes/3e3efa60aa9b9",
    "comirela.com": "https://comirela.com/episodes/78d565d1005a1",
    "ebookstore.corkagency.com": "https://ebookstore.corkagency.com/episodes/ad51b31190681",
    "g-comi.jp": "https://g-comi.jp/episodes/cb3488365c75c",
    "hanayume.com": "https://hanayume.com/episodes/bc43f35254f09",
    "hayacomic.jp": "https://hayacomic.jp/episodes/86dbdd38cbdba",
    "heros-web.com": "https://heros-web.com/episodes/3a2698c5efefe",
    "kansai.mag-garden.co.jp": "https://kansai.mag-garden.co.jp/episodes/24c0c8b3e0b5b",
    "kimicomi.com": "https://kimicomi.com/episodes/a5c306c4268e1",
    "manga-zegra.com": "https://manga-zegra.com/episodes/ce0950449d914",
    "mangabu.jp": "https://mangabu.jp/episodes/ff7e9e1616543",
    "mangalt.jp": "https://mangalt.jp/episodes/2e416d98ce780",
    "mangaspa.nikkan-spa.jp": "https://mangaspa.nikkan-spa.jp/episodes/5fbbe0d610d7b",
    "namicomic.jp": "https://namicomic.jp/episodes/6372afa2ba503",
    "piacomic.jp": "https://piacomic.jp/episodes/d3d3e7ba955dd",
    "studio.booklista.co.jp": "https://studio.booklista.co.jp/episodes/de1ce4d9cc0ae",
    "takecomic.jp": "https://takecomic.jp/episodes/6b35483f82ab3",
    "younganimal.com": "https://younganimal.com/episodes/b790a79dd70a7",
    "youngchampion.jp": "https://youngchampion.jp/episodes/2bd90287798ab",
}


def test_every_known_host_is_covered():
    assert set(TEST_URLS) == set(VALID_HOSTS)


@pytest.mark.parametrize("host", TEST_URLS)
def test_site_download(tmp_path, host):
    _next_url, save_dir, saved = Comici().get(TEST_URLS[host], tmp_path, only_first=True)

    assert saved is True
    assert (save_dir / "0.jpg").exists()
