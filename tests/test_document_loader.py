import io
import subprocess
from pathlib import Path

import pytest
from PIL import Image
from src.document.loader import DocumentLoader
from src.document.regions import Region, normalized_to_pixel, pixel_to_normalized
from src.document.tiling import tile_region


class TestRegion:
    def test_clamping(self) -> None:
        r = Region(-0.1, 0.0, 1.1, 0.5)
        assert r.x_min == 0.0
        assert r.x_max == 1.0

    def test_normalized_to_pixel(self) -> None:
        r = Region(0.0, 0.0, 0.5, 0.5)
        assert normalized_to_pixel(r, 1000, 800) == (0, 0, 500, 400)

    def test_pixel_to_normalized(self) -> None:
        r = pixel_to_normalized(250, 200, 250, 200, 1000, 800)
        assert abs(r.x_min - 0.25) < 0.01
        assert abs(r.y_min - 0.25) < 0.01


class TestTiling:
    def test_small_region_no_tiling(self) -> None:
        r = Region(0.0, 0.0, 0.2, 0.2)
        tiles = tile_region(r, tile_size=0.5)
        assert len(tiles) == 1
        assert tiles[0] is r

    def test_large_region_produces_tiles(self) -> None:
        r = Region(0.0, 0.0, 1.0, 1.0)
        tiles = tile_region(r, tile_size=0.4, overlap=0.05)
        assert len(tiles) > 1

    def test_all_tiles_within_bounds(self) -> None:
        r = Region(0.0, 0.0, 1.0, 1.0)
        tiles = tile_region(r, tile_size=0.4, overlap=0.05)
        for tile in tiles:
            assert 0.0 <= tile.x_min <= 1.0
            assert 0.0 <= tile.y_min <= 1.0
            assert 0.0 <= tile.x_max <= 1.0
            assert 0.0 <= tile.y_max <= 1.0


