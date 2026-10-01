#!/usr/bin/env python3
"""Recolor the existing BIOS GIF for light themes without changing its frames."""

from __future__ import annotations

import argparse
import colorsys
import io
import tempfile
from functools import lru_cache
from pathlib import Path

from PIL import Image

from generate_boot_gif import parse_gif


PAPER = (246, 248, 250)  # Matches the light Conway panel.
INK = (36, 41, 47)


def blend(
    background: tuple[int, int, int],
    foreground: tuple[int, int, int],
    amount: float,
) -> tuple[int, int, int]:
    return tuple(
        round(back + (front - back) * amount)
        for back, front in zip(background, foreground)
    )


@lru_cache(maxsize=None)
def light_color(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    brightest = max(rgb)
    if brightest == 0:
        return PAPER

    opacity = min(1.0, brightest / 170)
    if brightest - min(rgb) <= 12:
        return blend(PAPER, INK, opacity)

    hue, saturation, _ = colorsys.rgb_to_hsv(*(channel / 255 for channel in rgb))
    print_color = colorsys.hsv_to_rgb(
        hue, max(0.45, min(saturation, 0.75)), 0.49
    )
    foreground = tuple(round(channel * 255) for channel in print_color)
    return blend(PAPER, foreground, opacity)


def recolor_palette(palette: bytes) -> bytes:
    if len(palette) % 3:
        raise ValueError("invalid GIF palette length")
    return bytes(
        channel
        for offset in range(0, len(palette), 3)
        for channel in light_color(tuple(palette[offset : offset + 3]))
    )


def recolor_gif(data: bytes) -> bytes:
    prefix, blocks, trailer = parse_gif(data)
    output = bytearray(prefix[:13])
    output += recolor_palette(prefix[13:])

    for block in blocks:
        raw = block.raw
        if block.kind == "image" and raw[9] & 0x80:
            palette_size = 3 * (2 ** ((raw[9] & 0x07) + 1))
            raw = (
                raw[:10]
                + recolor_palette(raw[10 : 10 + palette_size])
                + raw[10 + palette_size :]
            )
        output += raw

    output += trailer
    return bytes(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("assets/boot.gif"))
    parser.add_argument("--output", type=Path, default=Path("assets/boot-light.gif"))
    args = parser.parse_args()
    if args.source.resolve() == args.output.resolve():
        raise ValueError("light GIF output must not overwrite the dark source")

    source = args.source.read_bytes()
    result = recolor_gif(source)
    if len(result) != len(source):
        raise ValueError("palette recoloring changed the GIF byte length")
    with Image.open(io.BytesIO(source)) as dark, Image.open(io.BytesIO(result)) as light:
        if dark.size != light.size or dark.n_frames != light.n_frames:
            raise ValueError("light GIF changed animation geometry")
        if dark.info.get("loop") != light.info.get("loop"):
            raise ValueError("light GIF changed looping")
        for index in range(dark.n_frames):
            dark.seek(index)
            light.seek(index)
            if dark.info.get("duration") != light.info.get("duration"):
                raise ValueError(f"light GIF changed frame {index} duration")
        size = light.size
        frames = light.n_frames

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=args.output.parent,
            prefix=f".{args.output.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(result)
            temporary_path = Path(temporary.name)
        temporary_path.replace(args.output)
        temporary_path = None
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    print(f"wrote {args.output}: {size[0]}x{size[1]}, {frames} frames")


if __name__ == "__main__":
    main()
