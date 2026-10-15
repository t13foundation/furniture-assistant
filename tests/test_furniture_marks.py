from __future__ import annotations

import pytest
from PIL import Image
from src.furniture.marks import build_chains, extract_marks

from tests.conftest import sample_file

ZM05 = sample_file("GLADIOGROUP_P02_PW-ZM05_12.pdf")


@pytest.mark.skipif(ZM05 is None, reason="sample drawing not available")
def test_zm05_marks_and_overall_height_chain():
    marks, runs = extract_marks(str(ZM05), 0)
    assert len(marks) >= 100
    assert {m.unit for m in marks} == {"cm"}
    assert any(r.text.lower() == "szuflada" for r in runs)

    chains = build_chains(marks)
    by_id = {m.id: m for m in marks}
    matching = [
        c for c in chains if c.orientation == "vertical" and sorted(by_id[i].value for i in c.mark_ids) == [10, 65, 195]
    ]
    assert matching
    chain = matching[0]
    assert chain.sum_value == 270
    assert chain.consistent is True
    assert chain.overall_mark_id is not None
    assert by_id[chain.overall_mark_id].value == 270
    assert all(by_id[i].chain_id == chain.id for i in chain.mark_ids)


@pytest.mark.skipif(ZM05 is None, reason="sample drawing not available")
def test_chains_from_different_modules_on_one_row_stay_separate():
    marks, _ = extract_marks(str(ZM05), 0)
    chains = build_chains(marks)
    by_id = {m.id: m for m in marks}
    # Width chain of one module (30+48+48+30) has its own 156 overall mark; the neighbouring
    # module's 53.5/60 chain on the same text row must not be merged into it.
    widths = [c for c in chains if sorted(by_id[i].value for i in c.mark_ids) == [30, 30, 48, 48]]
    assert len(widths) == 1
    assert widths[0].consistent is True


def test_non_pdf_has_no_marks(tmp_path):
    image = tmp_path / "scan.png"
    Image.new("RGB", (20, 20), "white").save(image)
    assert extract_marks(str(image), 0) == ([], [])
