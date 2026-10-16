from .config import load_config_file

# Runs before db.py reads TINYTABLE_DB at import time.
load_config_file()
