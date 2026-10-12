"""Checks that catch the problems we actually ran into."""
from __future__ import annotations

import csv
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .media import ffmpeg_exe, media_seconds
from .render import page_count
from .steps import Project, mp3_name
from .tts import clip_is_valid

_NUM_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12",
    "fifteen": "15", "twenty": "20", "thirty": "30", "forty": "40", "fifty": "50",
    "hundred": "100",
}


@dataclass
class Issue:
    level: str   # "error" | "warning"
    step: Optional[int]
    message: str

    def __str__(self) -> str:
        where = f"step {self.step}: " if self.step else ""
        return f"[{self.level}] {where}{self.message}"


def _numbers(text: str) -> set:
    # list markers ("1.", "2)") at the start of a line are not quantities
    text = re.sub(r"(?m)^\s*\d+[.)]\s+", "", text)
    nums = set(re.findall(r"\d+(?:\.\d+)?", text))
    for w in re.findall(r"[a-z]+", text.lower()):
        if w in _NUM_WORDS:
            nums.add(_NUM_WORDS[w])
    return nums


def check_narration(project: Project) -> List[Issue]:
    issues: List[Issue] = []
    for s in project.steps:
        if not s.speak.strip():
            issues.append(Issue("error", s.step, "no narration"))
            continue
        spoken = _numbers(s.speak)
        on_slide = _numbers("\n".join(s.text))
        extra = sorted(spoken - on_slide)
        if extra:
            issues.append(Issue("warning", s.step, f"narration has numbers that are not on the slide: {', '.join(extra)}"))
        missing = sorted(_numbers("\n".join(s.new_text)) - spoken)
        if missing:
            issues.append(Issue("warning", s.step, f"new numbers on the slide are not spoken: {', '.join(missing)}"))
    return issues


def check_audio(project: Project, audio: Path) -> List[Issue]:
    issues: List[Issue] = []
    for s in project.steps:
        ok, info = clip_is_valid(Path(audio) / mp3_name(s.step), s.speak)
        if not ok:
            issues.append(Issue("error", s.step, f"bad audio clip: {info}"))
    return issues


def check_pdf(project: Project, pdf: Path) -> List[Issue]:
    n = page_count(pdf)
    if n != len(project.steps):
        return [Issue("error", None, f"PDF has {n} pages but steps.json has {len(project.steps)} steps. "
                                     "Export the split deck, not the original, and keep hidden slides visible.")]
    return []


def _cut_times(video: Path) -> List[float]:
    proc = subprocess.run([ffmpeg_exe(), "-i", str(video), "-vf",
                           "select='gt(scene,0.002)',showinfo", "-an", "-f", "null", "-"],
                          capture_output=True, text=True)
    return [float(x) for x in re.findall(r"pts_time:([0-9.]+)", proc.stderr)]


def check_video(project: Project, video: Path, timeline_csv: Path, tol: float = 0.35) -> List[Issue]:
    issues: List[Issue] = []
    rows = list(csv.DictReader(open(timeline_csv, encoding="utf-8")))
    ends = [float(r["end_s"]) for r in rows]
    total = media_seconds(video)
    if abs(total - ends[-1]) > 0.5:
        issues.append(Issue("error", None, f"video is {total:.1f}s but the timeline ends at {ends[-1]:.1f}s"))
    boundaries = [float(r["start_s"]) for r in rows][1:]
    cuts = [c for c in _cut_times(video) if c > 0.05]
    for c in cuts:
        if min(abs(c - b) for b in boundaries) > tol:
            issues.append(Issue("warning", None, f"picture changes at {c:.2f}s, which is not a step boundary"))
    missing = [i + 2 for i, b in enumerate(boundaries) if not any(abs(c - b) <= tol for c in cuts)]
    if missing:
        issues.append(Issue("warning", None, "no visible picture change at the start of steps "
                            + ", ".join(map(str, missing[:15])) + (" ..." if len(missing) > 15 else "")
                            + " (fine if consecutive frames are identical)"))
    return issues
