from __future__ import annotations
import argparse
from pathlib import Path
import sys
from .config import initialize, read_config
from .errors import DomainError


def main():
    parser = argparse.ArgumentParser(prog="trade-pricing", description="Self-hosted evidence-backed export pricing")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Create private role credentials, never seed business data")
    init.add_argument("--directory", default="private")
    init.add_argument("--tenant", required=True)
    init.add_argument("--seller", required=True)
    init.add_argument("--port", type=int, default=8875)
    serve = sub.add_parser("serve", help="Serve on 127.0.0.1 only; reverse proxy required for remote use")
    serve.add_argument("--config", default="private/server.json")
    mcp = sub.add_parser("mcp", help="Agent-only MCP stdio bridge to a running HTTP service")
    mcp.add_argument("--url", default="http://127.0.0.1:8875")
    mcp.add_argument("--token-file", required=True)
    audit = sub.add_parser("verify-audit")
    audit.add_argument("--config", default="private/server.json")
    args = parser.parse_args()
    try:
        if args.command == "init":
            p = initialize(Path(args.directory), args.tenant, args.seller, args.port)
            print(f"Initialized {p}\nKeep private/*.token secret. No business records were created.")
        elif args.command == "serve":
            import uvicorn
            from .api import create_app
            cfg = read_config(args.config)
            uvicorn.run(create_app(cfg), host="127.0.0.1", port=cfg.port, proxy_headers=False,
                        access_log=False, timeout_keep_alive=5, limit_concurrency=100)
        elif args.command == "mcp":
            from .adapters.mcp import run_stdio
            run_stdio(args.url, Path(args.token_file))
        elif args.command == "verify-audit":
            from .store import Store
            import json
            cfg = read_config(args.config)
            result = Store(cfg.database).verify_audit(cfg.tenant)
            print(json.dumps(result, indent=2))
            if not result["valid"]:
                sys.exit(1)
    except (DomainError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
