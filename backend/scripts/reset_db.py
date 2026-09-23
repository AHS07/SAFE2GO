"""Reset to a clean demo state.

    python -m scripts.reset_db          Clear the previous demo run and reseed it (seconds).
                                         Generated history and the trained model are kept.
    python -m scripts.reset_db --full   Drop and recreate every table (Alembic). Generated
                                         data is lost; run generate, train, and this script
                                         again afterwards.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import subprocess
import sys
import time

from app.core.logging import setup_logging
from scripts import seed_demo

log = logging.getLogger("safe2go.reset_db")


def full_reset() -> None:
    for cmd in (
        [sys.executable, "-m", "alembic", "downgrade", "base"],
        [sys.executable, "-m", "alembic", "upgrade", "head"],
    ):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            log.error("Command failed", extra={"cmd": " ".join(cmd), "stderr": result.stderr})
            sys.exit(1)
        log.info("Command ok", extra={"cmd": " ".join(cmd)})
    log.info("Database recreated. Next: generate, train, then python -m scripts.reset_db")


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset SAFE2GO to a clean demo state.")
    parser.add_argument("--full", action="store_true", help="drop and recreate all tables")
    args = parser.parse_args()
    setup_logging("INFO")
    if args.full:
        full_reset()
        return
    started = time.perf_counter()
    asyncio.run(seed_demo.run())
    log.info("Demo reset complete", extra={"seconds": round(time.perf_counter() - started, 1)})


if __name__ == "__main__":
    main()
