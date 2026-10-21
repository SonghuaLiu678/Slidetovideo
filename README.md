# Slidetovideo

Turn a PowerPoint deck into a narrated MP4 where each slide change lands on the
second the voice starts talking about it.

I built this for an instruction video that had to be re-recorded every time a
sentence changed. It splits the deck into reveal steps, drafts a narration,
synthesizes the voice, and assembles the video from a PDF export. It is a
command-line tool, a few hundred lines of Python, and it does not need a
screen recorder.

```
deck.pptx ──split──▶ deck_split.pptx ──(you: File > Export > PDF)──▶ deck_split.pdf
                │                                                        │
            steps.json ◀───────────── script ◀──────────── render ───────┘
                │
              audio ──▶ one mp3 per step ──▶ video ──▶ out/video.mp4, timeline.csv, captions.srt
                                                  │
                                               verify
```

## Install

The repository is called Slidetovideo; the command it installs is `slides2video`.

Python 3.9 or newer. No system ffmpeg needed (a static build comes with the
`imageio-ffmpeg` dependency; a system `ffmpeg` is used if you have one).

```bash
git clone https://github.com/SonghuaLiu678/Slidetovideo
cd Slidetovideo
python -m venv .venv && source .venv/bin/activate
pip install -e ".[llm]"        # drop [llm] if you will not use Claude for the script
```

## Quick start

```bash
# 1. Split the deck into steps (one paragraph or table row per step)
slides2video split deck.pptx

# 2. Open work/deck_split.pptx in PowerPoint, File > Export > PDF, save as work/deck_split.pdf

# 3. Render frames, write a draft script, stop for review
slides2video run deck.pptx --pdf work/deck_split.pdf --llm verbatim

# 4. Edit work/steps.json (the "speak" fields), then run the same command with --yes
slides2video run deck.pptx --pdf work/deck_split.pdf --llm verbatim --yes
```

The result is in `work/out/`: `video.mp4`, `timeline.csv` (start and end of every
step) and `captions.srt`.

If your deck is already split into one slide per step, skip step 1 and use
`--no-split` (one step per slide).

## What each stage does

| Command | What it does | Needs |
|---|---|---|
| `split` | Clones each slide once per reveal step and makes the not-yet-shown text transparent, so layout never shifts. Writes `deck_split.pptx` and `steps.json`. | the .pptx |
| `render` | Renders the exported PDF to 1440-px-wide PNG frames and checks that the page count equals the step count. | the PDF |
| `script` | Fills the `speak` field of every step. `--llm verbatim` reads the new slide text cleaned up for speech. `--llm anthropic` asks Claude for connected narration and for descriptions of screenshots. | `ANTHROPIC_API_KEY` for `anthropic` |
| `annotate` | Turns one screenshot slide into several steps, one per region, each frame marked with a red bracket and label. Optional; see below. | a plan file |
| `move` | Moves slides to another place in the video; frames and audio are renamed to match. | |
| `voices`, `audition` | Lists voices; renders one sentence in several voices so someone can choose. | internet |
| `audio` | One mp3 per step. Every clip is measured; near-empty ones are retried. | internet |
| `video` | One still frame plus one audio clip per step, each cut to exactly audio + pause, then joined. `--speed 1.2` plays every step faster. | frames, audio |
| `verify` | Checks narration numbers against the slides, every audio clip, the PDF page count, and that picture changes line up with step boundaries. | |

## Writing the script

`steps.json` is the single source of truth. Each step looks like this:

```json
{
  "step": 14,
  "source_slide": 5,
  "title": "Earnings Examples",
  "text": ["Scenario | SENDER Earnings", "Actual score HIGH | Starting = 30 ..."],
  "new_text": ["Actual score LOW | Starting = 30 ..."],
  "has_screen": false,
  "speak": "Now a case where the score is low and the sender reports low.",
  "hold": null,
  "note": ""
}
```

`--llm verbatim` is flat but safe. `--llm anthropic` writes much better
narration (bridges between steps, a few sentences describing each screenshot),
but read it before you record: it is a draft. `verify` warns when the narration
contains a number that is not on the slide.

```bash
export ANTHROPIC_API_KEY=...
slides2video script --llm anthropic          # fills steps that have no narration yet
slides2video script --llm anthropic --only 12,13 --overwrite
```

The model defaults to `claude-sonnet-5-5`; set `S2V_MODEL` to change it.

## Walking through a screenshot

A slide that shows an interface is hard to follow if the voice describes it in
one go. `annotate` turns that slide into several steps. Each step shows the same
screenshot with one more region marked in red (a bracket and a label in the left
margin, earlier regions stay marked more thinly), and the voice talks only about
that region.

Write a plan file with the regions in the order they should be explained. Boxes
are `[x0, y0, x1, y1]` in frame pixels (the frames in `work/frames/` are 1440 px
wide; open one in any image viewer to read coordinates):

```json
[
  {"slide": 22,
   "regions": [
     {"label": "General Info",   "box": [297, 150, 1138, 298],
      "speak": "At the top is the general information: the round number and your role."},
     {"label": "Reference Info", "box": [297, 313, 1138, 472],
      "speak": "The gray box repeats the key information from the instructions, for reference when you need it."},
     {"label": "Make Decision",  "box": [297, 486, 1138, 967], "hold": 1.5,
      "speak": "This is where you make your decision."}
   ]}
]
```

