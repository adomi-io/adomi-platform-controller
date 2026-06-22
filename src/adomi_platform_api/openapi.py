"""Dump the OpenAPI schema to a file (for generating typed clients).

    adomi-platform-api-openapi [output.json]   # default: openapi.json
"""

from __future__ import annotations

import json
import sys


def main() -> None:
    from .app import app

    out = sys.argv[1] if len(sys.argv) > 1 else "openapi.json"

    with open(out, "w") as fh:
        json.dump(app.openapi(), fh, indent=2, sort_keys=True)
        fh.write("\n")

    print(f"wrote {out}")


if __name__ == "__main__":
    main()
