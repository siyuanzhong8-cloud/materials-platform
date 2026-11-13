"""Linux / public read-only launcher for the materials platform."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("MATERIALS_ALLOWED_HOSTS", "*")
os.environ.setdefault("MATERIALS_READ_ONLY", "1")

import uvicorn
from backend.app import create_app


def main():
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(
        filename=ROOT / "logs/web.log",
        encoding="utf-8",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    host = os.environ.get("MATERIALS_HOST", "0.0.0.0")
    port = int(os.environ.get("MATERIALS_PORT", "8886"))
    logging.info("starting read-only server on %s:%s", host, port)
    uvicorn.run(create_app(), host=host, port=port, log_config=None, access_log=True)


if __name__ == "__main__":
    sys.exit(main())
