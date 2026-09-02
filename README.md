# getcomici

[![PyPI](
  <https://img.shields.io/pypi/v/getcomici?color=blue>
  )](
  <https://pypi.org/project/getcomici/>
) [![CI](
  <https://github.com/eggplants/getcomici/actions/workflows/ci.yml/badge.svg>
  )](
  <https://github.com/eggplants/getcomici/actions/workflows/ci.yml>
)

[![ghcr size](
  <https://ghcr-badge.egpl.dev/eggplants/getcomici/size>
)](
  <https://github.com/eggplants/getcomici/pkgs/container/getcomici>
)

Retrieve and save images from manga distribution sites using [Comici+](https://comici.co.jp/business/comici-plus).

Note: Redistribution of downloaded image data is prohibited. Please keep it to private use.

## Valid URL Formats

- `<host>/episodes/<id>`
  - e.g. <https://takecomic.jp/episodes/74f33031e13cd>
- `<host>/series/<id>/rss`
  - e.g. <https://takecomic.jp/series/b167ea507d35f/rss>
  - Every episode the feed lists is downloaded, newest first.
  - The feed only carries the most recent episodes; use the series page below for the whole series.
- `<host>/series/<id>`, with or without `/new` or a page number on it
  - e.g. <https://takecomic.jp/series/3f846451aff2d>, <https://takecomic.jp/series/3f846451aff2d/new>,
    <https://takecomic.jp/series/3f846451aff2d/2>
  - The episode list is read from page 1 upwards until a page 404s, and every episode linked from it
    is downloaded, oldest first.

## Available Hosts

<details>

- <https://asacomi.jp>
- <https://bibibi-comic.com>
- <https://championcross.jp>
- <https://comic-growl.com>
- <https://comic-room-base.com>
- <https://comic.j-nbooks.jp>
- <https://comicpash.jp>
- <https://comicride.jp>
- <https://comics.manga-bang.com>
- <https://comirela.com>
- <https://ebookstore.corkagency.com>
- <https://g-comi.jp>
- <https://hanayume.com>
- <https://hayacomic.jp>
- <https://heros-web.com>
- <https://kansai.mag-garden.co.jp>
- <https://kimicomi.com>
- <https://manga-zegra.com>
- <https://mangabu.jp>
- <https://mangalt.jp>
- <https://mangaspa.nikkan-spa.jp>
- <https://namicomic.jp>
- <https://piacomic.jp>
- <https://studio.booklista.co.jp>
- <https://takecomic.jp>
- <https://younganimal.com>
- <https://youngchampion.jp>

</details>

## Installation

```bash
# mise via github release
mise use -g github:eggplants/getcomici

# mise via pipx
mise use -g pipx:getcomici

# pipx
pipx install getcomici

# pip
pip install getcomici
```

### Docker

```bash
docker pull ghcr.io/eggplants/getcomici

docker run --rm -v "$PWD:/work" -w /work \
  ghcr.io/eggplants/getcomici https://takecomic.jp/episodes/74f33031e13cd
```

## CLI

```shellsession
$ cget https://takecomic.jp/episodes/74f33031e13cd
get: https://takecomic.jp/episodes/74f33031e13cd
  Downloading... ━━━━━━━━━━━━ 100% ( 31/31 pages ) remain: 0:00:00 spent: 0:00:02
saved: メイドインアビス/1話
done.
```

## Library

```python
from getcomici import Comici

comici = Comici()
next_url, save_dir, saved = comici.get(
    "https://takecomic.jp/episodes/74f33031e13cd",
    save_path="out",
)
```

## License

[MIT License](
  <https://github.com/eggplants/getcomici/blob/master/LICENSE.txt>
)