```bash
slides2video annotate plan.json     # after render, before audio
slides2video audio
slides2video video
```

`slide` is the slide number in the original deck, and the slide must have
exactly one step (a screenshot slide normally does). Each region needs `label`,
`box` and `speak`. Optional keys: `hold` (pause after the step), `style`
(`"bracket"` by default; `"box"` draws an outline with a small label badge, for
pictures that touch the frame edge and leave no margin), `mask` (rectangles
painted white first, to hide part of the slide) and `group`.

Two screenshots on one slide can be introduced one at a time with `mask`: give
the regions of the first screenshot a mask over the second, and the other way
round, and give each screenshot's regions its own `group` so that marks on a
hidden screenshot are not redrawn. Running `annotate` again with the same plan
does nothing, because tour steps are recognised by their note. To change a tour,
start again from `split`, which rewrites `steps.json`.

If you already have narration, run `annotate` before `audio`; existing clips
are kept for every other step and renamed to the new step numbers.

## Moving slides

```bash
slides2video move 20 --before 26         # the summary slide goes just before slide 26
slides2video move 3-5 --after 12
```

Slide numbers are those of the original deck. Steps, frames and audio clips are
renumbered together, so nothing has to be regenerated.

## Speeding up

```bash
slides2video video --speed 1.2           # between 0.5 and 2.0
```

The speed-up is applied to each clip separately. Speeding up the finished video
instead is shorter to write and looks fine, but picture and voice end up out of
step: in my deck 62 of 124 page changes came 0.05 to 0.25 s before the voice
had finished, so the last words of a step were heard on the next slide.

## Choosing a voice

```bash
slides2video voices --locale en-US
slides2video audition --voices en-US-AndrewMultilingualNeural,en-US-BrianNeural,en-US-GuyNeural
```

Send the files in `work/audition/` to whoever has to approve the voice, then
pass the winner with `slides2video audio --voice <name>`. It is stored in
`steps.json` so later runs reuse it. `--rate "-5%"` slows the voice down.

## Fixing something small

Change the `speak` text (or the slide) and rebuild only what changed:

```bash
slides2video audio --only 30        # regenerate step 30
slides2video video                  # re-join; takes about a minute for 100 steps
slides2video verify
```

If you edit the slides, export the PDF again and run `slides2video render` before `video`.
If you add or remove slides the page count no longer matches the steps; run `split` again.

Per step, `"hold": 1.5` sets the pause (seconds) after the narration. By default
it is 0.6 s, and 3 s for steps that show a large picture (`has_screen`), so
people have time to look at an interface.

## Things that went wrong while building this

These are all handled now, but they are the reason the code looks the way it does.

- **Empty clips.** edge-tts sometimes returns a clip of a third of a second for a
  14-word sentence and reports success. Every clip is measured against a
  minimum seconds-per-word and retried.
- **The slide lagged the voice.** Cutting clips with `-shortest` on a looped
  still image left up to six seconds of silent tail after long narration. Each
  clip is now cut to an explicit `audio + pause` length.
- **Soft screenshots.** PowerPoint's PNG export is 72 dpi (720 px wide for a
  10 in slide). Upscaled to video width, interface text turned to mush. The
  frames come from the PDF instead.
- **Page count drift.** Hidden slides or an export of the wrong deck shift every
  later frame by one. The PDF page count must equal the step count, or nothing runs.
- **Speeding up the joined video.** `setpts` plus `atempo` on the finished file
  made 62 of 124 page changes arrive 0.05 to 0.25 s before the voice had
  finished. Speed is now applied to each clip before joining.
- **Two screenshots on one slide.** Explaining both at once loses the viewer.
  `annotate` can hide one with a `mask` until it is its turn.
- **Left and right.** A cropped screenshot can be mirrored in the slide
  relative to the source image. Check any sentence that says "on the left" against
  the rendered frame, not the original picture. `verify` cannot catch this.

## Limits

- The splitter is a heuristic: the text box with the most paragraphs is
  revealed paragraph by paragraph, otherwise the first table row by row.
  Diagrams, grouped shapes and animations are not split. On the deck this was
  built for it matched a hand-made split on 16 of 23 slides. Use `--keep` for slides
  that should stay whole, or edit `steps.json` and the deck by hand.
- The PDF has to be exported from PowerPoint (or anything else that gives one
  page per slide). I did not wire in LibreOffice because the fonts differ.
- Narration is spoken English. Other languages need a different system prompt in
  `narrate.py` and a matching voice.
- `annotate` does not find regions by itself: you give the coordinates.
- No transitions, no background music, one voice for the whole video.

## Privacy

Slide text is sent to Microsoft's read-aloud service by `edge-tts`, which is an
unofficial client and can break or change terms without notice. With
`--llm anthropic`, slide text and the rendered pages of screenshot steps are sent
to Anthropic. Do not use either on a deck you are not allowed to send out.

## Development

```bash
pip install -e ".[dev,llm]"
pytest                 # offline tests
pytest -m network      # also calls the speech service
```

## License

MIT. See `LICENSE`.
