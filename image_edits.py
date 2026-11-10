"""Cosmetic transforms for the sky and cursor editors."""
from io import BytesIO

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from modloader import ASSETS, SKY_TEXTURES, validate_pack
from skybox import encode_dds

# key: (label, minimum, maximum, default, step)
SKY_CONTROLS = {
    "brightness": ("Яркость", 0, 200, 100, 1),
    "contrast": ("Контраст", 0, 200, 100, 1),
    "saturation": ("Насыщенность", 0, 200, 100, 1),
    "temperature": ("Теплота", -100, 100, 0, 1),
    "hue": ("Оттенок, °", -180, 180, 0, 1),
    "gamma": ("Гамма ×100", 25, 300, 100, 1),
    "shadows": ("Тени", -100, 100, 0, 1),
    "highlights": ("Света", -100, 100, 0, 1),
    "sharpness": ("Резкость", 0, 300, 100, 1),
    "blur": ("Размытие, px", 0, 16, 0, 1),
    "rotation": ("Поворот, °", 0, 270, 0, 90),
    "mirror": ("Зеркало по X", 0, 1, 0, 1),
    "flip": ("Зеркало по Y", 0, 1, 0, 1),
    "vignette": ("Затемнение углов", 0, 100, 0, 1),
}
CURSOR_CONTROLS = {
    "brightness": SKY_CONTROLS["brightness"],
    "hue": SKY_CONTROLS["hue"],
    "rotation": SKY_CONTROLS["rotation"],
    "mirror": SKY_CONTROLS["mirror"],
    "flip": SKY_CONTROLS["flip"],
    "opacity": ("Непрозрачность, %", 0, 100, 100, 1),
    "size": ("Масштаб, %", 25, 200, 100, 1),
    "outline": ("Обводка, px", 0, 5, 0, 1),
}
CURSOR_PATHS = tuple(path for path in ASSETS if path.endswith(".png"))
GLOBAL_SKY = "Все стороны неба"
CONTROL_COUNT = (1 + len(SKY_TEXTURES)) * len(SKY_CONTROLS) + len(CURSOR_PATHS) * len(CURSOR_CONTROLS)


def controls(scope):
    return CURSOR_CONTROLS if scope in CURSOR_PATHS else SKY_CONTROLS


def options(scope, settings):
    result = {}
    for key, value in settings.items():
        label, low, high, default, step = controls(scope)[key]
        if not low <= value <= high or (value - low) % step:
            raise ValueError(f"{label}: нужно значение от {low} до {high} с шагом {step}.")
        if value != default:
            result[key] = value
    return result


def geometry(image, settings):
    if settings.get("rotation"):
        image = image.rotate(-settings["rotation"], expand=True)
    if settings.get("mirror"):
        image = ImageOps.mirror(image)
    if settings.get("flip"):
        image = ImageOps.flip(image)
    return image


def hue_shift(image, degrees):
    hue, saturation, value = image.convert("HSV").split()
    offset = round(degrees * 256 / 360)
    hue = hue.point([(level + offset) % 256 for level in range(256)])
    return Image.merge("HSV", (hue, saturation, value)).convert("RGB")


def transform_sky(image, settings):
    image = geometry(image.convert("RGB"), settings)
    for key, enhance in (("brightness", ImageEnhance.Brightness), ("contrast", ImageEnhance.Contrast),
                         ("saturation", ImageEnhance.Color), ("sharpness", ImageEnhance.Sharpness)):
        if key in settings:
            image = enhance(image).enhance(settings[key] / 100)
    if "hue" in settings:
        image = hue_shift(image, settings["hue"])
    if "temperature" in settings:
        warmth = settings["temperature"] / 100
        red, green, blue = image.split()
        image = Image.merge("RGB", (red.point(lambda x: x * (1 + warmth * .3)),
                                   green, blue.point(lambda x: x * (1 - warmth * .3))))
    if any(key in settings for key in ("gamma", "shadows", "highlights")):
        gamma = settings.get("gamma", 100) / 100
        shadows = settings.get("shadows", 0) / 100
        highlights = settings.get("highlights", 0) / 100
        table = []
        for level in range(256):
            t = (level / 255) ** (1 / gamma)
            adjusted = t + .32 * (shadows * (1 - t) ** 2 + highlights * t ** 2)
            table.append(round(255 * min(1, max(0, adjusted))))
        image = image.point(table * 3)
    if "blur" in settings:
        image = image.filter(ImageFilter.GaussianBlur(settings["blur"]))
    if "vignette" in settings:
        mask = Image.radial_gradient("L").resize(image.size, Image.Resampling.BILINEAR)
        mask = mask.point(lambda value: value * settings["vignette"] / 100)
        image = Image.composite(Image.new("RGB", image.size), image, mask)
    return image


def transform_cursor(image, settings):
    image = geometry(image.convert("RGBA"), settings)
    if "size" in settings:
        ratio = min(settings["size"] / 100, 128 / max(image.size))
        image = image.resize(tuple(max(1, round(side * ratio)) for side in image.size),
                             Image.Resampling.LANCZOS)
    alpha = image.getchannel("A")
    rgb = image.convert("RGB")
    if "hue" in settings:
        rgb = hue_shift(rgb, settings["hue"])
    if "brightness" in settings:
        rgb = ImageEnhance.Brightness(rgb).enhance(settings["brightness"] / 100)
    rgb.putalpha(alpha)
    if "outline" in settings:
        outline = Image.new("RGBA", rgb.size)
        outline.putalpha(alpha.filter(ImageFilter.MaxFilter(2 * settings["outline"] + 1)))
        rgb = Image.alpha_composite(outline, rgb)
    if "opacity" in settings:
        rgb.putalpha(rgb.getchannel("A").point(lambda level: level * settings["opacity"] / 100))
    return rgb


def png_data(image):
    output = BytesIO()
    image.save(output, "PNG", optimize=True)
    return output.getvalue()


def open_cursor(file):
    with Image.open(file, formats=("PNG", "JPEG")) as image:
        if max(image.size) > 4096:
            raise ValueError("Изображение курсора должно быть не больше 4096 px по стороне.")
        image = ImageOps.exif_transpose(image).convert("RGBA")
        image.thumbnail((128, 128), Image.Resampling.LANCZOS)
        return png_data(image)


def edited_image(path, data, changes):
    with Image.open(BytesIO(data), formats=("DDS", "PNG")) as original:
        if path in SKY_TEXTURES:
            image = transform_sky(original, changes.get(GLOBAL_SKY, {}))
            return transform_sky(image, changes[path]) if changes.get(path) else image
        return transform_cursor(original, changes.get(path, {}))


def render_pack(pack, changes):
    result = dict(pack)
    for path, data in pack.items():
        if not changes.get(path) and not (path in SKY_TEXTURES and changes.get(GLOBAL_SKY)):
            continue
        image = edited_image(path, data, changes)
        result[path] = encode_dds(image) if path in SKY_TEXTURES else png_data(image)
    validate_pack(result)
    return result
