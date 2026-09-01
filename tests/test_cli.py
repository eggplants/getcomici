from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from getcomici import __version__
from getcomici.cli import check_url, main, parse_args
from getcomici.comici import ComiciError, NeedPurchase, NotAComiciPageError


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
        RecordingComici.instances.append(self)

    @staticmethod
    def is_valid_uri(url):
        return "mangabu.jp" in url

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
