from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from zipfile import ZipFile

from modloader import CURSORS, Manager, Target, detect, read_pack

EXAMPLE = Path(__file__).parent / "examples/PurpleCursor.zip"


class ModLoaderTest(TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.pack = read_pack(EXAMPLE)
        self.manager = Manager(self.root / "state", ensure_closed=lambda: None)
        self.name = CURSORS + "ArrowCursor.png"

    def make_install(self, product):
        version = self.root / product / "Versions/version-012345"
        version.mkdir(parents=True)
        (version / "RobloxPlayerBeta.exe").touch()
        for name in self.pack:
            original = version / name
            original.parent.mkdir(parents=True, exist_ok=True)
            original.write_bytes(b"original")
        return next(t for t in detect(self.root) if t.label == product)

    def test_direct_restores_existing_and_rejects_second_pack(self):
        target = self.make_install("Roblox")
        asset = target.destination / self.name
        self.manager.apply(target, self.pack)
        self.assertEqual(asset.read_bytes(), self.pack[self.name])
        with self.assertRaises(ValueError):
            self.manager.apply(target, self.pack)
        self.manager.restore(target)
        self.assertEqual(asset.read_bytes(), b"original")
        self.assertFalse(self.manager.active(target))

    def test_overlay_restores_previous_and_removes_new_files(self):
        target = self.make_install("Bloxstrap")
        asset = target.destination / self.name
        asset.parent.mkdir(parents=True)
        asset.write_bytes(b"previous override")
        self.manager.apply(target, self.pack)
        self.assertEqual((target.version / self.name).read_bytes(), b"original")
        self.manager.restore(target)
        self.assertEqual(asset.read_bytes(), b"previous override")
        self.assertFalse((target.destination / (CURSORS + "ArrowFarCursor.png")).exists())

    def test_restore_preserves_external_changes(self):
        target = self.make_install("Roblox")
        self.manager.apply(target, self.pack)
        asset = target.destination / self.name
        asset.write_bytes(b"changed elsewhere")
        with self.assertRaises(ValueError):
            self.manager.restore(target)
        self.assertEqual(asset.read_bytes(), b"changed elsewhere")
        self.assertTrue(self.manager.active(target))

    def test_partial_apply_restores_earlier_writes(self):
        from modloader import atomic_write
        target = self.make_install("Roblox")
        failure = target.destination / (CURSORS + "ArrowFarCursor.png")

        def fail_one(path, data):
            if path == failure and data == self.pack[CURSORS + "ArrowFarCursor.png"]:
                raise OSError("file busy")
            return atomic_write(path, data)

        with patch("modloader.atomic_write", side_effect=fail_one):
            with self.assertRaises(OSError):
                self.manager.apply(target, self.pack)
        self.assertEqual((target.destination / self.name).read_bytes(), b"original")
        self.assertFalse(self.manager.active(target))

    def test_zip_rejects_traversal_code_duplicates_and_wrong_content(self):
        for name, data, duplicate in (
            ("../" + self.name, self.pack[self.name], False),
            ("content/plug.dll", b"MZ", False),
            (self.name, self.pack[self.name], True),
            (self.name, b"not a png", False),
        ):
            with self.subTest(name=name, duplicate=duplicate):
                archive = self.root / "bad.zip"
                with ZipFile(archive, "w") as bundle:
                    bundle.writestr(name, data)
                    if duplicate:
                        bundle.writestr(name.upper(), data)
                with self.assertRaises(ValueError):
                    read_pack(archive)
