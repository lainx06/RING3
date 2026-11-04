"""T-19: `ring3 console`: no --budget-usd means no key, no run directory, no voice; with one, its checks."""

import pytest

from ring3bridge import cli, console


class FakeSock:
    def close(self):
        pass


class FakeStd:
    def fileno(self):
        return 0

    def write(self, _s):
        return 0

    def flush(self):
        pass


@pytest.fixture
def no_com1(monkeypatch):
    seen = {}

    def fake_serve(com1, in_fd, out_fd, voice=None, events=None):
        seen["voice"] = voice
        return 0

    monkeypatch.setattr(console, "_connect", lambda *a: FakeSock())
    monkeypatch.setattr(console, "serve", fake_serve)
    monkeypatch.setattr("sys.stdin", FakeStd())
    monkeypatch.setattr(cli, "RUNS", None)  # any use of the run folder would fail
    return seen


def test_without_a_budget_voice_is_off_and_no_key_is_read(no_com1, monkeypatch, capfd):
    def no_key(*_a):
        raise AssertionError("the key was looked up")

    monkeypatch.setattr("ring3bridge.models.transport.HttpsTransport.__init__", no_key)
    assert cli.main(["console"]) == 0
    assert no_com1["voice"] is None
    assert "Voice off" in capfd.readouterr().out


def test_a_budget_without_the_helper_folder_is_refused(no_com1, tmp_path):
    with pytest.raises(SystemExit, match="--voice-dir"):
        cli.main(["console", "--budget-usd", "0.01"])
    with pytest.raises(SystemExit, match="--voice-dir"):
        cli.main(["console", "--budget-usd", "0.01", "--voice-dir", str(tmp_path / "missing")])
    assert "voice" not in no_com1


def test_a_budget_without_the_key_is_refused_without_naming_a_file(no_com1, monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    assert cli.main(["console", "--budget-usd", "0.01", "--voice-dir", str(tmp_path)]) == 2
    assert "environment" in capsys.readouterr().err
    assert "voice" not in no_com1


def test_a_bad_budget_is_refused(no_com1, tmp_path):
    for bad in ("0", "-1", "lots", "0.0000001"):
        with pytest.raises(ValueError):
            cli.main(["console", "--budget-usd", bad, "--voice-dir", str(tmp_path)])
