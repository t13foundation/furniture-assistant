import asyncio
from typing import cast
from unittest.mock import MagicMock

import pytest
from config.settings import AppConfig
from src.document.loader import DocumentLoader
from src.pipeline.agent import DrawingDeps
from src.pipeline.orchestrator import DrawingPipeline, _apply_project_hint, derive_project_hint
from src.pipeline.passes import ALL_PASSES, PASS1_CONFIG, PASS2_CONFIG, PASS3_CONFIG
from src.pipeline.tokens import DrawingMetrics, TokenBudget
from src.schema.enums import ConfidenceLevel, DrawingType
from src.schema.extraction import ExtractionResult, Panel
from src.schema.geometry import reconcile_panel_geometry


class TestPassConfig:
    def test_all_passes_defined(self) -> None:
        assert len(ALL_PASSES) == 3
        assert PASS1_CONFIG.pass_name == "layout_analysis"
        assert PASS2_CONFIG.pass_name == "data_extraction"
        assert PASS3_CONFIG.pass_name == "validation"

    def test_pass_prompts_not_empty(self) -> None:
        for p in ALL_PASSES:
            assert len(p.prompt) > 100


class TestDeriveProjectHint:
    """Tests for the folder-derived project_name hint.

    The hint prevents the model from echoing the filename as project_name — the
    measured failure on 2 SZAFA - NASZ RYSUNEK.png (got the filename instead of
    the project folder name). The hint is derived from the parent folder.
    """

    def test_strips_category_prefix(self) -> None:
        hint = derive_project_hint("sample-data/BIUROWE - BIUROWIEC A/2 SZAFA - NASZ RYSUNEK.png")
        assert hint == "BIUROWIEC A"

    def test_keeps_everything_after_first_separator(self) -> None:
        hint = derive_project_hint("sample-data/BIUROWE - KLIENT B - BIUROWIEC B/05_zabudowy meblowe.pdf")
        # " - " splits on first occurrence: "BIUROWE" vs "KLIENT B - BIUROWIEC B"
        assert hint == "KLIENT B - BIUROWIEC B"

    def test_apartment_folder(self) -> None:
        hint = derive_project_hint("sample-data/BIUROWE - APARTAMENT 4/umowa_26062026.pdf")
        assert hint == "APARTAMENT 4"

    def test_nested_subfolder_finds_project_ancestor(self) -> None:
        # Drawings often sit in a dated subfolder, NOT directly under the project
        # folder. The hint must walk UP past "2026.01.21 PROJEKTY OSTATECZNE" to
        # find "BIUROWE - BIUROWIEC A".
        hint = derive_project_hint(
            "sample-data/BIUROWE - BIUROWIEC A/2026.01.21 PROJEKTY OSTATECZNE/PROJEKT_ZM01_04.pdf"
        )
        assert hint == "BIUROWIEC A"

    def test_no_customer_prefix_returns_full_folder(self) -> None:
        # A folder without " - " is returned as-is
        hint = derive_project_hint("data/SOMEPROJECT/drawing.pdf")
        assert hint == "SOMEPROJECT"

    def test_sample_data_root_returns_empty(self) -> None:
        # A drawing directly under sample-data/ has no project hint
        hint = derive_project_hint("sample-data/drawing.png")
        assert hint == ""

    def test_bare_filename_returns_empty(self) -> None:
        hint = derive_project_hint("drawing.png")
        assert hint == ""

    def test_folder_hint_overrides_model_project_name(self) -> None:
        result = ExtractionResult(
            source_file="drawing.pdf",
            project_name={"value": "Long architectural document title", "confidence": "MEDIUM"},
        )
        source = "sample-data/BIUROWE - BIUROWIEC A/drawing.pdf"

        _apply_project_hint(result, source)

        assert result.project_name.value == "BIUROWIEC A"
        assert result.project_name.confidence == ConfidenceLevel.HIGH
        assert result.project_name.source_region is None


