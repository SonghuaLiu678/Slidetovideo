"""Command line interface."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .steps import Project

PDF_HELP = (
    "Next: open {pptx} in PowerPoint, choose File > Export > PDF (all slides), save it as\n"
    "  {pdf}\n"
    "then run:  slides2video run {deck} --pdf {pdf}"
)


def _ints(text: Optional[str]) -> List[int]:
    return [int(x) for x in text.replace(" ", "").split(",") if x] if text else []


def _paths(work: Path) -> dict:
    return {
        "steps": work / "steps.json", "frames": work / "frames", "audio": work / "audio",
        "video": work / "out" / "video.mp4", "timeline": work / "out" / "timeline.csv",
        "srt": work / "out" / "captions.srt", "split": work / "deck_split.pptx",
    }


def cmd_split(a) -> int:
    from .split import split_deck

    work = Path(a.work)
    p = _paths(work)
    work.mkdir(parents=True, exist_ok=True)
    proj = split_deck(Path(a.deck), p["split"], keep=_ints(a.keep))
    proj.save(p["steps"])
    print(f"split: {len(proj.steps)} steps from {len(set(s.source_slide for s in proj.steps))} slides")
    print(f"  deck:  {p['split']}\n  steps: {p['steps']}")
    print(PDF_HELP.format(pptx=p["split"], pdf=work / "deck_split.pdf", deck=a.deck))
    return 0


def cmd_init(a) -> int:
    from .split import steps_from_deck

    work = Path(a.work)
    p = _paths(work)
    proj = steps_from_deck(Path(a.deck))
    proj.save(p["steps"])
    print(f"init: {len(proj.steps)} steps (one per slide) -> {p['steps']}")
    return 0


def cmd_render(a) -> int:
    from .render import page_count, render_pdf

    p = _paths(Path(a.work))
    proj = Project.load(p["steps"])
    n = page_count(Path(a.pdf))
    if n != len(proj.steps):
        print(f"error: PDF has {n} pages, steps.json has {len(proj.steps)} steps", file=sys.stderr)
        return 2
    render_pdf(Path(a.pdf), p["frames"], width=a.width)
    print(f"render: {n} frames -> {p['frames']}")
    return 0


def cmd_annotate(a) -> int:
    from .tour import annotate, load_plan

    p = _paths(Path(a.work))
    proj = Project.load(p["steps"])
    plan = load_plan(Path(a.plan))
    known = {s.source_slide for s in proj.steps}
    for item in plan:
        if int(item["slide"]) not in known:
            print(f"warning: slide {item['slide']} is not in steps.json", file=sys.stderr)
    done = annotate(proj, p["frames"], p["audio"], plan)
    if not done:
        print("annotate: nothing to do (already annotated, or no matching slides)")
        return 0
    proj.save(p["steps"])
    for slide, n in sorted(done.items()):
        print(f"annotate: slide {slide} -> {n} steps")
    print(f"  now {len(proj.steps)} steps. Fill in or check the 'speak' fields, then run: slides2video audio")
    return 0


def cmd_move(a) -> int:
    from .layout import move_slides, parse_slides

    p = _paths(Path(a.work))
    proj = Project.load(p["steps"])
    try:
        n = move_slides(proj, p["frames"], p["audio"], parse_slides(a.slides), before=a.before, after=a.after)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    proj.save(p["steps"])
    print(f"move: {n} steps moved; frames and audio renamed to match")
    return 0


def cmd_script(a) -> int:
    from .narrate import generate

    p = _paths(Path(a.work))
    proj = Project.load(p["steps"])
    n = generate(proj, mode=a.llm, frames=p["frames"], model=a.model, only=set(_ints(a.only)) or None,
                 overwrite=a.overwrite, progress=lambda s: print(s, flush=True))
    proj.save(p["steps"])
    print(f"script: wrote narration for {n} steps -> {p['steps']}")
    print("Read it and edit the 'speak' fields before generating audio.")
    return 0


def cmd_voices(a) -> int:
    from .tts import list_voices

    for v in list_voices(a.locale):
        print(f"{v['ShortName']:<42} {v['Gender']:<7} {', '.join(v.get('VoiceTag', {}).get('VoicePersonalities', []))}")
    return 0


def cmd_audition(a) -> int:
    from .tts import audition

    out = Path(a.work) / "audition"
    voices = [v.strip() for v in a.voices.split(",") if v.strip()]
    for path in audition(a.text, voices, out):
        print(path)
    return 0


def cmd_audio(a) -> int:
    from .tts import print_results, synthesize

    p = _paths(Path(a.work))
    proj = Project.load(p["steps"])
    if a.voice:
        proj.voice = a.voice
        proj.save(p["steps"])
    res = synthesize(proj, p["audio"], voice=proj.voice, rate=a.rate, only=_ints(a.only), force=a.force)
    if not res:
        print("audio: all clips already valid")
        return 0
    return 1 if print_results(res) else 0


def cmd_video(a) -> int:
    from .video import assemble, write_timeline

    p = _paths(Path(a.work))
    proj = Project.load(p["steps"])
    out = Path(a.output) if a.output else p["video"]
    timings = assemble(proj, p["frames"], p["audio"], out, hold=a.hold, screen_hold=a.screen_hold, speed=a.speed)
    write_timeline(proj, timings, p["timeline"] if not a.output else out.with_suffix(".csv"),
                   p["srt"] if not a.output else out.with_suffix(".srt"), p["audio"], speed=a.speed)
    print(f"video: {out}  ({timings[-1].end / 60:.1f} min, {len(timings)} steps)")
    return 0


def cmd_verify(a) -> int:
    from . import verify as v

    p = _paths(Path(a.work))
    proj = Project.load(p["steps"])
    issues = v.check_narration(proj) + (v.check_audio(proj, p["audio"]) if p["audio"].exists() else [])
    if a.pdf:
        issues += v.check_pdf(proj, Path(a.pdf))
    video = Path(a.video) if a.video else p["video"]
    if video.exists() and p["timeline"].exists():
        issues += v.check_video(proj, video, p["timeline"])
    for i in issues:
        print(i)
    errors = sum(1 for i in issues if i.level == "error")
    print(f"verify: {errors} error(s), {len(issues) - errors} warning(s)")
    return 1 if errors else 0


def cmd_run(a) -> int:
    work = Path(a.work)
    p = _paths(work)
    if not p["steps"].exists():
        (cmd_init if a.no_split else cmd_split)(a)
    if not a.pdf:
        if a.no_split:
            print(PDF_HELP.format(pptx=a.deck, pdf=work / "deck.pdf", deck=a.deck))
        return 0
    a.width = a.width
    if cmd_render(a):
        return 2
    proj = Project.load(p["steps"])
    if any(not s.speak.strip() for s in proj.steps):
        a.only, a.overwrite, a.model = None, False, a.model
        cmd_script(a)
        if not a.yes:
            print("\nStopped for review. Edit the 'speak' fields in", p["steps"],
                  "then re-run the same command (or add --yes to skip the pause).")
            return 0
    a.only, a.force, a.rate, a.voice = None, False, a.rate, a.voice
    if cmd_audio(a):
        return 3
    a.output = None
    if cmd_video(a):
        return 4
    a.video = None
    return cmd_verify(a)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="slides2video", description="PowerPoint deck -> narrated MP4.")
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--work", default="work", help="working directory (default: ./work)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("split", help="split a deck into reveal steps and write steps.json")
    s.add_argument("deck")
    s.add_argument("--keep", help="comma-separated slide numbers that must not be split")
    s.set_defaults(fn=cmd_split)

    s = sub.add_parser("init", help="use a deck as it is: one step per slide")
    s.add_argument("deck")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("render", help="render the exported PDF to frames")
    s.add_argument("pdf")
    s.add_argument("--width", type=int, default=1440)
    s.set_defaults(fn=cmd_render)

    s = sub.add_parser("script", help="write narration into steps.json")
    s.add_argument("--llm", choices=["verbatim", "anthropic"], default="verbatim")
    s.add_argument("--model")
    s.add_argument("--only", help="comma-separated step numbers")
    s.add_argument("--overwrite", action="store_true")
    s.set_defaults(fn=cmd_script)

    s = sub.add_parser("annotate", help="walk through a screenshot slide: one marked frame per region")
    s.add_argument("plan", help="JSON plan file (see the README, 'Walking through a screenshot')")
    s.set_defaults(fn=cmd_annotate)

    s = sub.add_parser("move", help="move slides to another place in the video")
    s.add_argument("slides", help="source slide numbers, e.g. 20 or 20-22 or 3,5")
    where = s.add_mutually_exclusive_group(required=True)
    where.add_argument("--before", type=int, help="put them just before this source slide")
    where.add_argument("--after", type=int, help="put them just after this source slide")
    s.set_defaults(fn=cmd_move)

    s = sub.add_parser("voices", help="list available voices")
    s.add_argument("--locale", default="en-")
    s.set_defaults(fn=cmd_voices)

    s = sub.add_parser("audition", help="render one sentence in several voices")
    s.add_argument("--voices", required=True, help="comma-separated voice names")
    s.add_argument("--text", default="Welcome. Thank you for taking part in today's session.")
    s.set_defaults(fn=cmd_audition)

    s = sub.add_parser("audio", help="synthesize one clip per step")
    s.add_argument("--voice")
    s.add_argument("--rate", default="+0%", help='speaking rate, e.g. "-5%%"')
    s.add_argument("--only", help="comma-separated step numbers to regenerate")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_audio)

    s = sub.add_parser("video", help="assemble the MP4")
    s.add_argument("-o", "--output")
    s.add_argument("--hold", type=float, default=0.6, help="seconds of pause after each step")
    s.add_argument("--screen-hold", type=float, default=3.0, help="pause after screenshot steps")
    s.add_argument("--speed", type=float, default=1.0, help="play every step faster, e.g. 1.2 (0.5-2.0)")
    s.set_defaults(fn=cmd_video)

    s = sub.add_parser("verify", help="check narration, audio, PDF and video")
    s.add_argument("--pdf")
    s.add_argument("--video")
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("run", help="run the whole pipeline, pausing for review")
    s.add_argument("deck")
    s.add_argument("--pdf", help="PDF exported from the split deck")
    s.add_argument("--no-split", action="store_true", help="deck is already split: one step per slide")
    s.add_argument("--keep")
    s.add_argument("--llm", choices=["verbatim", "anthropic"], default="verbatim")
    s.add_argument("--model")
    s.add_argument("--voice")
    s.add_argument("--rate", default="+0%")
    s.add_argument("--width", type=int, default=1440)
    s.add_argument("--hold", type=float, default=0.6)
    s.add_argument("--screen-hold", type=float, default=3.0)
    s.add_argument("--speed", type=float, default=1.0)
    s.add_argument("--yes", action="store_true", help="do not pause after writing the script")
    s.set_defaults(fn=cmd_run)
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
