from pathlib import Path

from pptx import Presentation
from pptx.oxml.ns import qn

from slides2video.split import split_deck, steps_from_deck


def _alpha_zero_paragraphs(slide) -> int:
    n = 0
    for p in slide._element.iter(qn("a:p")):
        runs = p.findall(qn("a:r"))
        if runs and all(r.find(qn("a:rPr")) is not None and
                        any(a.get("val") == "0" for a in r.find(qn("a:rPr")).iter(qn("a:alpha")))
                        for r in runs):
            n += 1
    return n


def test_split_counts_and_text(sample_deck: Path, tmp_path: Path):
    out = tmp_path / "split.pptx"
    proj = split_deck(sample_deck, out)
    # 3 paragraphs -> 3 steps, 3 data rows -> 3 steps, picture slide -> 1 step
    assert [s.source_slide for s in proj.steps] == [1, 1, 1, 2, 2, 2, 3]
    assert [s.step for s in proj.steps] == list(range(1, 8))
    assert proj.steps[0].new_text[0] == "How it works"          # title appears with the first paragraph
    assert "First we do the first thing." in proj.steps[0].new_text
    assert proj.steps[1].new_text == ["Then the second thing, worth 30 percent."]
    assert proj.steps[2].new_text == ["Finally the third thing."]
    assert proj.steps[-1].has_screen is True
    assert proj.steps[0].has_screen is False


def test_split_deck_reloads_and_hides_unrevealed_text(sample_deck: Path, tmp_path: Path):
    out = tmp_path / "split.pptx"
    split_deck(sample_deck, out)
    prs = Presentation(str(out))
    assert len(prs.slides) == 7
    # step 1 hides paragraphs 2 and 3, step 2 hides paragraph 3, step 3 hides none
    assert [_alpha_zero_paragraphs(prs.slides[i]) for i in range(3)] == [2, 1, 0]
    # table: header always visible, 3 rows -> 3, 2, 1 hidden-row paragraphs... then none
    hidden = [_alpha_zero_paragraphs(prs.slides[i]) for i in (3, 4, 5)]
    assert hidden[0] > hidden[1] > hidden[2] == 0


def test_keep_prevents_split(sample_deck: Path, tmp_path: Path):
    proj = split_deck(sample_deck, tmp_path / "s.pptx", keep=[1])
    assert [s.source_slide for s in proj.steps] == [1, 2, 2, 2, 3]
    assert len(proj.steps[0].text) == 4  # title + 3 paragraphs, all visible


def test_steps_from_deck_uses_slides_as_is(sample_deck: Path):
    proj = steps_from_deck(sample_deck)
    assert [s.step for s in proj.steps] == [1, 2, 3]
    assert proj.steps[0].title == "How it works"
