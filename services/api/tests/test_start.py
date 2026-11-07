"""Container entrypoint: artifact restore gating and uvicorn arguments (no process is started)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from civicpulse_shared import artifacts as art

pytestmark = pytest.mark.integration
spec = importlib.util.spec_from_file_location("api_start", Path(__file__).resolve().parents[1] / "start.py")
assert spec and spec.loader
start = importlib.util.module_from_spec(spec)
spec.loader.exec_module(start)


def test_sample_mode_when_no_artifact_configured(capsys):
    env = {"PORT": "9000"}
    argv = start.prepare(env)
    assert argv[:2] == ["uvicorn", "civicpulse_api.main:app"]
    assert argv[argv.index("--port") + 1] == "9000" and argv[argv.index("--workers") + 1] == "1"
    assert "--limit-concurrency" in argv and "--timeout-graceful-shutdown" in argv
    assert "artifact_skipped" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("env", "reason"),
    [
        ({"CIVICPULSE_REQUIRE_ARTIFACT": "true"}, "CIVICPULSE_ARTIFACT_URL is not set"),
        ({"CIVICPULSE_ARTIFACT_URL": "https://x.invalid/s.tar.gz"}, "SHA256 is required"),
        ({"PORT": "eighty"}, "must be integers"),
    ],
)
def test_fails_closed(env, reason, capsys):
    with pytest.raises(SystemExit) as e:
        start.prepare(dict(env))
    assert e.value.code == 2 and reason in capsys.readouterr().out


def test_restores_pinned_snapshot_and_drops_token(sample_models, tmp_path, capsys):
    side = art.pack(sample_models.root, tmp_path / "dist")
    env = {
        "CIVICPULSE_ARTIFACT_URL": str(tmp_path / "dist" / side["archive"]),
        "CIVICPULSE_ARTIFACT_SHA256": side["archive_sha256"],
        "CIVICPULSE_ARTIFACT_ROOT": str(tmp_path / "a"),
        "CIVICPULSE_ARTIFACT_TOKEN": "secret-token-value",
    }
    start.prepare(env)
    assert Path(env["CIVICPULSE_DATA_DIR"]).name == side["snapshot_id"]
    assert "CIVICPULSE_ARTIFACT_TOKEN" not in env  # not inherited by the API or the MCP subprocess
    out = capsys.readouterr().out
    assert "artifact_ready" in out and "secret-token-value" not in out and str(tmp_path / "dist") not in out


def test_tampered_pin_refuses_to_start(sample_models, tmp_path, capsys):
    side = art.pack(sample_models.root, tmp_path / "dist")
    env = {
        "CIVICPULSE_ARTIFACT_URL": str(tmp_path / "dist" / side["archive"]),
        "CIVICPULSE_ARTIFACT_SHA256": "0" * 64,
        "CIVICPULSE_ARTIFACT_ROOT": str(tmp_path / "a"),
    }
    with pytest.raises(SystemExit):
        start.prepare(env)
    assert "checksum mismatch" in capsys.readouterr().out and "CIVICPULSE_DATA_DIR" not in env
