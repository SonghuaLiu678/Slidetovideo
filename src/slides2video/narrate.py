"""Narration: turn what is on each step into something a person would say.

Two modes:
  * ``verbatim``  reads the new on-slide text, cleaned up for speech. Free,
    offline, flat. Good as a first draft or when the slides are already prose.
  * ``anthropic`` asks Claude to write connected narration, and to describe
    interface screenshots when the slide is a picture. Needs ANTHROPIC_API_KEY.

Both fill ``Step.speak``. The result is meant to be read and edited by a
person in steps.json before any audio is generated.
"""
from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Callable, List, Optional

from .steps import Project, Step, png_name

DEFAULT_MODEL = "claude-sonnet-5-5"

SYSTEM_PROMPT = """You write the spoken narration for a slide deck that will be turned into a video.
A text-to-speech voice reads your words aloud, so write for the ear.

Rules:
- Cover everything that is new on this step, and nothing that is not on the slides.
  Do not invent numbers, rules, names or reasons.
- Plain spoken English. Short sentences. Contractions are fine. No bullets, no markdown,
  no parentheses, no symbols a voice would stumble on. Write "30 percent", "minus 20",
  "plus 80", "5 or 6".
- Connect the steps. Use light bridges ("Next,", "Now the receiver.", "Here is an example.")
  where they help, but do not repeat what earlier steps already said, and do not
  announce the slide ("On this slide...").
- When a step shows an interface screenshot, say what the screen is for and what the
  person does on it, using only what is visible. Mention left or right only if it is
  clearly visible. Keep it to two or three sentences.
- Avoid filler such as "as you can see", "let's dive in", "it is important to note".
- The first step may greet briefly. The last step may close in one sentence.
Reply with JSON only: {"speak": "<narration for this step>"}"""

_SYMBOLS = [
    (r"[\ue000-\uf8ff]", ", "),            # private-use glyphs, e.g. Wingdings arrows
    (r"\s*\|\s*", ", "),                  # table cell separator
    (r"\?{2,}", ""),
    (r"\s*\(([^)]*)\)", r", \1"),
    (r"\s=\s*,", ","),                     # "x = <glyph>" left an empty right-hand side
    (r"\s*%", " percent"),
    (r"(?<![\w])\+\s?(\d)", r"plus \1"),
    (r"(?<![\w])[−–-]\s?(\d)", r"minus \1"),
    (r"\s=\s", " equals "),
    (r"\s*[→➔➜]\s*", ", "),
    (r"\s&\s", " and "),
    (r"^\s*[-•·*]\s+", ""),
    (r"^\s*\d+[.)]\s+", ""),
    (r"\s+,", ","),
]


def verbatim_speak(step: Step) -> str:
    parts: List[str] = []
    for line in step.new_text or step.text:
        s = line.strip()
        for pat, rep in _SYMBOLS:
            s = re.sub(pat, rep, s)
        s = re.sub(r"\s+", " ", s).strip()
        if not s:
            continue
        if s[-1] not in ".!?:;":
            s += "."
        parts.append(s)
    return " ".join(parts)


def _parse_reply(text: str) -> str:
    text = text.strip()
    m = re.search(r"\{.*\}", text, flags=re.S)
    if m:
        try:
            return str(json.loads(m.group(0))["speak"]).strip()
        except (ValueError, KeyError):
            pass
    return text.strip().strip('"')


def _make_client():
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("Install the extra first: pip install 'slides2video[llm]'") from exc
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is not set. Set it, or use --llm verbatim.")
    return anthropic.Anthropic()


def _user_prompt(project: Project, step: Step, history: List[str]) -> str:
    n = len(project.steps)
    outline = "\n".join(f"{s.step}. {s.title}" for s in project.steps if s.step == 1 or
                        project.steps[s.step - 2].title != s.title)
    return (
        f"Deck outline (first step of each slide):\n{outline}\n\n"
        f"Previous narration (most recent last):\n" + ("\n".join(f"- {h}" for h in history[-4:]) or "(none yet)") + "\n\n"
        f"Now write step {step.step} of {n}.\n"
        f"Slide title: {step.title}\n"
        f"Visible on the slide now:\n" + "\n".join(f"  {t}" for t in step.text) + "\n"
        f"New in this step:\n" + "\n".join(f"  {t}" for t in step.new_text) + "\n"
        f"Shows an interface screenshot: {'yes, see the attached image' if step.has_screen else 'no'}\n"
        f"First step: {step.step == 1}. Last step: {step.step == n}."
    )


def generate(project: Project, mode: str = "verbatim", frames: Optional[Path] = None,
             model: Optional[str] = None, only: Optional[set] = None, overwrite: bool = False,
             client=None, progress: Callable[[str], None] = lambda s: None) -> int:
    """Fill ``speak`` for steps that have none (or all, with ``overwrite``)."""
    if mode not in ("verbatim", "anthropic"):
        raise ValueError(f"unknown mode {mode!r}")
    if mode == "anthropic" and client is None:
        client = _make_client()
    model = model or os.environ.get("S2V_MODEL", DEFAULT_MODEL)
    history: List[str] = []
    done = 0
    for step in project.steps:
        wanted = (only is None or step.step in only) and (overwrite or not step.speak.strip())
        if not wanted:
            if step.speak.strip():
                history.append(step.speak)
            continue
        if mode == "verbatim":
            step.speak = verbatim_speak(step)
        else:
            content: list = []
            if step.has_screen and frames is not None and (Path(frames) / png_name(step.step)).exists():
                data = base64.standard_b64encode((Path(frames) / png_name(step.step)).read_bytes()).decode()
                content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}})
            content.append({"type": "text", "text": _user_prompt(project, step, history)})
            reply = client.messages.create(model=model, max_tokens=700, system=SYSTEM_PROMPT,
                                           messages=[{"role": "user", "content": content}])
            step.speak = _parse_reply("".join(b.text for b in reply.content if getattr(b, "type", "") == "text"))
        history.append(step.speak)
        done += 1
        progress(f"script: step {step.step}/{len(project.steps)}")
    return done
