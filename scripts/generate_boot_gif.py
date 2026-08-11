#!/usr/bin/env python3
"""Build the one-shot BIOS and VGA profile POST animation.

The legacy asset contains three memory-counting passes and was widened from
720 px to 920 px with empty sidebars.  The first run removes the two duplicate
passes, restores the original 720x400 BIOS viewport, and renders the profile
POST on the same 80-column VGA grid.  Re-running the generator is idempotent.
The output deliberately omits the NETSCAPE loop extension.
"""

from __future__ import annotations

import argparse
import base64
import io
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops


LEGACY_BASE_FRAME_COUNT = 233
LEGACY_DUPLICATE_START = 25
LEGACY_DUPLICATE_END = 52
LEGACY_SELECTED_FRAME_COUNT = 206
BASE_FRAME_COUNT = 203
BASE_DURATION_MS = 12_300
SOURCE_SIZE = (720, 400)
LEGACY_OUTPUT_SIZE = (920, 400)
LEGACY_SOURCE_X = (LEGACY_OUTPUT_SIZE[0] - SOURCE_SIZE[0]) // 2
OUTPUT_SIZE = SOURCE_SIZE

CELL_WIDTH = 9
CELL_HEIGHT = 16
GLYPH_WIDTH = 8
TEXT_COLUMNS = 80
PROFILE_X = (OUTPUT_SIZE[0] - TEXT_COLUMNS * CELL_WIDTH) // 2
VALUE_COLUMN = 22
OK_COLUMN = 74
OK_TEXT = "[ OK ]"
TEXT_COLOR = (170, 170, 170)
OK_COLOR = (169, 216, 169)
BACKGROUND = (0, 0, 0)

LOADING_ROW = 1
MODULE_ROWS = (5, 8, 11, 14, 17)
COLLABORATION_ROW = 20
CTA_ROW = 23
CTA_TEXT = "> LET'S BUILD SOMETHING REAL"
TYPE_DELAY_MS = 60
WORD_PAUSE_MS = 160

# ASCII 32..126 from the same IBM-VGA-style 8x16 raster family visible in
# boot.gif.  Each decompressed byte is one eight-pixel glyph row (MSB first).
VGA_FONT_ZLIB_B64 = (
    "eNptU71q5DAQ9l0glYqUKpZwxT2AShfmuEcRJqgSx8LBosKEe4A8QB7kHkBFUDW4NCEsJpWrcGwV"
    "zGGcG9kaSd69b9eLv53RaL75KYpLCGOMEAV+VyilvuZ2rWe9PMFdNNYaxsZ4oHgCLiQ8BLZ/ft7/"
    "7eF4oPhCyDweQ2cpBWeB+1fm/4weqvqoVHaC83vOz9MuyyzqfCmrLOnt87WPbn8F2ri2794H1wR+"
    "I07CYwrcAHh/SJyxkjEwlP+u0jAzdhv4ZK0dM3spkQNEPjvnE4jlwvhv5pjsyKHhXOw3qaf0iXOR"
    "6mez+iHu8RPhTddZPY2Xk/W3ca7ve2ub2H9MYMKH9OAANPiQ/ko5VOhUFfiolYceSV8t75o7WVMP"
    "ZlV7Lk/5+dapb4FDuI7uM2IF1eMWp4Fl9RuU1kZrNQR+wumRtVJ0n/szz51DEHfD+9y3kZfaG50u"
    "c30y5bfY276hhi521Dik/tg9T/lMv7f5QsAZNyLm41zXadqfYrGZpH91TvHm9hGbVzvSV4X6UP2L"
    "70uHrz6Rna0I9hvUU/wXH2EdL1apMKwBoH19ldK3Xx0zO1gb9e2Y907+i31K9lJLOWb1RRz8iNOG"
    "YHx9UKm+uNqGI2iEGVslQfJXOACpH3xFNvJTh8hUj5DNV1ixmB/iqJYZOOX5QRyAonj7cdjkb0Dy"
    "dP6LnFCezhYUzuqx6f8yAn4AMq7LTZd8OhXj8cbpgcsn2r+dWPZd7GK9sGLLT+gXdtRv+GvgP1/a"
    "GPkfFZ5ZRQ=="
)


