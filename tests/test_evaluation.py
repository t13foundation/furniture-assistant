"""Tests for the evaluation/accuracy module (panel + hardware comparison)."""

from src.evaluation.accuracy import (
    _filename_id,
    compare_drawing,
    compare_hardware,
    compare_panels,
    match_drawings,
)


class TestPanelComparison:
    def test_matched_panel(self) -> None:
        gt = [{"name": "Blat", "length_mm": 800, "width_mm": 600, "thickness_mm": 18, "board_type": "MFC 18mm"}]
        pred = [{"name": "Blat", "length_mm": 800, "width_mm": 600, "thickness_mm": 18, "board_type": "MFC 18mm"}]
        comps = compare_panels(gt, pred)
        assert len(comps) == 1
        assert comps[0].status == "matched"
        assert comps[0].matched is True

    def test_partial_wrong_dimension(self) -> None:
        gt = [{"name": "Bok", "length_mm": 800, "width_mm": 600}]
        pred = [{"name": "Bok", "length_mm": 999, "width_mm": 600}]
        comps = compare_panels(gt, pred)
        assert comps[0].status == "partial"
        assert comps[0].length_ok is False

    def test_dimension_tolerance(self) -> None:
        # A 1mm difference is within the 2mm tolerance → still a match.
        gt = [{"name": "Front", "length_mm": 800, "width_mm": 600}]
        pred = [{"name": "Front", "length_mm": 801, "width_mm": 600}]
        comps = compare_panels(gt, pred)
        assert comps[0].status == "matched"

    def test_missing_panel(self) -> None:
        gt = [{"name": "Półka", "length_mm": 500, "width_mm": 400}]
        comps = compare_panels(gt, [])
        assert comps[0].status == "missing"
        assert comps[0].matched is False

    def test_extra_predicted_panel_ignored(self) -> None:
        gt = [{"name": "Blat", "length_mm": 800, "width_mm": 600}]
        pred = [
            {"name": "Blat", "length_mm": 800, "width_mm": 600},
            {"name": "Szuflada", "length_mm": 200, "width_mm": 100},
        ]
        comps = compare_panels(gt, pred)
        assert len(comps) == 1  # only GT panels produce comparisons


class TestHardwareComparison:
    def test_matched_hardware(self) -> None:
        gt = [{"name": "Zawias Blum", "code": "Blum CLIP top", "qty": 4}]
        pred = [{"name": "Zawias Blum", "code": "Blum CLIP top", "qty": 4}]
        comps = compare_hardware(gt, pred)
        assert comps[0].status == "matched"

    def test_wrong_qty_is_partial(self) -> None:
        gt = [{"name": "Prowadnica", "qty": 2}]
        pred = [{"name": "Prowadnica", "qty": 3}]
        comps = compare_hardware(gt, pred)
        assert comps[0].status == "partial"


class TestFilenameMatching:
    def test_filename_id_stem(self) -> None:
        assert _filename_id("2 SZAFA - NASZ RYSUNEK.png") == "2 SZAFA - NASZ RYSUNEK"

    def test_filename_id_legacy_kopia(self) -> None:
        # The legacy "Kopia <code>" pattern captures the rest of the filename token.
        assert _filename_id("Kopia ABC123.pdf") == "ABC123.PDF"

    def test_match_drawings_exact_stem(self) -> None:
        gt = {"2 SZAFA - NASZ RYSUNEK.png": {"panels": []}}
        results = {"2 SZAFA - NASZ RYSUNEK.png": {"panels": []}}
        matched = match_drawings(gt, results)
        assert len(matched) == 1

    def test_match_drawings_substring(self) -> None:
        gt = {"front.png": {"project_name": "X"}}
        results = {"PROJEKT front.png": {"project_name": {"value": "X", "confidence": "HIGH"}}}
        matched = match_drawings(gt, results)
        assert len(matched) == 1


class TestCompareDrawingEndToEnd:
    def test_panels_and_hardware_populated(self) -> None:
        gt = {
            "project_name": "BIUROWIEC",
            "client": "KLIENT",
            "panels": [{"name": "Blat", "length_mm": 800, "width_mm": 600, "thickness_mm": 18}],
            "hardware": [{"name": "Zawias", "qty": 4}],
        }
        result = {
            "project_name": {"value": "BIUROWIEC", "confidence": "HIGH"},
            "client": {"value": "KLIENT", "confidence": "HIGH"},
            "panels": [{"name": "Blat", "length_mm": 800, "width_mm": 600, "thickness_mm": 18}],
            "hardware": [{"name": "Zawias", "qty": 4}],
        }
        comp = compare_drawing(gt, result)
        assert comp.panels_gt == 1
        assert comp.panels_matched == 1
        assert comp.panel_accuracy == 1.0
        assert comp.hardware_matched == 1
        assert comp.hardware_accuracy == 1.0

    def test_missing_panels_counted(self) -> None:
        gt = {
            "panels": [
                {"name": "Blat", "length_mm": 800, "width_mm": 600},
                {"name": "Bok", "length_mm": 700, "width_mm": 500},
            ],
        }
        result = {"panels": [{"name": "Blat", "length_mm": 800, "width_mm": 600}]}
        comp = compare_drawing(gt, result)
        assert comp.panels_gt == 2
        assert comp.panels_matched == 1
        assert comp.panels_missing == 1
        assert comp.panel_accuracy == 0.5
