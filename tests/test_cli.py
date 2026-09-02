from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from getcomici import __version__
from getcomici.cli import check_url, episode_urls, main, parse_args
from getcomici.comici import ComiciError, LoginError, NeedPurchase, NotAComiciPageError


def test_check_url_accepts_an_unlisted_https_host():
    assert check_url("https://example.com/episodes/1") == "https://example.com/episodes/1"


def test_check_url_rejects_plain_http():
    with pytest.raises(Exception, match="not an https URL"):
        check_url("http://mangabu.jp/episodes/1")


def test_parse_args_defaults():
    parsed = parse_args(["https://mangabu.jp/episodes/1"])
    assert parsed.savedir == "."
    assert (parsed.bulk, parsed.first, parsed.overwrite, parsed.metadata, parsed.quiet) == (
        False,
        False,
        False,
        False,
        False,
    )


def test_version_flag_prints_the_version(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out


class RecordingComici:
    """Stands in for `Comici`, walking a canned chain of episodes."""

    instances: list[RecordingComici] = []

    def __init__(self, *_args, **_kwargs):
        self.gets: list[str] = []
        self.logins: list[tuple[str, str, str]] = []
        RecordingComici.instances.append(self)

    def login(self, url, user_id, password):
        self.logins.append((url, user_id, password))

    @staticmethod
    def is_valid_uri(url):
        return "mangabu.jp" in url

    @staticmethod
    def is_series(url):
        return "/series/" in url

    def series_urls(self, url):
        return [f"https://mangabu.jp/episodes/feed{index}" for index in range(3)]

    def get(self, url, save_path=".", **_kwargs):
        self.gets.append(url)
        index = len(self.gets)
        next_url = f"https://mangabu.jp/episodes/{index}" if index < 3 else None
        return next_url, Path(save_path) / f"ep{index}", True


@pytest.fixture
def recording(monkeypatch):
    RecordingComici.instances.clear()
    monkeypatch.setattr("getcomici.cli.Comici", RecordingComici)
    return RecordingComici


def test_main_downloads_a_single_episode(recording, capsys):
    main(["https://mangabu.jp/episodes/0"])
    assert recording.instances[0].gets == ["https://mangabu.jp/episodes/0"]
    assert "done." in capsys.readouterr().out


def test_bulk_follows_the_next_episode_chain(recording):
    main(["-b", "https://mangabu.jp/episodes/0"])
    assert recording.instances[0].gets == [
        "https://mangabu.jp/episodes/0",
        "https://mangabu.jp/episodes/1",
        "https://mangabu.jp/episodes/2",
    ]


def test_unknown_host_warns_but_still_tries(recording, capsys):
    main(["https://example.com/episodes/0"])
    assert "not a known Comici+ site" in capsys.readouterr().out
    assert recording.instances[0].gets == ["https://example.com/episodes/0"]


def test_quiet_prints_nothing(recording, capsys):
    main(["-q", "https://example.com/episodes/0"])
    assert capsys.readouterr().out == ""


FEED_URL = "https://mangabu.jp/series/b167ea507d35f/rss"
SERIES_URL = "https://mangabu.jp/series/b167ea507d35f/2"


@pytest.mark.parametrize("url", [FEED_URL, SERIES_URL])
def test_a_series_downloads_every_episode_it_lists(recording, capsys, url):
    main([url])

    assert recording.instances[0].gets == [
        "https://mangabu.jp/episodes/feed0",
        "https://mangabu.jp/episodes/feed1",
        "https://mangabu.jp/episodes/feed2",
    ]
    assert "series: 3 episodes listed." in capsys.readouterr().out


def test_bulk_does_nothing_for_a_series(recording, capsys):
    main(["-b", FEED_URL])

    # The listing already names every episode, so no next episode is followed.
    assert len(recording.instances[0].gets) == 3
    assert "-b does nothing for a series" in capsys.readouterr().err


def test_episode_urls_leaves_an_episode_url_alone(recording):
    assert episode_urls(recording(), "https://mangabu.jp/episodes/0", quiet=True) == [
        "https://mangabu.jp/episodes/0",
    ]


def test_a_locked_episode_only_skips_itself_in_a_series(monkeypatch, capsys):
    class SecondLocked(RecordingComici):
        def get(self, url, save_path=".", **_kwargs):
            self.gets.append(url)
            if url.endswith("feed1"):
                msg = "no '#comici-viewer' element"
                raise NotAComiciPageError(msg)
            return None, Path(save_path) / "ep", True

    monkeypatch.setattr("getcomici.cli.Comici", SecondLocked)
    main([FEED_URL])

    captured = capsys.readouterr()
    assert len(SecondLocked.instances[-1].gets) == 3
    assert "skip: https://mangabu.jp/episodes/feed1 is not readable." in captured.err
    assert "done." in captured.out


def test_a_paid_episode_in_a_series_is_skipped(monkeypatch, capsys):
    class Paywalled(RecordingComici):
        def get(self, url, save_path=".", **_kwargs):
            self.gets.append(url)
            if url.endswith("feed0"):
                warnings.warn("locked", NeedPurchase, stacklevel=1)
            return None, Path(save_path) / "ep", True

    monkeypatch.setattr("getcomici.cli.Comici", Paywalled)
    main([FEED_URL])

    captured = capsys.readouterr()
    assert "skip: 'locked' needs a purchase or a login." in captured.err
    assert len(Paywalled.instances[-1].gets) == 3


def test_a_series_with_nothing_readable_exits_nonzero(monkeypatch, capsys):
    class AllLocked(RecordingComici):
        def get(self, url, save_path=".", **_kwargs):
            msg = "no '#comici-viewer' element"
            raise NotAComiciPageError(msg)

    monkeypatch.setattr("getcomici.cli.Comici", AllLocked)
    with pytest.raises(SystemExit) as excinfo:
        main([FEED_URL])

    assert excinfo.value.code == 1
    assert "no episode in the series was readable" in capsys.readouterr().err


def test_a_paid_episode_stops_the_run(monkeypatch, capsys):
    class Paywalled(RecordingComici):
        def get(self, url, save_path=".", **_kwargs):
            warnings.warn("locked", NeedPurchase, stacklevel=1)
            raise AssertionError

    monkeypatch.setattr("getcomici.cli.Comici", Paywalled)
    main(["-b", "https://mangabu.jp/episodes/0"])
    assert "needs a purchase or a login" in capsys.readouterr().err


def test_a_comici_error_exits_nonzero(monkeypatch, capsys):
    class Broken(RecordingComici):
        def get(self, url, save_path=".", **_kwargs):
            msg = "contentsInfo refused the request"
            raise ComiciError(msg)

    monkeypatch.setattr("getcomici.cli.Comici", Broken)
    with pytest.raises(SystemExit) as excinfo:
        main(["https://mangabu.jp/episodes/0"])
    assert excinfo.value.code == 1
    assert "contentsInfo refused" in capsys.readouterr().err


def test_a_locked_next_episode_ends_a_bulk_run(monkeypatch, capsys):
    class LockedSecond(RecordingComici):
        def get(self, url, save_path=".", **_kwargs):
            self.gets.append(url)
            if len(self.gets) > 1:
                msg = "no '#comici-viewer' element"
                raise NotAComiciPageError(msg)
            return "https://mangabu.jp/episodes/locked", Path(save_path) / "ep1", True

    monkeypatch.setattr("getcomici.cli.Comici", LockedSecond)
    main(["-b", "https://mangabu.jp/episodes/0"])

    captured = capsys.readouterr()
    assert "stop: the next episode is not readable." in captured.err
    assert "done." in captured.out


def test_a_locked_first_episode_exits_nonzero(monkeypatch, capsys):
    class LockedFirst(RecordingComici):
        def get(self, url, save_path=".", **_kwargs):
            msg = "no '#comici-viewer' element"
            raise NotAComiciPageError(msg)

    monkeypatch.setattr("getcomici.cli.Comici", LockedFirst)
    with pytest.raises(SystemExit) as excinfo:
        main(["https://mangabu.jp/episodes/0"])

    assert excinfo.value.code == 1
    assert "comici-viewer" in capsys.readouterr().err


def test_login_runs_before_downloading(recording, capsys):
    main(["-u", "comici-id", "-p", "pw", "https://mangabu.jp/episodes/0"])
    assert recording.instances[0].logins == [("https://mangabu.jp/episodes/0", "comici-id", "pw")]
    assert "logged in as: comici-id" in capsys.readouterr().out


def test_a_missing_password_is_prompted_for(recording, monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda *_: "typed-pw")
    main(["-u", "comici-id", "https://mangabu.jp/episodes/0"])
    assert recording.instances[0].logins[0][2] == "typed-pw"


def test_no_credentials_means_no_login(recording):
    main(["https://mangabu.jp/episodes/0"])
    assert recording.instances[0].logins == []


def test_password_without_username_warns(recording, capsys):
    main(["-p", "pw", "https://mangabu.jp/episodes/0"])
    assert "-p without -u does nothing" in capsys.readouterr().err
    assert recording.instances[0].logins == []


def test_a_refused_login_exits_nonzero(monkeypatch, capsys):
    class Refused(RecordingComici):
        def login(self, url, user_id, password):
            msg = "refused the credentials"
            raise LoginError(msg)

    monkeypatch.setattr("getcomici.cli.Comici", Refused)
    with pytest.raises(SystemExit) as excinfo:
        main(["-u", "x", "-p", "y", "https://mangabu.jp/episodes/0"])

    assert excinfo.value.code == 1
    assert "refused the credentials" in capsys.readouterr().err
