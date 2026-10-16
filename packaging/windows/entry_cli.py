"""tinytable-cli: packaged equivalent of tinytable_cli.py."""

import sys

import app  # noqa: F401  applies config.env defaults

if __name__ == "__main__":
    if sys.argv[1:2] == ["channel"]:
        import argparse

        from app.channel_cli import add_cli_parser, run

        parser = argparse.ArgumentParser(prog="tinytable-cli")
        add_cli_parser(parser.add_subparsers(dest="command", required=True))
        raise SystemExit(run(parser.parse_args()))
    from app.cli_room import cli

    raise SystemExit(cli())
