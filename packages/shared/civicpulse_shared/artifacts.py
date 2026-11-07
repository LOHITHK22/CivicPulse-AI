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


def build_manifest(data_dir: Path) -> dict[str, Any]:
    data_dir = Path(data_dir)
    files = [
        {"path": rel.as_posix(), "bytes": (data_dir / rel).stat().st_size, "sha256": sha256_file(data_dir / rel)}
        for rel in select_files(data_dir)
    ]
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
    manifest = build_manifest(data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
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
            with (data_dir / f["path"]).open("rb") as fh:
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
