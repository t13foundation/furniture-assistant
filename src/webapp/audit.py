"""Integrity helpers and CLI for project audit logs."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

GENESIS_HASH = "0" * 64


def canonical_json(entry: dict[str, Any]) -> bytes:
    return json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def entry_hash(entry: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(entry)).hexdigest()


def _read_bytes(source: Path | bytes) -> bytes:
    if isinstance(source, bytes):
        return source
    return source.read_bytes() if source.exists() else b""


def checkpoint_for_existing_log(raw: bytes) -> tuple[int, str] | None:
    """Find and validate the first chained record for one-time checkpoint migration."""
    lines = raw.splitlines(keepends=True)
    first_hashed = None
    for line_number, raw_line in enumerate(lines, 1):
        try:
            entry = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError(f"line {line_number}: invalid JSON") from None
        if not isinstance(entry, dict):
            raise ValueError(f"line {line_number}: expected a JSON object")
        if "hash" in entry or "prev_hash" in entry:
            first_hashed = line_number
            break
    if first_hashed is None:
        return None
    prefix = b"".join(lines[: first_hashed - 1])
    legacy_hash = hashlib.sha256(prefix).hexdigest() if prefix else GENESIS_HASH
    valid, error = verify_audit(raw, first_hashed, legacy_hash)
    if not valid:
        raise ValueError(error or "invalid audit chain")
    return first_hashed, legacy_hash


def audit_head_hash(raw: bytes) -> str | None:
    lines = raw.splitlines()
    if not lines:
        return None
    try:
        entry = json.loads(lines[-1])
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    claimed = entry.get("hash") if isinstance(entry, dict) else None
    return claimed if isinstance(claimed, str) else None


def last_audit_record(path: Path) -> tuple[dict[str, Any] | None, bool]:
    """Read the final record only; return whether it already has a line terminator."""
    if not path.exists() or path.stat().st_size == 0:
        return None, True
    with path.open("rb") as stream:
        stream.seek(0, 2)
        end = stream.tell()
        stream.seek(end - 1)
        terminated = stream.read(1) == b"\n"
        position = end - 1 if terminated else end
        chunks = bytearray()
        while position:
            start = max(0, position - 4096)
            stream.seek(start)
            chunk = stream.read(position - start)
            newline = chunk.rfind(b"\n")
            if newline >= 0:
                chunks[:0] = chunk[newline + 1 :]
                break
            chunks[:0] = chunk
            position = start
        try:
            entry = json.loads(bytes(chunks))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("audit log final line is not valid JSON") from None
        if not isinstance(entry, dict):
            raise ValueError("audit log final line is not a JSON object")
        return entry, terminated


def verify_audit(
    source: Path | bytes,
    chain_since: int | None = None,
    legacy_hash: str | None = None,
) -> tuple[bool, str | None]:
    """Verify a snapshot, allowing only the legacy prefix recorded in project metadata."""
    metadata_path = None
    metadata = None
    if isinstance(source, Path) and chain_since is None:
        metadata_path = source.parent / "project.json"
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                chain_since = metadata.get("audit_chain_since")
                legacy_hash = metadata.get("audit_legacy_hash")
            except (OSError, json.JSONDecodeError):
                return False, "project metadata is invalid"
    try:
        raw = _read_bytes(source)
    except OSError as exc:
        return False, str(exc)
    if chain_since is None and metadata_path is not None and metadata is not None:
        try:
            checkpoint = checkpoint_for_existing_log(raw)
        except ValueError:
            checkpoint = None
        if checkpoint is not None:
            chain_since, legacy_hash = checkpoint
            metadata["audit_chain_since"] = chain_since
            metadata["audit_legacy_hash"] = legacy_hash
            fd, temporary = tempfile.mkstemp(dir=metadata_path.parent, prefix=".project.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(metadata, stream, ensure_ascii=False, indent=2)
                os.replace(temporary, metadata_path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(temporary)
                raise
    lines = raw.splitlines(keepends=True)
    if chain_since is None:
        for line_number, raw_line in enumerate(lines, 1):
            try:
                entry = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                return False, f"line {line_number}: invalid JSON"
            if not isinstance(entry, dict):
                return False, f"line {line_number}: expected a JSON object"
            if "hash" in entry or "prev_hash" in entry:
                return False, "chain checkpoint is missing"
        return True, None
    if chain_since < 1:
        return False, "chain checkpoint is invalid"
    prefix = b"".join(lines[: chain_since - 1])
    expected_legacy_hash = hashlib.sha256(prefix).hexdigest() if prefix else GENESIS_HASH
    if legacy_hash != expected_legacy_hash:
        return False, "legacy prefix hash mismatch"
    previous = expected_legacy_hash
    for line_number, raw_line in enumerate(lines, 1):
        try:
            entry = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False, f"line {line_number}: invalid JSON"
        if line_number < chain_since:
            if not isinstance(entry, dict) or "hash" in entry or "prev_hash" in entry:
                return False, f"line {line_number}: invalid legacy prefix"
            continue
        if not isinstance(entry, dict):
            return False, f"line {line_number}: expected a JSON object"
        if "hash" not in entry or "prev_hash" not in entry:
            return False, f"line {line_number}: unhashed entry after chain start"
        if entry.get("prev_hash") != previous:
            return False, f"line {line_number}: prev_hash mismatch"
        claimed = entry.get("hash")
        payload = {key: value for key, value in entry.items() if key != "hash"}
        if not isinstance(claimed, str) or entry_hash(payload) != claimed:
            return False, f"line {line_number}: hash mismatch"
        previous = claimed
    if len(lines) < chain_since:
        return False, "chain record is missing"
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
