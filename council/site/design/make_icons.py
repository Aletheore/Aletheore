"""Draw the Council mark (a hollow diamond with a small solid diamond inside, dark on the white badge colour) as exact PNGs: apple-touch-icon.png (180) and favicon-32.png (32).
Same geometry as favicon.svg: a 64-unit box, the outline diamond at (32,11) (53,32) (32,53) (11,32) with a 6-unit stroke, the inner one at (32,23) (41,32) (32,41) (23,32)."""
import sys
from pathlib import Path
from PIL import Image, ImageDraw

out = Path(sys.argv[1])


def mark(size: int) -> Image.Image:
    scale = 8  # draw large, then shrink, for clean edges
    big = size * scale
    im = Image.new("RGB", (big, big), "#FFFFFF")
    k = big / 64
    draw = ImageDraw.Draw(im)
    # outline: the outer diamond grown by half the stroke (3 units, measured across the edge), the inner one cut out of it
    outer = [(32 * k, 11 * k - 3 * 2 ** 0.5 * k), (53 * k + 3 * 2 ** 0.5 * k, 32 * k), (32 * k, 53 * k + 3 * 2 ** 0.5 * k), (11 * k - 3 * 2 ** 0.5 * k, 32 * k)]
    inner = [(32 * k, 11 * k + 3 * 2 ** 0.5 * k), (53 * k - 3 * 2 ** 0.5 * k, 32 * k), (32 * k, 53 * k - 3 * 2 ** 0.5 * k), (11 * k + 3 * 2 ** 0.5 * k, 32 * k)]
    draw.polygon(outer, fill="#0F0E0B")
    draw.polygon(inner, fill="#FFFFFF")
    draw.polygon([(32 * k, 23 * k), (41 * k, 32 * k), (32 * k, 41 * k), (23 * k, 32 * k)], fill="#0F0E0B")
    return im.resize((size, size), Image.LANCZOS)


mark(180).save(out / "apple-touch-icon.png", optimize=True)
mark(32).save(out / "favicon-32.png", optimize=True)
print("ok")
