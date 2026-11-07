"""Versioned data/model snapshot artifacts: package, publish-ready manifest, verified restore.

A *snapshot* is everything the API and the MCP server read at runtime, and nothing else:

* ``processed/**``                  the warehouse (Parquet tables + data-quality runs)
* ``state/**``                      build/freshness metadata (last build, watermark, source metadata, run logs)
* ``models/current.json``           pointer to the active model run
* ``models/runs/<current run>/**``  the model outputs and evaluations of that run only

Raw ingestion pages and older model runs are excluded. The snapshot id combines the warehouse build id and the
model run id, so data and models always travel together.

``pack`` writes a deterministic ``.tar.gz`` plus a sidecar manifest (archive SHA-256, per-file SHA-256, dataset
cutoff, model versions). ``restore`` fetches the archive (local path or HTTPS URL), checks the archive SHA-256
against a value pinned in the deployment configuration, extracts into ``<root>/snapshots/<id>.partial`` with path
and link checks, verifies every file against the embedded manifest and only then renames the directory into place.
A snapshot directory is immutable once in place; an existing verified snapshot is reused, never re-downloaded.

CLI::

    python -m civicpulse_shared.artifacts pack    --data-dir data --out dist/artifacts
    python -m civicpulse_shared.artifacts restore --source <path|https-url> --sha256 <hex> --root /app/artifacts
    python -m civicpulse_shared.artifacts verify  --dir /app/artifacts/snapshots/<id>
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

MANIFEST_NAME = "ARTIFACT_MANIFEST.json"
NOTICE_NAME = "DATA_NOTICE.md"
# Columns removed from the published snapshot because nothing at runtime reads them and they add precision a
# portfolio deployment does not need: building-level coordinates of residential complaints. Lineage is kept by
# recording the source file's SHA-256 next to the redacted file's.
REDACTED_COLUMNS: dict[str, tuple[str, ...]] = {
    "processed/fact_service_requests.parquet": ("latitude", "longitude"),
}
SCHEMA_VERSION = 1
CHUNK = 1 << 20
DEFAULT_MAX_BYTES = 2 * 1024**3  # refuse archives larger than 2 GiB (the current snapshot is ~100 MB)


class ArtifactError(RuntimeError):
    """Snapshot missing, incomplete, tampered with or unsafe. Never served."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def select_files(data_dir: Path) -> list[Path]:
    """Relative paths of every runtime file. Raises if a required part is missing."""
    data_dir = Path(data_dir)
    required = [data_dir / "processed" / "fact_service_requests.parquet", data_dir / "state" / "last_build.json"]
    pointer = data_dir / "models" / "current.json"
    for p in [*required, pointer]:
        if not p.is_file():
            raise ArtifactError(f"required artifact missing: {p.relative_to(data_dir).as_posix()}")
    run_dir = data_dir / "models" / str(_read_json(pointer)["path"])
    if not (run_dir / "manifest.json").is_file():
        raise ArtifactError("current model run has no manifest.json")
    roots = [data_dir / "processed", data_dir / "state", run_dir]
    files = {pointer.relative_to(data_dir)}
    for root in roots:
        for p in root.rglob("*"):
            if p.is_symlink():
                raise ArtifactError(f"symlink not allowed in a snapshot: {p.relative_to(data_dir).as_posix()}")
            if p.is_file():
                files.add(p.relative_to(data_dir))
    return sorted(files, key=lambda p: p.as_posix())


def describe(data_dir: Path) -> dict[str, Any]:
    """Dataset cutoff, build and model identity of the snapshot in ``data_dir``."""
    data_dir = Path(data_dir)
    build = _read_json(data_dir / "state" / "last_build.json")
    pointer = _read_json(data_dir / "models" / "current.json")
    run_manifest = _read_json(data_dir / "models" / str(pointer["path"]) / "manifest.json")
    models = {name: m.get("version") for name, m in (run_manifest.get("models") or {}).items() if isinstance(m, dict)}
    return {
        "build_id": build["build_id"],
        "built_at_utc": build.get("built_at_utc"),
        "fact_rows": build.get("accepted_rows"),
        "dataset_min_created_at": build.get("min_created_at"),
        "dataset_cutoff": build.get("max_created_at"),
        "model_run_id": pointer["run_id"],
        "model_versions": models,
    }


def _sql_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "''")