class TestDrawingDeps:
    def test_creation(self, sample_pdf_path: str | None) -> None:
        if sample_pdf_path is None:
            pytest.skip("No sample PDF available")
        loader = DocumentLoader(sample_pdf_path)
        deps = DrawingDeps(loader=loader, drawing_path=sample_pdf_path)
        assert deps.layout_map is None
        assert deps.previous_extraction is None
        assert deps.filename == ""


class TestDrawingPipeline:
    def test_geometry_reconciliation_preserves_range_for_duplicate_views(self) -> None:
        result = ExtractionResult(
            source_file="drawing.png",
            panels=[
                Panel(name="Front", length_mm=1950, width_mm=500, qty=4),
                Panel(name="Drzwi", length_mm=500, width_mm=1950, qty=4),
                Panel(name="Front", length_mm=477, width_mm=298, qty=5),
                Panel(name="Front", length_mm=477, qty=5),
            ],
        )

        warnings = reconcile_panel_geometry(result)

        assert result.geometry_summary.panel_records == 4
        assert result.geometry_summary.resolved_records == 3
        assert result.geometry_summary.unresolved_records == 1
        assert result.geometry_summary.minimum_panel_count == 9
        assert result.geometry_summary.maximum_panel_count == 18
        assert result.geometry_groups[0].source_panel_indices == [0, 1]
        assert result.geometry_groups[0].minimum_qty == 4
        assert result.geometry_groups[0].maximum_qty == 8
        assert any("ambiguous quantity" in warning for warning in warnings)

    def test_geometry_reconciliation_keeps_categories_separate(self) -> None:
        result = ExtractionResult(
            source_file="drawing.png",
            panels=[
                Panel(name="Bok lewy", length_mm=2700, width_mm=600),
                Panel(name="Półka", length_mm=2700, width_mm=600),
            ],
        )

        reconcile_panel_geometry(result)

        assert [group.category for group in result.geometry_groups] == ["side_left", "shelf"]
        assert result.geometry_summary.minimum_panel_count == 2
        assert result.geometry_summary.maximum_panel_count == 2

    def test_discover_drawings_finds_files(self, tmp_path) -> None:
        for name in ("drawing.pdf", "photo.PNG", "cabinet.dwg"):
            (tmp_path / name).write_bytes(b"")
        (tmp_path / "notes.txt").write_text("not a drawing")
        pipeline = DrawingPipeline(AppConfig(input_dir=str(tmp_path)))

        drawings = pipeline.discover_drawings(str(tmp_path))

        assert [drawing.name for drawing in drawings] == [
            "cabinet.dwg",
            "drawing.pdf",
            "photo.PNG",
        ]

    def test_discover_drawings_filters_extensions(self, tmp_path) -> None:
        (tmp_path / "drawing.jpg").write_bytes(b"")
        (tmp_path / "notes.txt").write_text("not a drawing")
        pipeline = DrawingPipeline(AppConfig(input_dir=str(tmp_path)))

        drawings = pipeline.discover_drawings(str(tmp_path))

        assert [drawing.name for drawing in drawings] == ["drawing.jpg"]

    def test_discover_single_file(self, sample_pdf_path: str | None) -> None:
        if sample_pdf_path is None:
            pytest.skip("No sample PDF available")
        config = AppConfig()
        pipeline = DrawingPipeline(config)
        drawings = pipeline.discover_drawings(sample_pdf_path)
        assert len(drawings) == 1

    def test_process_p3d_records_export_requirement(self, tmp_path) -> None:
        path = tmp_path / "cabinet.p3d"
        path.write_bytes(b"p3d")

        result, metrics = asyncio.run(DrawingPipeline(AppConfig()).process_drawing(str(path)))

        assert result.drawing_type == DrawingType.CAD
        assert result.needs_review is True
        assert result.model_used == "not-processed"
        assert result.unresolvable_fields == [
            "P3D requires a Palette CAD export as PDF, DWG, or DXF before panel extraction."
        ]
        assert metrics.drawing_type == DrawingType.CAD.value

    def test_failed_fallback_marks_result_for_review(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fail_extraction(**_kwargs):
            raise RuntimeError("model unavailable")

        monkeypatch.setattr("src.pipeline.simple_extractor.extract_drawing", fail_extraction)
        pipeline = DrawingPipeline(AppConfig())
        result, _ = asyncio.run(
            pipeline._fallback_to_simple(
                DrawingDeps(loader=MagicMock(), drawing_path="drawing.dwg"),
                DrawingType.CAD,
                "drawing.dwg",
                "drawing.dwg",
                DrawingMetrics(
                    filename="drawing.dwg",
                    drawing_type=DrawingType.CAD.value,
                ),
            )
        )

        assert result.needs_review is True
        assert result.review_fields == ["extraction"]
        assert result.unresolvable_fields == ["Primary and fallback extraction both failed."]

    def test_extract_single_forces_row_major_tiles_for_every_page(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class FakeResult:
            def __init__(self, output: str, input_tokens: int, output_tokens: int) -> None:
                self.output = output
                self._usage = MagicMock(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )

            def usage(self):
                return self._usage

            def all_messages(self) -> list:
                return []

        class FakeAgent:
            def __init__(self, result: FakeResult) -> None:
                self.result = result
                self.prompts: list = []

            async def run(self, *, user_prompt, **_kwargs):
                self.prompts.append(user_prompt)
                return self.result

        class FakeLoader:
            page_count = 2

            def __init__(self) -> None:
                self.requested_pages: list[int] = []

            def get_tiles(self, grid_size: int, overlap: float, page_index: int) -> list[dict]:
                assert grid_size == 3
                assert overlap == 0.15
                self.requested_pages.append(page_index)
                return [
                    {
                        "row": row,
                        "col": col,
                        "label": f"tile-{row}-{col}",
                        "x_min": col / 3,
                        "y_min": row / 3,
                        "x_max": (col + 1) / 3,
                        "y_max": (row + 1) / 3,
                        "image_bytes": b"tile",
                        "media_type": "image/png",
                    }
                    for row in range(3)
                    for col in range(3)
                ]

        explore_agent = FakeAgent(FakeResult("No relevant evidence in this tile.", 10, 5))
        summarize_agent = FakeAgent(FakeResult('{"source_file": "drawing.png"}', 20, 5))
        monkeypatch.setattr(
            "src.pipeline.orchestrator.create_unified_agent",
            lambda **_kwargs: explore_agent,
        )
        monkeypatch.setattr(
            "src.pipeline.agent.create_summarizing_agent",
            lambda **_kwargs: summarize_agent,
        )
        loader = FakeLoader()
        pipeline = DrawingPipeline(AppConfig())
        tool_calls = []

        result, budget = asyncio.run(
            pipeline._extract_single(
                DrawingDeps(loader=cast(DocumentLoader, loader), drawing_path="drawing.png"),
                DrawingType.IMAGE,
                "drawing.png",
                "drawing.png",
                TokenBudget("test"),
                tool_calls,
            )
        )

        expected_coordinates = [
            {"page_index": page, "row": row, "col": col} for page in range(2) for row in range(3) for col in range(3)
        ]
        assert loader.requested_pages == [0, 1]
        assert len(explore_agent.prompts) == 18
        assert [record.args for record in tool_calls] == expected_coordinates
        assert all(record.tool_name == "forced_tile_coverage" for record in tool_calls)
        assert "page_index=0 row=0 col=0" in summarize_agent.prompts[0]
        assert "page_index=1 row=2 col=2" in summarize_agent.prompts[0]
        assert budget.request_count == 19

        metrics = DrawingMetrics(
            filename="drawing.png",
            drawing_type=DrawingType.IMAGE.value,
            tool_calls=tool_calls,
        )
        pipeline._finalize_result(result, metrics)
        assert [entry["args"] for entry in result.tool_call_log] == expected_coordinates

    def test_config_defaults(self) -> None:
        config = AppConfig()
        assert config.api.base_url == "http://localhost:1234/v1"
        assert config.pipeline.max_passes == 3
        assert config.models.orchestrator == "qwen3.6-35b"
        assert config.models.vision == "qwen3.6-35b"
        assert config.models.benchmark == ["qwen3.6-35b", "qwen3.5-35b"]
