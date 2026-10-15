import asyncio

import pytest
from pydantic_ai.messages import BinaryContent
from src.document.loader import DocumentLoader
from src.document.regions import Region
from src.tools.cross_reference import validate_board_type, validate_edge_material, validate_hardware
from src.tools.dimensions import read_dimensions
from src.tools.symbols import find_symbols
from src.tools.text_search import search_text
from src.tools.title_block import read_title_block
from src.tools.view import find_hardware, read_legend, read_panel_detail, view_overview, zoom_region


class TestViewTools:
    def test_view_overview(self, sample_pdf_path: str | None) -> None:
        if sample_pdf_path is None:
            pytest.skip("No sample PDF available")
        loader = DocumentLoader(sample_pdf_path)
        result = view_overview(loader)
        assert isinstance(result, list)
        assert isinstance(result[0], str)
        assert "overview" in result[0].lower()
        assert isinstance(result[1], BinaryContent)

    def test_zoom_region(self, sample_pdf_path: str | None) -> None:
        if sample_pdf_path is None:
            pytest.skip("No sample PDF available")
        loader = DocumentLoader(sample_pdf_path)
        result = asyncio.run(zoom_region(loader, 0.7, 0.8, 1.0, 1.0, "title block"))
        assert isinstance(result, list)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], BinaryContent)

    def test_view_tools_accept_page_index(self, sample_pdf_path: str | None) -> None:
        if sample_pdf_path is None:
            pytest.skip("No sample PDF available")
        loader = DocumentLoader(sample_pdf_path)
        page_index = loader.page_count - 1

        overview = view_overview(loader, page_index=page_index)
        zoom = asyncio.run(zoom_region(loader, 0.0, 0.0, 0.2, 0.2, page_index=page_index))

        assert f"page {page_index + 1}/{loader.page_count}" in overview[0].lower()
        assert f"page {page_index + 1}/{loader.page_count}" in zoom[0].lower()

    def test_all_visual_tools_report_requested_page(self, sample_pdf_path: str | None) -> None:
        if sample_pdf_path is None:
            pytest.skip("No sample PDF available")
        loader = DocumentLoader(sample_pdf_path)
        page_index = loader.page_count - 1
        expected = f"page {page_index + 1}/{loader.page_count}"
        region = Region(0.0, 0.0, 0.2, 0.2)

        async def exercise_tools() -> list[list]:
            return [
                await read_panel_detail(
                    loader,
                    0.0,
                    0.0,
                    0.2,
                    0.2,
                    "Panel",
                    page_index=page_index,
                ),
                await read_legend(loader, page_index=page_index),
                await find_hardware(
                    loader,
                    0.0,
                    0.0,
                    0.2,
                    0.2,
                    page_index=page_index,
                ),
                await read_dimensions(loader, region, page_index=page_index),
                await search_text(loader, ["panel"], region, page_index=page_index),
                await find_symbols(loader, region, page_index=page_index),
                await read_title_block(loader, region, page_index=page_index),
            ]

        for result in asyncio.run(exercise_tools()):
            assert expected in result[0].lower()

    def test_zoom_region_clamps_out_of_bounds(self, sample_pdf_path: str | None) -> None:
        if sample_pdf_path is None:
            pytest.skip("No sample PDF available")
        loader = DocumentLoader(sample_pdf_path)
        result = asyncio.run(zoom_region(loader, -0.5, -0.5, 1.5, 1.5, "test"))
        assert isinstance(result, list)
        assert isinstance(result[1], BinaryContent)
        assert "(0.00,0.00)-(1.00,1.00)" in result[0]


class TestCrossReference:
    def test_validate_board_type_exact(self) -> None:
        result = validate_board_type("MFC")
        assert result["found"] is True
        assert result["board_type"] == "MFC"
        assert result["confidence"] == "HIGH"

    def test_validate_board_type_fuzzy(self) -> None:
        result = validate_board_type("PLYWOD")
        assert result["found"] is True
        assert result["confidence"] == "MEDIUM"

    def test_validate_board_type_not_found_keeps_panel(self) -> None:
        # A genuinely unknown board string must still instruct the agent to KEEP
        # the panel (regression for the panels=[] dropout bug — see §7 of plan).
        result = validate_board_type("XYZ 99999")
        assert result["found"] is False
        assert result["keep_panel"] is True
        assert result["confidence"] == "LOW"
        assert result["board_type"] == "XYZ 99999"

    def test_validate_board_type_polish_bare_word(self) -> None:
        # The generic Polish word "Płyta" (board) must now resolve via the alias
        # map instead of returning a dead-end found:False (root cause of the
        # original panels=[] failure).
        result = validate_board_type("Płyta")
        assert result["found"] is True
        assert result["board_type"] == "MFC"
        assert result["confidence"] == "MEDIUM"

    def test_validate_board_type_polish_phrase(self) -> None:
        result = validate_board_type("płyta wiórowa 18mm")
        assert result["found"] is True
        assert result["board_type"] == "WIOROWA"

    def test_validate_hardware_exact(self) -> None:
        result = validate_hardware("ZAWIAS")
        assert result["found"] is True
        assert result["category"] == "ZAWIAS"
        assert result["confidence"] == "HIGH"

    def test_validate_edge_material_exact(self) -> None:
        result = validate_edge_material("ABS")
        assert result["found"] is True
        assert result["material"] == "ABS"
        assert result["confidence"] == "HIGH"

    def test_validate_edge_material_fuzzy(self) -> None:
        # Regression: validate_edge_material previously had NO fuzzy fallback.
        # "AKRIL" is close to "AKRYL" but not a substring, so it exercises fuzzy.
        result = validate_edge_material("AKRIL")
        assert result["found"] is True
        assert result["confidence"] == "MEDIUM"

    def test_validate_edge_material_not_found_keeps_panel(self) -> None:
        result = validate_edge_material("Unobtainium")
        assert result["found"] is False
        assert result["keep_panel"] is True
        assert result["confidence"] == "LOW"

    def test_validate_hardware_unknown(self) -> None:
        result = validate_hardware("UnknownHardware")
        assert result["found"] is False
        assert result["keep_panel"] is True
