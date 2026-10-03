"""The command line. Every command is headless; --json gives machine output."""

from __future__ import annotations

import argparse

from opent5 import APP_NAME, LICENCE_NOTICE, __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="opent5", description=f"{APP_NAME} command line")
    parser.add_argument(
        "--version", action="version", version=f"{APP_NAME} {__version__}\n{LICENCE_NOTICE}"
    )
    parser.parse_args(argv)
    parser.print_help()
    return 0
