"""
Command-line interface (CLI) for running the Sport5 Fantasy REST API.

Usage:
    sport5-api [--host HOST] [--port PORT] [--reload]
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

import uvicorn


def build_parser() -> argparse.ArgumentParser:
    """Construct the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="sport5-api",
        description="Run the Sport5 Fantasy REST API server via Uvicorn.",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host interface to bind the server to (default: 0.0.0.0).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to listen on (default: 8000).",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        default=False,
        help="Enable auto-reload on source code changes (development mode).",
    )
    return parser


def main(args: Sequence[str] | None = None) -> int:
    """CLI entry point for sport5-api."""
    parser = build_parser()
    parsed = parser.parse_args(args)

    uvicorn.run(
        "sport5_fantasy_api.api.main:app",
        host=parsed.host,
        port=parsed.port,
        reload=parsed.reload,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
