import base64
import hashlib
from io import BytesIO
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import re
import stat
import struct
import subprocess
import tempfile
from zipfile import ZipFile

from PIL import Image

from skybox import DDS_BYTES, PATHS, valid_dds

CURSORS = "content/textures/Cursors/KeyboardMouse/"
CURSOR_ROOT = "content/textures/Cursors/"
MAX_ZIP_BYTES = 16 * 1024 * 1024
ASSETS = {
    CURSORS + "ArrowCursor.png": 256 * 1024,
    CURSORS + "ArrowFarCursor.png": 256 * 1024,
    CURSORS + "IBeamCursor.png": 256 * 1024,
    "content/textures/ArrowCursor.png": 256 * 1024,
    "content/textures/ArrowFarCursor.png": 256 * 1024,
    **{CURSOR_ROOT + path: 256 * 1024 for path in (
        "FocusLocation.png", "DragDetector/ActivatedCursor.png", "DragDetector/HoverCursor.png",
        "Gamepad/IBeamCursor.png", "Gamepad/Pointer.png", "Gamepad/Pointer@2x.png",
        "Gamepad/PointerOver.png", "Gamepad/PointerOver@2x.png",
    )},
    "content/sounds/oof.ogg": 2 * 1024 * 1024,
    "content/sounds/ouch.ogg": 2 * 1024 * 1024,
    "content/sounds/volume_slider.ogg": 2 * 1024 * 1024,
    **dict.fromkeys(PATHS.values(), DDS_BYTES),
}
SKY_TEXTURES = frozenset(PATHS.values())
CANONICAL = {path.lower(): path for path in ASSETS}
VERSION_PATTERN = re.compile(r"version-[a-f0-9]+")


@dataclass(frozen=True)
class Target:
    label: str
    version: Path
    destination: Path
    overlay: bool = False


def detect(local_app_data):
    found = []
    for product in ("Roblox", "Bloxstrap"):
        base = local_app_data / product
        versions = base / "Versions"
        if not versions.is_dir():
            continue
        candidates = [
            p for p in versions.iterdir()
            if VERSION_PATTERN.fullmatch(p.name) and (p / "RobloxPlayerBeta.exe").is_file()
        ]
        if candidates:
            latest = max(candidates, key=lambda p: (p / "RobloxPlayerBeta.exe").stat().st_mtime)
            overlay = product == "Bloxstrap"
            found.append(Target(product, latest, base / "Modifications" if overlay else latest, overlay))
    return found


def checked_path(root, relative):
    if relative not in ASSETS:
        raise ValueError("Этот ресурс не входит в список разрешённых.")
    path = root
    assert root.is_absolute()
    for part in (*root.parents[::-1], root):
        reject_link(part)
    for component in relative.split("/"):
        path = path / component
        reject_link(path)
    return path


