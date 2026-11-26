"""Presence Studio desktop interface."""

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
from queue import Empty, SimpleQueue
import re
from threading import Event, Thread
import time
import tkinter as tk
from tkinter import ttk
from urllib.parse import urlsplit
import webbrowser

from discord_rpc import DiscordError, DiscordRPC

BG = "#0B0B13"
CARD = "#151520"
FIELD = "#1D1D2B"
BORDER = "#303044"
WHITE = "#F8F7FF"
MUTED = "#ADAAC0"
ACCENT = "#BCA9FF"
LIVE = "#79E8C4"
SETTINGS = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PresenceStudio" / "settings.json"
PORTAL = "https://discord.com/developers/applications"


@dataclass(frozen=True)
class Activity:
    app_id: str
    title: str
    state: str
    image: str
    minutes: int

    def payload(self, start):
        timestamps = {"start": start}
        if self.minutes:
            timestamps["end"] = start + self.minutes * 60
        payload = {"name": self.title, "details": self.title, "timestamps": timestamps}
        if self.state:
            payload["state"] = self.state
        if self.image:
            payload["assets"] = {"large_image": self.image}
        return payload


def run_activity(activity, stop, events):
    rpc = None
    try:
        rpc = DiscordRPC(activity.app_id)
        if stop.is_set():
            events.put(("finished", "Публикация отменена"))
            return
        start = time.monotonic()
        duration = activity.minutes * 60
        rpc.set_activity(activity.payload(int(time.time())))
        events.put(("live", (start, duration)))
        while True:
            if stop.wait(0.2):
                reason = "Активность снята"
                break
            if duration and time.monotonic() - start >= duration:
                reason = "Время вышло. Активность снята"
                break
            rpc.poll()
        rpc.set_activity(None)
        events.put(("finished", reason))
    except (DiscordError, OSError, ValueError) as error:
        events.put(("finished", f"Ошибка: {error}"))
    finally:
        if rpc:
            rpc.close()


