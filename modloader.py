import base64
import hashlib
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

from skybox import DDS_BYTES, PATHS, valid_dds

CURSORS = "content/textures/Cursors/KeyboardMouse/"
ASSETS = {
    CURSORS + "ArrowCursor.png": 256 * 1024,
    CURSORS + "ArrowFarCursor.png": 256 * 1024,
    CURSORS + "IBeamCursor.png": 256 * 1024,
    "content/textures/ArrowCursor.png": 256 * 1024,
    "content/textures/ArrowFarCursor.png": 256 * 1024,
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


def read_pack(archive):
    if archive.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("ZIP должен быть не больше 8 МиБ.")
    with ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if len(entries) > 64:
            raise ValueError("В ZIP должно быть не больше 64 записей.")
        pack = {}
        for info in entries:
            name = info.filename
            parts = name.rstrip("/").split("/")
            if "\\" in name or any(part in ("", ".", "..") for part in parts) or ":" in name:
                raise ValueError("В ZIP есть недопустимый путь.")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError("Ссылки в ZIP запрещены.")
            if info.is_dir():
                continue
            path = CANONICAL.get(name.lower())
            if not path:
                raise ValueError(f"Неподдерживаемый файл в ZIP: {name}")
            if path in pack or info.file_size > ASSETS[path] or info.flag_bits & 1:
                raise ValueError(f"Дубликат, шифрование или превышен размер: {name}")
            with bundle.open(info) as member:
                data = member.read(ASSETS[path] + 1)
            validate_asset(path, data)
            pack[path] = data
    if not pack:
        raise ValueError("ZIP не содержит поддерживаемых ресурсов.")
    require_complete_skybox(pack)
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

    def apply(self, target, pack):
        with exclusive(self.state_dir / "operation.lock"):
            self.ensure_closed()
            journal = self.journal_path(target)
            if journal.exists():
                raise ValueError("Сначала восстанови предыдущий пак для этой установки.")
            require_complete_skybox(pack)
            entries = {}
            for name, data in pack.items():
                installed = checked_path(target.version, name)
                if not installed.is_file():
                    raise ValueError(f"В этой версии Player отсутствует ресурс: {name}")
                destination = checked_path(target.destination, name)
                validate_asset(name, data)
                previous = read_existing(destination)
                if previous is None and not target.overlay:
                    raise ValueError(f"Исходный ресурс исчез: {name}")
                entries[name] = {
                    "before": base64.b64encode(previous).decode("ascii") if previous is not None else None,
                    "after": digest(data),
                }
            if not entries:
                raise ValueError("Пак пуст.")
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
