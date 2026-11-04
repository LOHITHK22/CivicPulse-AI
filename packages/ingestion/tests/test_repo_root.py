"""REPO_ROOT resolution: must work for editable checkouts AND regular installs (Docker images)."""

from __future__ import annotations

from pathlib import Path

from civicpulse_shared import config


def test_checkout_root_contains_configs():
    assert (config.REPO_ROOT / "configs" / "ingestion.yaml").is_file()


def test_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("CIVICPULSE_REPO_ROOT", str(tmp_path))
    assert config._repo_root() == tmp_path.resolve()


def test_falls_back_to_cwd_when_installed_outside_checkout(monkeypatch, tmp_path):
    # Simulate a site-packages install: the module file is not under a directory with configs/.
    fake = tmp_path / "site-packages" / "civicpulse_shared" / "x" / "config.py"
    monkeypatch.delenv("CIVICPULSE_REPO_ROOT", raising=False)
    monkeypatch.setattr(config, "__file__", str(fake))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    assert config._repo_root() == Path(work).resolve()