def reject_link(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    reparse = getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    if stat.S_ISLNK(info.st_mode) or reparse:
        raise ValueError(f"Ссылки и точки перенаправления запрещены: {path}")


def validate_asset(path, data):
    if len(data) > ASSETS[path]:
        raise ValueError(f"Файл слишком большой: {path}")
    if path.endswith(".png"):
        if len(data) < 45 or data[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR":
            raise ValueError(f"Ожидался PNG: {path}")
        width, height = struct.unpack_from(">II", data, 16)
        if not (1 <= width <= 128 and 1 <= height <= 128):
            raise ValueError(f"Размер PNG должен быть не больше 128×128: {path}")
        try:
            with Image.open(BytesIO(data), formats=("PNG",)) as image:
                image.verify()
        except (OSError, SyntaxError, ValueError) as exc:
            raise ValueError(f"Повреждённый PNG: {path}") from exc
    elif path in SKY_TEXTURES:
        if not valid_dds(data):
            raise ValueError(f"Для неба нужен DDS DXT1 1024×1024 с 11 уровнями: {path}")
    else:
        if len(data) < 34 or data[:5] != b"OggS\x00" or not data[27 + data[26]:].startswith(b"\x01vorbis"):
            raise ValueError(f"Ожидался Ogg Vorbis: {path}")


def require_complete_skybox(pack):
    included = SKY_TEXTURES.intersection(pack)
    if included and included != SKY_TEXTURES:
        raise ValueError("Для неба нужны все шесть текстур.")


def validate_pack(pack):
    if not pack:
        raise ValueError("Пак не содержит поддерживаемых ресурсов.")
    require_complete_skybox(pack)
    for name, data in pack.items():
        if name not in ASSETS:
            raise ValueError(f"Неподдерживаемый ресурс: {name}")
        validate_asset(name, data)


def read_pack(archive):
    if archive.stat().st_size > MAX_ZIP_BYTES:
        raise ValueError("ZIP должен быть не больше 16 МиБ.")
    with ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if len(entries) > 64:
            raise ValueError("В ZIP должно быть не больше 64 записей.")
        files = []
        for info in entries:
            name = info.filename
            parts = name.rstrip("/").split("/")
            if "\\" in name or any(part in ("", ".", "..") for part in parts) or ":" in name:
                raise ValueError("В ZIP есть недопустимый путь.")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError("Ссылки в ZIP запрещены.")
            if not info.is_dir():
                files.append(info)
        prefixes = {info.filename.split("/", 1)[0].lower() for info in files}
        wrapper = len(prefixes) == 1 and not prefixes.intersection({"content", "platformcontent"})
        pack = {}
        for info in files:
            name = info.filename.partition("/")[2] if wrapper else info.filename
            path = CANONICAL.get(name.lower())
            if not path:
                raise ValueError(f"Неподдерживаемый файл в ZIP: {info.filename}")
            if path in pack or info.file_size > ASSETS[path] or info.flag_bits & 1:
                raise ValueError(f"Дубликат, шифрование или превышен размер: {name}")
            with bundle.open(info) as member:
                pack[path] = member.read(ASSETS[path] + 1)
    validate_pack(pack)
    return pack


def raise_walk_error(error):
    raise error


def read_folder(folder):
    reject_link(folder)
    pack = {}
    for parent, dirs, files in os.walk(folder, onerror=raise_walk_error, followlinks=False):
        directory = Path(parent)
        for name in dirs:
            reject_link(directory / name)
        for name in files:
            file = directory / name
            reject_link(file)
            relative = file.relative_to(folder).as_posix()
            path = CANONICAL.get(relative.lower())
            if not path:
                raise ValueError(f"Неподдерживаемый файл в папке: {relative}")
            if path in pack or not file.is_file() or file.stat().st_size > ASSETS[path]:
                raise ValueError(f"Дубликат или недопустимый файл: {relative}")
            with file.open("rb") as source:
                pack[path] = source.read(ASSETS[path] + 1)
    validate_pack(pack)
    return pack


def player_closed():
    if os.name != "nt":
        raise OSError("Программа предназначена для Windows.")
    tasklist = Path(os.environ["SystemRoot"]) / "System32" / "tasklist.exe"
    for exe in ("RobloxPlayerBeta.exe", "Bloxstrap.exe", "RobloxPlayerInstaller.exe"):
        result = subprocess.run(
            [str(tasklist), "/FI", f"IMAGENAME eq {exe}", "/FO", "CSV", "/NH"],
            capture_output=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if exe.lower().encode() in result.stdout.lower():
            raise ValueError("Сначала закрой Roblox Player и дождись завершения работы установщика или Bloxstrap.")


def digest(data):
    return None if data is None else hashlib.sha256(data).hexdigest()


def read_existing(path):
    try:
        if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
            if path.exists():
                raise ValueError(f"Ожидался обычный файл до 8 МиБ: {path}")
            return None
        return path.read_bytes()
    except FileNotFoundError:
        return None


def atomic_write(path, data):
    if data is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix=".playermod-", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


@contextmanager
def exclusive(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as file:
        if os.name == "nt":
            import msvcrt
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


class Manager:
    def __init__(self, state_dir, ensure_closed=player_closed):
        self.state_dir = state_dir
        self.ensure_closed = ensure_closed

    def journal_path(self, target):
        identity = str(target.destination).casefold().encode("utf-8")
        return self.state_dir / (hashlib.sha256(identity).hexdigest() + ".json")

    def active(self, target):
        return self.journal_path(target).is_file()

    def inspect(self, target, pack):
        validate_pack(pack)
        rows = []
        for name, data in pack.items():
            installed = checked_path(target.version, name)
            destination = checked_path(target.destination, name)
            if not installed.is_file():
                rows.append((name, "отсутствует в Player", None))
                continue
            previous = read_existing(destination)
            if previous == data:
                action = "совпадает"
            elif previous is None:
                action = "добавить"
            else:
                action = "заменить"
            rows.append((name, action, previous))
        return rows

    def health(self, target):
        journal = self.journal_path(target)
        if not journal.is_file():
            return []
        rows = []
        for name, entry in json.loads(journal.read_text(encoding="ascii")).items():
            destination = checked_path(target.destination, name)
            current = read_existing(destination)
            before = entry["before"]
            original = None if before is None else base64.b64decode(before, validate=True)
            if current == original:
                state = "исходный файл"
            elif digest(current) == entry["after"]:
                state = "активен"
            else:
                state = "конфликт"
            rows.append((name, state))
        return rows

    def apply(self, target, pack):
        with exclusive(self.state_dir / "operation.lock"):
            self.ensure_closed()
            journal = self.journal_path(target)
            if journal.exists():
                raise ValueError("Сначала восстанови предыдущий пак для этой установки.")
            entries = {}
            for name, action, previous in self.inspect(target, pack):
                if action == "отсутствует в Player" or (previous is None and not target.overlay):
                    raise ValueError(f"В этой версии Player отсутствует ресурс: {name}")
                entries[name] = {
                    "before": base64.b64encode(previous).decode("ascii") if previous is not None else None,
                    "after": digest(pack[name]),
                }
            atomic_write(journal, json.dumps(entries).encode("ascii"))
            try:
                for name, data in pack.items():
                    destination = checked_path(target.destination, name)
                    previous = entries[name]["before"]
                    before = None if previous is None else base64.b64decode(previous)
                    if read_existing(destination) != before:
                        raise ValueError(f"Файл изменился после проверки: {name}")
                    atomic_write(destination, data)
            except Exception:
                self._restore(target, journal)
                raise

    def restore(self, target):
        with exclusive(self.state_dir / "operation.lock"):
            self.ensure_closed()
            journal = self.journal_path(target)
            if not journal.exists():
                raise ValueError("Резервная копия для этой установки отсутствует.")
            self._restore(target, journal)

    def _restore(self, target, journal):
        pending = []
        for name, entry in json.loads(journal.read_text(encoding="ascii")).items():
            path = checked_path(target.destination, name)
            before = entry["before"]
            original = None if before is None else base64.b64decode(before, validate=True)
            current = read_existing(path)
            if current == original:
                continue
            if digest(current) != entry["after"]:
                raise ValueError(f"Файл изменён другой программой. Копия сохранена: {name}")
            pending.append((name, original, entry["after"]))
        for name, data, expected in pending:
            path = checked_path(target.destination, name)
            if digest(read_existing(path)) != expected:
                raise ValueError(f"Файл изменился после проверки. Копия сохранена: {name}")
            atomic_write(path, data)
        journal.unlink()
