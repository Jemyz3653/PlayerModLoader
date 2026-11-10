import os
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from zipfile import BadZipFile

from PIL import Image

from modloader import Manager, detect, read_pack
from skybox import build_skybox

VERSION = "0.2.0"


class App:
    def __init__(self, window):
        self.window = window
        self.base = Path(os.environ["LOCALAPPDATA"])
        self.manager = Manager(self.base / "PlayerModLoader")
        self.pack = None
        self.targets = []
        window.title(f"PlayerModLoader {VERSION}")
        window.geometry("680x490")
        window.minsize(540, 440)
        window.configure(bg="#171923")
        style = ttk.Style(window)
        style.theme_use("clam")
        style.configure("TFrame", background="#171923")
        style.configure("TLabel", background="#171923", foreground="#f1f0ff", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI", 24, "bold"))
        style.configure("TButton", background="#343750", foreground="#f1f0ff",
                        padding=(12, 7), font=("Segoe UI", 10))
        style.map("TButton", background=[("active", "#575bb1")],
                  foreground=[("disabled", "#a2a2ad")])
        style.configure("TCombobox", padding=5)
        frame = ttk.Frame(window, padding=22)
        frame.pack(expand=True, fill="both")
        ttk.Label(frame, text="PlayerModLoader", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text="Скайбоксы, курсоры и звуки для Roblox Player").pack(anchor="w", pady=(0, 14))
        target_row = ttk.Frame(frame)
        target_row.pack(fill="x", pady=(0, 10))
        self.selector = ttk.Combobox(target_row, state="readonly")
        self.selector.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.selector.bind("<<ComboboxSelected>>", lambda _: self.refresh())
        ttk.Button(target_row, text="Найти Player", command=self.scan).pack(side="left")
        self.files = tk.Listbox(frame, height=7, bg="#242638", fg="#f1f0ff",
                                highlightthickness=0, relief="flat", font=("Consolas", 10))
        self.files.pack(fill="both", expand=True)
        scrollbar = ttk.Scrollbar(frame, orient="horizontal", command=self.files.xview)
        scrollbar.pack(fill="x")
        self.files.configure(xscrollcommand=scrollbar.set)
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=10)
        ttk.Button(row, text="Открыть ZIP", command=self.choose).pack(side="left", padx=(0, 8))
        ttk.Button(row, text="Собрать небо", command=self.choose_sky).pack(side="left", padx=(0, 8))
        self.apply_button = ttk.Button(row, text="Применить", command=self.apply)
        self.apply_button.pack(side="left", padx=(0, 8))
        self.restore_button = ttk.Button(row, text="Восстановить", command=self.restore)
        self.restore_button.pack(side="left")
        self.status = tk.StringVar()
        ttk.Label(frame, textvariable=self.status, wraplength=495).pack(anchor="w")
        ttk.Label(frame, text="Roblox может заблокировать аккаунт за изменённый клиент.",
                  foreground="#ffb7a4", wraplength=495).pack(anchor="w", pady=(9, 0))
        self.scan()

    def target(self):
        index = self.selector.current()
        return self.targets[index] if index >= 0 else None

    def scan(self):
        current = self.target()
        try:
            self.targets = detect(self.base)
        except OSError as exc:
            messagebox.showerror("Поиск Roblox Player", str(exc), parent=self.window)
            return
        self.selector["values"] = [
            f"{target.label} · {target.version.name}" for target in self.targets
        ]
        if self.targets:
            self.selector.current(self.targets.index(current) if current in self.targets else 0)
        else:
            self.selector.set("")
        self.refresh()

    def refresh(self):
        target = self.target()
        active = target is not None and self.manager.active(target)
        self.apply_button["state"] = "normal" if target and self.pack and not active else "disabled"
        self.restore_button["state"] = "normal" if active else "disabled"
        if active:
            message = "Пак применён. Исходное состояние можно восстановить."
        elif target is None:
            message = "Player не найден. Поддерживаются установки Roblox с сайта и Bloxstrap."
        else:
            message = "Открой ZIP или собери небо. Перед применением закрой Player."
        self.status.set(message)

    def choose(self):
        filename = filedialog.askopenfilename(parent=self.window, filetypes=[("ZIP-паки", "*.zip")])
        if not filename:
            return
        try:
            pack = read_pack(Path(filename))
        except (OSError, ValueError, BadZipFile, RuntimeError, NotImplementedError) as exc:
            messagebox.showerror("Импорт ZIP", str(exc), parent=self.window)
            return
        self.show_pack(Path(filename).name, pack)

    def choose_sky(self):
        directory = filedialog.askdirectory(parent=self.window,
            title="Папка с bk, dn, ft, lf, rt и up в PNG или JPEG")
        if not directory:
            return
        try:
            self.window.configure(cursor="watch")
            self.window.update_idletasks()
            pack = build_skybox(Path(directory))
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            messagebox.showerror("Сборка неба", str(exc), parent=self.window)
            return
        finally:
            self.window.configure(cursor="")
        self.show_pack(Path(directory).name, pack)

    def show_pack(self, name, pack):
        self.pack = pack
        self.files.delete(0, "end")
        self.files.insert("end", name, *pack)
        self.refresh()

    def apply(self):
        target = self.target()
        if not target or not self.pack:
            return
        accepted = messagebox.askyesno(
            "Риск блокировки",
            "Roblox может заблокировать аккаунт за изменённый клиент. "
            "Ты отвечаешь за решение использовать этот пак. Продолжить?",
            parent=self.window,
        )
        if accepted:
            self.run(self.manager.apply, target, self.pack)

    def restore(self):
        target = self.target()
        if target:
            self.run(self.manager.restore, target)

    def run(self, action, *args):
        try:
            action(*args)
        except (OSError, ValueError, RuntimeError) as exc:
            messagebox.showerror("PlayerModLoader", str(exc), parent=self.window)
        finally:
            self.refresh()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    if "--smoke-test" in sys.argv:
        root.after(1000, root.destroy)
    root.mainloop()
