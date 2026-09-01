from __future__ import annotations

import argparse
import json
import logging
import sys

from dotenv import load_dotenv

from src.spoa_monitor.config import Settings
from src.spoa_monitor.pipeline import MonitorPipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor Fiscalía SPOA V3 para Jamundí")
    parser.add_argument("--force-run", action="store_true", help="Reconstruye el boletín aunque no haya cambio")
    parser.add_argument("--dry-run", action="store_true", help="Genera productos sin correo ni escritura en SISC")
    parser.add_argument("--no-sisc-sync", action="store_true", help="Omite integración con SISC")
    parser.add_argument("--sample-limit", type=int, default=None, help="Limita filas por conjunto; sólo para prueba")
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, __import__("os").getenv("SPOA_LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    manifest = MonitorPipeline(Settings.from_env()).run(
        force_run=args.force_run,
        dry_run=args.dry_run,
        sample_limit=args.sample_limit,
        sync_sisc=False if args.no_sisc_sync else None,
    )
    print(json.dumps({
        "run_id": manifest["run_id"],
        "status": manifest["status"],
        "updated_datasets": manifest.get("updated_datasets", []),
        "bulletin_path": manifest.get("bulletin_path"),
        "pdf_sha256": manifest.get("pdf_sha256"),
    }, ensure_ascii=False))
    return 0 if manifest["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())

