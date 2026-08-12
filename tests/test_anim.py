"""Tests for the `.anim` encoder.

The load-bearing test is `test_rebuilds_a_shipped_animation_byte_for_byte`.
The fixtures are a real animation from the BUSY Bar firmware — its source
frames as the firmware repo ships them, and the compiled `.anim` as read back
off a physical bar.  If our encoder turns the first into the second exactly,
every field, offset, and compression decision is right, because a single wrong
byte anywhere moves everything after it.
"""

import io
import json
import random
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from horse import anim

FIXTURES = Path(__file__).parent / "fixtures"


def load_zip_frames(path: Path):
    """Read a firmware animation source archive: meta.json plus frame_N.png."""
    stem = path.stem
    archive = zipfile.ZipFile(path)
    meta = json.loads(archive.read(f"{stem}/meta.json"))

    names = [
        name
        for name in archive.namelist()
        if name.startswith(f"{stem}/") and name.endswith(".png") and "__MACOSX" not in name
    ]
    names.sort(key=lambda name: int("".join(c for c in Path(name).stem if c.isdigit()) or 0))

    frames = [Image.open(io.BytesIO(archive.read(name))) for name in names]
    return meta, frames


def test_rebuilds_a_shipped_animation_byte_for_byte():
    meta, frames = load_zip_frames(FIXTURES / "spinner_front_8x8.zip")
    expected = (FIXTURES / "spinner_front_8x8.anim").read_bytes()

    sections = [anim.Section(s["name"], s["start"], s["end"]) for s in meta["sections"]]
    built = anim.encode(frames, meta["fps"], meta["color_mode"], sections)

    assert built == expected


@pytest.mark.parametrize("blk_size", [1, 3, 4])
def test_rle_round_trips(blk_size):
    rng = random.Random(7)
    for _ in range(200):
        blocks = rng.randrange(1, 60)
        # A small alphabet so runs actually occur and both opcodes get used.
        data = bytes(rng.choice(b"\x00\x01\xff\x80") for _ in range(blocks * blk_size))
        assert anim.rle_decompress(anim.rle_compress(data, blk_size), blk_size) == data


def test_header_reports_the_frame_geometry():
    frames = [Image.new("RGB", (72, 16), (10, 20, 30))]
    data = anim.encode(frames, 30)

    assert data[:8] == b"bicycle0"
    assert data[8] == 0  # flags: the firmware loader rejects any other value
    assert (data[9], data[10]) == (72, 16)
    assert data[11] == anim.COLOR_MODES["rgb888"]
    assert data[12] == 30  # fps
    # Header + sections + frames must equal the file size, or the bar bails.
    assert len(data) == 36 + int.from_bytes(data[16:20], "little") + int.from_bytes(
        data[20:24], "little"
    )


def test_rgb888_is_actually_bgr():
    """The firmware's `rgb888` stores blue first. Getting this backwards is
    invisible in greyscale test art and wrong on every colour frame."""
    packed = anim.pack_pixels(bytes([0x11, 0x22, 0x33, 0xFF]), "rgb888")
    assert packed == bytes([0x33, 0x22, 0x11])


def test_gray4_packs_two_pixels_per_byte_from_the_red_channel():
    rgba = bytes([0xA0, 0, 0, 0xFF, 0xB0, 0, 0, 0xFF])
    assert anim.pack_pixels(rgba, "gray4") == bytes([0xAB])


def test_identical_consecutive_frames_collapse_into_one():
    same = Image.new("RGB", (8, 8), (1, 2, 3))
    one = anim.encode([same], 30)
    four = anim.encode([same] * 4, 30)

    assert len(four) == len(one)  # no extra frame data was written
    assert int.from_bytes(four[28:32], "little") == 1  # file_frame_count
    assert int.from_bytes(four[32:36], "little") == 4  # display_frame_count


def test_default_section_spans_every_display_frame():
    frames = [Image.new("RGB", (8, 8), (i, i, i)) for i in range(5)]
    data = anim.encode(frames, 30)

    start = int.from_bytes(data[36:40], "little")
    end = int.from_bytes(data[40:44], "little")
    frame_offs = int.from_bytes(data[44:48], "little")
    sections_len = int.from_bytes(data[16:20], "little")

    assert (start, end) == (0, 4)
    assert data[49:57] == b"default\0"
    assert frame_offs == 36 + sections_len


def test_named_sections_are_addressable():
    frames = [Image.new("RGB", (8, 8), (i, i, i)) for i in range(5)]
    data = anim.encode(frames, 30, sections=[anim.Section("tail", 2, 4)])

    assert int.from_bytes(data[24:28], "little") == 2  # section_count
    assert b"tail\0" in data


def test_reserved_and_out_of_range_sections_are_rejected():
    frames = [Image.new("RGB", (8, 8), (0, 0, 0))] * 3

    with pytest.raises(ValueError, match="reserved"):
        anim.encode(frames, 30, sections=[anim.Section("default", 0, 2)])

    with pytest.raises(ValueError, match="outside"):
        anim.encode(frames, 30, sections=[anim.Section("past", 1, 9)])


def test_mismatched_frame_sizes_are_rejected():
    frames = [Image.new("RGB", (8, 8)), Image.new("RGB", (8, 9))]

    with pytest.raises(ValueError, match="frame 1 is 8x9"):
        anim.encode(frames, 30)


def test_fps_must_fit_the_single_byte_the_format_gives_it():
    frames = [Image.new("RGB", (8, 8))]

    with pytest.raises(ValueError, match="fps"):
        anim.encode(frames, 300)