MODULES = (
    ("SYSTEMS", "C / C++ / Rust / Linux"),
    ("SOFTWARE", "Python / TypeScript / Solidity"),
    ("CRYPTOGRAPHY", "ECC / Hash Functions / Digital Signatures"),
    ("AI & AUTOMATION", "Agent Systems / MCP / LLM Workflows"),
    ("BLOCKCHAIN SYSTEMS", "UTXO / Account Models / EVM / L1 / L2"),
)


@dataclass(frozen=True)
class GifBlock:
    kind: str
    raw: bytes
    label: int | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("assets/boot.gif"))
    parser.add_argument("--output", type=Path, default=Path("assets/boot.gif"))
    parser.add_argument(
        "--preview",
        type=Path,
        help="Optional PNG path for the fully composited final frame.",
    )
    return parser.parse_args()


def parse_gif(data: bytes) -> tuple[bytes, list[GifBlock], bytes]:
    if data[:6] not in (b"GIF87a", b"GIF89a"):
        raise ValueError("source is not a GIF")
    if len(data) < 14:
        raise ValueError("truncated GIF")

    packed = data[10]
    palette_size = 3 * (2 ** ((packed & 0x07) + 1)) if packed & 0x80 else 0
    prefix_end = 13 + palette_size
    prefix = data[:prefix_end]
    blocks: list[GifBlock] = []
    pos = prefix_end

    while pos < len(data):
        marker = data[pos]
        if marker == 0x3B:
            return prefix, blocks, data[pos : pos + 1]

        if marker == 0x21:
            start = pos
            if pos + 2 > len(data):
                raise ValueError("truncated GIF extension")
            label = data[pos + 1]
            pos += 2
            while True:
                if pos >= len(data):
                    raise ValueError("truncated GIF extension data")
                size = data[pos]
                pos += 1
                if size == 0:
                    break
                pos += size
                if pos > len(data):
                    raise ValueError("truncated GIF extension sub-block")
            blocks.append(GifBlock("extension", data[start:pos], label))
            continue

        if marker == 0x2C:
            start = pos
            if pos + 10 > len(data):
                raise ValueError("truncated GIF image descriptor")
            descriptor_packed = data[pos + 9]
            pos += 10
            if descriptor_packed & 0x80:
                local_size = 3 * (2 ** ((descriptor_packed & 0x07) + 1))
                pos += local_size
            if pos >= len(data):
                raise ValueError("missing GIF LZW code size")
            pos += 1
            while True:
                if pos >= len(data):
                    raise ValueError("truncated GIF image data")
                size = data[pos]
                pos += 1
                if size == 0:
                    break
                pos += size
                if pos > len(data):
                    raise ValueError("truncated GIF image sub-block")
            blocks.append(GifBlock("image", data[start:pos]))
            continue

        raise ValueError(f"unexpected GIF block marker 0x{marker:02x} at {pos}")

    raise ValueError("GIF trailer is missing")


def is_loop_extension(block: GifBlock) -> bool:
    return (
        block.kind == "extension"
        and block.label == 0xFF
        and b"NETSCAPE2.0" in block.raw
    )

def encode_frames(frames: list[Image.Image], durations: list[int]) -> bytes:
    if not frames or len(frames) != len(durations):
        raise ValueError("frames and durations must be non-empty and equal length")
    buffer = io.BytesIO()
    frames[0].save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        disposal=1,
        optimize=True,
    )
    return buffer.getvalue()


