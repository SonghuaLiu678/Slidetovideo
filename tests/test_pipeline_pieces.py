from pathlib import Path
from types import SimpleNamespace

import pytest

from slides2video import narrate, verify
from slides2video.media import media_seconds
from slides2video.render import page_count, render_pdf
from slides2video.steps import Project, Step
from slides2video.tts import clip_is_valid, word_count
from slides2video.video import assemble, write_timeline

from conftest import make_frames, make_pdf, make_tone


def _project(n=3, speak="One two three four five six seven eight."):
    return Project(deck="d.pptx", steps=[
        Step(step=i, source_slide=i, title=f"T{i}", text=["a 30 b"], new_text=["a 30 b"], speak=speak)
        for i in range(1, n + 1)])


# ---- narration -------------------------------------------------------------

def test_verbatim_cleans_symbols():
    s = Step(step=1, source_slide=1, new_text=["Starting = 30", "+80 tokens", "- get the subsidy", "Penalty = −100", "30% chance"])
    out = narrate.verbatim_speak(s)
    assert "equals 30" in out and "plus 80" in out and "minus 100" in out and "30 percent" in out
    assert "get the subsidy." in out and "- get" not in out


def test_generate_with_fake_client_and_history():
    seen = []

    class FakeMessages:
        def create(self, **kw):
            seen.append(kw)
            n = len(seen)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=f'{{"speak": "Narration {n}."}}')])

    client = SimpleNamespace(messages=FakeMessages())
    proj = _project(3, speak="")
    done = narrate.generate(proj, mode="anthropic", client=client, model="m")
    assert done == 3
    assert [s.speak for s in proj.steps] == ["Narration 1.", "Narration 2.", "Narration 3."]
    assert "Narration 2." in seen[2]["messages"][0]["content"][-1]["text"]   # earlier lines are passed on
    assert seen[0]["system"] == narrate.SYSTEM_PROMPT


def test_parse_reply_variants():
    assert narrate._parse_reply('{"speak": "Hi there."}') == "Hi there."
    assert narrate._parse_reply('Sure: {"speak": "Hi."} done') == "Hi."
    assert narrate._parse_reply("Just text") == "Just text"


# ---- audio validation ------------------------------------------------------

def test_clip_validation_flags_near_empty_clip(tmp_path: Path):
    short, ok = tmp_path / "short.mp3", tmp_path / "ok.mp3"
    make_tone(short, 0.36)    # the real failure: a third of a second for a sentence
    make_tone(ok, 4.0)
    text = "And you will stay paired with the same person for all rounds of the game."
    assert word_count(text) == 15
    assert clip_is_valid(short, text)[0] is False
    assert clip_is_valid(ok, text)[0] is True
    assert clip_is_valid(tmp_path / "missing.mp3", text)[0] is False


# ---- render / assemble / verify --------------------------------------------

def test_render_pdf_width_and_count(tmp_path: Path):
    pdf = tmp_path / "d.pdf"
    make_pdf(pdf, 4)
    assert page_count(pdf) == 4
    assert render_pdf(pdf, tmp_path / "frames", width=1440) == 4
    from PIL import Image
    assert Image.open(tmp_path / "frames" / "001.png").size == (1440, 1080)


def test_assemble_lengths_have_no_tail(tmp_path: Path):
    """The ffmpeg -shortest bug added ~6 s after long clips. Each clip must be
    audio + hold, to within one frame."""
    proj = _project(3)
    proj.steps[2].has_screen = True
    frames, audio = tmp_path / "frames", tmp_path / "audio"
    make_frames(frames, 3)
    audio.mkdir()
    for n, sec in ((1, 17.0), (2, 4.0), (3, 2.0)):
        make_tone(audio / f"{n:03d}.mp3", sec)
    out = tmp_path / "out" / "v.mp4"
    timings = assemble(proj, frames, audio, out, hold=0.6, screen_hold=3.0, jobs=2)
    expected = [17.0 + 0.6, 4.0 + 0.6, 2.0 + 3.0]
    for t, e in zip(timings, expected):
        assert abs((t.end - t.start) - e) < 0.15, (t, e)
    assert abs(media_seconds(out) - sum(expected)) < 0.5
    csv_path, srt_path = tmp_path / "t.csv", tmp_path / "c.srt"
    write_timeline(proj, timings, csv_path, srt_path, audio)
    assert srt_path.read_text().startswith("1\n00:00:00,000 --> 00:00:1")
    issues = verify.check_video(proj, out, csv_path)
    assert not [i for i in issues if i.level == "error"]


def test_step_hold_override():
    from slides2video.video import step_hold
    s = Step(step=1, source_slide=1, hold=1.5)
    assert step_hold(s, 0.6, 3.0) == 1.5
    s2 = Step(step=2, source_slide=2, has_screen=True)
    assert step_hold(s2, 0.6, 3.0) == 3.0


def test_verify_number_checks():
    p = _project(1)
    p.steps[0].speak = "It costs 40 tokens."
    msgs = [str(i) for i in verify.check_narration(p)]
    assert any("not on the slide: 40" in m for m in msgs)
    p.steps[0].speak = "It is thirty."          # spelled-out numbers count
    assert not any("not on the slide" in str(i) for i in verify.check_narration(p))


def test_check_pdf_page_mismatch(tmp_path: Path):
    pdf = tmp_path / "d.pdf"
    make_pdf(pdf, 2)
    issues = verify.check_pdf(_project(3), pdf)
    assert issues and issues[0].level == "error"


@pytest.mark.network
def test_real_synthesis(tmp_path: Path):
    from slides2video.tts import synthesize
    proj = _project(1, speak="This is a short test of the voice.")
    res = synthesize(proj, tmp_path / "audio")
    assert res and res[0][1] == "ok"


def test_line_break_becomes_space_and_cells_join():
    from lxml import etree
    from slides2video.split import _para_text
    ns = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    p = etree.fromstring(f'<a:p {ns}><a:r><a:t>Actual score HIGH</a:t></a:r><a:br/><a:r><a:t>Reported score HIGH</a:t></a:r></a:p>')
    assert _para_text(p) == "Actual score HIGH Reported score HIGH"


def test_verbatim_handles_table_text_and_glyphs():
    s = Step(step=1, source_slide=1, new_text=["Screens (Sender)", "Decision payment = ??  depends on quality", "Case A | 110 tokens"])
    out = narrate.verbatim_speak(s)
    assert "Screens, Sender." in out
    assert "depends on quality" in out and "" not in out and "??" not in out
    assert "Case A, 110 tokens." in out