def redacted_copy(src: Path, dst: Path, drop: tuple[str, ...]) -> list[str]:
    """Write ``src`` without the ``drop`` columns (same row order, zstd). Returns the columns actually removed."""
    import duckdb  # runtime dependency of the analytics package; imported lazily for the restore path

    con = duckdb.connect(":memory:")
    con.execute("SET threads=1")  # deterministic output for reproducible archives
    con.execute("SET preserve_insertion_order=true")
    cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{_sql_path(src)}')").fetchall()]
    keep = [c for c in cols if c not in drop]
    select = ", ".join('"' + c.replace('"', '""') + '"' for c in keep)
    con.execute(
        f"COPY (SELECT {select} FROM read_parquet('{_sql_path(src)}')) TO '{_sql_path(dst)}' "
        "(FORMAT parquet, COMPRESSION zstd)"
    )
    con.close()
    return [c for c in cols if c in drop]


def data_notice(data_dir: Path, snap: dict[str, Any], redactions: list[dict[str, Any]]) -> str:
    """Source, version and modifications of the public data set (NYC Admin Code § 23-502(d))."""
    from civicpulse_shared.config import load_ingestion_config

    cfg = load_ingestion_config()
    meta_file = Path(data_dir) / "state" / "source_metadata.json"
    meta = _read_json(meta_file) if meta_file.is_file() else {}
    build = _read_json(Path(data_dir) / "state" / "last_build.json")
    cats = "; ".join(f"{c.label} ({', '.join(c.complaint_types)})" for c in cfg.categories)
    removed = "; ".join(f"{r['path']}: {', '.join(r['dropped_columns'])}" for r in redactions) or "none"
    return f"""# Data notice

Source: {meta.get("name", "NYC 311 Service Requests")}, NYC Open Data data set `{cfg.source.dataset_id}`
({cfg.source.base_url}/d/{cfg.source.dataset_id}), published by NYC 311 / Office of Technology and Innovation
(attribution: "{meta.get("attribution", "311")}"). Borough boundaries shown by the application: NYC Department
of City Planning, data set `gthc-hcne`.

Version: source rows updated at {build.get("source_rows_updated_at")}; snapshot taken {build.get("snapshot_at_local")}
(America/New_York); records created {snap.get("dataset_min_created_at")} to {snap.get("dataset_cutoff")}.
This is a static snapshot, not live data.

Modifications by CivicPulse AI: limited to the complaint types {cats}; created on or after the configured backfill
start; deduplicated on unique_key (latest source version kept); normalised types, borough and status values;
derived columns (durations, calendar parts, data-quality flags); aggregates and model outputs computed from the
snapshot. Columns removed before publication: {removed}. Model outputs are statistical estimates; the
closure-delay model is research-only and anomaly flags are unverified.

The City of New York provides the source data as is, without warranty, and does not endorse this project.
Snapshot id: {snap["build_id"]}__{snap["model_run_id"]}.
"""


def build_manifest(data_dir: Path, staged: dict[str, Path] | None = None) -> dict[str, Any]:
    """Manifest of the snapshot. ``staged`` maps a relative path to the file actually published (redacted copy
    or generated notice) when it differs from ``data_dir / path``."""
    data_dir, staged = Path(data_dir), staged or {}
    rels = [rel.as_posix() for rel in select_files(data_dir)]
    rels += [p for p in staged if p not in rels]
    files = []
    for rel in sorted(rels):
        src = staged.get(rel, data_dir / rel)
        files.append({"path": rel, "bytes": src.stat().st_size, "sha256": sha256_file(src)})
    snap = describe(data_dir)
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": f"{snap['build_id']}__{snap['model_run_id']}",
        **snap,
        "total_bytes": sum(f["bytes"] for f in files),
        "files": files,
    }


def _tarinfo(name: str, size: int) -> tarfile.TarInfo:
    ti = tarfile.TarInfo(name)
    ti.size, ti.mtime, ti.mode, ti.uid, ti.gid, ti.uname, ti.gname = size, 0, 0o644, 0, 0, "", ""
    return ti


def pack(data_dir: Path, out_dir: Path) -> dict[str, Any]:
    """Write ``civicpulse-snapshot-<id>.tar.gz`` and ``<same>.manifest.json``; return the sidecar manifest.

    The archive is byte-for-byte reproducible for the same inputs (sorted entries, fixed metadata, gzip mtime 0)."""
    data_dir, out_dir = Path(data_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out_dir, prefix=".stage-") as stage_dir:
        return _pack(data_dir, out_dir, Path(stage_dir))


