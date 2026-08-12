# horse

> _A galloping horse, procedurally drawn, 72×16 pixels at a time._

`horse` renders a loopable galloping-horse animation as a sprite
sheet PNG. There are no source assets: the horse is a small skeletal
model — barrel, neck, head, tail, and four two-bone legs solved with
inverse kinematics — sampled at N evenly spaced points around one 
stride, so frame N wraps seamlessly back to frame 0. You pick the 
frame count, the sheet layout, and the palette mode; the geometry is
recomputed for every frame rather than interpolated between 
keyframes. It is deliberately narrow: one animal, one gait, one 
frame size (72×16), one palette (palomino, with flaxen mane and tail).

---

## Table of Contents

- [Features](#features)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Usage](#usage)
- [Examples](#examples)
- [How it works](#how-it-works)
- [Known Limitations](#known-limitations)
- [Contributing](#contributing)
- [License](#license)

---

## Features

- **Seamless loop by construction** — every part of the model is a periodic
  function of stride phase `t`, so any frame count loops cleanly. There is no
  keyframe blending step to get subtly wrong.
- **IK-solved gait** — hooves follow a stance/swing cycle (30% of the stride
  planted) and the knees fall out of a two-bone IK solve, not out of hand-placed
  joint positions. Leg touchdown phases are set for a four-beat transverse
  gallop with a suspension phase.
- **8× supersampled, layered rendering** — each body part is drawn as a coverage
  mask at 576×128, box-downsampled to 72×16, then composited with its own colour
  source. Antialiasing stays clean while the barrel takes a vertical gradient
  and the off-side limbs take a cooler, darker tint (atmospheric perspective).
- **Three sheet layouts** — `row`, `column`, or `grid` with a configurable
  column count.
- **Preview GIF in the same run** — `--gif` writes a 6× nearest-neighbour scaled
  animation alongside the sheet, so you can eyeball the loop without importing
  the sheet into anything.
- **Optional scrolling ground** — `--ground` adds a dashed ground line that
  scrolls at the speed implied by the stance sweep, with a brighter patch under
  each planted hoof.

---

## Prerequisites

- **Python**: 3.14 or later (`requires-python = ">=3.14"`)
- **Pillow**: 12.3.0 or later — installed automatically
- **[uv](https://docs.astral.sh/uv/)** _(recommended)_: the project uses
  `uv_build` as its build backend and ships a `uv.lock`

---

## Installation

### With uv

```sh
git clone https://github.com/PeteRichardson/horse.git
cd horse
uv sync
```

Run it without installing anything globally:

```sh
uv run horse --help
```

### With pip

```sh
pip install .
```


---

## Quick Start

```sh
uv run horse --ground --flip --gif preview.gif
horse_sheet.png: 864x16, 12 frames, 12x1 cells of 72x16
preview.gif: preview at 15.0 fps
```
<p align="center">
  <img src="docs/images/demo.gif" alt="horse demo" width="432">
</p>

---

## Usage

```
usage: horse [-h] [-n FRAMES] [-o OUT] [--layout {row,column,grid}]
             [--cols COLS] [--ground] [--mono] [--flip] [--gif GIF]
             [--fps FPS]

options:
  -h, --help                  show this help message and exit
  -n, --frames FRAMES         frames sampled around one stride [default: 12]
  -o, --out OUT               sprite sheet output path [default: horse_sheet.png]
  --layout {row,column,grid}  sheet arrangement [default: row]
  --cols COLS                 columns when --layout grid [default: 4]
  --ground                    scrolling ground + contact patches
  --mono                      original white silhouette
  --flip                      face right instead of left
  --gif GIF                   also write a preview GIF
  --fps FPS                   preview GIF frame rate [default: 15.0]
```

Each cell is always 72×16 pixels; the sheet dimensions follow from the frame
count and layout.

---

## Examples

### A 4×3 grid with the ground line

```sh
uv run horse -n 12 --layout grid --cols 4 --ground -o sheet.png
```

```
sheet.png: 288x48, 12 frames, 4x3 cells of 72x16
```

### A right-facing white silhouette

```sh
# --mono drops the palette entirely: white near side, grey off side, black bg
uv run horse --mono --flip -n 8 -o silhouette.png
```

```
silhouette.png: 576x16, 8 frames, 8x1 cells of 72x16
```

### A smoother loop, previewed fast

```sh
# More samples around the same stride; the loop is still seamless at any n
uv run horse -n 24 --gif preview.gif --fps 24
```

### A vertical strip

```sh
uv run horse --layout column -o strip.png
```

---

## How it works

Everything lives in one module, `src/horse/__init__.py`, in four sections:

| Section | What it does |
|---------|--------------|
| **skeleton** | Joint positions, segment lengths, and the per-leg touchdown phases in `LEGS`. `hoof()` returns the stance/swing trajectory; `ik()` solves the knee. |
| **drawing** | `taper()` and `disc()` paint into named supersampled masks held by `Layers` — `far`, `body`, `shade`, `tail`, `mane`, `near`, `eye`, and the two hoof layers. |
| **compositing** | `gradient()` builds the vertical body ramp from `BODY_STOPS`; `compose()` alpha-blends each mask with its colour source. The `shade` mask is multiplied by `body` so shoulder and flank creases carve the barrel instead of outlining it. |
| **main** | Argument parsing, frame loop, sheet paste, optional GIF write. |

The palette constants (`BODY_STOPS`, `FLAXEN`, `HOOF`, `FAR_MUL`, `GROUND_C`,
`CONTACT`) are module-level, so recolouring is a source edit rather than a flag.

---

## Known Limitations

- **Frame size is fixed at 72×16.** `W` and `H` are module constants and several
  drawing coordinates (`GROUND = 15.4`, the ground rectangles, the skeleton
  positions) are tuned to that canvas. Changing them requires retuning, not just
  a new value.
- **One gait, one animal, one palette.** The transverse gallop in `LEGS` and the
  palomino colours are hardcoded. There is no walk, trot, or canter, and no
  `--palette` flag.
- **No transparency.** Output is RGB with an opaque black background, so the
  sheet needs keying before it can be dropped over an arbitrary background in a
  game engine.
- **Compositing is pure Python, per pixel.** `_alpha()` and `scale_img()` loop
  over all 1,152 pixels of every layer of every frame. Fine at 72×16 and a dozen
  frames; it will not scale to large canvases or long sequences.
- **The `over()` helper is unused** and its fallback branch is dubious — treat
  `_alpha()` as the real compositing path.


## License

Licensed under the **MIT License** — see [LICENSE](LICENSE) for details.
