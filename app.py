import os
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from zipfile import BadZipFile

from PIL import Image, ImageTk

from catalog import Catalog, zip_data
from image_edits import CONTROL_COUNT, GLOBAL_SKY, controls, edited_image, open_cursor, options, render_pack
from modloader import ASSETS, SKY_TEXTURES, Manager, atomic_write, detect, read_folder, read_pack, validate_asset
from skybox import PATHS, build_skybox

VERSION = "0.3.0"
ERRORS = (OSError, ValueError, BadZipFile, RuntimeError, NotImplementedError, KeyError,
          Image.DecompressionBombError)
EXAMPLES = (Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__)).parent / "examples"


def row(parent):
    frame = ttk.Frame(parent)
    frame.pack(fill="x", pady=(4, 4))
    return frame


def button(parent, label, action):
    control = ttk.Button(parent, text=label, command=action)
    control.pack(side="left", padx=(0, 5))
    return control


def table(parent, columns, height):
    tree = ttk.Treeview(parent, columns=[label for label, _ in columns], show="headings",
                        selectmode="browse", height=height)
    for label, width in columns:
        tree.heading(label, text=label)
        tree.column(label, width=width, minwidth=75, stretch=label == columns[0][0])
    tree.pack(fill="both", expand=True, pady=4)
    return tree