def _pack(data_dir: Path, out_dir: Path, stage: Path) -> dict[str, Any]:
    select_files(data_dir)  # fail clearly on a missing required artifact before any staging work
    staged: dict[str, Path] = {}
    redactions = []
    for rel, drop in REDACTED_COLUMNS.items():
        src = data_dir / rel
        if src.is_file():
            dst = stage / f"redacted-{len(staged)}.parquet"
            removed = redacted_copy(src, dst, drop)
            if removed:
                staged[rel] = dst
                redactions.append({"path": rel, "dropped_columns": removed, "source_sha256": sha256_file(src)})
    notice = stage / NOTICE_NAME
    notice.write_text(data_notice(data_dir, describe(data_dir), redactions), encoding="utf-8", newline="\n")
    staged[NOTICE_NAME] = notice
    manifest = build_manifest(data_dir, staged)
    manifest["redactions"] = redactions
    name = f"civicpulse-snapshot-{manifest['snapshot_id']}"
    archive = out_dir / f"{name}.tar.gz"
    tmp = archive.with_suffix(".tmp")
    body = json.dumps(manifest, indent=2, sort_keys=True).encode()
    with (
        tmp.open("wb") as raw,
        gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz,
        tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar,
    ):
        tar.addfile(_tarinfo(MANIFEST_NAME, len(body)), io.BytesIO(body))
        for f in manifest["files"]:
            with staged.get(f["path"], data_dir / f["path"]).open("rb") as fh:
                tar.addfile(_tarinfo(f["path"], f["bytes"]), fh)
    os.replace(tmp, archive)
    sidecar = {
        "snapshot_id": manifest["snapshot_id"],
        "archive": archive.name,
        "archive_bytes": archive.stat().st_size,
        "archive_sha256": sha256_file(archive),
        **{k: v for k, v in manifest.items() if k not in ("files", "snapshot_id")},
        "file_count": len(manifest["files"]),
        "files": manifest["files"],
    }
    (out_dir / f"{name}.manifest.json").write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")
    return sidecar


def _redact(source: str) -> str:
    """Never log query strings (pre-signed URLs carry credentials in them)."""
    u = urllib.parse.urlsplit(source)
    return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, "", "")) if u.scheme else source


