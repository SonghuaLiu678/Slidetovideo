"""Assemble the video: one still frame + one audio clip per step.

Each clip is cut to an explicit length, audio + hold. Letting ffmpeg decide
with ``-shortest`` on a looped still image can leave several seconds of
silent tail after a long clip, which shows up as the slide lagging the voice.
Clips share codecs and frame rate, so the final join is a stream copy.
"""
from __future__ import annotations

import csv
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import List

from .media import media_seconds, run_ffmpeg
from .steps import Project, mp3_name, png_name

FPS = 10
AUDIO_RATE = 44100


@dataclass
class Timing:
    step: int
    start: float
    end: float


def step_hold(step, hold: float, screen_hold: float) -> float:
    if step.hold is not None:
        return step.hold
    return screen_hold if step.has_screen else hold


def _png_size(png: Path) -> tuple:
    """PNG width/height without pillow."""
    import struct

    with open(png, "rb") as f:
        head = f.read(24)
    w, h = struct.unpack(">II", head[16:24])
    return w - (w % 2), h - (h % 2)


def build_clip(png: Path, mp3: Path, seconds: float, hold: float, size: tuple, out: Path,
               speed: float = 1.0) -> None:
    """One still frame + one audio clip, cut to (audio + hold) / speed.
    The speed-up is applied to each clip on its own, not to the finished video:
    speeding up the joined video lets picture and voice drift apart."""
    w, h = size
    tempo = f"atempo={speed:.4f}," if speed != 1.0 else ""
    run_ffmpeg([
        "-loop", "1", "-framerate", str(FPS), "-i", str(png), "-i", str(mp3),
        "-vf", f"scale={w}:{h}:flags=lanczos,format=yuv420p",
        "-af", f"{tempo}apad=pad_dur={hold / speed:.3f},aresample={AUDIO_RATE}",
        "-ac", "1", "-c:v", "libx264", "-tune", "stillimage", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "96k", "-t", f"{(seconds + hold) / speed:.3f}", str(out),
    ])


def assemble(project: Project, frames: Path, audio: Path, out: Path, hold: float = 0.6,
             screen_hold: float = 3.0, jobs: int = 3, speed: float = 1.0) -> List[Timing]:
    if not 0.5 <= speed <= 2.0:
        raise ValueError("speed must be between 0.5 and 2.0 (the range of ffmpeg's atempo filter)")
    frames, audio, out = Path(frames), Path(audio), Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    first = frames / png_name(project.steps[0].step)
    size = _png_size(first)
    with tempfile.TemporaryDirectory(prefix="s2v_") as tmpname:
        tmp = Path(tmpname)
        clips = []

        def work(step):
            png = frames / png_name(step.step)
            mp3 = audio / mp3_name(step.step)
            if not png.exists():
                raise FileNotFoundError(png)
            if not mp3.exists():
                raise FileNotFoundError(mp3)
            clip = tmp / f"{step.step:03d}.mp4"
            build_clip(png, mp3, media_seconds(mp3), step_hold(step, hold, screen_hold), size, clip, speed)
            return step.step, clip

        with ThreadPoolExecutor(max_workers=jobs) as pool:
            for n, clip in pool.map(work, project.steps):
                clips.append((n, clip))
        clips.sort()
        listing = tmp / "list.txt"
        listing.write_text("".join(f"file '{c.as_posix()}'\n" for _, c in clips), encoding="utf-8")
        run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(out)])
        # measured, not computed: each clip is rounded to a whole frame
        timings, t = [], 0.0
        for n, clip in clips:
            d = media_seconds(clip)
            timings.append(Timing(n, t, t + d))
            t += d
    return timings


def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_timeline(project: Project, timings: List[Timing], csv_path: Path, srt_path: Path,
                   audio: Path, speed: float = 1.0) -> None:
    by = {t.step: t for t in timings}
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["step", "source_slide", "start_s", "end_s", "title"])
        for s in project.steps:
            t = by[s.step]
            w.writerow([s.step, s.source_slide, f"{t.start:.2f}", f"{t.end:.2f}", s.title])
    lines = []
    for i, s in enumerate(project.steps, start=1):
        t = by[s.step]
        speech_end = t.start + media_seconds(Path(audio) / mp3_name(s.step)) / speed
        lines += [str(i), f"{_srt_time(t.start)} --> {_srt_time(min(speech_end, t.end))}", s.speak, ""]
    Path(srt_path).write_text("\n".join(lines), encoding="utf-8")