class Studio:
    def __init__(self, root):
        self.root = root
        root.title("Presence Studio")
        root.geometry("1020x748")
        root.minsize(850, 720)
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.settings = self.load_settings()
        self.events = SimpleQueue()
        self.stop = Event()
        self.running = False
        self.closing = False
        self.started = None
        self.duration = 0
        self.active = None
        self.title = tk.StringVar(value=self.settings.get("title") or "")
        self.state = tk.StringVar(value=self.settings.get("state") or "")
        self.image = tk.StringVar(value=self.settings.get("image") or "")
        self.minutes = tk.StringVar(value=self.settings.get("minutes", "0"))
        self.app_id = tk.StringVar(value=self.settings.get("app_id") or "")
        self.status = tk.StringVar(value="Готово к публикации")
        self.timer = tk.StringVar(value="—")
        self.preview_title = tk.StringVar()
        self.preview_state = tk.StringVar()
        self.preview_image = tk.StringVar()
        self.motion_start = time.monotonic()
        self.build()
        for variable in (self.title, self.state, self.image):
            variable.trace_add("write", self.update_preview)
        self.update_preview()
        self.root.after(100, self.update)
        self.animate()

    @staticmethod
    def load_settings():
        try:
            settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
            if isinstance(settings, dict):
                return {key: value for key, value in settings.items() if isinstance(value, str)}
        except (OSError, ValueError):
            pass
        return {}

    def save_settings(self):
        settings = {key: getattr(self, key).get().strip()
                    for key in ("app_id", "title", "state", "minutes", "image")}
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        temporary = SETTINGS.with_suffix(".tmp")
        temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(SETTINGS)

    def build(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), foreground=WHITE, background=CARD,
                        lightcolor=FIELD, darkcolor=FIELD, focuscolor=ACCENT)
        style.configure("TEntry", fieldbackground=FIELD, bordercolor=BORDER, padding=(10, 4),
                        insertcolor=WHITE)
        style.map("TEntry", bordercolor=[("focus", ACCENT)])
        style.configure("TButton", background=FIELD, bordercolor=BORDER, padding=(14, 8))
        style.map("TButton", background=[("active", "#322945")],
                  foreground=[("disabled", "#747086")])
        style.configure("Accent.TButton", background=ACCENT, foreground=BG,
                        bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT,
                        font=("Segoe UI Semibold", 10))
        style.map("Accent.TButton", background=[("disabled", "#342C4A"), ("active", "#D5C7FF")])
        container = tk.Frame(self.root, bg=BG)
        container.pack(fill="both", expand=True, padx=28, pady=18)

        self.hero = tk.Canvas(container, height=120, bg=CARD, highlightthickness=0)
        self.hero.pack(fill="x")
        self.hero.bind("<Configure>", self.draw_hero)
        columns = tk.Frame(container, bg=BG)
        columns.pack(fill="both", expand=True, pady=(16, 0))
        columns.grid_columnconfigure(0, weight=3, minsize=470)
        columns.grid_columnconfigure(1, weight=2, minsize=310)
        columns.grid_rowconfigure(0, weight=1)
        form = tk.Frame(columns, bg=CARD, padx=19, pady=14, highlightthickness=1,
                        highlightbackground=BORDER)
        form.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        preview = tk.Frame(columns, bg=CARD, padx=19, pady=14, highlightthickness=1,
                           highlightbackground=BORDER)
        preview.grid(row=0, column=1, sticky="nsew")

        self.label(form, "Твоя активность", CARD, size=16, bold=True).pack(anchor="w", pady=(0, 12))
        self.field(form, "Название", self.title)
        self.field(form, "Описание", self.state)
        self.field(form, "Картинка · HTTPS-ссылка или ключ изображения", self.image)
        self.field(form, "Длительность в минутах · 0 без ограничения", self.minutes)
        self.field(form, "Discord Application ID", self.app_id,
                   link=("Developer Portal ↗", lambda: webbrowser.open(PORTAL)))

        actions = tk.Frame(form, bg=CARD)
        actions.pack(fill="x", pady=(10, 0))
        self.publish_button = ttk.Button(actions, text="Опубликовать", command=self.publish,
                                         style="Accent.TButton")
        self.publish_button.pack(side="left", fill="x", expand=True)
        self.stop_button = ttk.Button(actions, text="Снять", command=self.stop_activity, state="disabled")
        self.stop_button.pack(side="left", padx=(8, 0))

        self.label(preview, "Предпросмотр", CARD, size=16, bold=True).pack(anchor="w")
        tile = tk.Frame(preview, bg=FIELD, highlightthickness=1, highlightbackground=BORDER)
        tile.pack(fill="x", pady=(10, 18))
        self.signal = tk.Canvas(tile, height=70, bg="#28203B", highlightthickness=0)
        self.signal.pack(fill="x")
        self.signal.bind("<Configure>", self.draw_signal)
        tile_body = tk.Frame(tile, bg=FIELD, padx=12, pady=9)
        tile_body.pack(fill="x")
        self.label(tile_body, self.preview_title, FIELD, bold=True, size=14,
                   wraplength=245).pack(anchor="w")
        self.label(tile_body, self.preview_state, FIELD, color=MUTED,
                   wraplength=245, size=10).pack(anchor="w", pady=(2, 0))
        self.label(tile_body, self.preview_image, FIELD, color=ACCENT, size=9,
                   wraplength=245).pack(anchor="w", pady=(8, 0))
        self.label(preview, "Таймер", CARD, color=MUTED, size=10).pack(anchor="w")
        tk.Label(preview, textvariable=self.timer, bg=CARD, fg=WHITE,
                 font=("Consolas", 28), padx=0).pack(anchor="w")
        self.track = tk.Canvas(preview, height=6, bg=FIELD, highlightthickness=0)
        self.track.pack(fill="x", pady=(2, 17))
        self.progress = self.track.create_rectangle(0, 0, 0, 6, fill=ACCENT, outline="")
        status_row = tk.Frame(preview, bg=CARD)
        status_row.pack(fill="x")
        self.status_dot = tk.Canvas(status_row, width=19, height=22, bg=CARD, highlightthickness=0)
        self.status_dot.pack(side="left", anchor="n")
        self.halo = self.status_dot.create_oval(0, 0, 0, 0, outline="")
        self.dot = self.status_dot.create_oval(4, 7, 12, 15, fill=MUTED, outline="")
        self.status_label = self.label(status_row, self.status, CARD, color=MUTED,
                                       wraplength=225, size=10)
        self.status_label.pack(side="left", fill="x", anchor="w")
        self.label(preview, "Для публикации нужен открытый настольный Discord.", CARD,
                   color=MUTED, wraplength=245).pack(side="bottom", anchor="w")

    def draw_hero(self, event):
        canvas = self.hero
        canvas.delete("all")
        width, height = event.width, event.height
        for index in range(48):
            amount = index / 47
            color = f"#{int(23 + 25 * amount):02x}{int(20 + 10 * amount):02x}{int(40 + 40 * amount):02x}"
            canvas.create_rectangle(width * index / 48, 0, width * (index + 1) / 48 + 1,
                                    height, fill=color, outline="")
        canvas.create_rectangle(0, 0, width - 1, height - 1, outline="#45375F")
        canvas.create_text(25, 36, anchor="w", text="Presence Studio",
                           font=("Segoe UI Semibold", 29), fill=WHITE)
        canvas.create_text(27, 83, anchor="w", text="Управляй своей активностью в Discord",
                           font=("Segoe UI", 11), fill="#D1C4EA")
        center_x, center_y = width - 144, height / 2
        for radius in (27, 44, 61, 78, 95):
            canvas.create_oval(center_x - radius, center_y - radius * .58,
                               center_x + radius, center_y + radius * .58,
                               outline="#605080")
        canvas.create_polygon(center_x, center_y - 16, center_x + 10, center_y,
                              center_x, center_y + 16, center_x - 10, center_y,
                              fill="#DDCBFF", outline="#F3E9FF")
        canvas.create_oval(0, 0, 0, 0, fill=ACCENT, outline="", tags="satellite")
        canvas.create_oval(0, 0, 0, 0, fill=LIVE, outline="", tags="satellite_small")

    def draw_signal(self, event):
        canvas = self.signal
        canvas.delete("all")
        width = event.width
        for index in range(24):
            color = f"#{40 + index:02x}{32 + index // 3:02x}{59 + index:02x}"
            canvas.create_rectangle(width * index / 24, 0, width * (index + 1) / 24 + 1,
                                    70, fill=color, outline="")
        for y in (16, 35, 54):
            canvas.create_line(0, y, width, y, fill="#4B3D61")
        canvas.create_line(0, 35, width, 35, width=2, fill=ACCENT, tags="wave")

    def animate(self):
        if self.root.winfo_viewable():
            now = time.monotonic()
            phase = now - self.motion_start
            center_x = self.hero.winfo_width() - 144
            for tag, radius, angle, dot_size in (
                ("satellite", 78, phase * 1.15, 4),
                ("satellite_small", 44, 2.6 - phase * 1.5, 3),
            ):
                x = center_x + radius * math.cos(angle)
                y = 60 + radius * .58 * math.sin(angle)
                self.hero.coords(tag, x - dot_size, y - dot_size, x + dot_size, y + dot_size)

            live = self.started is not None
            pending = self.running and not live
            color = LIVE if live else ACCENT if pending else MUTED
            self.status_dot.itemconfigure(self.dot, fill=color)
            self.status_label.configure(fg=color)
            if live or pending:
                radius = 4 + 3 * (1 + math.sin(phase * 5)) / 2
                self.status_dot.coords(self.halo, 8 - radius, 11 - radius, 8 + radius, 11 + radius)
                self.status_dot.itemconfigure(self.halo, outline="#356552" if live else "#685696")
            else:
                self.status_dot.itemconfigure(self.halo, outline="")

            width = self.signal.winfo_width()
            amplitude = 13 if live else 7
            wave = []
            for index in range(49):
                position = index / 48
                envelope = math.sin(position * math.pi)
                offset = math.sin(position * 22 - phase * 3) * amplitude * envelope
                wave.extend((width * position, 35 + offset))
            self.signal.coords("wave", *wave)
            self.signal.itemconfigure("wave", fill=LIVE if live else ACCENT)

            track_width = self.track.winfo_width()
            left, right = 0, 0
            if live and self.duration:
                right = track_width * max(0, 1 - (now - self.started) / self.duration)
            elif self.running:
                sweep = (math.sin(phase * 2.4) + 1) / 2
                left = track_width * .72 * sweep
                right = left + track_width * .28
            self.track.coords(self.progress, left, 0, right, 6)
            self.track.itemconfigure(self.progress, fill=LIVE if live else ACCENT)
        self.root.after(33, self.animate)

    @staticmethod
    def label(parent, content, background, color=WHITE, size=11, bold=False, **options):
        binding = {"textvariable": content} if isinstance(content, tk.StringVar) else {"text": content}
        return tk.Label(parent, **binding, bg=background, fg=color, anchor="w", justify="left",
                        font=("Segoe UI", size, "bold" if bold else "normal"), **options)

    def field(self, parent, name, variable, link=None):
        heading = tk.Frame(parent, bg=CARD)
        heading.pack(fill="x")
        self.label(heading, name, CARD, color=MUTED).pack(side="left")
        if link:
            button = self.label(heading, link[0], CARD, color=ACCENT, size=9, cursor="hand2",
                                takefocus=True)
            button.pack(side="right")
            button.bind("<Button-1>", lambda _: link[1]())
            button.bind("<Return>", lambda _: link[1]())
        entry = ttk.Entry(parent, textvariable=variable)
        entry.pack(fill="x", pady=(2, 8))
        entry.bind("<<Paste>>", lambda event: self.paste(event.widget))
        entry.bind("<Control-KeyPress>", self.paste_shortcut)
        menu = tk.Menu(entry, tearoff=False)
        menu.add_command(label="Вставить", accelerator="Ctrl+V", command=lambda: self.paste(entry))
        entry.bind("<Button-3>", lambda event: self.open_field_menu(event, menu))

    @staticmethod
    def open_field_menu(event, menu):
        event.widget.focus_set()
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def paste_shortcut(self, event):
        if event.keycode == 0x56 and not (event.state & 0x0001):  # Windows virtual key V.
            return self.paste(event.widget)
        return None

    @staticmethod
    def paste(entry):
        try:
            clipboard = entry.clipboard_get()
        except tk.TclError:
            return "break"
        value = " ".join(clipboard.splitlines())
        if value:
            index = entry.index("insert")
            if entry.selection_present():
                index = entry.index("sel.first")
                entry.delete(index, "sel.last")
            entry.icursor(index)
            entry.insert("insert", value)
            entry.xview("insert")
        return "break"

    def update_preview(self, *_):
        title = self.title.get().strip()
        state = self.state.get().strip()
        image = self.image.get().strip()
        if self.active:
            title, state, image = self.active.title, self.active.state, self.active.image
        self.preview_title.set(title or "Название активности")
        self.preview_state.set(state or " ")
        image_label = image if len(image) <= 48 else image[:45] + "…"
        self.preview_image.set(f"Изображение · {image_label}" if image else "Изображение Discord")

    def publish(self):
        app_id = self.app_id.get().strip()
        if not re.fullmatch(r"[0-9]{17,20}", app_id) or not 0 < int(app_id) < 2**64:
            self.status.set("Введи Application ID из Discord Developer Portal.")
            return
        title = self.title.get().strip()
        state = self.state.get().strip()
        image = self.image.get().strip()
        minutes = self.minutes.get().strip()
        if not re.fullmatch(r"[0-9]{1,4}", minutes) or int(minutes) > 1440:
            self.status.set("Введи длительность от 0 до 1440 минут.")
            return
        if not 2 <= len(title) <= 128 or (state and not 2 <= len(state) <= 128):
            self.status.set("Название и заполненное описание должны содержать от 2 до 128 символов.")
            return
        try:
            if image and not re.fullmatch(r"[a-z0-9_-]{1,128}", image):
                url = urlsplit(image)
                if url.scheme != "https" or not url.hostname or url.username or url.password:
                    raise ValueError
        except ValueError:
            self.status.set("Укажи HTTPS-ссылку или ключ изображения из Developer Portal.")
            return
        try:
            self.save_settings()
        except OSError:
            self.status.set("Публикую. Сохранить настройки не удалось.")
        else:
            self.status.set("Подключаюсь к Discord…")
        self.active = Activity(app_id, title, state, image, int(minutes))
        self.update_preview()
        self.running = True
        self.stop.clear()
        self.publish_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        Thread(target=run_activity, args=(self.active, self.stop, self.events), daemon=True).start()

    def stop_activity(self):
        self.stop.set()
        self.stop_button.configure(state="disabled")
        self.status.set("Снимаю активность…")

    def update(self):
        while True:
            try:
                kind, value = self.events.get_nowait()
            except Empty:
                break
            if kind == "live":
                self.started, self.duration = value
                if not self.stop.is_set():
                    self.status.set("Discord принял активность")
            elif kind == "finished":
                self.running = False
                self.started = None
                self.active = None
                self.update_preview()
                self.timer.set("—")
                self.status.set(value)
                self.publish_button.configure(state="normal")
                self.stop_button.configure(state="disabled")
                if self.closing:
                    self.root.destroy()
                    return
        if self.started is not None:
            elapsed = time.monotonic() - self.started
            seconds = max(0, self.duration - int(elapsed)) if self.duration else int(elapsed)
            hours, remainder = divmod(seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            self.timer.set(f"{hours:02}:{minutes:02}:{seconds:02}")
        self.root.after(100, self.update)

    def close(self):
        if self.running:
            self.closing = True
            self.stop_activity()
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    Studio(root)
    root.mainloop()


if __name__ == "__main__":
    main()
