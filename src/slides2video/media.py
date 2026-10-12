"""Small ffmpeg helpers. Uses the system ffmpeg if present, else the static
binary that ships with imageio-ffmpeg, so nothing has to be installed by hand."""
from __future__ import annotations

import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def ffmpeg_exe() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def run_ffmpeg(args: list, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", *args],
                          capture_output=True, text=True, check=check)


def media_seconds(path: Path) -> float:
    """Duration from the container header. Accurate for the CBR mp3 files and
    the mp4 clips this tool writes."""
    proc = subprocess.run([ffmpeg_exe(), "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", proc.stderr)
    if not m:
        raise ValueError(f"cannot read duration of {path}")
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)
