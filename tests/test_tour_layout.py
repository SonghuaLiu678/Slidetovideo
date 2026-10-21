import json
from pathlib import Path

import pytest
from PIL import Image

from slides2video import cli
from slides2video.layout import move_slides, parse_slides
from slides2video.media import media_seconds
from slides2video.steps import Project, Step
from slides2video.tour import annotate, load_plan, render_overlays
from slides2video.video import assemble, write_timeline

from conftest import make_frames, make_tone


def _project(slides, speak="One two three four five six seven eight."):
    return Project(deck="d.pptx", steps=[
        Step(step=i, source_slide=s, title=f"T{s}", text=["a"], new_text=["a"], speak=speak)
        for i, s in enumerate(slides, start=1)])


def _setup(tmp_path: Path, slides):
    proj = _project(slides)
    frames, audio = tmp_path / "frames", tmp_path / "audio"
    make_frames(frames, len(slides))
    audio.mkdir()
    for i in range(1, len(slides) + 1):
        make_tone(audio / f"{i:03d}.mp3", 1.0 + i)       # a different length per step, to tell them apart
    return proj, frames, audio


REGIONS = [
    {"label": "Top", "box": [150, 40, 600, 160], "speak": "This is the top."},
    {"label": "Middle", "box": [150, 180, 600, 300], "speak": "This is the middle.", "hold": 1.5},
    {"label": "Bottom", "box": [150, 320, 600, 440], "speak": "This is the bottom."},
]


def _pixels(im):
    pa = im.load()
    return (pa[x, y] for y in range(im.height) for x in range(im.width))


def _is_red(px):
    return px[0] > 150 and px[1] < 80 and px[2] < 80


# ---- overlays ----------------------------------------------------------------

def test_overlays_are_cumulative_and_mark_in_red(tmp_path: Path):
    base = tmp_path / "base.png"
    Image.new("RGB", (640, 480), "white").save(base)
    paths = render_overlays(base, REGIONS, tmp_path / "o")
    assert [p.name for p in paths] == ["001.png", "002.png", "003.png"]

    def red(path):
        return sum(1 for px in _pixels(Image.open(path).convert("RGB")) if _is_red(px))

    assert red(paths[0]) > 0
    assert red(paths[2]) > red(paths[1]) > red(paths[0])      # earlier marks stay


def test_mask_hides_and_group_limits_earlier_marks(tmp_path: Path):
    base = tmp_path / "base.png"
    Image.new("RGB", (640, 480), (10, 120, 60)).save(base)    # dark green everywhere
    regions = [
        {"label": "A", "box": [150, 40, 300, 160], "speak": "a", "group": "left", "mask": [[320, 0, 640, 480]]},
        {"label": "B", "box": [450, 40, 620, 160], "speak": "b", "group": "right", "style": "box",
         "mask": [[0, 0, 320, 480]]},
    ]
    one, two = (Image.open(p).convert("RGB") for p in render_overlays(base, regions, tmp_path / "o"))
    assert one.getpixel((500, 300)) == (255, 255, 255)        # right half hidden in step 1
    assert two.getpixel((100, 300)) == (255, 255, 255)        # left half hidden in step 2
    # step 2 does not redraw the bracket of group "left"
    assert not any(_is_red(px) for px in _pixels(two.crop((120, 30, 160, 170))))


# ---- annotate ----------------------------------------------------------------

def test_annotate_expands_and_renumbers(tmp_path: Path):
    proj, frames, audio = _setup(tmp_path, [1, 2, 3])
    done = annotate(proj, frames, audio, [{"slide": 2, "regions": REGIONS}])
    assert done == {2: 3}
    assert [s.step for s in proj.steps] == [1, 2, 3, 4, 5]
    assert [s.source_slide for s in proj.steps] == [1, 2, 2, 2, 3]
    assert [s.speak for s in proj.steps[1:4]] == [r["speak"] for r in REGIONS]
    assert proj.steps[2].hold == 1.5 and proj.steps[2].has_screen
    assert all(s.note.startswith("tour:") for s in proj.steps[1:4])
    assert sorted(p.name for p in frames.glob("*.png")) == [f"{i:03d}.png" for i in range(1, 6)]
    assert not (tmp_path / "frames.new").exists() and not (tmp_path / "audio.new").exists()
    # old step 1 and old step 3 keep their narration, under their new numbers; tour steps have none yet
    assert abs(media_seconds(audio / "001.mp3") - 2.0) < 0.2
    assert abs(media_seconds(audio / "005.mp3") - 4.0) < 0.2
    assert not (audio / "002.mp3").exists() and not (audio / "003.mp3").exists()
    # the frame of a tour step differs from the plain slide
    assert Image.open(frames / "002.png").tobytes() != Image.open(frames / "001.png").tobytes()


def test_annotate_twice_does_nothing(tmp_path: Path):
    proj, frames, audio = _setup(tmp_path, [1, 2, 3])
    plan = [{"slide": 2, "regions": REGIONS}]
    annotate(proj, frames, audio, plan)
    assert annotate(proj, frames, audio, plan) == {}
    assert len(proj.steps) == 5


def test_annotate_needs_a_single_step_slide(tmp_path: Path):
    proj, frames, audio = _setup(tmp_path, [1, 2, 2])
    with pytest.raises(ValueError, match="needs exactly one"):
        annotate(proj, frames, audio, [{"slide": 2, "regions": REGIONS}])
    assert len(proj.steps) == 3 and len(list(frames.glob("*.png"))) == 3     # nothing was changed


