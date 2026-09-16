"""
Unit tests for the CLI runner (sport5_fantasy_api.cli).
"""

from __future__ import annotations

from unittest.mock import patch

from sport5_fantasy_api.cli import build_parser, main


def test_cli_parser_defaults() -> None:
    """Verify default CLI arguments."""
    parser = build_parser()
    args = parser.parse_args([])
    assert args.host == "0.0.0.0"
    assert args.port == 8000
    assert args.reload is False


def test_cli_parser_custom_args() -> None:
    """Verify parsed custom CLI arguments."""
    parser = build_parser()
    args = parser.parse_args(["--host", "127.0.0.1", "--port", "9000", "--reload"])
    assert args.host == "127.0.0.1"
    assert args.port == 9000
    assert args.reload is True


def test_cli_main_invokes_uvicorn() -> None:
    """Verify main() parses arguments and delegates to uvicorn.run()."""
    with patch("uvicorn.run") as mock_run:
        exit_code = main(["--host", "127.0.0.1", "--port", "5000"])

    assert exit_code == 0
    mock_run.assert_called_once_with(
        "sport5_fantasy_api.api.main:app",
        host="127.0.0.1",
        port=5000,
        reload=False,
    )
