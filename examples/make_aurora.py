"""Generate an original violet night skybox pack."""
from math import exp, sqrt
from pathlib import Path
import random
import sys
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from skybox import PATHS, encode_dds


def paint(face, index):
    size = 384
    rng = random.Random(301 + index)
    directions = [(2 * (i + 0.5) / size - 1) for i in range(size)]
    pixels = []
    for y in directions:
        for x in directions:
            length = sqrt(1 + x * x + y * y)
            altitude = {"up": 1, "dn": -1}.get(face, -y) / length
            horizon = exp(-12 * altitude ** 2)
            aurora = exp(-95 * (altitude - 0.28) ** 2)
            glow = aurora * (0.74 + 0.26 * exp(-3 * x ** 2))
            if face in ("up", "dn"):
                glow = aurora * 0.76
            dither = rng.randrange(3)
            pixels.append((int(10 + 52 * horizon + 36 * glow) + dither,
                           int(8 + 13 * horizon + 66 * glow) + dither,
                           int(28 + 65 * horizon + 94 * glow) + dither))
    sky = Image.new("RGB", (size, size))
    sky.putdata(pixels)
    for _ in range(170 if face == "up" else 85 if face != "dn" else 0):
        x = rng.randrange(5, size - 5)
        y = rng.randrange(5, size - 5)
        bright = rng.randrange(105, 235)
        sky.putpixel((x, y), (bright, bright, min(255, bright + 25)))
    return sky


if __name__ == "__main__":
    destination = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("VioletAurora.zip")
    with ZipFile(destination, "w", ZIP_DEFLATED) as pack:
        for index, (face, path) in enumerate(PATHS.items()):
            pack.writestr(path, encode_dds(paint(face, index)))
