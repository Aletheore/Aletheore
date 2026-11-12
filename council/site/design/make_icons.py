"""Draw the Council mark (a hollow diamond with a small solid diamond inside, dark on the white badge colour) as exact PNGs and an ICO:
apple-touch-icon.png (180), favicon-48.png, icon-192.png, icon-512.png and favicon.ico (16, 32, 48).
Google wants a square favicon in a multiple of 48 px, crawlable, ideally also at /favicon.ico.
Same geometry as favicon.svg: a 64-unit box, the outline diamond at (32,11) (53,32) (32,53) (11,32) with a 6-unit stroke, the inner one at (32,23) (41,32) (32,41) (23,32).
Usage: python make_icons.py OUTPUT_DIR"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw

out = Path(sys.argv[1])


def mark(size: int) -> Image.Image:
    scale = 8  # draw large, then shrink, for clean edges
    big = size * scale
    k = big / 64
    g = 3 * 2 ** 0.5 * k  # half the stroke, measured along the diagonal
    im = Image.new("RGB", (big, big), "#FFFFFF")
    draw = ImageDraw.Draw(im)
    draw.polygon([(32 * k, 11 * k - g), (53 * k + g, 32 * k), (32 * k, 53 * k + g), (11 * k - g, 32 * k)], fill="#0F0E0B")
    draw.polygon([(32 * k, 11 * k + g), (53 * k - g, 32 * k), (32 * k, 53 * k - g), (11 * k + g, 32 * k)], fill="#FFFFFF")
    draw.polygon([(32 * k, 23 * k), (41 * k, 32 * k), (32 * k, 41 * k), (23 * k, 32 * k)], fill="#0F0E0B")
    return im.resize((size, size), Image.LANCZOS)


for name, px in (("apple-touch-icon.png", 180), ("favicon-48.png", 48), ("icon-192.png", 192), ("icon-512.png", 512)):
    mark(px).save(out / name, optimize=True)
mark(256).save(out / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
print("ok")
