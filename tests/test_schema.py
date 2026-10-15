from src.schema.enums import ConfidenceLevel, DrawingType, classify_drawing
from src.schema.extraction import ExtractionResult, Panel
from src.schema.validation import merge_diff, normalize_values, panel_review_reasons, validate_result


class TestEnums:
    def test_classify_elevation(self) -> None:
        assert classify_drawing("05_A_BIUROWIEC-zabudowy meblowe.pdf") == DrawingType.ELEVATION

    def test_classify_floor_plan(self) -> None:
        assert classify_drawing("PROJEKT_P02_PW-RZUT_POSADZEK.pdf") == DrawingType.FLOOR_PLAN

    def test_classify_cost_sheet(self) -> None:
        assert classify_drawing("2025.09.12 - NASZE KOSZTY.pdf") == DrawingType.COST_SHEET

    def test_classify_image(self) -> None:
        assert classify_drawing("2 SZAFA - NASZ RYSUNEK.png") == DrawingType.IMAGE

    def test_classify_section(self) -> None:
        assert classify_drawing("PROJEKT_P02_PW-PRZEKROJ.pdf") == DrawingType.SECTION

    def test_classify_other(self) -> None:
        assert classify_drawing("unknown_file.xyz") == DrawingType.OTHER

    def test_confidence_levels(self) -> None:
        assert ConfidenceLevel.HIGH.value == "HIGH"
        assert ConfidenceLevel.MEDIUM.value == "MEDIUM"
        assert ConfidenceLevel.LOW.value == "LOW"


class TestExtractionResult:
    def test_default_creation(self) -> None:
        r = ExtractionResult(
            source_file="test.pdf",
            drawing_type=DrawingType.ELEVATION,
        )
        assert r.source_file == "test.pdf"
        assert r.project_name.value is None
        assert r.project_name.confidence == ConfidenceLevel.MEDIUM
        assert r.needs_review is False
        assert r.passes_completed == 0
        assert r.panels == []
        assert r.hardware == []

    def test_full_creation(self, sample_result: ExtractionResult) -> None:
        assert sample_result.project_name.value == "BIUROWIEC A"
        assert sample_result.client.value == "Klient testowy"
        assert len(sample_result.panels) == 1
        assert sample_result.panels[0].name == "Blat"
        assert sample_result.panels[0].length_mm == 800

    def test_serialization_roundtrip(self, sample_result: ExtractionResult) -> None:
        json_str = sample_result.model_dump_json()
        restored = ExtractionResult.model_validate_json(json_str)
        assert restored.project_name.value == sample_result.project_name.value
        assert restored.panels[0].length_mm == sample_result.panels[0].length_mm
        assert restored.hardware[0].name == sample_result.hardware[0].name

    def test_panels_default_empty(self) -> None:
        r = ExtractionResult(source_file="test.pdf")
        assert r.panels == []
        assert r.hardware == []

    def test_panel_null_quantity_defaults_to_one(self) -> None:
        panel = Panel.model_validate({"qty": None})
        assert panel.qty == 1


class TestValidation:
    def test_elevation_no_panels_warning(self) -> None:
        r = ExtractionResult(
            source_file="test.pdf",
            drawing_type=DrawingType.ELEVATION,
        )
        warnings = validate_result(r)
        assert any("no panels" in w for w in warnings)

    def test_panel_unusual_thickness_warning(self) -> None:
        r = ExtractionResult(
            source_file="test.pdf",
            panels=[Panel(name="Blat", thickness_mm=100)],
        )
        warnings = validate_result(r)
        assert any("thickness" in w for w in warnings)

    def test_valid_result_no_warnings(self) -> None:
        r = ExtractionResult(
            source_file="test.pdf",
            drawing_type=DrawingType.COST_SHEET,
            panels=[Panel(name="Blat", length_mm=800, width_mm=600, thickness_mm=18, board_type="MFC", qty=1)],
        )
        warnings = validate_result(r)
        assert len(warnings) == 0

    def test_cm_normalization_does_not_scale_panel_thickness(self) -> None:
        result = ExtractionResult(
            source_file="test.pdf",
            units={"value": "cm"},
            panels=[Panel(length_mm=210, width_mm=60, thickness_mm=18)],
        )

        normalize_values(result)

        assert result.panels[0].length_mm == 2100
        assert result.panels[0].width_mm == 600
        assert result.panels[0].thickness_mm == 18
        assert result.units.value == "mm"

    def test_output_units_are_always_normalized_to_mm(self) -> None:
        result = ExtractionResult(source_file="test.pdf")
        result.units.value = "m"
        result.panels = [Panel(length_mm=2100, width_mm=600, thickness_mm=18)]

        normalize_values(result)

        assert result.units.value == "mm"
        assert result.panels[0].length_mm == 2100
        assert result.panels[0].width_mm == 600
        assert result.panels[0].thickness_mm == 18

    def test_validation_missing_sentinel_does_not_erase_known_value(self) -> None:
        result = ExtractionResult(source_file="test.pdf")
        result.units.value = "mm"
        result.units.confidence = ConfidenceLevel.HIGH

        diffs = merge_diff(
            result,
            {"units": {"value": "None", "confidence": "HIGH"}},
        )

        assert diffs == []
        assert result.units.value == "mm"
        assert result.units.confidence == ConfidenceLevel.HIGH

    def test_incomplete_panel_has_production_review_reasons(self) -> None:
        panel = Panel(
            length_mm=100,
            width_mm=None,
            thickness_mm=None,
            confidence=ConfidenceLevel.LOW,
        )

        assert panel_review_reasons(panel) == [
            "LOW confidence",
            "Missing width",
            "Missing thickness",
            "Missing board type/material code",
        ]
