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
  ghcr.io/eggplants/getcomici https://mangabu.jp/episodes/71f48a2c352ed
```

## CLI

```shellsession
$ cget https://mangabu.jp/episodes/71f48a2c352ed
get: https://mangabu.jp/episodes/71f48a2c352ed
  Downloading... ━━━━━━━━━━━━ 100% ( 18/18 pages ) remain: 0:00:00 spent: 0:00:02
saved: IRUKA/prologue
done.
```

## Library

```python
from getcomici import Comici

comici = Comici()
next_url, save_dir, saved = comici.get(
    "https://mangabu.jp/episodes/71f48a2c352ed",
    save_path="out",
)
```

## License

[MIT License](
  <https://github.com/eggplants/getcomici/blob/master/LICENSE.txt>
)
