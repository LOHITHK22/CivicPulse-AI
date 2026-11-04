"""``civicpulse-model train``: offline training + evaluation (never exposed through the API)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from civicpulse_modeling.config import load_modeling_config
from civicpulse_modeling.pipeline import train_all
from civicpulse_shared.config import get_settings
from civicpulse_shared.logging import configure_logging
from civicpulse_shared.paths import DataPaths


def main(argv: list[str] | None = None) -> int:
    configure_logging(get_settings().log_level)
    p = argparse.ArgumentParser(prog="civicpulse-model", description="CivicPulse AI model training")
    p.add_argument("--data-dir", help="override CIVICPULSE_DATA_DIR")
    p.add_argument("--config", help="modeling YAML (default configs/modeling.yaml)")
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train", help="train + evaluate all models from the current warehouse build")
    t.add_argument("--run-id", help="explicit run id (default: timestamp)")
    args = p.parse_args(argv)
    cfg_path = Path(args.config) if args.config else None
    cfg = load_modeling_config(cfg_path)
    paths = DataPaths(Path(args.data_dir) if args.data_dir else get_settings().data_dir)
    manifest = train_all(cfg, paths, config_path=cfg_path, run_id=args.run_id)
    print(json.dumps({k: manifest[k] for k in ("run_id", "models", "elapsed_s")}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
