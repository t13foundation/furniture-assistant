from __future__ import annotations

import base64
import io
import math
import shutil
import subprocess
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Literal

import pypdfium2  # type: ignore[import-untyped]
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

Image.MAX_IMAGE_PIXELS = 200_000_000

from src.document.regions import Region, TitleBlockCell, normalized_to_pixel

OVERVIEW_MAX_WIDTH = 1500
DETAIL_MAX_PX = 2000
GRID_DIVISIONS = 10

# DWG is converted locally to DXF, then rasterized by ezdxf's Matplotlib
# backend. P3D is a proprietary Palette CAD container and is routed by the
# pipeline to a review result instead of being presented as a drawing image.
SUPPORTED_SUFFIXES = (".pdf", ".tif", ".tiff", ".png", ".jpg", ".jpeg", ".dwg")
DWG_RENDER_MAX_PX = 4000


class DocumentLoader:
    """Loads a drawing file into PIL images and provides overview/tile/crop helpers.

    PDF files are rendered to **all pages** (multi-page aware). Each image-like
    file (TIF/PNG/JPG) is treated as a single page. All public methods accept an
    optional ``page_index`` (0-based) defaulting to ``0`` so existing single-page
    callers keep working unchanged.
    """

    def __init__(
        self,
        path: str,
        *,
        overview_max_width: int = OVERVIEW_MAX_WIDTH,
        detail_max_px: int = DETAIL_MAX_PX,
        grid_divisions: int = GRID_DIVISIONS,
        pdf_render_scale: float = 2.0,
        overview_format: Literal["PNG", "JPEG"] = "JPEG",
        overview_quality: int = 85,
        detail_format: Literal["PNG", "JPEG"] = "PNG",
        detail_quality: int = 90,
    ) -> None:
        self.path = Path(path)
        self.overview_max_width = overview_max_width
        self.detail_max_px = detail_max_px
        self.grid_divisions = grid_divisions
        self.pdf_render_scale = pdf_render_scale
        self.overview_format = overview_format
        self.overview_quality = overview_quality
        self.detail_format = detail_format
        self.detail_quality = detail_quality

        # Lazy, cached list of all rendered pages (RGB).
        self._images: list[Image.Image] | None = None
        # Backward-compat alias for the first page.
        self._image: Image.Image | None = None

        suffix = self.path.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            msg = f"Unsupported file type: {suffix}"
            raise ValueError(msg)

    @property
    def file_type(self) -> str:
        return self.path.suffix.lower()

    @property
    def page_count(self) -> int:
        """Total number of pages (1 for image files; N for multi-page PDFs)."""
        self._ensure_loaded()
        assert self._images is not None
        return len(self._images)

    def _ensure_loaded(self) -> None:
        if self._images is not None:
            return

        suffix = self.path.suffix.lower()
        if suffix == ".pdf":
            pages = self._load_pdf_all_pages()
        elif suffix == ".dwg":
            pages = [self._load_dwg()]
        else:
            pages = [self._load_image()]
        # Normalize to RGB for consistent downstream processing.
        self._images = [p.convert("RGB") if p.mode in ("1", "L", "P", "LA", "PA", "RGBA") else p for p in pages]
        self._image = self._images[0]

    def _load_pdf_all_pages(self) -> list[Image.Image]:
        """Render every page of the PDF to a PIL image."""
        pdf = pypdfium2.PdfDocument(str(self.path))
        try:
            pages: list[Image.Image] = []
            for i in range(len(pdf)):
                bitmap = pdf[i].render(scale=self.pdf_render_scale)
                pages.append(bitmap.to_pil())
            return pages
        finally:
            pdf.close()

    def _load_image(self) -> Image.Image:
        return Image.open(str(self.path))

    def _load_dwg(self) -> Image.Image:
        """Render a DWG through a local DXF conversion."""
        converter = shutil.which("dwg2dxf")
        if converter is None:
            raise ValueError("DWG rendering requires: dwg2dxf")

        try:
            with tempfile.TemporaryDirectory(prefix="furniture-assistant-dwg-") as temp_dir:
                dxf_path = Path(temp_dir) / "drawing.dxf"
                converted = subprocess.run(
                    [converter, "-y", "-o", str(dxf_path), str(self.path)],
                    capture_output=True,
                    check=False,
                    timeout=120,
                )
                if converted.returncode != 0 or not dxf_path.exists():
                    detail = converted.stderr.decode(errors="replace").strip()[-500:]
                    raise ValueError(f"DWG to DXF conversion failed: {detail or converted.returncode}")
                return self._render_dxf(dxf_path)
        except subprocess.TimeoutExpired as exc:
            raise ValueError("DWG rendering timed out") from exc

    @staticmethod
    def _render_dxf(dxf_path: Path) -> Image.Image:
        """Rasterize a DXF modelspace to an image no larger than the DWG limit."""
        import ezdxf
        import matplotlib

        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
        from ezdxf.addons.drawing import Frontend, RenderContext
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

        figure = plt.figure()
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                document = ezdxf.readfile(dxf_path)
                axes = figure.add_axes([0, 0, 1, 1])
                axes.set_axis_off()
                Frontend(RenderContext(document), MatplotlibBackend(axes)).draw_layout(
                    document.modelspace(), finalize=True
                )
            axes.set_facecolor("white")

            x_min, x_max = axes.get_xlim()
            y_min, y_max = axes.get_ylim()
            aspect_ratio = abs((x_max - x_min) / (y_max - y_min))
            if aspect_ratio >= 1:
                width, height = DWG_RENDER_MAX_PX, round(DWG_RENDER_MAX_PX / aspect_ratio)
            else:
                width, height = round(DWG_RENDER_MAX_PX * aspect_ratio), DWG_RENDER_MAX_PX
            figure.set_size_inches(max(width, 1) / 100, max(height, 1) / 100)

            output = io.BytesIO()
            figure.savefig(output, format="png", dpi=100, facecolor="white")
            output.seek(0)
            with Image.open(output) as image:
                return image.convert("RGB")
        finally:
            plt.close(figure)

    # ── Page access ──────────────────────────────────────────────────

    def load(self, page_index: int = 0) -> Image.Image:
        """Return the rendered page at ``page_index`` (0-based)."""
        self._ensure_loaded()
        assert self._images is not None
        if not 0 <= page_index < len(self._images):
            msg = f"Page index {page_index} out of range (0..{len(self._images) - 1})"
            raise IndexError(msg)
        return self._images[page_index]

    def load_page(self, page_index: int) -> Image.Image:
        """Explicit alias for :meth:`load` (clearer at call sites)."""
        return self.load(page_index)

    def iter_pages(self) -> list[Image.Image]:
        """Return all pages as a list (loads them if not already loaded)."""
        self._ensure_loaded()
        assert self._images is not None
        return list(self._images)

    # ── Overview / tiles / crops (all page-aware) ────────────────────

    def get_overview(self, annotate_grid: bool = True, page_index: int = 0) -> str:
        img = self.load(page_index)
        if annotate_grid:
            img = self._add_grid_overlay(img, self.grid_divisions)
        return self._to_base64(img, self.overview_max_width, self.overview_format, self.overview_quality)

    def get_overview_bytes(
        self,
        annotate_grid: bool = True,
        page_index: int = 0,
        max_width: int | None = None,
    ) -> tuple[bytes, str]:
        """Return (image_bytes, media_type) for native vision encoding."""
        img = self.load(page_index)
        if annotate_grid:
            img = self._add_grid_overlay(img, self.grid_divisions)
        return self._image_to_resized_bytes(
            img,
            max_width or self.overview_max_width,
            self.overview_format,
            self.overview_quality,
        )

    def get_contact_sheet_bytes(
        self,
        max_size: int = 2000,
    ) -> tuple[bytes, str]:
        """Return a bounded thumbnail sheet containing every zero-based page."""
        pages = self.iter_pages()
        columns = math.ceil(math.sqrt(len(pages)))
        rows = math.ceil(len(pages) / columns)
        cell_width = max_size // columns
        cell_height = max_size // rows
        label_height = min(28, max(16, cell_height // 8))
        contact_sheet = Image.new(
            "RGB",
            (cell_width * columns, cell_height * rows),
            "white",
        )
        draw = ImageDraw.Draw(contact_sheet)

        for page_index, page in enumerate(pages):
            thumbnail = page.copy()
            thumbnail.thumbnail(
                (max(1, cell_width - 8), max(1, cell_height - label_height - 8)),
                Image.Resampling.LANCZOS,
            )
            cell_x = (page_index % columns) * cell_width
            cell_y = (page_index // columns) * cell_height
            image_x = cell_x + (cell_width - thumbnail.width) // 2
            image_y = cell_y + label_height + (cell_height - label_height - thumbnail.height) // 2
            contact_sheet.paste(thumbnail, (image_x, image_y))
            draw.rectangle(
                (cell_x, cell_y, cell_x + cell_width - 1, cell_y + label_height),
                fill="black",
            )
            draw.text((cell_x + 4, cell_y + 3), f"page_index={page_index}", fill="white")

        return self._image_to_bytes(contact_sheet, "JPEG", self.overview_quality)

    def get_tiles(self, grid_size: int = 3, overlap: float = 0.1, page_index: int = 0) -> list[dict]:
        """Divide the page into a grid of tiles with overlap.

        Args:
            grid_size: Number of rows/columns (3 = 3x3 = 9 tiles).
            overlap: Fraction of tile size to extend on each side (0.1 = 10%).
            page_index: Which page to tile (0-based).

        Returns a list of dicts with:
        - 'row', 'col': grid position
        - 'x_min', 'y_min', 'x_max', 'y_max': normalized coords (with overlap)
        - 'label': human-readable position (e.g. 'top-left')
        - 'image_bytes': JPEG bytes
        - 'media_type': MIME type
        - 'page': page index this tile came from
        """
        img = self.load(page_index)
        tiles = []
        position_labels = {
            (0, 0): "top-left",
            (0, 1): "top-center",
            (0, 2): "top-right",
            (1, 0): "middle-left",
            (1, 1): "center",
            (1, 2): "middle-right",
            (2, 0): "bottom-left",
            (2, 1): "bottom-center",
            (2, 2): "bottom-right",
        }
        for row in range(grid_size):
            for col in range(grid_size):
                x_min = max(0.0, col / grid_size - overlap)
                y_min = max(0.0, row / grid_size - overlap)
                x_max = min(1.0, (col + 1) / grid_size + overlap)
                y_max = min(1.0, (row + 1) / grid_size + overlap)

                region = Region(x_min, y_min, x_max, y_max)
                px1, py1, px2, py2 = normalized_to_pixel(region, img.width, img.height)
                cropped = img.crop((px1, py1, px2, py2))

                max_dim = max(cropped.width, cropped.height)
                if max_dim > self.detail_max_px:
                    scale = self.detail_max_px / max_dim
                    cropped = cropped.resize(
                        (int(cropped.width * scale), int(cropped.height * scale)),
                        Image.Resampling.LANCZOS,
                    )

                img_bytes, media_type = self._image_to_bytes(
                    cropped,
                    self.detail_format,
                    self.detail_quality,
                )
                label = position_labels.get((row, col), f"tile-{row}-{col}")
                tiles.append(
                    {
                        "row": row,
                        "col": col,
                        "x_min": x_min,
                        "y_min": y_min,
                        "x_max": x_max,
                        "y_max": y_max,
                        "label": label,
                        "image_bytes": img_bytes,
                        "media_type": media_type,
                        "page": page_index,
                    }
                )
        return tiles

    def crop_title_block_cells(
        self,
        region: Region,
        fallback_cols: int = 4,
        fallback_rows: int = 5,
        max_px: int | None = None,
        page_index: int = 0,
    ) -> list[TitleBlockCell]:
        """Extract individual cells from a title block region on a given page.

        Detects grid lines via pixel projection analysis. Falls back to a uniform
        grid if auto-detection fails. Each cell is cropped at high resolution.

        Returns list of TitleBlockCell with image bytes for vision analysis.
        """
        import numpy as np

        max_px = max_px or 4000  # Higher res for title block cells
        img = self.load(page_index)
        px1, py1, px2, py2 = normalized_to_pixel(region, img.width, img.height)
        tb_crop = img.crop((px1, py1, px2, py2))

        # Detect grid lines via pixel projection
        gray = tb_crop.convert("L")
        arr = np.array(gray)

        # Horizontal projection: sum dark pixels per row
        # (inverted: dark text/lines have low values)
        h_proj = (255 - arr).sum(axis=1)
        v_proj = (255 - arr).sum(axis=0)

        # Detect horizontal line positions (peaks in horizontal projection)
        h_threshold = h_proj.mean() + h_proj.std() * 1.5
        h_lines = self._detect_line_positions(h_proj, h_threshold, min_gap=10)

        # Detect vertical line positions
        v_threshold = v_proj.mean() + v_proj.std() * 1.5
        v_lines = self._detect_line_positions(v_proj, v_threshold, min_gap=10)

        # Determine grid dimensions
        if len(h_lines) >= 2 and len(v_lines) >= 2:
            row_bounds = [0, *h_lines, tb_crop.height]
            col_bounds = [0, *v_lines, tb_crop.width]
        else:
            # Fallback to uniform grid
            row_bounds = [int(tb_crop.height * i / fallback_rows) for i in range(fallback_rows + 1)]
            col_bounds = [int(tb_crop.width * i / fallback_cols) for i in range(fallback_cols + 1)]

        cells: list[TitleBlockCell] = []
        for row_idx in range(len(row_bounds) - 1):
            for col_idx in range(len(col_bounds) - 1):
                ry1, ry2 = row_bounds[row_idx], row_bounds[row_idx + 1]
                cx1, cx2 = col_bounds[col_idx], col_bounds[col_idx + 1]

                if ry2 - ry1 < 5 or cx2 - cx1 < 5:
                    continue

                cell_img = tb_crop.crop((cx1, ry1, cx2, ry2))
                cell_img = self.enhance_for_ocr(cell_img)

                # Upscale if cell is too small
                max_dim = max(cell_img.width, cell_img.height)
                if max_dim < 200:
                    scale = 200 / max_dim
                    cell_img = cell_img.resize(
                        (int(cell_img.width * scale), int(cell_img.height * scale)),
                        Image.Resampling.LANCZOS,
                    )
                elif max_dim > max_px:
                    scale = max_px / max_dim
                    cell_img = cell_img.resize(
                        (int(cell_img.width * scale), int(cell_img.height * scale)),
                        Image.Resampling.LANCZOS,
                    )

                cell_bytes, media_type = self._image_to_bytes(cell_img, "PNG", 95)

                # Compute normalized coordinates relative to full drawing
                cell_x_min = (px1 + cx1) / img.width
                cell_y_min = (py1 + ry1) / img.height
                cell_x_max = (px1 + cx2) / img.width
                cell_y_max = (py1 + ry2) / img.height

                cells.append(
                    TitleBlockCell(
                        row=row_idx,
                        col=col_idx,
                        x_min=cell_x_min,
                        y_min=cell_y_min,
                        x_max=cell_x_max,
                        y_max=cell_y_max,
                        label=f"cell_{row_idx}_{col_idx}",
                        image_bytes=cell_bytes,
                        media_type=media_type,
                    )
                )

        return cells

    @staticmethod
    def _detect_line_positions(
        projection: np.ndarray,  # type: ignore[name-defined]
        threshold: float,
        min_gap: int = 10,
    ) -> list[int]:
        """Detect line positions from a pixel projection array.

        Returns sorted list of positions where projection exceeds threshold,
        with at least min_gap pixels between consecutive positions.
        """
        positions: list[int] = []
        in_peak = False
        peak_start = 0

        for i, val in enumerate(projection):
            if val > threshold:
                if not in_peak:
                    in_peak = True
                    peak_start = i
            else:
                if in_peak:
                    # Record the center of this peak
                    center = (peak_start + i) // 2
                    if not positions or center - positions[-1] >= min_gap:
                        positions.append(center)
                    in_peak = False

        # Handle peak at end of array
        if in_peak:
            center = (peak_start + len(projection)) // 2
            if not positions or center - positions[-1] >= min_gap:
                positions.append(center)

        return positions

    def crop_region(
        self,
        x_min: float,
        y_min: float,
        x_max: float,
        y_max: float,
        max_px: int | None = None,
        page_index: int = 0,
    ) -> str:
        max_px = max_px or self.detail_max_px
        img = self.load(page_index)
        region = Region(x_min, y_min, x_max, y_max)
        px1, py1, px2, py2 = normalized_to_pixel(region, img.width, img.height)
        cropped = img.crop((px1, py1, px2, py2))

        max_dim = max(cropped.width, cropped.height)
        if max_dim > max_px:
            scale = max_px / max_dim
            cropped = cropped.resize(
                (int(cropped.width * scale), int(cropped.height * scale)),
                Image.Resampling.LANCZOS,
            )

        return self._image_to_base64(cropped, self.detail_format, self.detail_quality)

    def crop_region_bytes(
        self,
        x_min: float,
        y_min: float,
        x_max: float,
        y_max: float,
        max_px: int | None = None,
        enhance: bool = False,
        page_index: int = 0,
    ) -> tuple[bytes, str]:
        """Return (image_bytes, media_type) for native vision encoding."""
        max_px = max_px or self.detail_max_px
        img = self.load(page_index)
        region = Region(x_min, y_min, x_max, y_max)
        px1, py1, px2, py2 = normalized_to_pixel(region, img.width, img.height)
        cropped = img.crop((px1, py1, px2, py2))

        if enhance:
            cropped = self.enhance_for_ocr(cropped)

        max_dim = max(cropped.width, cropped.height)
        if max_dim > max_px:
            scale = max_px / max_dim
            cropped = cropped.resize(
                (int(cropped.width * scale), int(cropped.height * scale)),
                Image.Resampling.LANCZOS,
            )

        return self._image_to_bytes(cropped, self.detail_format, self.detail_quality)

    @staticmethod
    def enhance_for_ocr(img: Image.Image) -> Image.Image:
        """Enhance image for better OCR/text reading by vision models.

        Applies contrast enhancement and sharpening.
        """
        # Increase contrast
        img = ImageEnhance.Contrast(img).enhance(1.5)
        # Sharpen edges for clearer text
        img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
        return img

    def _to_base64(
        self,
        img: Image.Image,
        max_width: int = OVERVIEW_MAX_WIDTH,
        fmt: str = "JPEG",
        quality: int = 85,
    ) -> str:
        if img.width > max_width:
            scale = max_width / img.width
            img = img.resize(
                (max_width, int(img.height * scale)),
                Image.Resampling.LANCZOS,
            )
        return self._image_to_base64(img, fmt, quality)

    def _image_to_resized_bytes(
        self,
        img: Image.Image,
        max_width: int,
        fmt: str,
        quality: int,
    ) -> tuple[bytes, str]:
        if img.width > max_width:
            scale = max_width / img.width
            img = img.resize(
                (max_width, int(img.height * scale)),
                Image.Resampling.LANCZOS,
            )
        return self._image_to_bytes(img, fmt, quality)

    @staticmethod
    def _image_to_bytes(
        img: Image.Image,
        fmt: str = "PNG",
        quality: int = 85,
    ) -> tuple[bytes, str]:
        buf = io.BytesIO()
        media_type = "image/png"
        if fmt.upper() == "JPEG":
            if img.mode in ("RGBA", "P", "LA", "PA"):
                img = img.convert("RGB")
            img.save(buf, format="JPEG", quality=quality)
            media_type = "image/jpeg"
        else:
            img.save(buf, format="PNG")
        return buf.getvalue(), media_type

    @staticmethod
    def _image_to_base64(
        img: Image.Image,
        fmt: str = "PNG",
        quality: int = 85,
    ) -> str:
        data, _ = DocumentLoader._image_to_bytes(img, fmt, quality)
        return base64.b64encode(data).decode("utf-8")

    def _add_grid_overlay(self, img: Image.Image, divisions: int = GRID_DIVISIONS) -> Image.Image:
        img = img.copy()
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype("arial.ttf", size=12)
        except OSError:
            font = ImageFont.load_default(size=14)

        w, h = img.size
        for i in range(1, divisions):
            x = int(w * i / divisions)
            draw.line([(x, 0), (x, h)], fill="gray", width=1)
            label = f"{i / divisions:.1f}"
            draw.text((x + 2, 2), label, fill="blue", font=font)

        for i in range(1, divisions):
            y = int(h * i / divisions)
            draw.line([(0, y), (w, y)], fill="gray", width=1)
            label = f"{i / divisions:.1f}"
            draw.text((2, y + 2), label, fill="blue", font=font)

        return img
