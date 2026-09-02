"""Command line entry point for getcomici."""

from __future__ import annotations

import getpass
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
    parser.add_argument("url", type=check_url, help="episode url, or a series url to take every episode from")
    parser.add_argument("-b", "--bulk", action="store_true", help="follow every next episode")
    parser.add_argument("-d", "--savedir", metavar="DIR", default=".", help="directory to save into")
    parser.add_argument("-f", "--first", action="store_true", help="download only the first page")
    parser.add_argument("-o", "--overwrite", action="store_true", help="download again if it exists")
    parser.add_argument("-m", "--metadata", action="store_true", help="save page metadata as json")
    parser.add_argument("-u", "--username", metavar="ID", help="comici id or email to log in with")
    parser.add_argument("-p", "--password", metavar="PW", help="password (prompted for if -u is given without it)")
    parser.add_argument("-q", "--quiet", action="store_true", help="disable console output")
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(args)


def episode_urls(comici: Comici, url: str, *, quiet: bool) -> list[str]:
    """The episodes to download: everything a series lists, or the URL itself.

    Args:
        comici: The client to read the series with.
        url: The URL given on the command line.
        quiet: Print nothing.

    Returns:
        One episode URL per download.
    """
    if not Comici.is_series(url):
        return [url]
    urls = comici.series_urls(url)
    if not quiet:
        print(f"series: {len(urls)} episodes listed.")
    return urls


def download(comici: Comici, queue: list[str], parsed: Namespace, *, series: bool) -> int:
    """Download every queued episode.

    Args:
        comici: The client to download with.
        queue: The episodes to download, extended with the next episode of each
            when `-b` walks a chain.
        parsed: The parsed command line.
        series: The queue came from a series listing, whose episodes stand on
            their own: a locked one only skips itself, where a chain has to end
            there.

    Returns:
        How many episodes were downloaded or found already there.
    """
    done = 0
    while queue:
        url = queue.pop(0)
        if not parsed.quiet:
            print("get:", url)
        with warnings.catch_warnings():
            warnings.simplefilter("error", NeedPurchase)
            try:
                next_url, save_dir, saved = comici.get(
                    url,
                    save_path=parsed.savedir,
                    overwrite=parsed.overwrite,
                    only_first=parsed.first,
                    save_metadata=parsed.metadata,
                    print_log=not parsed.quiet,
                )
            except NeedPurchase as exc:
                print(
                    f"{'skip' if series else 'stop'}: '{exc.args[0]}' needs a purchase or a login.",
                    file=sys.stderr,
                )
                if series:
                    continue
                break
            except NotAComiciPageError:
                # Locked episodes serve a purchase page with no viewer on it,
                # which is where a bulk run is meant to end rather than fail.
                if not series and not done:
                    raise
                print(
                    f"skip: {url} is not readable." if series else "stop: the next episode is not readable.",
                    file=sys.stderr,
                )
                if series:
                    continue
                break
        done += 1
        if not parsed.quiet:
            print("saved:" if saved else "skipped (already there):", save_dir)
        if parsed.bulk and not series and next_url:
            queue.append(next_url)
    return done


def main(args: list[str] | None = None) -> None:
    """Run the command."""
    parsed = parse_args(args)
    comici = Comici()
    if parsed.username:
        password = parsed.password or getpass.getpass("password: ")
        try:
            comici.login(parsed.url, parsed.username, password)
        except ComiciError as exc:
            print(f"error: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        if not parsed.quiet:
            print("logged in as:", parsed.username)
    elif parsed.password:
        print("warning: -p without -u does nothing.", file=sys.stderr)
    if not parsed.quiet and not Comici.is_valid_uri(parsed.url):
        print(f"warning: {urlparse(parsed.url).hostname} is not a known Comici+ site, trying anyway.")

    # A series listing already names every episode, so there is no next episode to follow.
    series = Comici.is_series(parsed.url)
    if series and parsed.bulk:
        print("warning: -b does nothing for a series, every listed episode is downloaded.", file=sys.stderr)

    try:
        queue = episode_urls(comici, parsed.url, quiet=parsed.quiet)
        done = download(comici, queue, parsed, series=series)
    except ComiciError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if series and not done:
        print("error: no episode in the series was readable.", file=sys.stderr)
        raise SystemExit(1)
    if not parsed.quiet:
        print("done.")


if __name__ == "__main__":
    main()
