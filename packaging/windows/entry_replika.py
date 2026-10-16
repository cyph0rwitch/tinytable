"""tinytable-replika: the Replika sender the server runs for guarded sends."""

import app  # noqa: F401  applies config.env defaults
from replika_provider import main

if __name__ == "__main__":
    main()