def fetch(source: str, dest: Path, max_bytes: int = DEFAULT_MAX_BYTES, token: str | None = None) -> None:
    """Copy a local file or download an HTTPS URL to ``dest`` (size-capped). Plain HTTP is refused."""
    scheme = urllib.parse.urlsplit(source).scheme
    if scheme in ("", "file") or (len(scheme) == 1 and os.name == "nt"):  # bare path or Windows drive letter
        path = Path(urllib.parse.urlsplit(source).path if scheme == "file" else source)
        if not path.is_file():
            raise ArtifactError(f"artifact not found: {path}")
        if path.stat().st_size > max_bytes:
            raise ArtifactError("artifact exceeds the size limit")
        shutil.copyfile(path, dest)
        return
    if scheme != "https":
        raise ArtifactError("artifact URL must use https")
    headers = {"User-Agent": "civicpulse-artifacts/1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(source, headers=headers)  # noqa: S310 (scheme checked above: https only)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, dest.open("wb") as out:  # noqa: S310 (https enforced)
            total = 0
            while chunk := resp.read(CHUNK):
                total += len(chunk)
                if total > max_bytes:
                    raise ArtifactError("artifact exceeds the size limit")
                out.write(chunk)
    except ArtifactError:
        raise
    except Exception as exc:  # report the kind and the redacted URL only
        raise ArtifactError(f"download failed ({type(exc).__name__}) from {_redact(source)}") from None


def _safe_members(tar: tarfile.TarFile, expected: set[str]) -> list[tarfile.TarInfo]:
    members = tar.getmembers()
    for m in members:
        p = Path(m.name)
        if not m.isfile() or p.is_absolute() or ".." in p.parts or m.name.startswith(("/", "\\")):
            raise ArtifactError(f"unsafe archive entry: {m.name!r}")
        if m.name not in expected:
            raise ArtifactError(f"archive entry not in manifest: {m.name!r}")
    return members


def verify(snapshot_dir: Path, *, deep: bool = True) -> dict[str, Any]:
    """Check every manifest file exists with the recorded size (and SHA-256 when ``deep``). Returns the manifest."""
    snapshot_dir = Path(snapshot_dir)
    mf = snapshot_dir / MANIFEST_NAME
    if not mf.is_file():
        raise ArtifactError(f"{MANIFEST_NAME} missing in {snapshot_dir}")
    manifest: dict[str, Any] = _read_json(mf)
    for f in manifest["files"]:
        p = snapshot_dir / f["path"]
        if not p.is_file() or p.stat().st_size != f["bytes"]:
            raise ArtifactError(f"missing or truncated: {f['path']}")
        if deep and sha256_file(p) != f["sha256"]:
            raise ArtifactError(f"checksum mismatch: {f['path']}")
    return manifest


def restore(
    source: str,
    root: Path,
    expected_sha256: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    token: str | None = None,
) -> Path:
    """Fetch, verify and install a snapshot; return its directory (usable as ``CIVICPULSE_DATA_DIR``).

    ``expected_sha256`` is mandatory: an archive is installed only if it is exactly the pinned one."""
    expected = expected_sha256.strip().lower()
    if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise ArtifactError("a pinned 64-hex-character archive SHA-256 is required")
    root = Path(root)
    snapshots = root / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    marker = snapshots / f"{expected}.id"  # archive sha -> installed snapshot id (written last)
    if marker.is_file():
        existing = snapshots / marker.read_text(encoding="utf-8").strip()
        try:
            verify(existing, deep=False)
            return existing
        except ArtifactError:
            pass  # fall through and reinstall
    with tempfile.TemporaryDirectory(dir=root, prefix=".download-") as tmp:
        archive = Path(tmp) / "snapshot.tar.gz"
        fetch(source, archive, max_bytes=max_bytes, token=token)
        actual = sha256_file(archive)
        if actual != expected:
            raise ArtifactError(f"archive checksum mismatch: expected {expected[:12]}…, got {actual[:12]}…")
        with tarfile.open(archive, mode="r:gz") as tar:
            head = tar.extractfile(MANIFEST_NAME) if MANIFEST_NAME in tar.getnames() else None
            if head is None:
                raise ArtifactError(f"{MANIFEST_NAME} missing in archive")
            manifest = json.loads(head.read())
            snap_id = str(manifest["snapshot_id"])
            if not snap_id.replace("_", "").replace("-", "").isalnum():
                raise ArtifactError("invalid snapshot id")
            members = _safe_members(tar, {MANIFEST_NAME, *(f["path"] for f in manifest["files"])})
            partial = snapshots / f"{snap_id}.partial"
            if partial.exists():
                shutil.rmtree(partial)
            partial.mkdir()
            for m in members:
                target = partial / m.name
                target.parent.mkdir(parents=True, exist_ok=True)
                src = tar.extractfile(m)
                if src is None:
                    raise ArtifactError(f"unreadable archive entry: {m.name!r}")
                with src, target.open("wb") as out:
                    shutil.copyfileobj(src, out, CHUNK)
        try:
            verify(partial, deep=True)
        except ArtifactError:
            shutil.rmtree(partial, ignore_errors=True)
            raise
        final = snapshots / snap_id
        if final.exists():  # same snapshot already installed (e.g. re-packed archive): keep the verified one
            shutil.rmtree(partial)
            verify(final, deep=True)
        else:
            os.replace(partial, final)
    marker.write_text(snap_id, encoding="utf-8")
    return final


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m civicpulse_shared.artifacts", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack")
    p.add_argument("--data-dir", required=True)
    p.add_argument("--out", required=True)
    r = sub.add_parser("restore")
    r.add_argument("--source", required=True)
    r.add_argument("--sha256", required=True)
    r.add_argument("--root", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--dir", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "pack":
            side = pack(Path(a.data_dir), Path(a.out))
            print(json.dumps({k: v for k, v in side.items() if k != "files"}, indent=2))
        elif a.cmd == "restore":
            token = os.environ.get("CIVICPULSE_ARTIFACT_TOKEN") or None
            print(restore(a.source, Path(a.root), a.sha256, token=token))
        else:
            m = verify(Path(a.dir))
            print(json.dumps({"ok": True, "snapshot_id": m["snapshot_id"], "files": len(m["files"])}))
    except ArtifactError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
