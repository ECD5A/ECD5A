#!/usr/bin/env python3
"""Render a source floppy GIF as an animated ASCII GIF."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageSequence


ASCII = " .:-=+*#%@"
FONT_CANDIDATES = (
    r"C:\Windows\Fonts\consola.ttf",
    r"C:\Windows\Fonts\lucon.ttf",
    r"C:\Windows\Fonts\cour.ttf",
)


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def corner_background(frame: Image.Image) -> tuple[int, int, int]:
    image = frame.convert("RGB")
    w, h = image.size
    points = [
        image.getpixel((0, 0)),
        image.getpixel((w - 1, 0)),
        image.getpixel((0, h - 1)),
        image.getpixel((w - 1, h - 1)),
    ]
    return tuple(sum(point[i] for point in points) // len(points) for i in range(3))


def distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def boost_color(color: tuple[int, int, int]) -> tuple[int, int, int]:
    r, g, b = color
    # Keep the source blue/steel look, but push it toward readable old-screen cyan.
    r = int(min(210, r * 0.72 + 18))
    g = int(min(235, g * 0.92 + 30))
    b = int(min(255, b * 1.15 + 45))
    return (r, g, b)


def block_average(image: Image.Image, x0: int, y0: int, x1: int, y1: int) -> tuple[int, int, int]:
    crop = image.crop((x0, y0, x1, y1)).resize((1, 1), Image.Resampling.BILINEAR)
    return crop.getpixel((0, 0))


def content_bbox(
    frames: list[Image.Image],
    background: tuple[int, int, int],
    threshold: float,
    padding: int,
) -> tuple[int, int, int, int]:
    union: tuple[int, int, int, int] | None = None

    for frame in frames:
        image = frame.convert("RGB")
        mask = Image.new("L", image.size, 0)
        pixels = mask.load()
        src = image.load()

        for y in range(image.height):
            for x in range(image.width):
                if distance(src[x, y], background) >= threshold:
                    pixels[x, y] = 255

        bbox = mask.getbbox()
        if bbox is None:
            continue

        if union is None:
            union = bbox
        else:
            union = (
                min(union[0], bbox[0]),
                min(union[1], bbox[1]),
                max(union[2], bbox[2]),
                max(union[3], bbox[3]),
            )

    if union is None:
        return (0, 0, frames[0].width, frames[0].height)

    left = max(0, union[0] - padding)
    top = max(0, union[1] - padding)
    right = min(frames[0].width, union[2] + padding)
    bottom = min(frames[0].height, union[3] + padding)
    return (left, top, right, bottom)


def build_ascii_frame(
    source: Image.Image,
    background: tuple[int, int, int],
    columns: int,
    rows: int,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    cell_w: int,
    cell_h: int,
    threshold: float,
    canvas_size: tuple[int, int],
) -> Image.Image:
    src = source.convert("RGB")
    canvas = Image.new("RGB", canvas_size, (2, 4, 6))
    draw = ImageDraw.Draw(canvas)

    start_x = (canvas_size[0] - columns * cell_w) // 2
    start_y = (canvas_size[1] - rows * cell_h) // 2

    for row in range(rows):
        y0 = int(row * src.height / rows)
        y1 = max(y0 + 1, int((row + 1) * src.height / rows))

        for col in range(columns):
            x0 = int(col * src.width / columns)
            x1 = max(x0 + 1, int((col + 1) * src.width / columns))
            avg = block_average(src, x0, y0, x1, y1)
            diff = distance(avg, background)

            if diff < threshold:
                continue

            density = min(1.0, max(0.0, (diff - threshold) / 170.0))
            char = ASCII[max(2, min(len(ASCII) - 1, round(density * (len(ASCII) - 1))))]
            color = boost_color(avg)
            draw.text(
                (start_x + col * cell_w, start_y + row * cell_h),
                char,
                font=font,
                fill=color,
            )

    return canvas


def make_ascii_gif(args: argparse.Namespace) -> None:
    source_path = Path(args.source)
    out_path = Path(args.out)

    with Image.open(source_path) as gif:
        frames = [frame.convert("RGBA") for frame in ImageSequence.Iterator(gif)]

    if not frames:
        raise RuntimeError(f"No frames found in {source_path}")

    step = max(1, len(frames) // args.frames)
    selected = frames[::step][: args.frames]
    background = corner_background(selected[0])
    if args.crop:
        crop_box = content_bbox(selected, background, args.threshold, args.crop_padding)
        selected = [frame.crop(crop_box) for frame in selected]
    else:
        crop_box = (0, 0, selected[0].width, selected[0].height)

    font = load_font(args.font_size)
    probe = Image.new("RGB", (64, 64))
    probe_draw = ImageDraw.Draw(probe)
    glyph_bbox = probe_draw.textbbox((0, 0), "@", font=font)
    cell_w = max(1, glyph_bbox[2] - glyph_bbox[0])
    cell_h = max(1, args.line_height)

    canvas_size = (args.width, args.height)
    ascii_frames = [
        build_ascii_frame(
            frame,
            background,
            args.columns,
            args.rows,
            font,
            cell_w,
            cell_h,
            args.threshold,
            canvas_size,
        )
        for frame in selected
    ]

    loop_frames = ascii_frames
    durations = [args.delay_ms] * len(loop_frames)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    loop_frames[0].save(
        out_path,
        save_all=True,
        append_images=loop_frames[1:],
        duration=durations,
        loop=0,
        optimize=False,
    )
    print(f"wrote {out_path} from {source_path} ({len(loop_frames)} frames, crop={crop_box})")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert a floppy GIF into animated ASCII GIF.")
    parser.add_argument("--source", default="assets/floppy-blue.gif")
    parser.add_argument("--out", default="assets/floppy-ascii.gif")
    parser.add_argument("--columns", type=int, default=82)
    parser.add_argument("--rows", type=int, default=48)
    parser.add_argument("--frames", type=int, default=29)
    parser.add_argument("--width", type=int, default=680)
    parser.add_argument("--height", type=int, default=470)
    parser.add_argument("--font-size", type=int, default=10)
    parser.add_argument("--line-height", type=int, default=9)
    parser.add_argument("--threshold", type=float, default=20.0)
    parser.add_argument("--delay-ms", type=int, default=72)
    parser.add_argument("--crop-padding", type=int, default=18)
    parser.add_argument("--crop", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    make_ascii_gif(parse_args())