def localized_frame_blocks(encoded: bytes) -> list[GifBlock]:
    """Make every encoded image independent of its temporary global palette."""
    prefix, blocks, _ = parse_gif(encoded)
    packed = prefix[10]
    if not packed & 0x80:
        raise ValueError("encoded helper GIF has no global palette")
    size_bits = packed & 0x07
    palette_size = 3 * (2 ** (size_bits + 1))
    palette = prefix[13 : 13 + palette_size]
    result: list[GifBlock] = []

    for block in blocks:
        if block.kind == "extension":
            if block.label == 0xF9:
                result.append(block)
            continue

        raw = block.raw
        descriptor = bytearray(raw[:10])
        if not descriptor[9] & 0x80:
            descriptor[9] = (descriptor[9] & 0x78) | 0x80 | size_bits
            raw = bytes(descriptor) + palette + raw[10:]
        result.append(GifBlock("image", raw))

    return result


def base_frame_indices(source: Image.Image) -> list[int]:
    """Select the normalized BIOS frames from a legacy or rebuilt asset."""
    if source.size == LEGACY_OUTPUT_SIZE:
        if source.n_frames < LEGACY_BASE_FRAME_COUNT:
            raise ValueError(
                f"legacy source has {source.n_frames} frames; "
                f"need at least {LEGACY_BASE_FRAME_COUNT}"
            )
        return [
            *range(LEGACY_DUPLICATE_START),
            *range(LEGACY_DUPLICATE_END, LEGACY_BASE_FRAME_COUNT),
        ]

    if source.size == OUTPUT_SIZE:
        if source.n_frames < BASE_FRAME_COUNT:
            raise ValueError(
                f"normalized source has {source.n_frames} frames; "
                f"need at least {BASE_FRAME_COUNT}"
            )
        return list(range(BASE_FRAME_COUNT))

    raise ValueError(f"unsupported source canvas: {source.size}")


def normalized_base_frame(source: Image.Image, source_index: int) -> Image.Image:
    source.seek(source_index)
    frame = source.convert("RGB")
    if source.size == LEGACY_OUTPUT_SIZE:
        return frame.crop(
            (
                LEGACY_SOURCE_X,
                0,
                LEGACY_SOURCE_X + SOURCE_SIZE[0],
                SOURCE_SIZE[1],
            )
        )
    return frame.copy()


def build_base_stream(
    source: Image.Image,
) -> tuple[bytes, list[Image.Image], list[int]]:
    """Return the normalized BIOS stream and its composited frame states."""
    frames: list[Image.Image] = []
    durations: list[int] = []
    for source_index in base_frame_indices(source):
        frames.append(normalized_base_frame(source, source_index))
        source.seek(source_index)
        durations.append(source.info.get("duration", 0))

    if len(frames) != LEGACY_SELECTED_FRAME_COUNT and source.size == LEGACY_OUTPUT_SIZE:
        raise ValueError(
            f"selected {len(frames)} legacy frames; "
            f"expected {LEGACY_SELECTED_FRAME_COUNT}"
        )

    encoded = encode_frames(frames, durations)
    encoded_frames: list[Image.Image] = []
    encoded_durations: list[int] = []
    with Image.open(io.BytesIO(encoded)) as normalized:
        if normalized.n_frames != BASE_FRAME_COUNT:
            raise ValueError(
                f"normalized base has {normalized.n_frames} frames; "
                f"expected {BASE_FRAME_COUNT}"
            )
        for index in range(normalized.n_frames):
            normalized.seek(index)
            encoded_frames.append(normalized.convert("RGB").copy())
            encoded_durations.append(normalized.info.get("duration", 0))

    prefix, blocks, _ = parse_gif(encoded)
    output = bytearray(prefix)
    for block in blocks:
        if not is_loop_extension(block):
            output += block.raw
    return bytes(output), encoded_frames, encoded_durations


def load_font() -> dict[str, Image.Image]:
    packed = base64.b64decode(VGA_FONT_ZLIB_B64)
    rows = zlib.decompress(packed)
    expected = (126 - 32 + 1) * CELL_HEIGHT
    if len(rows) != expected:
        raise ValueError(f"invalid embedded VGA font length: {len(rows)}")

    font: dict[str, Image.Image] = {}
    for codepoint in range(32, 127):
        offset = (codepoint - 32) * CELL_HEIGHT
        glyph = Image.new("1", (GLYPH_WIDTH, CELL_HEIGHT))
        pixels = glyph.load()
        for y, byte in enumerate(rows[offset : offset + CELL_HEIGHT]):
            for x in range(GLYPH_WIDTH):
                pixels[x, y] = bool(byte & (0x80 >> x))
        font[chr(codepoint)] = glyph
    return font


