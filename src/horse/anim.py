#!/usr/bin/env python3
"""
Write BUSY Bar `.anim` files — the `bicycle0` container the bar plays natively.

Why this exists: the bar refuses to *draw* any image larger than the panel
(`display/draw` answers 400 "exceeds display dimensions 72x16"), so a sprite
sheet cannot be uploaded and sampled frame by frame.  Nor is there a source
rectangle in the draw API to sample it with.  Animation on the device means
handing it an `.anim` and letting the firmware play it.

The format is defined by the BUSY Bar firmware, which is public but carries no
licence file: `lib/anim_file/anim_file_format.h` for the container and
`lib/toolbox/rle_encode.c` for the compression, in busy-app/busybar-firmware.
This module is written from that description rather than adapted from their
`scripts/seq2anim.py`, and `tests/test_anim.py` pins it by rebuilding a shipped
animation byte for byte.

Layout is a 36-byte header, a sections chunk, then a frames chunk.  All
integers are little-endian and nothing is aligned.
"""

import struct
from dataclasses import dataclass, field

HEADER_FORMAT = "<8sBBBBBHBIIIII"
SIGNATURE = b"bicycle0"

COLOR_MODES = {"rgb888": 0, "gray4": 1, "argb8888": 2}
BLOCK_SIZES = {"rgb888": 3, "gray4": 1, "argb8888": 4}

MAX_BLOCKS = 127
RUN_THRESHOLD = 3

ENCODING_RAW = 0
ENCODING_RLE = 1


def rle_compress(source: bytes, blk_size: int) -> bytes:
    """Compress in the dialect the firmware decoder expects.

    One opcode byte introduces each group.  High bit set means its low 7 bits
    count blocks stored verbatim; high bit clear means they are a repeat count
    for the single block that follows.

    This reproduces the firmware's Python encoder exactly, including one place
    it disagrees with the C encoder beside it: on breaking out of a run the C
    version discards the partial run count and the Python one keeps it, so they
    choose different (equally valid) encodings.  Matching Python is what makes
    the shipped animations reproducible byte for byte, which is how this
    function is tested.
    """
    assert len(source) % blk_size == 0
    out = bytearray()
    i = 0

    while i < len(source):
        block = source[i : i + blk_size]

        run = 0
        for j in range(i, len(source), blk_size):
            if source[j : j + blk_size] != block:
                break
            run += 1
        run = min(run, MAX_BLOCKS)

        if run == 0:
            break

        if run >= RUN_THRESHOLD:
            out.append(run)
            out.extend(block)
            i += run * blk_size
            continue

        # Too short to be worth a run: gather everything up to the next real
        # run and store it verbatim.
        verbatim = 0
        run = 0
        for j in range(i, len(source), blk_size):
            if source[j : j + blk_size] == source[j + blk_size : j + 2 * blk_size]:
                run += 1
                if run > RUN_THRESHOLD:
                    break
            else:
                verbatim += 1 + run
                run = 0
        verbatim = min(verbatim + run, MAX_BLOCKS)

        out.append(0x80 | verbatim)
        out.extend(source[i : i + verbatim * blk_size])
        i += verbatim * blk_size

    return bytes(out)


def rle_decompress(source: bytes, blk_size: int) -> bytes:
    """Inverse of `rle_compress`, for tests and for inspecting device files."""
    out = bytearray()
    i = 0
    while i < len(source):
        opcode = source[i]
        count = opcode & 0x7F
        i += 1
        if opcode & 0x80:
            out.extend(source[i : i + count * blk_size])
            i += count * blk_size
        else:
            out.extend(source[i : i + blk_size] * count)
            i += blk_size
    return bytes(out)


def pack_pixels(rgba: bytes, mode: str) -> bytes:
    """Pack RGBA bytes — `Image.convert("RGBA").tobytes()` — into `mode`.

    Two things here are easy to get wrong and silent when you do.  The mode the
    firmware calls `rgb888` stores **B, G, R**.  And `gray4` reads the **red
    channel only**, two pixels to a byte with the earlier pixel in the high
    nibble, so anything but a greyscale source quietly becomes its red channel.
    """
    out = bytearray()
    if mode == "rgb888":
        for i in range(0, len(rgba), 4):
            out.extend((rgba[i + 2], rgba[i + 1], rgba[i]))
    elif mode == "argb8888":
        for i in range(0, len(rgba), 4):
            out.extend((rgba[i + 2], rgba[i + 1], rgba[i], rgba[i + 3]))
    elif mode == "gray4":
        for i in range(0, len(rgba), 8):
            out.append((rgba[i] & 0xF0) | ((rgba[i + 4] & 0xF0) >> 4))
    else:
        raise ValueError(f"unknown colour mode: {mode!r}")
    return bytes(out)


