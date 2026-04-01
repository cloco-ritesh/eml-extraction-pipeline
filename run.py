#!/usr/bin/env python3
"""
EML Processing Pipeline CLI.

Usage:
    python run.py watch                    # Start folder watcher
    python run.py process <path.eml>       # Process a single file
"""

import asyncio
import logging
import sys
from pathlib import Path


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if len(sys.argv) < 2:
        print(__doc__.strip())
        sys.exit(1)

    command = sys.argv[1]

    if command == "watch":
        from ingestion.config import WATCH_FOLDER
        from ingestion.watcher import start_watching
        start_watching(WATCH_FOLDER)

    elif command == "process":
        if len(sys.argv) < 3:
            print("Usage: python run.py process <path-to-file.eml>")
            sys.exit(1)

        eml_path = Path(sys.argv[2])
        if not eml_path.exists():
            print(f"Error: File not found: {eml_path}")
            sys.exit(1)

        from ingestion.pipeline import process_eml
        asyncio.run(process_eml(eml_path))

    else:
        print(f"Unknown command: {command}")
        print(__doc__.strip())
        sys.exit(1)


if __name__ == "__main__":
    main()
