"""Draw the Council mark (dark diamond on the white badge colour) as exact PNGs: apple-touch-icon.png (180) and favicon-32.png (32).
Same geometry as favicon.svg: a 64-unit box with the diamond at (32,11) (53,32) (32,53) (11,32)."""
import sys
from pathlib import Path
from PIL import Image, ImageDraw

out = Path(sys.argv[1])


def mark(size: int) -> Image.Image:
    scale = 8  # draw large, then shrink, for clean edges
    big = size * scale
    im = Image.new("RGB", (big, big), "#FFFFFF")
    k = big / 64
    pts = [(32 * k, 11 * k), (53 * k, 32 * k), (32 * k, 53 * k), (11 * k, 32 * k)]
    ImageDraw.Draw(im).polygon(pts, fill="#0F0E0B")
    return im.resize((size, size), Image.LANCZOS)


mark(180).save(out / "apple-touch-icon.png", optimize=True)
mark(32).save(out / "favicon-32.png", optimize=True)
print("ok")
