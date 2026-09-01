"""Command line entry point for getcomici."""

from __future__ import annotations

import shutil
import sys
import warnings
from argparse import (
    ArgumentDefaultsHelpFormatter,
    ArgumentParser,
    ArgumentTypeError,
    Namespace,
    RawDescriptionHelpFormatter,
)
from urllib.parse import urlparse

from . import __version__
from .comici import VALID_HOSTS, Comici, ComiciError, NeedPurchase, NotAComiciPageError


class HelpFormatter(ArgumentDefaultsHelpFormatter, RawDescriptionHelpFormatter):
    """Show argument defaults while keeping the description's own line breaks."""


def available_list() -> str:
    """Render the known Comici+ sites for the help epilog."""
    return "known sites:\n  - https://" + "\n  - https://".join(VALID_HOSTS)


def check_url(url: str) -> str:
    """Accept any https URL, since unlisted sites may still run the viewer.

    Args:
        url: The URL given on the command line.

    Returns:
        The URL unchanged.

    Raises:
        ArgumentTypeError: The URL is not https.
    """
    if urlparse(url).scheme != "https":
        msg = f"'{url}' is not an https URL.\n{available_list()}"
        raise ArgumentTypeError(msg)
    return url


def parse_args(args: list[str] | None = None) -> Namespace:
    """Parse the command line.

    Args:
        args: Arguments to parse instead of `sys.argv[1:]`. Used by the tests.

    Returns:
        The parsed arguments.
    """
    parser = ArgumentParser(
        prog="getcomici",
        description="Retrieve and save images from manga distribution sites using Comici+.",
        epilog=available_list(),
        formatter_class=lambda prog: HelpFormatter(
            prog,
            width=shutil.get_terminal_size(fallback=(120, 50)).columns,
            max_help_position=40,
        ),
    )
    parser.add_argument("url", type=check_url, help="episode url")
    parser.add_argument("-b", "--bulk", action="store_true", help="follow every next episode")
    parser.add_argument("-d", "--savedir", metavar="DIR", default=".", help="directory to save into")
    parser.add_argument("-f", "--first", action="store_true", help="download only the first page")
    parser.add_argument("-o", "--overwrite", action="store_true", help="download again if it exists")
    parser.add_argument("-m", "--metadata", action="store_true", help="save page metadata as json")
    parser.add_argument("-q", "--quiet", action="store_true", help="disable console output")
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(args)


def main(args: list[str] | None = None) -> None:
    """Run the command."""
    parsed = parse_args(args)
    comici = Comici()
    if not parsed.quiet and not Comici.is_valid_uri(parsed.url):
        print(f"warning: {urlparse(parsed.url).hostname} is not a known Comici+ site, trying anyway.")

    next_url: str | None = parsed.url
    first = True
    try:
        while next_url:
            if not parsed.quiet:
                print("get:", next_url)
            with warnings.catch_warnings():
                warnings.simplefilter("error", NeedPurchase)
                try:
                    next_url, save_dir, saved = comici.get(
                        next_url,
                        save_path=parsed.savedir,
                        overwrite=parsed.overwrite,
                        only_first=parsed.first,
                        save_metadata=parsed.metadata,
                        print_log=not parsed.quiet,
                    )
                except NeedPurchase as exc:
                    print(f"stop: '{exc.args[0]}' needs a purchase or a login.", file=sys.stderr)
                    break
                except NotAComiciPageError:
                    # Locked episodes serve a purchase page with no viewer on it,
                    # which is where a bulk run is meant to end rather than fail.
                    if first:
                        raise
                    print("stop: the next episode is not readable.", file=sys.stderr)
                    break
            if not parsed.quiet:
                print("saved:" if saved else "skipped (already there):", save_dir)
            if not parsed.bulk:
                break
            first = False
    except ComiciError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if not parsed.quiet:
        print("done.")


if __name__ == "__main__":
    main()
