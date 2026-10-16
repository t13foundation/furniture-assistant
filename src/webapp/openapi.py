"""Export the API schema for the frontend type generator (``make web-types``).

Responses are serialized with every field present, so fields that have a default are marked required for the
generated TypeScript types. Schemas the client builds itself with only some fields keep them optional.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from src.webapp.api import app

# Nested inside request bodies and built field-by-field by the client (only the fields it sets is sent):
# their fields stay optional in the generated TS.
CLIENT_BUILT = {"Override", "AnswersBody", "LoginBody"}
# Request bodies the client round-trips whole (loaded, edited, sent back): every field is present.
ROUND_TRIPPED = {"FurnitureModule", "Pricing", "ProjectNameBody"}


def _request_body_schemas(spec: dict[str, Any]) -> set[str]:
    """Names of schemas used directly as a request body (FastAPI wraps file uploads as Body_*)."""
    names: set[str] = set()
    for operations in spec.get("paths", {}).values():
        if not isinstance(operations, dict):
            continue
        for operation in operations.values():
            if not isinstance(operation, dict):
                continue
            for media in (operation.get("requestBody") or {}).get("content", {}).values():
                ref = media.get("schema", {}).get("$ref", "")
                if ref:
                    names.add(ref.rsplit("/", 1)[-1])
    return names


def typed_openapi() -> dict[str, Any]:
    spec = app.openapi()
    request_built = {n for n in _request_body_schemas(spec) if not n.startswith("Body_")}
    listed = CLIENT_BUILT | ROUND_TRIPPED
    unlisted = request_built - listed
    stale = listed - request_built - {"Override"}
    if unlisted or stale:
        raise ValueError(
            f"CLIENT_BUILT/ROUND_TRIPPED in {__name__} are out of date: add {sorted(unlisted)}, remove {sorted(stale)}"
        )
    for name, schema in spec.get("components", {}).get("schemas", {}).items():
        if name in CLIENT_BUILT or name.startswith("Body_") or "properties" not in schema:
            continue
        schema["required"] = list(schema["properties"])
    return spec


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m src.webapp.openapi OUTPUT.json")
    path = Path(sys.argv[1])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(typed_openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
