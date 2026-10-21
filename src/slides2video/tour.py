"""Walk through a screenshot: one frame per region, marked in red, one sentence each.

A slide that shows an interface is hard to follow if the voice talks about it
in one go. A plan file lists the regions of that slide in the order they are
explained. Each region becomes its own step: the frame is the slide with that
region boxed (earlier regions stay marked, more thinly), and the narration is
the region's own text.

Plan file (frame pixels, origin at the top left):

    [
      {"slide": 22,
       "regions": [
         {"label": "General Info", "box": [297, 150, 1138, 298],
          "speak": "At the top is the general information."},
         {"label": "Make Decision", "box": [297, 486, 1138, 967], "speak": "Here you decide.",
          "hold": 1.5}
       ]}
    ]

Region keys: ``label`` and ``speak`` (required); ``box`` = [x0, y0, x1, y1];
``style`` = "bracket" (default: a bracket and the label in the left margin, so
the picture needs about 150 px of free space to the left of ``x0``) or "box"
(outline with a small label badge, for pictures that touch the frame edge);
``mask`` = rectangles [x0, y0, x1, y1] painted white first, to hide part of the
slide (for example the second of two screenshots until it is explained);
``group`` = marks from earlier regions are only drawn when their group matches
(use it when a mask makes earlier marks refer to something no longer visible);
``hold`` = pause after this step in seconds.
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Dict, List

from PIL import Image, ImageDraw, ImageFont

from .layout import Entry, current_entries, relayout
from .steps import Project, Step, png_name

RED = (200, 16, 30)
TICK = 12
_FONTS = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf",
]
TOUR_NOTE = "tour:"


def _font(size: int):
    for name in _FONTS:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _label(d, text: str, x_right: float, y_mid: float, size: int) -> None:
    f = _font(size)
    l, t, r, b = d.textbbox((0, 0), text, font=f)
    d.text((x_right - (r - l), y_mid - (b - t) / 2 - t), text, font=f, fill=RED)


def _badge(d, text: str, x_right: float, y_top: float, size: int) -> None:
    f = _font(size)
    l, t, r, b = d.textbbox((0, 0), text, font=f)
    pad = 6
    x0, y0 = x_right - (r - l) - 2 * pad, y_top - ((b - t) + 2 * pad) // 2
    d.rectangle([x0, y0, x_right, y0 + (b - t) + 2 * pad], fill=RED)
    d.text((x0 + pad - l, y0 + pad - t), text, font=f, fill=(255, 255, 255))


def _mark(d, region: dict, current: bool) -> None:
    x0, y0, x1, y1 = region["box"]
    line, size = (5, 23) if current else (2, 19)
    if region.get("style", "bracket") == "bracket":
        bx = x0 - 6
        if current:
            d.rectangle([bx + TICK - 6, y0 - 4, x1 + 4, y1 + 4], outline=RED, width=4)
        d.line([(bx + TICK, y0), (bx, y0), (bx, y1), (bx + TICK, y1)], fill=RED, width=line)
        _label(d, region["label"], bx - 10, (y0 + y1) / 2, size)
    else:
        d.rectangle([x0 - 4, y0 - 4, x1 + 4, y1 + 4], outline=RED, width=4 if current else 2)
        _badge(d, region["label"], x1 - 6, y0 - 4, 19 if current else 17)


def render_overlays(base: Path, regions: List[dict], out_dir: Path) -> List[Path]:
    """One PNG per region: the base frame with regions 1..i marked."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, cur in enumerate(regions):
        im = Image.open(base).convert("RGB")
        d = ImageDraw.Draw(im)
        for rect in cur.get("mask", []):
            d.rectangle(rect, fill=(255, 255, 255))
        for prev in regions[:i]:
            if prev.get("group") == cur.get("group"):
                _mark(d, prev, False)
        _mark(d, cur, True)
        p = out_dir / png_name(i + 1)
        im.save(p)
        paths.append(p)
    return paths


def load_plan(path: Path) -> List[dict]:
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(plan, dict):
        plan = plan.get("slides", [])
    for item in plan:
        if "slide" not in item or not item.get("regions"):
            raise ValueError(f"each plan entry needs 'slide' and a non-empty 'regions' list: {item}")
        for r in item["regions"]:
            for key in ("label", "box", "speak"):
                if key not in r:
                    raise ValueError(f"slide {item['slide']}: region is missing '{key}': {r}")
            if len(r["box"]) != 4:
                raise ValueError(f"slide {item['slide']}: box must be [x0, y0, x1, y1]: {r}")
    return plan


def annotate(project: Project, frames: Path, audio: Path, plan: List[dict]) -> Dict[int, int]:
    """Replace the step of each planned slide by one step per region.
    Returns {slide: number of steps it became}. Slides that already have tour
    steps are left alone, so running the same plan twice does nothing."""
    entries = current_entries(project, frames, audio)
    by_slide = {int(item["slide"]): item for item in plan}
    done: Dict[int, int] = {}
    with tempfile.TemporaryDirectory(prefix="s2v_tour_") as tmp:
        new: List[Entry] = []
        for e in entries:
            item = by_slide.get(e.step.source_slide)
            if item is None or e.step.note.startswith(TOUR_NOTE):   # not planned, or already a tour
                new.append(e)
                continue
            same = [x for x in entries if x.step.source_slide == e.step.source_slide]
            if len(same) != 1:
                raise ValueError(f"slide {e.step.source_slide} has {len(same)} steps; a tour needs exactly one "
                                 "(use `split --keep` for this slide, or edit steps.json first)")
            overlays = render_overlays(e.frame, item["regions"], Path(tmp) / f"s{e.step.source_slide}")
            for r, png in zip(item["regions"], overlays):
                step = replace(e.step, new_text=[r["label"]], has_screen=True, speak=r["speak"],
                               hold=r.get("hold"), note=f"{TOUR_NOTE} {r['label']}")
                new.append(Entry(step, png, None))
            done[e.step.source_slide] = len(overlays)
        if done:
            relayout(project, frames, audio, new)
    return done
