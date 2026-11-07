"""Snapshot artifact packaging and verified restore (local files only; no network)."""

from __future__ import annotations

import gzip
import io
import json
import shutil
import tarfile
from pathlib import Path

import duckdb
import pytest

from civicpulse_analytics import AnalyticsService
from civicpulse_analytics.predictions import PredictionService
from civicpulse_shared import artifacts as art

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def packed(sample_models, tmp_path_factory):
    out = tmp_path_factory.mktemp("dist")
    return art.pack(sample_models.root, out), out


def _archive(packed) -> Path:
    side, out = packed
    return out / side["archive"]


def test_manifest_identifies_data_and_models_together(packed, sample_models):
    side, _ = packed
    assert side["snapshot_id"] == f"{side['build_id']}__{side['model_run_id']}"
    assert side["dataset_cutoff"] and side["fact_rows"] > 0 and side["model_versions"]
    paths = {f["path"] for f in side["files"]}
    assert "processed/fact_service_requests.parquet" in paths and "models/current.json" in paths
    assert "state/last_build.json" in paths
    assert not any(p.startswith("raw/") for p in paths)  # raw ingestion pages are not runtime artifacts
    runs = {p.split("/")[2] for p in paths if p.startswith("models/runs/")}
    assert runs == {side["model_run_id"]}  # only the active model run
    assert all(len(f["sha256"]) == 64 for f in side["files"])
    assert art.NOTICE_NAME in paths
    (red,) = side["redactions"]  # building-level coordinates are not published
    assert red["path"] == "processed/fact_service_requests.parquet"
    assert red["dropped_columns"] == ["latitude", "longitude"]
    assert red["source_sha256"] == art.sha256_file(sample_models.root / red["path"])


def test_pack_is_reproducible(packed, sample_models, tmp_path):
    again = art.pack(sample_models.root, tmp_path)
    assert again["archive_sha256"] == packed[0]["archive_sha256"]


def test_restore_verifies_and_serves(packed, tmp_path):
    side, _ = packed
    snap = art.restore(str(_archive(packed)), tmp_path, side["archive_sha256"])
    assert snap.name == side["snapshot_id"]
    assert art.verify(snap)["snapshot_id"] == side["snapshot_id"]
    cols = duckdb.sql(f"DESCRIBE SELECT * FROM '{(snap / 'processed/fact_service_requests.parquet').as_posix()}'")
    names = {r[0] for r in cols.fetchall()}
    assert "latitude" not in names and "longitude" not in names and "incident_zip" in names
    notice = (snap / art.NOTICE_NAME).read_text(encoding="utf-8")
    assert "erm2-nwe9" in notice and "latitude, longitude" in notice and "not live" in notice
    health = AnalyticsService(snap).health()
    assert health["ready"] and health["fact_rows"] == side["fact_rows"]
    assert PredictionService(snap).registry()["run_id"] == side["model_run_id"]
    # Second start: reused from disk, the source is not needed again (no re-download on restart).
    assert art.restore("does/not/exist.tar.gz", tmp_path, side["archive_sha256"]) == snap


def test_wrong_pin_installs_nothing(packed, tmp_path):
    with pytest.raises(art.ArtifactError, match="checksum mismatch"):
        art.restore(str(_archive(packed)), tmp_path, "0" * 64)
    assert [p.name for p in (tmp_path / "snapshots").iterdir()] == []
    with pytest.raises(art.ArtifactError, match="pinned"):
        art.restore(str(_archive(packed)), tmp_path, "not-a-sha")


def _repack(packed, tmp_path: Path, mutate) -> Path:
    """Rewrite the archive with `mutate(name, data) -> (name, data)` applied to each entry."""
    src, dst = _archive(packed), tmp_path / "evil.tar.gz"
    with tarfile.open(src, "r:gz") as tin, gzip.open(dst, "wb") as gz, tarfile.open(fileobj=gz, mode="w") as tout:
        for m in tin.getmembers():
            name, data = mutate(m.name, tin.extractfile(m).read())  # type: ignore[union-attr]
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tout.addfile(ti, io.BytesIO(data))
    return dst


def test_tampered_file_is_rejected_and_not_activated(packed, tmp_path):
    def flip(name: str, data: bytes) -> tuple[str, bytes]:
        return name, (data[:-1] + bytes([data[-1] ^ 1])) if name.endswith("current.json") else data

    evil = _repack(packed, tmp_path, flip)
    with pytest.raises(art.ArtifactError, match=r"checksum mismatch: models/current\.json"):
        art.restore(str(evil), tmp_path / "root", art.sha256_file(evil))
    assert list((tmp_path / "root" / "snapshots").iterdir()) == []  # partial directory removed


def test_path_traversal_is_rejected(packed, tmp_path):
    def escape(name: str, data: bytes) -> tuple[str, bytes]:
        if name == art.MANIFEST_NAME:
            m = json.loads(data)
            m["files"][0]["path"] = "../escaped.parquet"
            return name, json.dumps(m).encode()
        return name, data

    side, _ = packed
    first = side["files"][0]["path"]
    evil = _repack(packed, tmp_path, lambda n, d: ("../escaped.parquet", d) if n == first else escape(n, d))
    with pytest.raises(art.ArtifactError, match="unsafe archive entry"):
        art.restore(str(evil), tmp_path / "root", art.sha256_file(evil))
    assert not (tmp_path / "root" / "escaped.parquet").exists()


def test_fail_clearly(sample_models, tmp_path):
    with pytest.raises(art.ArtifactError, match="https"):
        art.restore("http://example.invalid/s.tar.gz", tmp_path, "a" * 64)
    with pytest.raises(art.ArtifactError, match="not found"):
        art.restore(str(tmp_path / "missing.tar.gz"), tmp_path, "a" * 64)
    broken = tmp_path / "broken"
    shutil.copytree(sample_models.root / "state", broken / "state")
    with pytest.raises(art.ArtifactError, match="required artifact missing"):
        art.pack(broken, tmp_path / "out")
    assert art._redact("https://bucket.example/s.tar.gz?X-Amz-Signature=secret") == "https://bucket.example/s.tar.gz"


def test_cli_exit_codes(packed, tmp_path, capsys):
    side, _ = packed
    assert art.main(["restore", "--source", str(_archive(packed)), "--sha256", "f" * 64, "--root", str(tmp_path)]) == 2
    assert "checksum mismatch" in capsys.readouterr().err
    code = art.main(
        ["restore", "--source", str(_archive(packed)), "--sha256", side["archive_sha256"], "--root", str(tmp_path)]
    )
    assert code == 0
    assert art.main(["verify", "--dir", str(tmp_path / "snapshots" / side["snapshot_id"])]) == 0
