"""tinytable-server: entry point for installed TinyTable.

Serves the API and UI on loopback, opens the browser with --open, and starts
the optional sidecars enabled in config.env. A second launch while the server
is already up just opens the browser.
"""

import argparse
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import app  # noqa: F401  applies config.env defaults before anything reads the environment
from app.config import default_db_path

SIDECARS = {"TINYTABLE_START_KINDROID": "tinytable-kindroid", "TINYTABLE_START_MATRIX": "tinytable-matrix"}


def log_dir() -> Path:
    path = default_db_path().parent.parent / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def redirect_output() -> None:
    # A windowed build has no console streams, and uvicorn's logging needs them.
    if sys.stdout is None or sys.stderr is None:
        stream = open(log_dir() / "server.log", "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream


def healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def open_when_ready(url: str) -> None:
    for _ in range(60):
        if healthy(url):
            webbrowser.open(url)
            return
        time.sleep(0.5)


def start_sidecars() -> list[subprocess.Popen]:
    processes = []
    for flag, name in SIDECARS.items():
        if os.getenv(flag, "0") != "1":
            continue
        exe = Path(sys.executable).with_name(name + (".exe" if os.name == "nt" else ""))
        if not exe.is_file():
            print(f"{flag}=1 but {exe.name} is not installed", file=sys.stderr)
            continue
        log = open(log_dir() / f"{name}.log", "a", encoding="utf-8")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        processes.append(subprocess.Popen([str(exe)], stdout=log, stderr=subprocess.STDOUT, creationflags=flags))
    return processes


def main() -> None:
    parser = argparse.ArgumentParser(prog="tinytable-server")
    parser.add_argument("--open", action="store_true", help="open the TinyTable UI in the default browser")
    args = parser.parse_args()
    redirect_output()

    host = os.getenv("TINYTABLE_HOST", "127.0.0.1")
    port = int(os.getenv("TINYTABLE_PORT", "8000"))
    url = f"http://{'127.0.0.1' if host in {'0.0.0.0', '::'} else host}:{port}"
    if healthy(url):
        if args.open:
            webbrowser.open(url)
        return
    if args.open:
        threading.Thread(target=open_when_ready, args=(url,), daemon=True).start()

    import uvicorn
    from app.main import app as application

    sidecars = start_sidecars()
    try:
        uvicorn.run(application, host=host, port=port)
    finally:
        for process in sidecars:
            process.terminate()


if __name__ == "__main__":
    main()
