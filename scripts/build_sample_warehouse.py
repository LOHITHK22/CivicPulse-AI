"""Build a warehouse from the committed REAL NYC 311 sample (no network needed).

Used by CI, Docker demo images and first-time local setup:

    python scripts/build_sample_warehouse.py --data-dir .sample-data

The sample is 969 genuine records (stratified by category x borough x month, coordinates
rounded to 3 decimals); see data/samples/README.md. Dashboards built on it are labelled by the
row counts they report, so they never masquerade as the full dataset.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from civicpulse_ingestion.transform import build_warehouse
from civicpulse_shared.config import REPO_ROOT, load_ingestion_config
from civicpulse_shared.paths import DataPaths

SAMPLE = REPO_ROOT / "data" / "samples" / "nyc311_raw_sample.parquet"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", required=True, help="target data directory (will be replaced)")
    args = p.parse_args()
    root = Path(args.data_dir).resolve()
    if root == (REPO_ROOT / "data").resolve():
        raise SystemExit("refusing to overwrite the main data directory; choose another --data-dir")
    if root.exists():
        shutil.rmtree(root)
    paths = DataPaths(root)
    paths.ensure()
    run_dir = paths.raw / "run=sample"
    run_dir.mkdir(parents=True)
    shutil.copy(SAMPLE, run_dir / "page-00001.parquet")
    summary = build_warehouse(load_ingestion_config(), paths)
    print(f"sample warehouse built at {root}: {summary.accepted_rows} accepted, {summary.rejected_rows} rejected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
