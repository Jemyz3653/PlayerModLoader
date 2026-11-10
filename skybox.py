"""Build the six local default sky textures used by Roblox Player."""
from io import BytesIO
import struct

from PIL import Image, ImageOps

FACES = ("bk", "dn", "ft", "lf", "rt", "up")
PATHS = {face: f"PlatformContent/pc/textures/sky/sky512_{face}.tex" for face in FACES}
SIZE = 1024
MIP_SIZES = tuple(SIZE >> level for level in range(SIZE.bit_length()))
LEVEL_BYTES = tuple(((size + 3) // 4) ** 2 * 8 for size in MIP_SIZES)
DDS_BYTES = 128 + sum(LEVEL_BYTES)


def valid_dds(data):
    return (
        len(data) == DDS_BYTES
        and data[:4] == b"DDS "
        and struct.unpack_from("<I", data, 4)[0] == 124
        and struct.unpack_from("<II", data, 12) == (SIZE, SIZE)
        and struct.unpack_from("<II", data, 24) == (0, len(MIP_SIZES))
        and data[76:88] == struct.pack("<II4s", 32, 4, b"DXT1")
        and data[112:124] == bytes(12)
    )


def encode_dds(image):
    image = image.convert("RGB").resize((SIZE, SIZE), Image.Resampling.LANCZOS)
    header = bytearray(128)
    header[:4] = b"DDS "
    struct.pack_into("<7I", header, 4, 124, 0xA1007, SIZE, SIZE, LEVEL_BYTES[0], 0, len(MIP_SIZES))
    struct.pack_into("<II4s", header, 76, 32, 4, b"DXT1")
    struct.pack_into("<I", header, 108, 0x401008)
    payload = []
    for size, expected in zip(MIP_SIZES, LEVEL_BYTES):
        buffer = BytesIO()
        image.save(buffer, format="DDS", pixel_format="DXT1")
        data = buffer.getvalue()
        if len(data) != 128 + expected or data[84:88] != b"DXT1":
            raise ValueError("Кодировщик DDS не вернул ожидаемый формат DXT1.")
        payload.append(data[128:])
        if size > 1:
            image = image.resize((size // 2, size // 2), Image.Resampling.BOX)
    return bytes(header) + b"".join(payload)


def build_skybox(folder):
    pack = {}
    for face, path in PATHS.items():
        matches = [folder / (face + ext) for ext in (".png", ".jpg", ".jpeg")]
        matches = [image for image in matches if image.is_file()]
        if len(matches) != 1:
            raise ValueError(f"Нужен ровно один файл {face}.png, {face}.jpg или {face}.jpeg.")
        with Image.open(matches[0], formats=("PNG", "JPEG")) as image:
            width, height = image.size
            if width != height or not 64 <= width <= 4096:
                raise ValueError(f"{matches[0].name}: нужна квадратная картинка от 64 до 4096 пикселей.")
            pack[path] = encode_dds(ImageOps.exif_transpose(image))
    return pack
