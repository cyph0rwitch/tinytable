#!/usr/bin/env python3
"""Executable entry point for the local TinyTable CLI-agent room."""

import sys


if __name__ == "__main__":
    if sys.argv[1:2] == ["channel"]:
        # The channel client is HTTP-only; skip the local database and its
        # dependencies so the system Python can drive a remote host.
        import argparse

        from backend.app.channel_cli import add_cli_parser, run

        parser = argparse.ArgumentParser(prog="tinytable-cli")
        add_cli_parser(parser.add_subparsers(dest="command", required=True))
        raise SystemExit(run(parser.parse_args()))
    from backend.app.cli_room import cli

    raise SystemExit(cli())
