"""tinytable-matrix: the optional Matrix bridge."""

import os

import app  # noqa: F401  applies config.env defaults
from app.config import default_db_path
from app.matrix_bridge import main

if __name__ == "__main__":
    # The bridge's container default (/data/...) is not a usable Windows path.
    os.environ.setdefault("MATRIX_BRIDGE_DB", str(default_db_path().with_name("matrix-bridge.db")))
    main()
