"""Speech synthesis with edge-tts, plus the checks that keep bad clips out.

edge-tts talks to the same service Microsoft Edge uses for "Read aloud". It is
not an official API: it can change or rate-limit without notice. Its worst
habit is returning a "successful" but almost empty clip (a third of a second
for a 14-word sentence). Every clip is therefore measured and retried.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from .media import media_seconds
from .steps import Project, Step, mp3_name

DEFAULT_VOICE = "en-US-AndrewMultilingualNeural"
MIN_SECONDS_PER_WORD = 0.15   # real speech runs 0.25-0.5 s/word; empty clips are far below
MIN_CLIP_SECONDS = 0.3


def word_count(text: str) -> int:
    return len(text.split())


def clip_is_valid(path: Path, text: str) -> Tuple[bool, str]:
    if not path.exists() or path.stat().st_size == 0:
        return False, "missing or empty file"
    try:
        sec = media_seconds(path)
    except ValueError as exc:
        return False, str(exc)
    need = max(MIN_CLIP_SECONDS, word_count(text) * MIN_SECONDS_PER_WORD)
    if sec < need:
        return False, f"{sec:.2f}s for {word_count(text)} words (expected at least {need:.2f}s)"
    return True, f"{sec:.2f}s"


async def _one(step: Step, voice: str, rate: str, out_dir: Path, sem: asyncio.Semaphore,
               retries: int) -> Tuple[int, str]:
    import edge_tts

    path = out_dir / mp3_name(step.step)
    last = "no attempt"
    async with sem:
        for _ in range(retries):
            try:
                await edge_tts.Communicate(step.speak, voice, rate=rate).save(str(path))
                ok, last = clip_is_valid(path, step.speak)
                if ok:
                    return step.step, "ok"
            except Exception as exc:  # network hiccups, throttling
                last = repr(exc)
            await asyncio.sleep(2)
    return step.step, f"FAILED: {last}"


async def _run(steps: Sequence[Step], voice: str, rate: str, out_dir: Path, concurrency: int,
               retries: int) -> List[Tuple[int, str]]:
    sem = asyncio.Semaphore(concurrency)
    return await asyncio.gather(*(_one(s, voice, rate, out_dir, sem, retries) for s in steps))


def synthesize(project: Project, out_dir: Path, voice: Optional[str] = None, rate: str = "+0%",
               only: Iterable[int] = (), force: bool = False, concurrency: int = 2,
               retries: int = 6) -> List[Tuple[int, str]]:
    """Generate one mp3 per step. Valid clips that already exist are kept
    unless ``force`` or the step is listed in ``only``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    voice = voice or project.voice or DEFAULT_VOICE
    only_set = set(only)
    todo: List[Step] = []
    for s in project.steps:
        if not s.speak.strip():
            raise ValueError(f"step {s.step} has no narration; run `slides2video script` first")
        if only_set and s.step not in only_set:
            continue
        ok, _ = clip_is_valid(out_dir / mp3_name(s.step), s.speak)
        if force or only_set or not ok:
            todo.append(s)
    if not todo:
        return []
    results = asyncio.run(_run(todo, voice, rate, out_dir, concurrency, retries))
    return sorted(results)


def list_voices(locale: str = "en-") -> List[dict]:
    import edge_tts

    voices = asyncio.run(edge_tts.list_voices())
    return [v for v in voices if v["Locale"].startswith(locale) or v["ShortName"].startswith(locale)]


def audition(text: str, voices: Sequence[str], out_dir: Path) -> List[Path]:
    """Render the same text in several voices so a reviewer can choose."""
    import edge_tts

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []

    async def go():
        for v in voices:
            p = out_dir / f"{v}.mp3"
            await edge_tts.Communicate(text, v).save(str(p))
            paths.append(p)

    asyncio.run(go())
    return paths


def print_results(results: List[Tuple[int, str]], stream=sys.stdout) -> int:
    failed = [r for r in results if not r[1].startswith("ok")]
    print(f"audio: {len(results) - len(failed)} generated, {len(failed)} failed", file=stream)
    for n, msg in failed:
        print(f"  step {n}: {msg}", file=stream)
    return len(failed)
