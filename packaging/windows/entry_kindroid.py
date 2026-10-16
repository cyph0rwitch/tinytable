"""tinytable-kindroid: the optional Kindroid companion sidecar."""

import app  # noqa: F401  applies config.env defaults
from kindroid_provider import main

if __name__ == "__main__":
    main()
