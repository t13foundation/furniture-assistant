import json
from pathlib import Path

from src.webapp.openapi import typed_openapi

SPEC = Path(__file__).resolve().parents[1] / "webapp" / "openapi.json"


def test_frontend_schema_matches_backend():
    """webapp/src/api-types.ts is generated from webapp/openapi.json; a stale copy means drifted TS types."""
    assert json.loads(SPEC.read_text(encoding="utf-8")) == json.loads(json.dumps(typed_openapi())), (
        "Backend API changed: run `make web-types`"
    )


def test_serialized_fields_are_required_but_client_built_overrides_are_not():
    schemas = typed_openapi()["components"]["schemas"]
    # Everything the server sends, and whole round-tripped request bodies, is always present.
    for name in ("ModuleModel", "FurnitureModule", "Valuation"):
        assert set(schemas[name]["required"]) == set(schemas[name]["properties"]), name
    # Built field-by-field by the client: only the fields it sets are sent.
    assert schemas["Override"]["required"] == ["target"]