def module_body(category: str, value: str) -> str:
    dots = "." * (VALUE_COLUMN - len(category) - 2)
    body = f"{category} {dots} {value}"
    if body.index(value) != VALUE_COLUMN:
        raise ValueError(f"value column drift in {body!r}")
    return body


def line_with_ok(body: str) -> str:
    if len(body) > OK_COLUMN:
        raise ValueError(f"line reaches the OK column: {body!r}")
    line = body.ljust(OK_COLUMN) + OK_TEXT
    if len(line) > TEXT_COLUMNS or line.index(OK_TEXT) != OK_COLUMN:
        raise ValueError(f"line does not fit the VGA grid: {line!r}")
    return line


def draw_text(
    frame: Image.Image,
    font: dict[str, Image.Image],
    text: str,
    *,
    column: int = 0,
    row: int,
    color: tuple[int, int, int] = TEXT_COLOR,
) -> None:
    for index, character in enumerate(text):
        if character == " ":
            continue
        try:
            glyph = font[character]
        except KeyError as exc:
            raise ValueError(f"character has no VGA glyph: {character!r}") from exc
        x = PROFILE_X + (column + index) * CELL_WIDTH
        y = row * CELL_HEIGHT
        ink = Image.new("RGB", glyph.size, color)
        frame.paste(ink, (x, y), glyph)


def make_continuation(source: Image.Image) -> tuple[list[Image.Image], list[int]]:
    base = normalized_base_frame(source, base_frame_indices(source)[-1])
    if base.size != OUTPUT_SIZE:
        raise ValueError(f"unsupported composited base size: {base.size}")

    # The BIOS/DOS sequence has completed.  Clear it like a text-mode CLS so
    # the profile owns the full terminal instead of being appended at the
    # bottom of the old boot log.
    screen = Image.new("RGB", OUTPUT_SIZE, BACKGROUND)

    font = load_font()
    frames: list[Image.Image] = []
    durations: list[int] = []

    def append_state(duration: int) -> None:
        frames.append(screen.copy())
        durations.append(duration)

    def add_status_line(
        body: str,
        row: int,
        body_ms: int = 200,
        ok_ms: int = 260,
    ) -> None:
        line_with_ok(body)
        draw_text(screen, font, body, row=row)
        append_state(body_ms)
        draw_text(
            screen,
            font,
            OK_TEXT,
            column=OK_COLUMN,
            row=row,
            color=OK_COLOR,
        )
        append_state(ok_ms)

    add_status_line("LOADING PROFILE", LOADING_ROW)
    for row, (category, value) in zip(MODULE_ROWS, MODULES, strict=True):
        add_status_line(module_body(category, value), row)

    add_status_line("COLLABORATION ........ OPEN", COLLABORATION_ROW)

    if len(CTA_TEXT) + 1 > TEXT_COLUMNS:
        raise ValueError(f"CTA does not fit the VGA grid: {CTA_TEXT!r}")
    for column, character in enumerate(CTA_TEXT):
        if character == " ":
            continue
        draw_text(screen, font, character, column=column, row=CTA_ROW)
        duration = (
            WORD_PAUSE_MS
            if column + 1 < len(CTA_TEXT) and CTA_TEXT[column + 1] == " "
            else TYPE_DELAY_MS
        )
        append_state(duration)

    cursor_column = len(CTA_TEXT)
    draw_text(screen, font, "_", column=cursor_column, row=CTA_ROW)
    append_state(240)

    cursor_x = PROFILE_X + cursor_column * CELL_WIDTH
    cursor_y = CTA_ROW * CELL_HEIGHT
    for blink_index in range(3):
        off = screen.copy()
        off.paste(
            BACKGROUND,
            (cursor_x, cursor_y, cursor_x + CELL_WIDTH, cursor_y + CELL_HEIGHT),
        )
        screen = off
        append_state(180)
        draw_text(screen, font, "_", column=cursor_column, row=CTA_ROW)
        append_state(5_000 if blink_index == 2 else 220)

    return frames, durations