def test_load_plan_validates(tmp_path: Path):
    bad = tmp_path / "p.json"
    bad.write_text(json.dumps([{"slide": 1, "regions": [{"label": "x", "box": [1, 2, 3], "speak": "s"}]}]))
    with pytest.raises(ValueError, match="box must be"):
        load_plan(bad)
    bad.write_text(json.dumps([{"slide": 1, "regions": [{"label": "x"}]}]))
    with pytest.raises(ValueError, match="missing"):
        load_plan(bad)
    bad.write_text(json.dumps({"slides": [{"slide": 1, "regions": [{"label": "x", "box": [1, 2, 3, 4], "speak": "s"}]}]}))
    assert load_plan(bad)[0]["slide"] == 1


# ---- move --------------------------------------------------------------------

def test_parse_slides():
    assert parse_slides("20") == {20}
    assert parse_slides("3,5,20-22") == {3, 5, 20, 21, 22}


def test_move_slides_carries_frames_and_audio(tmp_path: Path):
    proj, frames, audio = _setup(tmp_path, [1, 2, 3, 4])
    before = {i: Image.open(frames / f"{i:03d}.png").tobytes() for i in range(1, 5)}
    n = move_slides(proj, frames, audio, {4}, before=2)
    assert n == 1
    assert [s.source_slide for s in proj.steps] == [1, 4, 2, 3]
    assert [s.step for s in proj.steps] == [1, 2, 3, 4]
    assert Image.open(frames / "002.png").tobytes() == before[4]          # picture moved with its step
    assert abs(media_seconds(audio / "002.mp3") - 5.0) < 0.2              # old step 4 was 1 + 4 s long
    assert abs(media_seconds(audio / "004.mp3") - 4.0) < 0.2              # old step 3


def test_move_after_and_errors(tmp_path: Path):
    proj, frames, audio = _setup(tmp_path, [1, 2, 3, 4])
    move_slides(proj, frames, audio, {1, 2}, after=4)
    assert [s.source_slide for s in proj.steps] == [3, 4, 1, 2]
    with pytest.raises(ValueError, match="exactly one"):
        move_slides(proj, frames, audio, {1}, before=3, after=4)
    with pytest.raises(ValueError, match="no steps come from slide 9"):
        move_slides(proj, frames, audio, {1}, before=9)
    with pytest.raises(ValueError, match="being moved"):
        move_slides(proj, frames, audio, {1}, before=1)


def test_move_without_audio_does_not_create_audio_dir(tmp_path: Path):
    proj = _project([1, 2, 3])
    frames = tmp_path / "frames"
    make_frames(frames, 3)
    move_slides(proj, frames, tmp_path / "audio", {3}, before=1)
    assert [s.source_slide for s in proj.steps] == [3, 1, 2]
    assert not (tmp_path / "audio").exists()


# ---- speed -------------------------------------------------------------------

def test_speed_keeps_clips_exact(tmp_path: Path):
    """Each clip is (audio + hold) / speed, so picture and voice stay together.
    Speeding up the joined video instead drifted by about 0.2 s per step."""
    proj = _project([1, 2, 3])
    frames, audio = tmp_path / "frames", tmp_path / "audio"
    make_frames(frames, 3)
    audio.mkdir()
    for n, sec in ((1, 10.0), (2, 4.0), (3, 6.0)):
        make_tone(audio / f"{n:03d}.mp3", sec)
    out = tmp_path / "out" / "v.mp4"
    timings = assemble(proj, frames, audio, out, hold=0.6, screen_hold=3.0, jobs=2, speed=2.0)
    for t, sec in zip(timings, (10.0, 4.0, 6.0)):
        assert abs((t.end - t.start) - (sec + 0.6) / 2.0) < 0.15
    csv_path, srt_path = tmp_path / "t.csv", tmp_path / "c.srt"
    write_timeline(proj, timings, csv_path, srt_path, audio, speed=2.0)
    first = srt_path.read_text().splitlines()[1]
    assert first.startswith("00:00:00,000 --> 00:00:05,0")        # speech ends at 10 s / 2


def test_speed_out_of_range(tmp_path: Path):
    proj, frames, audio = _setup(tmp_path, [1])
    with pytest.raises(ValueError, match="speed"):
        assemble(proj, frames, audio, tmp_path / "v.mp4", speed=3.0)


# ---- command line ------------------------------------------------------------

def test_cli_annotate_then_move(tmp_path: Path, capsys):
    work = tmp_path / "work"
    proj = _project([1, 2, 3])
    proj.save(work / "steps.json")
    make_frames(work / "frames", 3)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps([{"slide": 2, "regions": REGIONS}]))
    assert cli.main(["--work", str(work), "annotate", str(plan)]) == 0
    assert "slide 2 -> 3 steps" in capsys.readouterr().out
    assert len(Project.load(work / "steps.json").steps) == 5
    assert cli.main(["--work", str(work), "annotate", str(plan)]) == 0          # second run: nothing to do
    assert "nothing to do" in capsys.readouterr().out
    assert cli.main(["--work", str(work), "move", "3", "--before", "2"]) == 0
    assert [s.source_slide for s in Project.load(work / "steps.json").steps] == [1, 3, 2, 2, 2]
    assert cli.main(["--work", str(work), "move", "3", "--before", "99"]) == 2
