#!/usr/bin/env python3
"""Round only the BIOS GIF corners, preserving all other pixels and timing.

Run after regenerating the BIOS assets. Repeated runs are byte-identical.
"""

from __future__ import annotations

import io
import struct
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

from generate_boot_gif import GifBlock, localized_frame_blocks, parse_gif


RADIUS = 6


def corner_mask(size: tuple[int, int], radius: int = RADIUS) -> Image.Image:
    if radius < 1 or 2 * radius > min(size):
        raise ValueError("corner radius does not fit the image")
    mask = Image.new("L", size, 255)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=0
    )
    return mask


def round_corners(data: bytes, radius: int = RADIUS) -> bytes:
    prefix, blocks, trailer = parse_gif(data)
    size = struct.unpack("<2H", prefix[6:10])
    outside = corner_mask(size, radius)
    points = [
        (x, y)
        for y in range(size[1])
        for x in range(size[0])
        if outside.getpixel((x, y))
    ]
    result = list(blocks)
    control_index: int | None = None
    first_image = True
    for index, block in enumerate(blocks):
        if block.kind == "extension" and block.label == 0xF9:
            control_index = index
            continue
        if block.kind != "image":
            continue
        if control_index is None:
            raise ValueError("BIOS frame has no graphic control extension")
        control = blocks[control_index]
        if (control.raw[3] >> 2) & 7 != 1:
            raise ValueError("only persistent BIOS frames are supported")
        x, y, width, height = struct.unpack("<4H", block.raw[1:9])
        if first_image and (x, y, width, height) != (0, 0, *size):
            raise ValueError("first BIOS frame must cover the full canvas")
        first_image = False
        affected = [
            (px, py) for px, py in points
            if x <= px < x + width and y <= py < y + height
        ]
        if affected:
            # The existing BIOS has two full-canvas frames. Leave every delta
            # frame's compressed stream and control metadata untouched.
            if (x, y, width, height) != (0, 0, *size):
                raise ValueError("a partial BIOS frame intersects the corners")
            single_frame = prefix + control.raw + block.raw + trailer
            with Image.open(io.BytesIO(single_frame)) as source:
                frame = source.copy()
            if frame.mode != "P":
                raise ValueError("BIOS frame must use an indexed palette")
            if control.raw[3] & 1:
                transparent = control.raw[6]
            else:
                used = set(frame.tobytes())
                slots = len(frame.getpalette()) // 3
                transparent = next(
                    (slot for slot in range(slots) if slot not in used), None
                )
                if transparent is None:
                    raise ValueError("no unused palette entry for transparent corners")
            for px, py in affected:
                frame.putpixel((px, py), transparent)
            encoded = io.BytesIO()
            frame.save(
                encoded, format="GIF", optimize=False, interlace=False,
                transparency=transparent, disposal=1,
            )
            image_blocks = [
                b for b in localized_frame_blocks(encoded.getvalue())
                if b.kind == "image"
            ]
            if len(image_blocks) != 1:
                raise ValueError("corner processing produced more than one frame")
            result[index] = image_blocks[0]
            raw = bytearray(control.raw)
            raw[3] |= 1
            raw[6] = transparent
            result[control_index] = GifBlock("extension", bytes(raw), 0xF9)
        control_index = None
    return prefix + b"".join(block.raw for block in result) + trailer


def validate(original: bytes, rounded: bytes, radius: int = RADIUS) -> int:
    with (
        Image.open(io.BytesIO(original)) as before,
        Image.open(io.BytesIO(rounded)) as after,
    ):
        if before.size != after.size or before.n_frames != after.n_frames:
            raise ValueError("corner processing changed the animation geometry")
        if before.info.get("loop") != after.info.get("loop"):
            raise ValueError("corner processing changed the loop behavior")
        outside = corner_mask(before.size, radius)
        inside = ImageChops.invert(outside)
        for index in range(before.n_frames):
            before.seek(index)
            after.seek(index)
            if before.info.get("duration") != after.info.get("duration"):
                raise ValueError(f"frame {index} timing changed")
            old, new = before.convert("RGBA"), after.convert("RGBA")
            pixel_difference = ImageChops.difference(
                old.convert("RGB"), new.convert("RGB")
            )
            if ImageChops.multiply(pixel_difference, inside.convert("RGB")).getbbox():
                raise ValueError(f"frame {index} pixels outside the corners changed")
            alpha = new.getchannel("A")
            if ImageChops.multiply(alpha, outside).getbbox():
                raise ValueError(f"frame {index} corners are not transparent")
            opacity_difference = ImageChops.difference(old.getchannel("A"), alpha)
            if ImageChops.multiply(opacity_difference, inside).getbbox():
                raise ValueError(f"frame {index} opacity outside the corners changed")
        return before.n_frames


def main() -> None:
    assets = Path(__file__).resolve().parent.parent / "assets"
    prepared = []
    for name in ("boot.gif", "boot-light.gif"):
        path = assets / name
        original = path.read_bytes()
        rounded = round_corners(original)
        frames = validate(original, rounded)
        prepared.append((path, rounded, frames))
    # Validate both themes before replacing either original file.
    for path, rounded, frames in prepared:
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=assets, prefix=f".{path.name}.", suffix=".tmp", delete=False
            ) as temporary:
                temporary.write(rounded)
                temporary_path = Path(temporary.name)
            temporary_path.replace(path)
            temporary_path = None
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        print(f"{path.name}: radius {RADIUS}px; {frames} frames verified")


if __name__ == "__main__":
    main()