def source_base_duration(source: Image.Image) -> int:
    total = 0
    for source_index in base_frame_indices(source):
        source.seek(source_index)
        total += source.info.get("duration", 0)
    return total


def validate_output(
    path: Path,
    base_frames: list[Image.Image],
    base_durations: list[int],
    continuation_frames: list[Image.Image],
    continuation_durations: list[int],
) -> tuple[int, int]:
    with Image.open(path) as result:
        if result.size != OUTPUT_SIZE:
            raise ValueError(f"wrong output canvas: {result.size}")
        if "loop" in result.info:
            raise ValueError(f"GIF still has loop metadata: {result.info['loop']}")

        expected_frames = len(base_frames) + len(continuation_frames)
        if result.n_frames != expected_frames:
            raise ValueError(
                f"wrong frame count: {result.n_frames}, expected {expected_frames}"
            )

        total_duration = 0
        for index in range(result.n_frames):
            result.seek(index)
            total_duration += result.info.get("duration", 0)

        expected_duration = sum(base_durations) + sum(continuation_durations)
        if total_duration != expected_duration:
            raise ValueError(
                f"wrong duration: {total_duration} ms, expected {expected_duration} ms"
            )

        for index, expected in enumerate(base_frames):
            result.seek(index)
            if ImageChops.difference(expected, result.convert("RGB")).getbbox():
                raise ValueError(f"base frame {index} changed visually")

        for offset, expected in enumerate(continuation_frames):
            index = len(base_frames) + offset
            result.seek(index)
            if ImageChops.difference(expected, result.convert("RGB")).getbbox():
                raise ValueError(
                    f"continuation frame {index} differs from its rendered state"
                )

    return expected_frames, total_duration


def main() -> None:
    args = parse_args()
    source_data = args.source.read_bytes()
    before_size = len(source_data)

    with Image.open(io.BytesIO(source_data)) as source:
        base_duration = source_base_duration(source)
        if base_duration != BASE_DURATION_MS:
            raise ValueError(
                f"base duration changed: {base_duration} ms, expected {BASE_DURATION_MS} ms"
            )

        base_stream, base_frames, base_durations = build_base_stream(source)
        continuation_frames, continuation_durations = make_continuation(source)
        continuation_data = encode_frames(continuation_frames, continuation_durations)
        continuation_blocks = localized_frame_blocks(continuation_data)
        output_data = bytearray(base_stream)
        for block in continuation_blocks:
            output_data += block.raw
        output_data.append(0x3B)

        args.output.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=args.output.parent,
                prefix=f".{args.output.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(output_data)
                temp_path = Path(temporary.name)

            frame_count, total_duration = validate_output(
                temp_path,
                base_frames,
                base_durations,
                continuation_frames,
                continuation_durations,
            )
            temp_path.replace(args.output)
            temp_path = None
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    if args.preview:
        args.preview.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(args.output) as result:
            result.seek(result.n_frames - 1)
            result.convert("RGB").save(args.preview)

    print(f"wrote: {args.output}")
    print(f"canvas: {OUTPUT_SIZE[0]}x{OUTPUT_SIZE[1]}")
    print(f"frames: {frame_count} ({BASE_FRAME_COUNT} base + {frame_count - BASE_FRAME_COUNT} new)")
    print(f"duration: {total_duration} ms ({BASE_DURATION_MS} base + {total_duration - BASE_DURATION_MS} new)")
    print("loop: absent (single play, then hold final frame)")
    print(f"size: {before_size} -> {args.output.stat().st_size} bytes")
    if args.preview:
        print(f"preview: {args.preview}")


if __name__ == "__main__":
    main()
