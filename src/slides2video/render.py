"""PDF -> PNG frames.

The PDF has to come from PowerPoint's own "Export as PDF". Its PNG export is
72 dpi (720 px wide for a 10 in slide); upscaling that to a video frame makes
small text, especially in screenshots, look soft. Rendering the PDF at video
width keeps text and embedded images sharp.
"""
from __future__ import annotations

from pathlib import Path

import pymupdf

from .steps import png_name


def page_count(pdf: Path) -> int:
    with pymupdf.open(str(pdf)) as doc:
        return len(doc)


def render_pdf(pdf: Path, out_dir: Path, width: int = 1440) -> int:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(str(pdf)) as doc:
        for i, page in enumerate(doc, start=1):
            zoom = width / page.rect.width
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            pix.save(str(out_dir / png_name(i)))
        return len(doc)
