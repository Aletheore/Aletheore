"""Container entry point for the room server: `python -m aletheore.council.server_main`.

Everything comes from the environment (see server_config.py): the database, limits, bind address. Tokens come from the JSON
file named by COUNCIL_TOKEN_FILE (mounted as a secret, never baked into the image). Inside a container the server listens on
all interfaces (COUNCIL_HOST=0.0.0.0, COUNCIL_ALLOW_PUBLIC=1); it is the container runtime that decides who can reach that port.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from aletheore.council.server import parse_tokens, run_server
from aletheore.council.server_config import check_bind, load_settings


def main() -> int:
    try:
        settings = load_settings(config_file=os.environ.get("COUNCIL_CONFIG"), environ=os.environ)
        check_bind(settings.host, settings.allow_public)
        token_file = os.environ.get("COUNCIL_TOKEN_FILE")
        if not token_file and not settings.accounts:
            raise ValueError("COUNCIL_TOKEN_FILE must name a JSON file of {name: token} (or set COUNCIL_ACCOUNTS=1)")
        tokens = parse_tokens([], token_file, allow_empty=settings.accounts)
    except (ValueError, OSError) as exc:
        print(f"cannot start: {exc}", file=sys.stderr)
        return 2
    run_server(Path(settings.db or "/data/server.db"), Path(settings.bundle_dir or "/data/bundles"), tokens,
               settings.retention_days, settings.host, settings.port, settings=settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
