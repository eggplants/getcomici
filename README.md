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

Retrieve and save images from manga distribution sites using Comici+.

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

docker run --rm ghcr.io/eggplants/getcomici eggplant
```

## CLI

```shellsession
$ getcomici
Hello, world!

$ getcomici eggplant
Hello, eggplant!
```

## Library

```python
import getcomici

print(getcomici.__version__)
```

## License

[MIT License](
  <https://github.com/eggplants/getcomici/blob/master/LICENSE.txt>
)
