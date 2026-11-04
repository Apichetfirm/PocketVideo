#!/usr/bin/env python3
"""Generate an original PocketVideo application mark for Windows and macOS.

Source-only, deterministic build step. No downloaded images, icon fonts, or
third-party brand assets. The desktop window uses the same geometric mark.
"""
from __future__ import annotations

import platform
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets"


def render_icon() -> Image.Image:
    n = 1024
    gradient = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    draw = ImageDraw.Draw(gradient)
    top, bottom = (78, 138, 255), (108, 66, 215)
    for y in range(n):
        k = y / max(n - 1, 1)
        rgb = tuple(round(a * (1 - k) + b * k) for a, b in zip(top, bottom))
        draw.line((0, y, n, y), fill=(*rgb, 255))
    mask = Image.new("L", (n, n))
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((41, 41, 983, 983), radius=246, fill=255)
    canvas = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    canvas.paste(gradient, (0, 0), mask)
    out = ImageDraw.Draw(canvas)
    out.rounded_rectangle((205, 246, 819, 778), radius=123, fill="white")
    out.polygon(((440, 358), (440, 665), (686, 512)), fill=(66, 108, 232, 255))
    out.rounded_rectangle((318, 713, 702, 743), radius=15, fill=(220, 229, 255, 255))
    return canvas


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    image = render_icon()
    image.resize((256, 256), Image.Resampling.LANCZOS).save(OUT / "pocketvideo.png")
    sizes = (16, 24, 32, 48, 64, 128, 256)
    image.save(OUT / "pocketvideo.ico", format="ICO", sizes=[(x, x) for x in sizes])

    if platform.system() == "Darwin":
        iconset = OUT / "pocketvideo.iconset"
        iconset.mkdir(parents=True, exist_ok=True)
        for n in (16, 32, 128, 256, 512):
            image.resize((n, n), Image.Resampling.LANCZOS).save(
                iconset / f"icon_{n}x{n}.png"
            )
            image.resize((n * 2, n * 2), Image.Resampling.LANCZOS).save(
                iconset / f"icon_{n}x{n}@2x.png"
            )
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(OUT / "pocketvideo.icns")],
            check=True,
        )
    print(f"Generated PocketVideo icon assets in {OUT}")


if __name__ == "__main__":
    main()