@dataclass
class Section:
    """A named, inclusive range of display frames, selectable when drawing.

    `busy draw --section gallop` and the firmware's own menu widget both work
    by name.  Sections may overlap.  The name `default` is reserved for the
    one covering every frame, which `encode` adds for you.
    """

    name: str
    start: int
    end: int
    frame_offs: int = 0
    duration_override: int = 0

    def length(self) -> int:
        return 13 + len(self.name) + 1

    def to_bytes(self) -> bytes:
        return (
            struct.pack("<IIIB", self.start, self.end, self.frame_offs, self.duration_override)
            + self.name.encode("utf8")
            + b"\0"
        )


@dataclass
class _FileFrame:
    encoding: int
    duration: int
    encoded: bytes = field(repr=False)

    def length(self) -> int:
        return 4 + len(self.encoded)

    def to_bytes(self) -> bytes:
        return struct.pack("<BBH", self.encoding, self.duration, len(self.encoded)) + self.encoded


def encode(frames, fps: int, color_mode: str = "rgb888", sections=()) -> bytes:
    """Encode same-sized PIL images into the bytes of an `.anim` file.

    Consecutive identical frames collapse into one stored frame held for
    longer, which together with the RLE is why a 16-frame 72x16 animation is
    around 20 KB rather than the 55 KB its raw pixels would take.
    """
    frames = list(frames)
    if not frames:
        raise ValueError("an animation needs at least one frame")
    if color_mode not in COLOR_MODES:
        raise ValueError(f"colour mode must be one of {sorted(COLOR_MODES)}, not {color_mode!r}")
    if not 1 <= fps <= 255:
        raise ValueError(f"fps must fit in a byte (1-255), not {fps}")

    size = frames[0].size
    if not (1 <= size[0] <= 255 and 1 <= size[1] <= 255):
        raise ValueError(f"frame size {size[0]}x{size[1]} does not fit the format's 8-bit fields")

    blk_size = BLOCK_SIZES[color_mode]
    file_frames: list[_FileFrame] = []
    previous = None

    for index, frame in enumerate(frames):
        if frame.size != size:
            raise ValueError(f"frame {index} is {frame.size[0]}x{frame.size[1]}, expected {size[0]}x{size[1]}")

        raw = frame.convert("RGBA").tobytes()
        if raw == previous:
            file_frames[-1].duration += 1
            continue
        previous = raw

        packed = pack_pixels(raw, color_mode)
        compressed = rle_compress(packed, blk_size)
        if len(compressed) < len(packed):
            file_frames.append(_FileFrame(ENCODING_RLE, 1, compressed))
        else:
            file_frames.append(_FileFrame(ENCODING_RAW, 1, packed))

    for section in sections:
        if section.name == "default":
            raise ValueError('the section name "default" is reserved')
        if not 0 <= section.start <= section.end < len(frames):
            raise ValueError(
                f"section {section.name!r} covers {section.start}..{section.end}, "
                f"outside the animation's 0..{len(frames) - 1}"
            )

    all_sections = [Section("default", 0, len(frames) - 1)] + list(sections)
    sections_len = sum(section.length() for section in all_sections)

    # Precomputed start info: for every display frame, the file offset of the
    # frame that shows it and how much of that frame's duration remains.  A
    # section opening midway through a held frame needs the remainder, not the
    # whole duration, or it plays long by however much it missed.
    starts: list[tuple[int, int]] = []
    offset = struct.calcsize(HEADER_FORMAT) + sections_len
    for file_frame in file_frames:
        for remaining in range(file_frame.duration, 0, -1):
            starts.append((offset, remaining))
        offset += file_frame.length()

    for section in all_sections:
        section.frame_offs, section.duration_override = starts[section.start]

    header = struct.pack(
        HEADER_FORMAT,
        SIGNATURE,
        0,  # flags — the loader rejects every non-zero value
        size[0],
        size[1],
        COLOR_MODES[color_mode],
        fps,
        max(len(file_frame.encoded) for file_frame in file_frames),
        0,  # unused
        sections_len,
        sum(file_frame.length() for file_frame in file_frames),
        len(all_sections),
        len(file_frames),
        len(frames),
    )

    return b"".join(
        [
            header,
            *(section.to_bytes() for section in all_sections),
            *(file_frame.to_bytes() for file_frame in file_frames),
        ]
    )