class App:
    def __init__(self, window):
        self.window = window
        self.base = Path(os.environ["LOCALAPPDATA"])
        state = self.base / "PlayerModLoader"
        self.manager = Manager(state)
        self.catalog = Catalog(state)
        self.queue = []
        self.targets = []
        window.title(f"PlayerModLoader {VERSION}")
        window.geometry("1010x700")
        window.minsize(890, 650)
        ttk.Style(window).theme_use("clam")
        main = ttk.Frame(window, padding=16)
        main.pack(fill="both", expand=True)
        ttk.Label(main, text="PlayerModLoader", font=("Segoe UI", 22, "bold")).pack(anchor="w")
        ttk.Label(main, text="Локальные моды для обычного Roblox Player", font=("Segoe UI", 10)).pack(anchor="w")
        self.tabs = ttk.Notebook(main)
        self.tabs.pack(fill="both", expand=True, pady=9)
        self.library_tab = ttk.Frame(self.tabs, padding=9)
        self.editor_tab = ttk.Frame(self.tabs, padding=9)
        self.install_tab = ttk.Frame(self.tabs, padding=9)
        for tab, label in ((self.library_tab, "Библиотека и профили"),
                           (self.editor_tab, "Редактор ресурсов"), (self.install_tab, "Установка и проверка")):
            self.tabs.add(tab, text=label)
        self.status = tk.StringVar()
        ttk.Label(main, textvariable=self.status, wraplength=850).pack(anchor="w")
        self.build_library()
        self.editor = Editor(self, self.editor_tab)
        self.build_installer()
        self.safe(self.refresh_library)()
        self.safe(self.scan)()

    def safe(self, action):
        def run(*args):
            try:
                return action(*args)
            except ERRORS as exc:
                messagebox.showerror("PlayerModLoader", str(exc), parent=self.window)
        return run

    def build_library(self):
        frame = self.library_tab
        bar = row(frame)
        button(bar, "Импорт ZIP", self.safe(self.import_zip))
        button(bar, "Импорт папки", self.safe(self.import_folder))
        button(bar, "Обновить список", self.safe(self.refresh_library))
        ttk.Label(bar, text="Поиск").pack(side="left", padx=(12, 5))
        self.search = tk.StringVar()
        ttk.Entry(bar, textvariable=self.search, width=23).pack(side="left")
        self.category = ttk.Combobox(bar, values=("Все", "Избранные", "Небо", "Курсоры", "Звуки"),
                                    state="readonly", width=12)
        self.category.current(0)
        self.category.pack(side="left", padx=7)
        self.category.bind("<<ComboboxSelected>>", self.safe(lambda _: self.refresh_library()))
        self.search.trace_add("write", self.safe(lambda *_: self.refresh_library()))
        ttk.Label(frame, text="Мои моды").pack(anchor="w")
        self.mods = table(frame, (("Мод", 340), ("Ресурсы", 155), ("Файлов", 60), ("Избранное", 80)), 5)
        self.mods.bind("<Double-1>", self.safe(lambda _: self.add_to_queue()))
        bar = row(frame)
        for label, action in (("В очередь", self.add_to_queue), ("Редактировать копию", self.edit_mod),
                              ("Имя", self.rename), ("Избранное", self.favorite), ("Копия", self.duplicate),
                              ("Экспорт ZIP", self.export_mod), ("Удалить", self.delete)):
            button(bar, label, self.safe(action))

        ttk.Label(frame, text="Очередь профиля. При конфликте нижний мод имеет приоритет.").pack(anchor="w", pady=(9, 0))
        self.order = tk.Listbox(frame, height=4, exportselection=False)
        self.order.pack(fill="x", pady=4)
        bar = row(frame)
        button(bar, "Выше", self.safe(lambda: self.move(-1)))
        button(bar, "Ниже", self.safe(lambda: self.move(1)))
        button(bar, "Убрать", self.safe(self.remove_from_queue))
        button(bar, "Очистить", self.clear_queue)
        button(bar, "К установке", lambda: self.tabs.select(self.install_tab))
        profiles = row(frame)
        self.profiles = ttk.Combobox(profiles, state="readonly", width=20)
        self.profiles.pack(side="left", padx=(0, 6))
        button(profiles, "Сохранить профиль", self.safe(self.save_profile))
        button(profiles, "Загрузить", self.safe(self.load_profile))
        button(profiles, "Удалить профиль", self.safe(self.delete_profile))

    def refresh_library(self):
        index = self.catalog.load()
        selected = self.mods.selection()
        self.mods.delete(*self.mods.get_children())
        category, needle = self.category.get(), self.search.get().strip().casefold()
        items = sorted(index["mods"].items(), key=lambda item: (
            not item[1]["favorite"], item[1]["name"].casefold(), item[0]))
        for key, entry in items:
            if needle not in entry["name"].casefold():
                continue
            if category == "Избранные" and not entry["favorite"]:
                continue
            if category not in ("Все", "Избранные") and category not in entry["kinds"]:
                continue
            self.mods.insert("", "end", iid=key, values=(entry["name"], ", ".join(entry["kinds"]),
                             entry["files"], "★" if entry["favorite"] else ""))
        if selected and self.mods.exists(selected[0]):
            self.mods.selection_set(selected[0])
        self.queue[:] = [key for key in self.queue if key in index["mods"]]
        self.show_queue(index)
        previous = self.profiles.get()
        names = sorted(index["profiles"], key=str.casefold)
        self.profiles["values"] = names
        self.profiles.set(previous if previous in names else (names[0] if names else ""))

    def show_queue(self, index=None):
        index = index if index is not None else self.catalog.load()
        self.order.delete(0, "end")
        for pos, key in enumerate(self.queue, 1):
            self.order.insert("end", f"{pos}. {index['mods'][key]['name']}")

    def selected(self):
        selected = self.mods.selection()
        if not selected:
            raise ValueError("Выбери мод в библиотеке.")
        key = selected[0]
        return key, self.catalog.load()["mods"][key]["name"]

    def prompt_name(self, initial):
        return simpledialog.askstring("Имя", "Название", initialvalue=initial[:70], parent=self.window)

    def add_mod(self, name, pack):
        name = self.prompt_name(name)
        if name is not None:
            key = self.catalog.add(name, pack)
            self.refresh_library()
            if self.mods.exists(key):
                self.mods.selection_set(key)
                self.mods.see(key)
            self.status.set(f"Сохранён мод «{name.strip()}». Добавь его в очередь для применения.")

    def import_zip(self):
        path = filedialog.askopenfilename(parent=self.window, filetypes=[("ZIP-паки", "*.zip")],
                                          initialdir=EXAMPLES if EXAMPLES.is_dir() else None)
        if path:
            path = Path(path)
            self.add_mod(path.stem, read_pack(path))

    def import_folder(self):
        path = filedialog.askdirectory(parent=self.window, title="Папка с content или PlatformContent")
        if path:
            path = Path(path)
            self.add_mod(path.name, read_folder(path))

    def edit_mod(self):
        key, name = self.selected()
        self.editor.load(name, self.catalog.read(key))
        self.tabs.select(self.editor_tab)

    def rename(self):
        key, name = self.selected()
        result = self.prompt_name(name)
        if result is not None:
            self.catalog.rename(key, result)
            self.refresh_library()

    def favorite(self):
        self.catalog.favorite(self.selected()[0])
        self.refresh_library()

    def duplicate(self):
        key, name = self.selected()
        self.add_mod(f"{name} · копия", self.catalog.read(key))

    def export(self, name, pack):
        safe_name = "".join(c for c in name if c not in '<>:"/\\|?*').strip() or "mod"
        path = filedialog.asksaveasfilename(parent=self.window, initialfile=safe_name + ".zip",
                                          defaultextension=".zip", filetypes=[("ZIP-паки", "*.zip")])
        if path:
            atomic_write(Path(path), zip_data(pack))
            self.status.set("ZIP экспортирован.")

    def export_mod(self):
        key, name = self.selected()
        self.export(name, self.catalog.read(key))

    def delete(self):
        key, name = self.selected()
        if messagebox.askyesno("Удалить мод", f"Удалить «{name}» из библиотеки и профилей?",
                               parent=self.window):
            self.catalog.remove(key)
            self.refresh_library()

    def add_to_queue(self):
        key, _ = self.selected()
        if key not in self.queue:
            self.queue.append(key)
            self.show_queue()

    def queue_position(self):
        selection = self.order.curselection()
        if not selection:
            raise ValueError("Выбери мод в очереди.")
        return selection[0]

    def move(self, delta):
        index = self.queue_position()
        other = index + delta
        if 0 <= other < len(self.queue):
            self.queue[index], self.queue[other] = self.queue[other], self.queue[index]
            self.show_queue()
            self.order.selection_set(other)

    def remove_from_queue(self):
        self.queue.pop(self.queue_position())
        self.show_queue()

    def clear_queue(self):
        self.queue.clear()
        self.safe(self.show_queue)()

    def save_profile(self):
        name = self.prompt_name(self.profiles.get() or "Мой профиль")
        if name is not None:
            self.catalog.save_profile(name, self.queue)
            self.refresh_library()
            self.profiles.set(name.strip())

    def load_profile(self):
        name = self.profiles.get()
        if not name:
            raise ValueError("Выбери сохранённый профиль.")
        self.queue = list(self.catalog.load()["profiles"][name])
        self.show_queue()
        self.status.set(f"Загружен профиль «{name}».")

    def delete_profile(self):
        name = self.profiles.get()
        if name and messagebox.askyesno("Удалить профиль", f"Удалить «{name}»?", parent=self.window):
            self.catalog.remove_profile(name)
            self.refresh_library()

    def build_installer(self):
        bar = row(self.install_tab)
        self.target = ttk.Combobox(bar, state="readonly", width=44)
        self.target.pack(side="left", padx=(0, 7))
        self.target.bind("<<ComboboxSelected>>", self.safe(lambda _: self.target_status()))
        button(bar, "Найти Player", self.safe(self.scan))
        button(bar, "Открыть каталог", self.safe(self.open_destination))
        self.destination = ttk.Label(self.install_tab, wraplength=820)
        self.destination.pack(anchor="w")
        bar = row(self.install_tab)
        for label, action in (("Проверить очередь", self.plan), ("Применить", self.apply),
                              ("Восстановить", self.restore), ("Проверить активное", self.health),
                              ("Экспорт очереди", self.export_queue), ("Копировать отчёт", self.copy_report)):
            button(bar, label, self.safe(action))
        self.report = table(self.install_tab, (("Ресурс", 405), ("Действие", 140), ("Источник", 220)), 12)
        ttk.Label(self.install_tab, text="Roblox может заблокировать аккаунт за изменённый клиент.",
                  foreground="#8a311c", wraplength=780).pack(anchor="w", pady=(6, 0))

    def get_target(self):
        current = self.target.current()
        if current < 0:
            raise ValueError("Установка Roblox Player не найдена.")
        return self.targets[current]

    def scan(self):
        self.targets = detect(self.base)
        self.target["values"] = [f"{target.label} · {target.version.name}" for target in self.targets]
        if self.targets:
            self.target.current(0)
        else:
            self.target.set("")
        self.target_status()

    def target_status(self):
        if self.target.current() < 0:
            self.destination.configure(text="Поддерживаются установки с сайта Roblox и через Bloxstrap.")
        else:
            target = self.get_target()
            suffix = " · Есть резервная копия" if self.manager.active(target) else ""
            self.destination.configure(text=str(target.destination) + suffix)

    def open_destination(self):
        target = self.get_target()
        os.startfile(target.destination if target.destination.exists() else target.version)

    def report_rows(self, rows):
        self.report.delete(*self.report.get_children())
        for values in rows:
            self.report.insert("", "end", values=values)

    def plan(self):
        target = self.get_target()
        pack, sources = self.catalog.resolve(self.queue)
        rows = self.manager.inspect(target, pack)
        self.report_rows((path, action, " → ".join(sources[path])) for path, action, _ in rows)
        conflicts = sum(len(names) > 1 for names in sources.values())
        self.status.set(f"{len(rows)} ресурсов. Конфликтов: {conflicts}. Последний источник имеет приоритет.")

    def health(self):
        rows = self.manager.health(self.get_target())
        self.report_rows((name, state, "Резервная копия") for name, state in rows)
        self.status.set(f"Проверено файлов: {len(rows)}." if rows else "Для этой установки нет активной копии.")

    def apply(self):
        target = self.get_target()
        pack, _ = self.catalog.resolve(self.queue)
        if messagebox.askyesno("Риск блокировки",
                f"Применить {len(pack)} ресурсов? Roblox может заблокировать аккаунт за изменённый клиент. "
                "Решение и ответственность за использование пака лежат на пользователе.", parent=self.window):
            self.manager.apply(target, pack)
            self.target_status()
            self.health()

    def restore(self):
        self.manager.restore(self.get_target())
        self.target_status()
        self.report_rows(())
        self.status.set("Исходные ресурсы восстановлены.")

    def export_queue(self):
        pack, _ = self.catalog.resolve(self.queue)
        self.export(self.profiles.get() or "PlayerMods", pack)

    def copy_report(self):
        values = ["\t".join(map(str, self.report.item(row, "values"))) for row in self.report.get_children()]
        if not values:
            raise ValueError("Сначала запусти проверку.")
        self.window.clipboard_clear()
        self.window.clipboard_append("\n".join(values))
        self.status.set("Отчёт скопирован.")


