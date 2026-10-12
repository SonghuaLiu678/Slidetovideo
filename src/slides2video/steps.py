"""The steps file: one entry per narrated step, the single source of truth.

Everything downstream (audio, video, captions, checks) reads steps.json, so
edits to the narration are made there and nowhere else.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

SCHEMA_VERSION = 1


@dataclass
class Step:
    step: int                      # 1-based position in the final video
    source_slide: int              # 1-based slide in the original deck
    title: str = ""
    text: List[str] = field(default_factory=list)      # everything visible at this step
    new_text: List[str] = field(default_factory=list)  # what appears for the first time
    has_screen: bool = False       # slide shows a picture, e.g. an interface screenshot
    speak: str = ""                # what the voice reads
    hold: Optional[float] = None   # seconds of silence after the voice; None = default
    note: str = ""                 # free text for the human reviewer


@dataclass
class Project:
    deck: str
    voice: str = "en-US-AndrewMultilingualNeural"
    steps: List[Step] = field(default_factory=list)
    version: int = SCHEMA_VERSION

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Project":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        steps = [Step(**s) for s in data.get("steps", [])]
        return cls(deck=data.get("deck", ""), voice=data.get("voice", cls.voice), steps=steps,
                   version=data.get("version", SCHEMA_VERSION))

    def by_number(self, n: int) -> Step:
        for s in self.steps:
            if s.step == n:
                return s
        raise KeyError(f"no step {n}")


def mp3_name(n: int) -> str:
    return f"{n:03d}.mp3"


def png_name(n: int) -> str:
    return f"{n:03d}.png"
