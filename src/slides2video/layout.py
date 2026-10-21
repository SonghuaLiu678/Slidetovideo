"""Re-arrange steps: insert, replace and move steps while frames and audio follow.

Frames and mp3 files are named by step number, so any change to the order or
the number of steps has to rename them too. Everything is copied into fresh
directories first and swapped in at the end, so a failure half way leaves the
old files alone.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, List, Optional, Set

from .steps import Project, Step, mp3_name, png_name


@dataclass
class Entry:
    step: Step
    frame: Path                      # picture for this step
    audio: Optional[Path] = None     # existing narration clip, if any


def current_entries(project: Project, frames: Path, audio: Path) -> List[Entry]:
    """One entry per step as it is on disk now."""
    out = []
    for s in project.steps:
        mp3 = Path(audio) / mp3_name(s.step)
        out.append(Entry(s, Path(frames) / png_name(s.step), mp3 if mp3.exists() else None))
    return out


def relayout(project: Project, frames: Path, audio: Path, entries: Iterable[Entry]) -> None:
    """Number ``entries`` 1..n, copy their frames and audio to the new names, and
    make ``project.steps`` match. The step objects in ``entries`` are not modified."""
    frames, audio = Path(frames), Path(audio)
    entries = list(entries)
    new_frames = frames.with_name(frames.name + ".new")
    new_audio = audio.with_name(audio.name + ".new")
    for d in (new_frames, new_audio):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
    steps, any_audio = [], False
    for n, e in enumerate(entries, start=1):
        steps.append(replace(e.step, step=n))
        if not Path(e.frame).exists():
            raise FileNotFoundError(e.frame)
        shutil.copyfile(e.frame, new_frames / png_name(n))
        if e.audio and Path(e.audio).exists():
            shutil.copyfile(e.audio, new_audio / mp3_name(n))
            any_audio = True
    shutil.rmtree(frames, ignore_errors=True)
    new_frames.rename(frames)
    if any_audio or audio.exists():
        shutil.rmtree(audio, ignore_errors=True)
        new_audio.rename(audio)
    else:                              # no audio yet: do not create an empty directory
        shutil.rmtree(new_audio, ignore_errors=True)
    project.steps = steps


def parse_slides(text: str) -> Set[int]:
    """'20', '20-22' or '3,5,20-22' -> {3, 5, 20, 21, 22}."""
    out: Set[int] = set()
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        lo, _, hi = part.partition("-")
        out.update(range(int(lo), int(hi or lo) + 1))
    return out


def move_slides(project: Project, frames: Path, audio: Path, slides: Set[int],
                before: Optional[int] = None, after: Optional[int] = None) -> int:
    """Move every step of the given source slides to just before slide ``before``
    (or just after slide ``after``). Returns the number of steps moved."""
    if (before is None) == (after is None):
        raise ValueError("give exactly one of before= or after=")
    target = before if before is not None else after
    if target in slides:
        raise ValueError(f"slide {target} is one of the slides being moved")
    entries = current_entries(project, frames, audio)
    moving = [e for e in entries if e.step.source_slide in slides]
    staying = [e for e in entries if e.step.source_slide not in slides]
    if not moving:
        raise ValueError(f"no steps come from slides {sorted(slides)}")
    hits = [i for i, e in enumerate(staying) if e.step.source_slide == target]
    if not hits:
        raise ValueError(f"no steps come from slide {target}")
    at = hits[0] if before is not None else hits[-1] + 1
    relayout(project, frames, audio, staying[:at] + moving + staying[at:])
    return len(moving)
