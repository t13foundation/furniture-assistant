"""Integrity helpers and CLI for project audit logs."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

GENESIS_HASH = "0" * 64


def canonical_json(entry: dict[str, Any]) -> bytes:
    return json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def entry_hash(entry: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(entry)).hexdigest()


def next_previous_hash(path: Path) -> str:
    """Return the previous chain hash, anchoring a legacy prefix by its raw bytes."""
    if not path.exists():
        return GENESIS_HASH
    raw = path.read_bytes()
    lines = raw.splitlines(keepends=True)
    previous = GENESIS_HASH
    legacy = bytearray()
    hashed_seen = False
    for line_number, raw_line in enumerate(lines, 1):
        try:
            entry = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            # Leave malformed log detection to verify; append does not conceal it.
            raise ValueError(f"audit log line {line_number} is not valid JSON") from None
        if not isinstance(entry, dict) or "hash" not in entry:
            if hashed_seen:
                raise ValueError(f"audit log line {line_number} has no hash after the chain started")
            legacy.extend(raw_line)
            continue
        hashed_seen = True
        previous = entry["hash"]
    if not hashed_seen and legacy:
        return hashlib.sha256(legacy).hexdigest()
    return previous


def verify_audit(path: Path) -> tuple[bool, str | None]:
    """Verify a log, accepting only a leading unhashed legacy prefix."""
    if not path.exists():
        return True, None
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return False, str(exc)
    lines = raw.splitlines(keepends=True)
    previous = GENESIS_HASH
    legacy = bytearray()
    chain_started = False
    for line_number, raw_line in enumerate(lines, 1):
        try:
            entry = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False, f"line {line_number}: invalid JSON"
        if not isinstance(entry, dict):
            return False, f"line {line_number}: expected a JSON object"
        if "hash" not in entry or "prev_hash" not in entry:
            if chain_started:
                return False, f"line {line_number}: unhashed entry after chain start"
            legacy.extend(raw_line)
            continue
        expected_previous = hashlib.sha256(legacy).hexdigest() if not chain_started and legacy else previous
        chain_started = True
        if entry.get("prev_hash") != expected_previous:
            return False, f"line {line_number}: prev_hash mismatch"
        claimed = entry.get("hash")
        payload = {key: value for key, value in entry.items() if key != "hash"}
        if not isinstance(claimed, str) or entry_hash(payload) != claimed:
            return False, f"line {line_number}: hash mismatch"
        previous = claimed
    return True, None


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] != "verify":
        print("usage: python -m src.webapp.audit verify [projects_root|project_dir]", file=sys.stderr)
        return 2
    target = Path(sys.argv[2])
    if target.is_dir() and not (target / "audit.jsonl").is_file():
        try:
            logs = sorted(path / "audit.jsonl" for path in target.iterdir() if (path / "audit.jsonl").is_file())
        except OSError as exc:
            print(f"{target}: {exc}", file=sys.stderr)
            return 1
        if not logs:
            print(f"{target}: no project audit logs found", file=sys.stderr)
            return 1
        success = True
        for log in logs:
            valid, error = verify_audit(log)
            print(f"{log}: {'valid' if valid else error}")
            success = success and valid
        return 0 if success else 1
    path = target / "audit.jsonl" if target.is_dir() else target
    valid, error = verify_audit(path)
    print(f"{path}: {'valid' if valid else error}")
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
