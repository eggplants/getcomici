""".. include:: ../README.md"""  # noqa: D415

from __future__ import annotations

import importlib.metadata

from .comici import (
    VALID_HOSTS,
    Comici,
    ComiciError,
    Episode,
    NeedPurchase,
    NotAComiciPageError,
    Page,
    descramble,
    parse_scramble,
)

try:
    __version__ = importlib.metadata.version(__name__)
except importlib.metadata.PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"

__all__ = (
    "VALID_HOSTS",
    "Comici",
    "ComiciError",
    "Episode",
    "NeedPurchase",
    "NotAComiciPageError",
    "Page",
    "descramble",
    "parse_scramble",
)
