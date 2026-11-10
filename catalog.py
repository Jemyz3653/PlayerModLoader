"""Local mod library and ordered profiles."""
from io import BytesIO
import json
import re
import uuid
from zipfile import ZIP_DEFLATED, ZipFile

from modloader import MAX_ZIP_BYTES, atomic_write, exclusive, read_pack, validate_pack

MOD_ID = re.compile(r"[0-9a-f]{32}")


def zip_data(pack):
    validate_pack(pack)
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as bundle:
        for path, data in sorted(pack.items()):
            bundle.writestr(path, data)
    data = buffer.getvalue()
    if len(data) > MAX_ZIP_BYTES:
        raise ValueError("Готовый ZIP превышает лимит 16 МиБ.")
    return data


def kinds(pack):
    groups = {".tex": "Небо", ".png": "Курсоры", ".ogg": "Звуки"}
    return sorted({groups["." + path.rsplit(".", 1)[-1]] for path in pack})


def title(name):
    name = name.strip()
    if not 1 <= len(name) <= 70 or not name.isprintable():
        raise ValueError("Имя должно содержать от 1 до 70 печатных символов.")
    return name


class Catalog:
    def __init__(self, root):
        self.root = root
        self.index = root / "catalog.json"
        self.lock = root / "catalog.lock"

    def load(self):
        if not self.index.is_file():
            return {"mods": {}, "profiles": {}}
        return json.loads(self.index.read_text(encoding="utf-8"))

    def save(self, data):
        atomic_write(self.index, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))

    def path(self, key):
        if not MOD_ID.fullmatch(key):
            raise ValueError("Недопустимый идентификатор мода.")
        return self.root / "mods" / (key + ".zip")

    def add(self, name, pack):
        name = title(name)
        payload = zip_data(pack)
        key = uuid.uuid4().hex
        with exclusive(self.lock):
            data = self.load()
            path = self.path(key)
            atomic_write(path, payload)
            data["mods"][key] = {"name": name, "kinds": kinds(pack), "files": len(pack), "favorite": False}
            try:
                self.save(data)
            except OSError:
                path.unlink(missing_ok=True)
                raise
        return key

    def read(self, key):
        if key not in self.load()["mods"]:
            raise ValueError("Мод отсутствует в библиотеке.")
        return read_pack(self.path(key))

    def rename(self, key, name):
        with exclusive(self.lock):
            data = self.load()
            data["mods"][key]["name"] = title(name)
            self.save(data)

    def favorite(self, key):
        with exclusive(self.lock):
            data = self.load()
            entry = data["mods"][key]
            entry["favorite"] = not entry["favorite"]
            self.save(data)

    def remove(self, key):
        with exclusive(self.lock):
            data = self.load()
            del data["mods"][key]
            # Keep profiles valid and remove only the deleted mod from their order.
            for mods in data["profiles"].values():
                if key in mods:
                    mods.remove(key)
            self.save(data)
            self.path(key).unlink(missing_ok=True)

    def save_profile(self, name, keys):
        name = title(name)
        with exclusive(self.lock):
            data = self.load()
            if not keys or len(set(keys)) != len(keys) or not set(keys) <= data["mods"].keys():
                raise ValueError("Профилю нужна непустая очередь без повторов.")
            data["profiles"][name] = list(keys)
            self.save(data)

    def remove_profile(self, name):
        with exclusive(self.lock):
            data = self.load()
            del data["profiles"][name]
            self.save(data)

    def resolve(self, keys):
        catalog = self.load()
        if not keys:
            raise ValueError("Добавь хотя бы один мод в очередь.")
        pack, sources = {}, {}
        for key in keys:
            entry = catalog["mods"][key]
            for path, data in self.read(key).items():
                pack[path] = data
                sources.setdefault(path, []).append(entry["name"])
        validate_pack(pack)
        return pack, sources
