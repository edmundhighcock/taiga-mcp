"""Command line: ``taiga-mcp serve --host 0.0.0.0 --port 8095``."""

from __future__ import annotations

import argparse

import uvicorn

from .server import Config, build_app


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="taiga-mcp")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the MCP server (streamable HTTP at /mcp)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8095)
    args = p.parse_args(argv)
    cfg = Config.from_env(host=args.host, port=args.port)
    # Access logging stays at uvicorn's default (method/path/status only);
    # request headers, and so tokens, are never logged.
    uvicorn.run(build_app(cfg), host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