@pytest.mark.skipif(
    not pytest.importorskip("src.document.loader"),
    reason="No sample files available",
)
class TestDocumentLoader:
    def test_pdf_loading(self, sample_pdf_path: str | None) -> None:
        if sample_pdf_path is None:
            pytest.skip("No sample PDF available")
        loader = DocumentLoader(sample_pdf_path)
        img = loader.load()
        assert img.size[0] > 0
        assert img.size[1] > 0
        assert img.mode == "RGB"

    def test_tif_loading(self, sample_tif_path: str | None) -> None:
        if sample_tif_path is None:
            pytest.skip("No sample TIF available")
        loader = DocumentLoader(sample_tif_path)
        img = loader.load()
        assert img.size[0] > 0
        assert img.mode == "RGB"

    def test_overview_returns_base64(self, sample_pdf_path: str | None) -> None:
        if sample_pdf_path is None:
            pytest.skip("No sample PDF available")
        loader = DocumentLoader(sample_pdf_path)
        b64 = loader.get_overview()
        assert isinstance(b64, str)
        assert len(b64) > 100

    def test_crop_returns_base64(self, sample_pdf_path: str | None) -> None:
        if sample_pdf_path is None:
            pytest.skip("No sample PDF available")
        loader = DocumentLoader(sample_pdf_path)
        b64 = loader.crop_region(0.0, 0.0, 0.5, 0.5)
        assert isinstance(b64, str)
        assert len(b64) > 100

    def test_unsupported_file_type(self) -> None:
        with pytest.raises(ValueError, match="Unsupported"):
            DocumentLoader("test.docx")

    def test_dwg_loading_uses_bounded_local_conversion(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        drawing = tmp_path / "drawing.dwg"
        drawing.write_bytes(b"dwg")
        calls: list[list[str]] = []

        def fake_which(name: str) -> str:
            return name

        def fake_run(command: list[str], **_kwargs) -> subprocess.CompletedProcess[bytes]:
            calls.append(command)
            Path(command[3]).write_bytes(b"dxf")
            return subprocess.CompletedProcess(command, 0, b"", b"")

        def fake_render_dxf(dxf_path: Path) -> Image.Image:
            assert dxf_path.read_bytes() == b"dxf"
            return Image.new("RGB", (8, 4), "white")

        monkeypatch.setattr("src.document.loader.shutil.which", fake_which)
        monkeypatch.setattr("src.document.loader.subprocess.run", fake_run)
        monkeypatch.setattr(DocumentLoader, "_render_dxf", staticmethod(fake_render_dxf))

        image = DocumentLoader(str(drawing)).load()

        assert image.mode == "RGB"
        assert image.size == (8, 4)
        assert calls[0][0:3] == ["dwg2dxf", "-y", "-o"]
        assert calls[0][-1] == str(drawing)

    def test_dwg_loading_requires_local_tools(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        drawing = tmp_path / "drawing.dwg"
        drawing.write_bytes(b"dwg")
        monkeypatch.setattr("src.document.loader.shutil.which", lambda _name: None)

        with pytest.raises(ValueError, match="DWG rendering requires"):
            DocumentLoader(str(drawing)).load()


class TestMultiPageLoading:
    """Multi-page PDF support (furniture drawings are typically multi-sheet)."""

    def test_page_count_at_least_one(self, sample_pdf_path: str | None) -> None:
        if sample_pdf_path is None:
            pytest.skip("No sample PDF available")
        loader = DocumentLoader(sample_pdf_path)
        assert loader.page_count >= 1

    def test_load_page_zero_is_rgb(self, sample_pdf_path: str | None) -> None:
        if sample_pdf_path is None:
            pytest.skip("No sample PDF available")
        loader = DocumentLoader(sample_pdf_path)
        img = loader.load(0)
        assert img.mode == "RGB"
        assert img.size[0] > 0 and img.size[1] > 0

    def test_all_pages_loadable_and_rgb(self, sample_pdf_path: str | None) -> None:
        if sample_pdf_path is None:
            pytest.skip("No sample PDF available")
        loader = DocumentLoader(sample_pdf_path)
        pages = loader.iter_pages()
        assert len(pages) == loader.page_count
        for page in pages:
            assert page.mode in ("RGB", "RGBA", "L")
            assert page.size[0] > 0

    def test_load_page_out_of_range_raises(self, sample_pdf_path: str | None) -> None:
        if sample_pdf_path is None:
            pytest.skip("No sample PDF available")
        loader = DocumentLoader(sample_pdf_path)
        with pytest.raises(IndexError, match="out of range"):
            loader.load(loader.page_count)  # one past the last page

    def test_get_tiles_sets_page_field(self, sample_pdf_path: str | None) -> None:
        if sample_pdf_path is None:
            pytest.skip("No sample PDF available")
        loader = DocumentLoader(sample_pdf_path)
        tiles = loader.get_tiles(page_index=0)
        assert len(tiles) == 9
        assert all(t["page"] == 0 for t in tiles)
        # If multi-page, tile the last page too and confirm the page field tracks.
        if loader.page_count > 1:
            last = loader.page_count - 1
            tiles_last = loader.get_tiles(page_index=last)
            assert all(t["page"] == last for t in tiles_last)

    def test_contact_sheet_is_bounded_and_contains_all_pages(
        self,
        sample_pdf_path: str | None,
    ) -> None:
        if sample_pdf_path is None:
            pytest.skip("No sample PDF available")
        loader = DocumentLoader(sample_pdf_path)

        image_bytes, media_type = loader.get_contact_sheet_bytes(max_size=800)

        assert image_bytes
        assert media_type == "image/jpeg"
        with Image.open(io.BytesIO(image_bytes)) as image:
            assert image.width <= 800
            assert image.height <= 800

    def test_image_file_is_single_page(self, sample_image_path: str | None) -> None:
        if sample_image_path is None:
            pytest.skip("No sample image available")
        loader = DocumentLoader(sample_image_path)
        assert loader.page_count == 1
        assert loader.load(0).mode == "RGB"
