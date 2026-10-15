"""Unit tests for tool registry, cache, and validation merge."""

from __future__ import annotations

from src.schema.enums import ConfidenceLevel
from src.schema.extraction import ExtractionResult
from src.schema.validation import merge_diff
from src.tools.cache import ToolCache


class TestToolCache:
    def test_cache_hit(self):
        cache = ToolCache()
        cache.put("get_tile", {"row": 2, "col": 2}, ["tile data"])
        result = cache.get("get_tile", {"row": 2, "col": 2})
        assert result == ["tile data"]

    def test_cache_miss(self):
        cache = ToolCache()
        result = cache.get("get_tile", {"row": 0, "col": 0})
        assert result is None

    def test_cache_key_deterministic(self):
        cache = ToolCache()
        cache.put("get_tile", {"col": 2, "row": 2}, ["data"])
        result = cache.get("get_tile", {"row": 2, "col": 2})
        assert result == ["data"]

    def test_cache_eviction(self):
        cache = ToolCache(max_size=2)
        cache.put("t1", {"a": 1}, "r1")
        cache.put("t2", {"a": 2}, "r2")
        cache.put("t3", {"a": 3}, "r3")  # evicts t1
        assert cache.get("t1", {"a": 1}) is None
        assert cache.get("t2", {"a": 2}) == "r2"

    def test_cache_ttl_expiry(self):
        cache = ToolCache(ttl=0.0)  # instant expiry
        cache.put("t", {"a": 1}, "r")
        assert cache.get("t", {"a": 1}) is None

    def test_hit_rate(self):
        cache = ToolCache()
        cache.put("t", {"a": 1}, "r")
        cache.get("t", {"a": 1})  # hit
        cache.get("t", {"a": 2})  # miss
        assert cache.hit_rate == 0.5

    def test_hit_rate_no_ops(self):
        cache = ToolCache()
        assert cache.hit_rate == 0.0

    def test_clear(self):
        cache = ToolCache()
        cache.put("t", {"a": 1}, "r")
        cache.clear()
        assert cache.get("t", {"a": 1}) is None
        assert cache.hit_rate == 0.0


class TestToolRegistry:
    def test_all_tools_defined(self):
        from src.tools.registry import ALL_TOOLS

        assert len(ALL_TOOLS) == 12

    def test_tool_names_unique(self):
        from src.tools.registry import ALL_TOOLS

        names = [t.name for t in ALL_TOOLS]
        assert len(names) == len(set(names))

    def test_validation_tools_subset(self):
        from src.tools.registry import ALL_TOOLS, VALIDATION_TOOLS

        all_names = {t.name for t in ALL_TOOLS}
        val_names = {t.name for t in VALIDATION_TOOLS}
        assert val_names.issubset(all_names)

    def test_all_tools_have_callable(self):
        from src.tools.registry import ALL_TOOLS

        for t in ALL_TOOLS:
            assert callable(t.func), f"Tool {t.name} has no callable func"


class TestMergeDiff:
    def _make_result(self, **overrides) -> ExtractionResult:
        """Create a minimal ExtractionResult for testing."""
        defaults = {"source_file": "test.pdf"}
        defaults.update(overrides)
        return ExtractionResult(**defaults)

    def test_no_changes(self):
        result = self._make_result()
        diffs = merge_diff(result, {"project_name": {"value": None, "confidence": "MEDIUM"}})
        assert len(diffs) == 0

    def test_value_changed(self):
        result = self._make_result()
        result.project_name.value = "BIUROWIEC A"
        diffs = merge_diff(result, {"project_name": {"value": "BIUROWIEC A 2"}})
        assert len(diffs) == 1
        assert diffs[0].new_value == "BIUROWIEC A 2"
        assert result.project_name.value == "BIUROWIEC A 2"

    def test_confidence_changed(self):
        result = self._make_result()
        result.project_name.confidence = ConfidenceLevel.MEDIUM
        diffs = merge_diff(result, {"project_name": {"confidence": "HIGH"}})
        assert len(diffs) == 1
        assert diffs[0].new_confidence == "HIGH"
        assert result.project_name.confidence == ConfidenceLevel.HIGH

    def test_skip_metadata_fields(self):
        result = self._make_result()
        diffs = merge_diff(
            result,
            {
                "source_file": "other.pdf",
                "model_used": "test",
                "tokens_used": 999,
            },
        )
        assert len(diffs) == 0
        assert result.source_file == "test.pdf"

    def test_unknown_field_ignored(self):
        result = self._make_result()
        diffs = merge_diff(result, {"nonexistent_field": {"value": "x"}})
        assert len(diffs) == 0

    def test_non_dict_value_ignored(self):
        result = self._make_result()
        diffs = merge_diff(result, {"project_name": "not a dict"})
        assert len(diffs) == 0

    def test_multiple_fields(self):
        result = self._make_result()
        result.project_name.value = "Project A"
        result.client.value = None
        diffs = merge_diff(
            result,
            {
                "project_name": {"value": "BIUROWIEC A", "confidence": "HIGH"},
                "client": {"value": "Klient testowy", "confidence": "MEDIUM"},
            },
        )
        assert len(diffs) == 2
        assert result.project_name.value == "BIUROWIEC A"
        assert result.client.value == "Klient testowy"

    def test_notes_updated(self):
        result = self._make_result()
        result.project_name.notes = ""
        diffs = merge_diff(result, {"project_name": {"notes": "verified via title block"}})
        assert len(diffs) == 1
        assert result.project_name.notes == "verified via title block"