class Editor:
    def __init__(self, app, frame):
        self.app = app
        self.pack, self.changes, self.fields = {}, {}, {}
        self.scope = None
        self.name = "Мой мод"
        self.photo = None
        bar = row(frame)
        button(bar, "Небо из 6 картинок", app.safe(self.import_sky))
        self.asset = ttk.Combobox(bar, values=[path for path in ASSETS if path not in SKY_TEXTURES],
                                  state="readonly", width=55)
        self.asset.current(0)
        self.asset.pack(side="left", padx=(3, 5))
        button(bar, "Загрузить ресурс", app.safe(self.import_asset))
        bar = row(frame)
        self.selector = ttk.Combobox(bar, state="readonly", width=55)
        self.selector.pack(side="left", padx=(0, 7))
        self.selector.bind("<<ComboboxSelected>>", app.safe(self.change_scope))
        button(bar, "Удалить ресурс", app.safe(self.drop))
        button(bar, "Пустой мод", self.clear)
        ttk.Label(frame, text=f"{CONTROL_COUNT} регулируемых позиций для всех поддерживаемых ресурсов.").pack(anchor="w")
        body = ttk.Frame(frame)
        body.pack(fill="both", expand=True, pady=8)
        self.grid = ttk.Frame(body)
        self.grid.pack(side="left", fill="both", expand=True)
        self.preview = ttk.Label(body, text="Выбери текстуру для предпросмотра", anchor="center", width=40)
        self.preview.pack(side="right", padx=8, fill="y")
        bar = row(frame)
        button(bar, "Предпросмотр", app.safe(self.update_preview))
        button(bar, "Сброс выбранного", app.safe(self.reset))
        button(bar, "Сохранить как мод", app.safe(self.save))
        button(bar, "Экспорт ZIP", app.safe(self.export))

    def load(self, name, pack):
        self.name = name
        self.pack = dict(pack)
        self.changes.clear()
        self.refresh(None)

    def clear(self):
        self.load("Мой мод", {})

    def refresh(self, preferred):
        scopes = [GLOBAL_SKY, *PATHS.values()] if SKY_TEXTURES <= self.pack.keys() else []
        scopes.extend(path for path in self.pack if path not in SKY_TEXTURES)
        self.selector["values"] = scopes
        self.selector.set(preferred if preferred in scopes else (scopes[0] if scopes else ""))
        self.set_fields(self.selector.get() or None)
        self.preview.configure(image="", text="Нажми «Предпросмотр»")
        self.photo = None

    def set_fields(self, scope):
        self.scope = scope
        for child in self.grid.winfo_children():
            child.destroy()
        self.fields = {}
        if scope is None:
            return
        if scope.endswith(".ogg"):
            ttk.Label(self.grid, text="Аудиоресурс Ogg Vorbis. Его можно заменить или экспортировать.").grid()
            return
        current = self.changes.get(scope, {})
        for index, (key, (label, low, high, default, step)) in enumerate(controls(scope).items()):
            group, line = divmod(index, 7)
            ttk.Label(self.grid, text=label).grid(row=line, column=group * 2, sticky="w", padx=4, pady=5)
            value = tk.StringVar(value=str(current.get(key, default)))
            ttk.Spinbox(self.grid, from_=low, to=high, increment=step, textvariable=value, width=5).grid(
                row=line, column=group * 2 + 1, sticky="w", padx=6, pady=5)
            self.fields[key] = value

    def capture(self):
        if self.fields:
            try:
                values = {key: int(value.get()) for key, value in self.fields.items()}
            except ValueError:
                raise ValueError("Введите целое число для каждой настройки.") from None
            self.changes[self.scope] = options(self.scope, values)

    def change_scope(self, _):
        next_scope = self.selector.get()
        try:
            self.capture()
        except ValueError:
            self.selector.set(self.scope or "")
            raise
        self.set_fields(next_scope)
        self.update_preview()

    def import_sky(self):
        path = filedialog.askdirectory(parent=self.app.window, title="Папка с bk, dn, ft, lf, rt и up")
        if path:
            self.capture()
            pack = build_skybox(Path(path))
            self.pack.update(pack)
            for scope in (GLOBAL_SKY, *PATHS.values()):
                self.changes.pop(scope, None)
            if len(self.pack) == len(pack):
                self.name = Path(path).name
            self.refresh(GLOBAL_SKY)

    def import_asset(self):
        path = self.asset.get()
        cursor = path.endswith(".png")
        filetypes = [("Изображения", "*.png *.jpg *.jpeg")] if cursor else [("Ogg Vorbis", "*.ogg")]
        source = filedialog.askopenfilename(parent=self.app.window, filetypes=filetypes)
        if source:
            self.capture()
            source = Path(source)
            if source.stat().st_size > (20 * 1024 * 1024 if cursor else ASSETS[path]):
                raise ValueError("Входной файл слишком большой.")
            data = open_cursor(source) if cursor else source.read_bytes()
            validate_asset(path, data)
            if not self.pack:
                self.name = source.stem
            self.pack[path] = data
            self.changes.pop(path, None)
            self.refresh(path)

    def drop(self):
        if not self.scope:
            return
        if self.scope == GLOBAL_SKY or self.scope in SKY_TEXTURES:
            for path in PATHS.values():
                self.pack.pop(path, None)
                self.changes.pop(path, None)
            self.changes.pop(GLOBAL_SKY, None)
        else:
            self.pack.pop(self.scope)
            self.changes.pop(self.scope, None)
        self.refresh(None)

    def update_preview(self):
        self.capture()
        if not self.scope:
            return
        if self.scope.endswith(".ogg"):
            self.preview.configure(image="", text="Ogg Vorbis")
            self.photo = None
            return
        path = PATHS["ft"] if self.scope == GLOBAL_SKY else self.scope
        image = edited_image(path, self.pack[path], self.changes)
        if path in SKY_TEXTURES:
            image.thumbnail((240, 240), Image.Resampling.LANCZOS)
        else:
            image.thumbnail((128, 128))
        self.photo = ImageTk.PhotoImage(image, master=self.app.window)
        self.preview.configure(image=self.photo, text="")

    def reset(self):
        if self.scope:
            self.changes.pop(self.scope, None)
            self.set_fields(self.scope)
            self.update_preview()

    def current_pack(self):
        self.capture()
        return render_pack(self.pack, self.changes)

    def save(self):
        self.app.add_mod(self.name, self.current_pack())

    def export(self):
        self.app.export(self.name, self.current_pack())


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    if "--smoke-test" in sys.argv:
        root.after(1000, root.destroy)
    root.mainloop()
