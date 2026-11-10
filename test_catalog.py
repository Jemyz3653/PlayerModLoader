from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from zipfile import ZipFile

from PIL import Image

from catalog import Catalog, zip_data
from image_edits import CONTROL_COUNT, GLOBAL_SKY, edited_image, options, png_data, render_pack
from modloader import CURSORS, Manager, Target, read_folder, read_pack, validate_asset
from skybox import PATHS, encode_dds

CURSOR = CURSORS + "ArrowCursor.png"


class CatalogTest(TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.catalog = Catalog(self.root / "catalog")
        self.red = png_data(Image.new("RGBA", (16, 16), "red"))
        self.blue = png_data(Image.new("RGBA", (16, 16), "blue"))

    def test_priority_and_profile_cleanup(self):
        first = self.catalog.add("Red", {CURSOR: self.red})
        second = self.catalog.add("Blue", {CURSOR: self.blue})
        self.catalog.save_profile("Mix", [first, second])
        self.catalog.rename(first, "Red renamed")
        self.catalog.favorite(first)
        index = self.catalog.load()
        self.assertEqual(index["profiles"]["Mix"], [first, second])
        self.assertTrue(index["mods"][first]["favorite"])
        mixed, sources = self.catalog.resolve([first, second])
        self.assertEqual(sources[CURSOR], ["Red renamed", "Blue"])
        self.assertEqual(mixed[CURSOR], self.blue)
        self.catalog.remove(second)
        self.assertEqual(self.catalog.load()["profiles"]["Mix"], [first])
        self.assertEqual(self.catalog.resolve([first])[0][CURSOR], self.red)

    def test_custom_folder_wrapper_and_export(self):
        pack_dir = self.root / "Pack"
        cursor = pack_dir / CURSOR
        cursor.parent.mkdir(parents=True)
        cursor.write_bytes(self.red)
        pack = read_folder(pack_dir)
        self.assertEqual(pack, {CURSOR: self.red})
        archive = self.root / "export.zip"
        archive.write_bytes(zip_data(pack))
        self.assertEqual(read_pack(archive), pack)
        with ZipFile(archive, "w") as bundle:
            bundle.writestr("MyMod/" + CURSOR, self.blue)
        self.assertEqual(read_pack(archive), {CURSOR: self.blue})
        (pack_dir / "script.dll").write_bytes(b"MZ")
        with self.assertRaises(ValueError):
            read_folder(pack_dir)

    def test_dry_run_and_health_work_with_existing_backup_schema(self):
        version = self.root / "version-123"
        cursor = version / CURSOR
        cursor.parent.mkdir(parents=True)
        cursor.write_bytes(self.red)
        manager = Manager(self.root / "state", ensure_closed=lambda: None)
        target = Target("Roblox", version, version)
        self.assertEqual(manager.inspect(target, {CURSOR: self.blue})[0][1], "заменить")
        self.assertEqual(cursor.read_bytes(), self.red)
        manager.apply(target, {CURSOR: self.blue})
        self.assertEqual(manager.health(target), [(CURSOR, "активен")])
        cursor.write_bytes(b"external edit")
        self.assertEqual(manager.health(target), [(CURSOR, "конфликт")])
        cursor.write_bytes(self.blue)
        manager.restore(target)
        self.assertEqual(cursor.read_bytes(), self.red)

    def test_image_options_are_real_and_leave_source_intact(self):
        self.assertEqual(CONTROL_COUNT, 202)
        pack = {CURSOR: self.red}
        changes = {CURSOR: options(CURSOR, {"opacity": 50, "size": 200, "hue": 120})}
        result = render_pack(pack, changes)
        self.assertEqual(pack[CURSOR], self.red)
        with Image.open(BytesIO(result[CURSOR])) as cursor:
            self.assertEqual(cursor.size, (32, 32))
            r, g, b, a = cursor.getpixel((16, 16))
            self.assertGreater(g, max(r, b))
            self.assertIn(a, (127, 128))
        with self.assertRaises(ValueError):
            options(CURSOR, {"rotation": 15})
        with self.assertRaises(ValueError):
            validate_asset(CURSOR, self.red[:-15])

    def test_global_sky_and_specific_face_combine(self):
        path = PATHS["ft"]
        raw = encode_dds(Image.new("RGB", (64, 64), (100, 60, 25)))
        all_changes = {GLOBAL_SKY: {"brightness": 50}}
        common = edited_image(path, raw, all_changes).getpixel((0, 0))
        face = edited_image(path, raw, {**all_changes, path: {"brightness": 50}}).getpixel((0, 0))
        self.assertEqual(face, tuple(channel // 2 for channel in common))
