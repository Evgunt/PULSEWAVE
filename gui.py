"""PULSEWAVE 4.0.4 — Personal VPN Suite."""
import csv
import socket
import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox, filedialog
from urllib.parse import urlsplit

from github_publisher import GitHubPublisher, GitHubPublishError, save_github_token, load_github_token, delete_github_token

APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
OUTPUT_FILE = APP_DIR / "fast_vless.txt"
LOG_FILE = APP_DIR / "vlesschecker.log"
SOURCES_FILE = APP_DIR / "sources.txt"
UI_CONFIG = APP_DIR / "vlesschecker_ui.json"
SINGBOX_EXE = APP_DIR / "singBox" / "sing-box.exe"
APP_VERSION = "4.0.4"
BRAND = "PULSEWAVE"
ACCENT = "#00DADA"
BG = "#080D12"
PANEL = "#101922"
TEXT = "#E6F0F4"
MUTED = "#8FA3AD"
TUNNEL_CONFIG = APP_DIR / "vlesschecker_tun.json"
TUNNEL_LOG = APP_DIR / "vlesschecker_tunnel.log"
PENDING_TUNNEL = APP_DIR / "pending_tunnel.json"


def resource_path(name):
    """Resolve bundled resources both in source mode and PyInstaller builds."""
    base = Path(getattr(sys, "_MEIPASS", APP_DIR))
    candidate = base / name
    return candidate if candidate.is_file() else APP_DIR / name


def install_input_shortcuts(root):
    """Make clipboard shortcuts and a small context menu reliable in Tk inputs."""
    def paste(event):
        try:
            event.widget.event_generate("<<Paste>>")
        except tk.TclError:
            pass
        return "break"

    def copy(event):
        try:
            event.widget.event_generate("<<Copy>>")
        except tk.TclError:
            pass
        return "break"

    def cut(event):
        try:
            event.widget.event_generate("<<Cut>>")
        except tk.TclError:
            pass
        return "break"

    for cls in ("Entry", "TEntry", "TCombobox", "Text"):
        root.bind_class(cls, "<Control-v>", paste, add="+")
        root.bind_class(cls, "<Control-V>", paste, add="+")
        root.bind_class(cls, "<Shift-Insert>", paste, add="+")
        root.bind_class(cls, "<Control-c>", copy, add="+")
        root.bind_class(cls, "<Control-C>", copy, add="+")
        root.bind_class(cls, "<Control-x>", cut, add="+")

    menu = tk.Menu(root, tearoff=False, bg="#17232D", fg="#E6F0F4",
                   activebackground="#00AEB4", activeforeground="#061014")
    def show_menu(event):
        widget = event.widget
        try:
            widget.focus_set()
            menu.delete(0, "end")
            menu.add_command(label="Вырезать", command=lambda: widget.event_generate("<<Cut>>"))
            menu.add_command(label="Копировать", command=lambda: widget.event_generate("<<Copy>>"))
            menu.add_command(label="Вставить", command=lambda: widget.event_generate("<<Paste>>"))
            menu.add_separator()
            menu.add_command(label="Выделить всё", command=lambda: widget.event_generate("<<SelectAll>>"))
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    for cls in ("Entry", "TEntry", "TCombobox", "Text"):
        root.bind_class(cls, "<Button-3>", show_menu, add="+")

class PULSEWAVEGUI:
    def __init__(self, root):
        self.root = root
        self.root.title(f"PULSEWAVE {APP_VERSION}")
        self.app_icon = None
        # Explicitly set both window and taskbar icons; keep a strong PhotoImage reference.
        try:
            ico_path = resource_path("app_icon.ico")
            if os.name == "nt" and ico_path.is_file():
                self.root.iconbitmap(str(ico_path))
                try:
                    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PULSEWAVE.Desktop.4")
                except Exception:
                    pass
        except Exception:
            pass
        try:
            png_path = resource_path("app_icon.png")
            if png_path.is_file():
                self.app_icon = tk.PhotoImage(file=str(png_path))
                self.root.iconphoto(True, self.app_icon)
        except Exception:
            self.app_icon = None
        # Re-apply the native ICO after iconphoto so Windows keeps the small title-bar icon.
        try:
            ico_path = resource_path("app_icon.ico")
            if os.name == "nt" and ico_path.is_file():
                self.root.iconbitmap(str(ico_path))
        except Exception:
            pass
        self.root.configure(bg=BG)
        self.root.option_add("*Background", BG)
        self.root.option_add("*Foreground", TEXT)
        self.root.geometry("1180x790")
        self.root.minsize(920, 640)
        self.root.after(250, self.apply_dark_titlebar)
        self.proc = None
        self.messages = queue.Queue()
        self.reader_thread = None
        self.rows_by_link = {}
        self.log_buffer = ""
        self.sort_column = "ping"
        self.sort_reverse = False
        self.quick_check_running = False
        self.quick_check_total = 0
        self.quick_check_done = 0
        self.config = self.load_ui_config()
        self.tray_icon = None
        self.tray_thread = None
        self.tray_error_shown = False
        from tunnel import TunnelController, is_windows_admin
        self.tunnel = TunnelController(SINGBOX_EXE, TUNNEL_CONFIG, TUNNEL_LOG)
        self.is_admin = is_windows_admin()
        self.scheduler_enabled = bool(self.config.get("schedule_enabled", False))
        self.schedule_interval_var = tk.StringVar(value=str(self.config.get("schedule_interval", 60)))
        self.next_scheduled_run = None
        self.scheduled_run_active = False
        self.publish_after_check = False
        self.github_status_var = tk.StringVar(value="GitHub: не настроен")
        self.schedule_status_var = tk.StringVar(value="Автопроверка выключена")

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=BG, foreground=TEXT, font=("Segoe UI", 9))
        style.configure("TFrame", background=BG)
        style.configure("AppShell.TFrame", background=BG)
        style.configure("TLabelframe", background="#0D151D", foreground=ACCENT, bordercolor="#263943", relief="solid", borderwidth=1)
        style.configure("TLabelframe.Label", background="#0D151D", foreground=ACCENT, font=("Segoe UI", 9, "bold"))
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Card.TLabel", background="#0D151D", foreground=TEXT)
        style.configure("AppTitle.TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 22, "bold"))
        style.configure("SubTitle.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Metric.TLabel", background=PANEL, foreground=ACCENT, font=("Segoe UI", 18, "bold"))
        style.configure("TButton", background="#172630", foreground=TEXT, bordercolor="#304650",
                        padding=(16, 10), relief="flat", font=("Segoe UI", 9, "bold"), focusthickness=1,
                        focuscolor=ACCENT)
        style.map("TButton", background=[("active", "#23404B"), ("disabled", "#17212A")], foreground=[("disabled", "#60717A")])
        style.configure("Accent.TButton", background="#00C8C8", foreground="#041014", font=("Segoe UI", 10, "bold"), padding=(18, 10), relief="flat")
        style.map("Accent.TButton", background=[("active", "#20E5E5")], foreground=[("active", "#061014")])
        style.configure("TEntry", fieldbackground="#111D27", foreground=TEXT, bordercolor="#2C4653",
                        lightcolor="#2C4653", darkcolor="#2C4653", padding=(12, 10), relief="flat",
                        insertcolor=ACCENT, font=("Segoe UI", 10))
        style.map("TEntry", bordercolor=[("focus", ACCENT), ("!focus", "#2C4653")],
                  lightcolor=[("focus", ACCENT), ("!focus", "#2C4653")],
                  darkcolor=[("focus", ACCENT), ("!focus", "#2C4653")],
                  fieldbackground=[("disabled", "#0D151D")])
        style.configure("TCombobox", fieldbackground="#111B24", background="#1A2933",
                        foreground=TEXT, arrowcolor=ACCENT, bordercolor="#304650",
                        padding=(10, 9), font=("Segoe UI", 10), relief="flat")
        style.map("TCombobox", fieldbackground=[("readonly", "#111B24"), ("focus", "#142630")],
                  foreground=[("readonly", TEXT)], bordercolor=[("focus", ACCENT)])
        style.configure("TCheckbutton", background=BG, foreground=TEXT, padding=(3, 5), font=("Segoe UI", 9))
        style.map("TCheckbutton", background=[("active", BG)], foreground=[("active", ACCENT)])
        style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 5, 0, 0))
        style.configure("TNotebook.Tab", background="#111D27", foreground=MUTED, padding=(20, 11), borderwidth=0, font=("Segoe UI", 9, "bold"))
        style.map("TNotebook.Tab", background=[("selected", "#18313A"), ("active", "#1A2933")], foreground=[("selected", ACCENT), ("active", TEXT)])
        style.configure("Treeview", rowheight=40, font=("Segoe UI", 9), background="#0B131B", fieldbackground="#0B131B", foreground=TEXT, bordercolor="#263943", relief="flat")
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"), background="#14232D", foreground=ACCENT, relief="flat", padding=(10, 11))
        style.map("Treeview", background=[("selected", "#17434A")], foreground=[("selected", "#FFFFFF")])
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor="#1A2933", bordercolor="#1A2933")
        style.configure("TScrollbar", background="#17232D", troughcolor=BG, bordercolor=BG, arrowcolor=MUTED, relief="flat")
        style.map("TScrollbar", background=[("active", "#29404A")])
        install_input_shortcuts(root)
        shell = ttk.Frame(root, padding=20, style="AppShell.TFrame")
        shell.pack(fill="both", expand=True)
        header = ttk.Frame(shell)
        header.pack(fill="x", pady=(0, 14))
        brand = ttk.Frame(header)
        brand.pack(side="left")
        logo = tk.Canvas(brand, width=48, height=42, bg=BG, highlightthickness=0)
        logo.create_line(3, 23, 14, 23, 20, 16, 26, 32, 34, 5, 40, 23, 46, 23, fill=ACCENT, width=3, capstyle="round", joinstyle="round")
        logo.pack(side="left", padx=(0, 12))
        brand_text = ttk.Frame(brand)
        brand_text.pack(side="left")
        ttk.Label(brand_text, text="PULSEWAVE", style="AppTitle.TLabel").pack(anchor="w")
        ttk.Label(brand_text, text="МЕНЕДЖЕР СЕТЕВЫХ ПОДКЛЮЧЕНИЙ  •  ТВОЯ СЕТЬ. ТВОЙ PULSE.", style="SubTitle.TLabel").pack(anchor="w")
        
        self.status = tk.StringVar(value="Готов к работе")
        ttk.Label(header, textvariable=self.status, font=("Segoe UI", 10, "bold")).pack(side="right", padx=6)

        # Счётчики оставлены для внутренней логики, но карточки статистики удалены из интерфейса.
        self.metric_vars = {k: tk.StringVar(value="0") for k in ("checked", "working", "best", "active")}

        self.concurrency_var = tk.StringVar(value=str(self.config.get("concurrency", 100)))
        self.target_var = tk.StringVar(value=str(self.config.get("target", 60)))
        self.batch_var = tk.StringVar(value=str(self.config.get("batch", 300)))
        self.git_var = tk.BooleanVar(value=bool(self.config.get("git_push", False)))
        self.autostart_var = tk.BooleanVar(value=self.is_autostart_enabled())
        self.tray_on_close_var = tk.BooleanVar(value=bool(self.config.get("tray_on_close", True)))
        self.schedule_enabled_var = tk.BooleanVar(value=self.scheduler_enabled)
        self.schedule_interval_var = tk.StringVar(value=str(self.config.get("schedule_interval", 60)))

        actions = ttk.Frame(shell)
        actions.pack(fill="x", pady=(0, 10))
        self.start_btn = ttk.Button(actions, text="▶  Запустить проверку", command=self.start, style="Accent.TButton")
        self.start_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=3)
        self.stop_btn = ttk.Button(actions, text="■  Остановить", command=self.stop, state="disabled")
        self.stop_btn.grid(row=0, column=1, sticky="ew", padx=6, pady=3)
        ttk.Button(actions, text="Источники…", command=self.edit_sources).grid(row=0, column=2, sticky="ew", padx=6, pady=3)
        ttk.Button(actions, text="Экспорт CSV…", command=self.export_csv).grid(row=0, column=3, sticky="ew", padx=6, pady=3)
        ttk.Button(actions, text="Открыть TXT", command=self.open_results).grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=3)
        ttk.Button(actions, text="Сохранить результат", command=self.save_results_as).grid(row=1, column=1, sticky="ew", padx=6, pady=3)
        ttk.Button(actions, text="⚙ Настройки", command=self.show_settings).grid(row=1, column=2, sticky="ew", padx=6, pady=3)
        ttk.Button(actions, text="О программе", command=self.about).grid(row=1, column=3, sticky="ew", padx=6, pady=3)
        ttk.Button(actions, text="Очистить журнал", command=self.clear_log).grid(row=1, column=4, sticky="e", padx=(6, 0), pady=3)
        for action_col in range(5):
            actions.columnconfigure(action_col, weight=1 if action_col < 4 else 0)

        tunnel_bar = ttk.LabelFrame(shell, text="  VPN-ТУННЕЛЬ  ", padding=(16, 12))
        tunnel_bar.pack(fill="x", pady=(0, 10))
        self.tunnel_status = tk.StringVar(value="Отключено")
        ttk.Label(tunnel_bar, text="Статус:").pack(side="left")
        ttk.Label(tunnel_bar, textvariable=self.tunnel_status, font=("Segoe UI", 10, "bold")).pack(side="left", padx=(5, 16))
        self.connect_btn = ttk.Button(tunnel_bar, text="Подключить выбранный сервер", command=self.connect_selected)
        self.connect_btn.pack(side="left")
        self.disconnect_btn = ttk.Button(tunnel_bar, text="Отключить", command=self.disconnect_tunnel, state="disabled")
        self.disconnect_btn.pack(side="left", padx=7)
        ttk.Label(tunnel_bar, text="Для TUN требуется подтверждение UAC.", style="SubTitle.TLabel").pack(side="right")

        self.progress = ttk.Progressbar(shell, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(0, 10))
        self.progress_text = tk.StringVar(value="Ожидание запуска")
        ttk.Label(shell, textvariable=self.progress_text, style="SubTitle.TLabel").pack(anchor="w", pady=(0, 6))

        notebook = ttk.Notebook(shell)
        notebook.pack(fill="both", expand=True)
        self.main_notebook = notebook
        results_tab = ttk.Frame(notebook, padding=8)
        log_tab = ttk.Frame(notebook, padding=8)
        settings_tab = ttk.Frame(notebook, padding=0)
        notebook.add(results_tab, text="  Рабочие серверы  ")
        notebook.add(log_tab, text="  Журнал  ")
        notebook.add(settings_tab, text="  Настройки  ")
        self.settings_tab = settings_tab

        settings_canvas = tk.Canvas(settings_tab, bg=BG, highlightthickness=0, borderwidth=0)
        settings_scroll = ttk.Scrollbar(settings_tab, orient="vertical", command=settings_canvas.yview)
        settings_canvas.configure(yscrollcommand=settings_scroll.set)
        settings_scroll.pack(side="right", fill="y")
        settings_canvas.pack(side="left", fill="both", expand=True)
        settings_content = ttk.Frame(settings_canvas, padding=14)
        settings_window = settings_canvas.create_window((0, 0), window=settings_content, anchor="nw")
        def _settings_content_changed(_event=None):
            settings_canvas.configure(scrollregion=settings_canvas.bbox("all"))
        def _settings_canvas_resized(event):
            settings_canvas.itemconfigure(settings_window, width=event.width)
        settings_content.bind("<Configure>", _settings_content_changed)
        settings_canvas.bind("<Configure>", _settings_canvas_resized)
        settings_canvas.bind_all("<MouseWheel>", lambda event: settings_canvas.yview_scroll(int(-event.delta / 120), "units") if self.main_notebook.select() == str(settings_tab) else None)

        self.settings_home = ttk.Frame(settings_content)
        self.settings_home.pack(fill="both", expand=True)
        ttk.Label(self.settings_home, text="НАСТРОЙКИ PULSEWAVE", style="AppTitle.TLabel").pack(anchor="w", pady=(0, 14))
        check_frame = ttk.LabelFrame(self.settings_home, text="  ПАРАМЕТРЫ ПРОВЕРКИ  ", padding=14)
        check_frame.pack(fill="x", pady=(0, 12))
        for col, (label, var) in enumerate((
            ("Параллельные проверки", self.concurrency_var),
            ("Найти рабочих серверов", self.target_var),
            ("Размер пачки", self.batch_var),
        )):
            ttk.Label(check_frame, text=label).grid(row=0, column=col, sticky="w", padx=(0, 12))
            ttk.Entry(check_frame, textvariable=var, width=14).grid(row=1, column=col, sticky="w", padx=(0, 18), pady=(5, 8))
        ttk.Checkbutton(check_frame, text="Публиковать fast_vless.txt в GitHub после проверки", variable=self.git_var, command=self.save_preferences).grid(row=2, column=0, columnspan=3, sticky="w", pady=5)
        ttk.Checkbutton(check_frame, text="Запускать вместе с Windows", variable=self.autostart_var, command=self.toggle_autostart).grid(row=3, column=0, sticky="w", pady=5)
        ttk.Checkbutton(check_frame, text="При закрытии прятать в системный трей", variable=self.tray_on_close_var, command=self.save_preferences).grid(row=3, column=1, columnspan=2, sticky="w", pady=5)

        schedule_frame = ttk.LabelFrame(self.settings_home, text="  АВТОМАТИЧЕСКАЯ ПРОВЕРКА  ", padding=14)
        schedule_frame.pack(fill="x", pady=(0, 12))
        ttk.Checkbutton(schedule_frame, text="Включить расписание", variable=self.schedule_enabled_var, command=self.toggle_scheduler).pack(side="left")
        ttk.Label(schedule_frame, text="Интервал:").pack(side="left", padx=(18, 6))
        self.schedule_combo = ttk.Combobox(schedule_frame, textvariable=self.schedule_interval_var, values=("15", "30", "60", "120", "240"), width=7, state="readonly")
        self.schedule_combo.pack(side="left")
        self.schedule_combo.bind("<<ComboboxSelected>>", self.schedule_interval_changed)
        ttk.Label(schedule_frame, text="минут").pack(side="left", padx=6)
        ttk.Label(schedule_frame, textvariable=self.schedule_status_var, style="SubTitle.TLabel").pack(side="left", padx=12)

        settings_actions = ttk.Frame(self.settings_home)
        settings_actions.pack(fill="x", pady=8)
        ttk.Button(settings_actions, text="Настроить GitHub…", command=self.open_github_settings, style="Accent.TButton").pack(side="left")
        ttk.Button(settings_actions, text="Сохранить настройки", command=self.save_preferences).pack(side="left", padx=8)
        ttk.Label(settings_actions, textvariable=self.github_status_var, style="SubTitle.TLabel").pack(side="right")

        self.github_page = ttk.Frame(settings_content)
        self.github_page_title = ttk.Label(self.github_page, text="ПУБЛИКАЦИЯ В GITHUB", style="AppTitle.TLabel")
        self.github_page_title.pack(anchor="w", pady=(0, 14))
        self.github_form = ttk.LabelFrame(self.github_page, text="  ПАРАМЕТРЫ GITHUB  ", padding=14)
        self.github_form.pack(fill="x")
        self.github_owner_var_page = tk.StringVar(value=self.config.get("github_owner", ""))
        self.github_repo_var_page = tk.StringVar(value=self.config.get("github_repo", ""))
        self.github_branch_var_page = tk.StringVar(value=self.config.get("github_branch", "main"))
        self.github_token_var_page = tk.StringVar(value="")
        for row, (label, var, secret) in enumerate((
            ("Владелец / пользователь GitHub", self.github_owner_var_page, False),
            ("Название репозитория", self.github_repo_var_page, False),
            ("Ветка", self.github_branch_var_page, False),
            ("Personal Access Token", self.github_token_var_page, True),
        )):
            ttk.Label(self.github_form, text=label).grid(row=row*2, column=0, sticky="w", pady=(4, 0))
            ttk.Entry(self.github_form, textvariable=var, width=54, show="•" if secret else "").grid(row=row*2+1, column=0, sticky="ew", pady=(3, 7))
        ttk.Label(self.github_form, text="Токен хранится в защищённом хранилище учётных данных Windows.", style="SubTitle.TLabel").grid(row=8, column=0, sticky="w", pady=(2, 10))
        self.github_page_status = tk.StringVar(value="Укажите репозиторий и токен GitHub.")
        ttk.Label(self.github_form, textvariable=self.github_page_status, style="SubTitle.TLabel", wraplength=700).grid(row=9, column=0, sticky="w", pady=(0, 10))
        github_buttons = ttk.Frame(self.github_form)
        github_buttons.grid(row=10, column=0, sticky="ew", pady=(4, 2))
        for button_col in range(4):
            github_buttons.columnconfigure(button_col, weight=1)
        ttk.Button(github_buttons, text="← Назад", command=self.show_settings).grid(row=0, column=0, sticky="ew", padx=(0, 5))
        ttk.Button(github_buttons, text="Проверить доступ", command=self.check_github_access).grid(row=0, column=1, sticky="ew", padx=5)
        ttk.Button(github_buttons, text="Сохранить GitHub", command=self.save_github_settings, style="Accent.TButton").grid(row=0, column=2, sticky="ew", padx=5)
        ttk.Button(github_buttons, text="Опубликовать TXT", command=lambda: self.publish_fast_vless(manual=True)).grid(row=0, column=3, sticky="ew", padx=(5, 0))
        self.github_form.columnconfigure(0, weight=1)

        results_toolbar = ttk.Frame(results_tab)
        results_toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Button(results_toolbar, text="↻  Обновить список", command=self.reload_results_from_file).pack(side="left", padx=(0, 6))
        self.quick_ping_btn = ttk.Button(results_toolbar, text="⚡ Быстрый пинг + доступность", command=self.start_quick_connectivity_check, style="Accent.TButton")
        self.quick_ping_btn.pack(side="left", padx=6)
        ttk.Button(results_toolbar, text="↓ Сортировать по пингу", command=self.sort_by_ping).pack(side="left", padx=6)
        self.quick_ping_status = tk.StringVar(value="Проверка TCP-доступности без изменения проверки VLESS")
        ttk.Label(results_toolbar, textvariable=self.quick_ping_status, style="SubTitle.TLabel").pack(side="left", padx=10)
        columns = ("rank", "name", "protocol", "ip", "ping", "availability", "link")
        self.table = ttk.Treeview(results_tab, columns=columns, show="headings", selectmode="extended")
        headings = {"rank":"#", "name":"Название", "protocol":"Протокол", "ip":"Внешний IP", "ping":"Пинг", "availability":"TCP-доступность", "link":"Ссылка"}
        widths = {"rank":42, "name":190, "protocol":82, "ip":125, "ping":85, "availability":125, "link":380}
        for key in columns:
            self.table.heading(key, text=headings[key], command=lambda c=key: self.sort_table(c))
            self.table.column(key, width=widths[key], minwidth=45, anchor="w")
        yscroll = ttk.Scrollbar(results_tab, orient="vertical", command=self.table.yview)
        xscroll = ttk.Scrollbar(results_tab, orient="horizontal", command=self.table.xview)
        self.table.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.table.grid(row=1, column=0, sticky="nsew")
        yscroll.grid(row=1, column=1, sticky="ns")
        xscroll.grid(row=2, column=0, sticky="ew")
        results_tab.rowconfigure(1, weight=1); results_tab.columnconfigure(0, weight=1)
        self.table.tag_configure("fast", foreground="#16834a")
        self.table.tag_configure("slow", foreground="#a36a16")

        log_frame = ttk.Frame(log_tab)
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(log_frame, wrap="word", font=("Consolas", 9), bg="#0B1218", fg="#DCE6EF", insertbackground=ACCENT, state="disabled")
        log_scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        self.log.pack(side="left", fill="both", expand=True); log_scroll.pack(side="right", fill="y")
        ttk.Label(shell, text=f"PULSEWAVE {APP_VERSION}  •  Результаты: fast_vless.txt  •  Настройки и источники хранятся рядом с приложением", style="SubTitle.TLabel").pack(anchor="w", pady=(9, 0))

        self.load_existing_results()
        self.root.after(100, self.poll_messages)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        if "--connect-pending" in sys.argv:
            self.root.after(900, self.connect_pending)
        self.root.after(800, self.poll_tunnel)
        if self.config.get("github_owner") and self.config.get("github_repo") and load_github_token():
            self.github_status_var.set(f"GitHub: {self.config.get('github_owner')}/{self.config.get('github_repo')}")
        if self.scheduler_enabled:
            self.next_scheduled_run = time.monotonic() + int(self.schedule_interval_var.get()) * 60
        self.root.after(1000, self.scheduler_tick)

    def apply_dark_titlebar(self):
        """Request a dark native title bar on supported Windows 10/11 builds."""
        if os.name != "nt":
            return
        try:
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            if not hwnd:
                hwnd = self.root.winfo_id()
            value = ctypes.c_int(1)
            dwm = ctypes.windll.dwmapi
            # Attribute 20 is DWMWA_USE_IMMERSIVE_DARK_MODE on current Windows;
            # 19 is used by older Windows 10 builds.
            result = dwm.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(value), ctypes.sizeof(value)
            )
            if result != 0:
                dwm.DwmSetWindowAttribute(
                    hwnd, 19, ctypes.byref(value), ctypes.sizeof(value)
                )
        except Exception:
            # Unsupported Windows versions simply retain their system title bar.
            pass

    def load_ui_config(self):
        try:
            return json.loads(UI_CONFIG.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_ui_config(self):
        data = {
            "concurrency": int(self.concurrency_var.get()),
            "target": int(self.target_var.get()),
            "batch": int(self.batch_var.get()),
            "git_push": bool(self.git_var.get()),
            "tray_on_close": bool(self.tray_on_close_var.get()),
            "schedule_enabled": bool(self.schedule_enabled_var.get()),
            "schedule_interval": int(self.schedule_interval_var.get()),
            "github_owner": self.github_owner_var_page.get().strip() if hasattr(self, "github_owner_var_page") else self.config.get("github_owner", ""),
            "github_repo": self.github_repo_var_page.get().strip() if hasattr(self, "github_repo_var_page") else self.config.get("github_repo", ""),
            "github_branch": (self.github_branch_var_page.get().strip() or "main") if hasattr(self, "github_branch_var_page") else self.config.get("github_branch", "main"),
            "autostart": bool(self.autostart_var.get()) if hasattr(self, "autostart_var") else self.config.get("autostart", False),
        }
        temporary = UI_CONFIG.with_suffix(UI_CONFIG.suffix + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, UI_CONFIG)
        self.config.update(data)

    def save_preferences(self):
        try:
            self.save_ui_config()
            self.append("[GUI] Настройки сохранены.\\n")
        except Exception as exc:
            messagebox.showwarning("Настройки", f"Не удалось сохранить настройки: {exc}")

    def autostart_command(self):
        if getattr(sys, "frozen", False):
            return f'"{Path(sys.executable).resolve()}"'
        script = Path(__file__).resolve()
        python = Path(sys.executable).resolve()
        # pythonw avoids opening a console window on Windows login.
        pythonw = python.with_name("pythonw.exe") if os.name == "nt" else python
        return f'"{pythonw}" "{script}"'

    def is_autostart_enabled(self):
        if os.name != "nt":
            return False
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ) as key:
                value, _ = winreg.QueryValueEx(key, "PULSEWAVE")
            return value == self.autostart_command()
        except (OSError, ImportError):
            return False

    def toggle_autostart(self):
        if os.name != "nt":
            self.autostart_var.set(False)
            messagebox.showinfo("Автозапуск", "Автозапуск через реестр доступен только в Windows.")
            return
        try:
            import winreg
            run_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key, 0, winreg.KEY_SET_VALUE) as key:
                if self.autostart_var.get():
                    winreg.SetValueEx(key, "PULSEWAVE", 0, winreg.REG_SZ, self.autostart_command())
                    self.append("[GUI] Автозапуск PULSEWAVE включён.\n")
                else:
                    try:
                        winreg.DeleteValue(key, "PULSEWAVE")
                    except FileNotFoundError:
                        pass
                    self.append("[GUI] Автозапуск PULSEWAVE отключён.\n")
            self.save_preferences()
        except Exception as exc:
            self.autostart_var.set(not self.autostart_var.get())
            messagebox.showerror("Автозапуск", f"Не удалось изменить автозапуск Windows:\n{exc}")

    def ensure_tray(self):
        if self.tray_icon is not None:
            return True
        try:
            import pystray
            from PIL import Image, ImageDraw
            image = Image.new("RGBA", (64, 64), (7, 13, 18, 255))
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((3, 3, 61, 61), radius=13, fill=(7, 13, 18, 255), outline=(31, 55, 65, 255), width=2)
            draw.line([(8, 34), (20, 34), (26, 26), (32, 43), (40, 13), (46, 34), (57, 34)], fill=(0, 218, 218, 255), width=4, joint="curve")
            menu = pystray.Menu(
                pystray.MenuItem("Открыть PULSEWAVE", self.tray_show, default=True),
                pystray.MenuItem("Быстрая проверка", self.tray_quick_check),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Выход", self.tray_exit),
            )
            self.tray_icon = pystray.Icon("PULSEWAVE", image, f"PULSEWAVE {APP_VERSION}", menu)
            self.tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True, name="PULSEWAVETray")
            self.tray_thread.start()
            return True
        except Exception as exc:
            self.tray_icon = None
            if not self.tray_error_shown:
                self.tray_error_shown = True
                messagebox.showerror("Системный трей", f"Не удалось создать значок в трее:\n{exc}\n\nПроверьте установку pystray и Pillow.")
            return False

    def tray_show(self, icon=None, item=None):
        self.root.after(0, self.show_window)

    def tray_quick_check(self, icon=None, item=None):
        self.root.after(0, self.start)

    def tray_exit(self, icon=None, item=None):
        self.root.after(0, self.exit_application)

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.focus_force()
        except tk.TclError:
            pass

    def exit_application(self):
        if self.proc and self.proc.poll() is None:
            if not messagebox.askyesno("Выход", "Проверка ещё выполняется. Остановить её и полностью закрыть программу?"):
                return
            self.stop()
        self.save_preferences_safely()
        if self.tunnel.is_running:
            if not messagebox.askyesno("VPN-туннель активен", "При выходе отключить VPN-туннель?"):
                return
            self.disconnect_tunnel()
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
            self.tray_icon = None
        self.root.destroy()

    def save_preferences_safely(self):
        try:
            self.save_ui_config()
        except Exception:
            pass

    def append(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def upsert_result(self, result):
        link = result.get("link", "")
        if not link:
            return
        try:
            ping = int(result.get("ping", 0))
        except (ValueError, TypeError):
            ping = 0
        row = {"name": result.get("name") or "Без имени", "protocol": result.get("protocol") or link.split(":", 1)[0], "ip": result.get("external_ip", ""), "ping": ping, "availability": result.get("availability", "Не проверено"), "link": link}
        self.rows_by_link[link] = row
        self.refresh_table()

    def refresh_table(self):
        for item in self.table.get_children(): self.table.delete(item)
        sorters = {"rank": lambda r: r["ping"] if r["ping"] > 0 else 10**9,
                   "name": lambda r: r["name"].lower(), "protocol": lambda r: r["protocol"],
                   "ip": lambda r: r["ip"], "ping": lambda r: r["ping"] if r["ping"] > 0 else 10**9,
                   "link": lambda r: r["link"]}
        rows = sorted(self.rows_by_link.values(), key=sorters.get(self.sort_column, sorters["ping"]), reverse=self.sort_reverse)
        for rank, row in enumerate(rows, 1):
            tag = "fast" if row.get("availability") == "Доступен" or (row["ping"] and row["ping"] < 250) else "slow"
            self.table.insert("", "end", values=(rank, row["name"], row["protocol"], row["ip"], f"{row['ping']} мс" if row["ping"] else "—", row.get("availability", "Не проверено"), row["link"]), tags=(tag,))
        self.metric_vars["working"].set(str(len(rows)))
        best = min((r["ping"] for r in rows if r["ping"] > 0), default=0)
        self.metric_vars["best"].set(f"{best} мс" if best else "—")

    def load_existing_results(self):
        if OUTPUT_FILE.exists():
            try:
                for line in OUTPUT_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
                    link = line.strip()
                    if link and "://" in link:
                        self.upsert_result({"link": link, "name": "Сохранённая конфигурация", "protocol": link.split(":",1)[0], "ping": 0})
            except OSError:
                pass

    @staticmethod
    def _tcp_probe(link, timeout=2.0):
        """Measure direct TCP handshake latency; does not parse or validate VLESS."""
        try:
            parsed = urlsplit(link.strip())
            host = parsed.hostname
            port = parsed.port
            if not host or not port:
                return 0, "Некорректный адрес"
            started = time.perf_counter()
            with socket.create_connection((host, port), timeout=timeout):
                elapsed = int((time.perf_counter() - started) * 1000)
            return max(1, elapsed), "Доступен"
        except (OSError, ValueError, socket.timeout):
            return 0, "Недоступен"

    def start_quick_connectivity_check(self):
        if self.quick_check_running:
            return
        rows = list(self.rows_by_link.values())
        if not rows:
            messagebox.showinfo("Быстрая проверка", "Список рабочих серверов пуст. Сначала обновите список или выполните основную проверку.")
            return
        self.quick_check_running = True
        self.quick_check_total = len(rows)
        self.quick_check_done = 0
        self.quick_ping_btn.configure(state="disabled")
        self.quick_ping_status.set(f"Проверка 0/{len(rows)}…")
        self.append(f"\n[Быстрая проверка] Проверяю TCP-доступность {len(rows)} серверов…\n")

        def worker():
            # Bounded concurrency keeps the UI responsive and avoids excessive socket load.
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=min(32, max(1, len(rows)))) as pool:
                futures = {pool.submit(self._tcp_probe, row["link"]): row["link"] for row in rows}
                for future in as_completed(futures):
                    link = futures[future]
                    try:
                        ping, availability = future.result()
                    except Exception:
                        ping, availability = 0, "Недоступен"
                    self.root.after(0, lambda l=link, p=ping, a=availability: self._apply_quick_probe(l, p, a))
            self.root.after(0, self._finish_quick_connectivity_check)

        threading.Thread(target=worker, daemon=True, name="PULSEWAVEQuickPing").start()

    def _apply_quick_probe(self, link, ping, availability):
        row = self.rows_by_link.get(link)
        if row is not None:
            row["ping"] = ping
            row["availability"] = availability
        self.quick_check_done += 1
        self.quick_ping_status.set(f"Проверено {self.quick_check_done}/{self.quick_check_total}")
        self.refresh_table()

    def _finish_quick_connectivity_check(self):
        self.quick_check_running = False
        self.quick_ping_btn.configure(state="normal")
        available = sum(1 for row in self.rows_by_link.values() if row.get("availability") == "Доступен")
        self.sort_column, self.sort_reverse = "ping", False
        self.refresh_table()
        self.quick_ping_status.set(f"Готово: доступно {available}/{self.quick_check_total}. Нажмите заголовок «Пинг» для сортировки.")
        self.append(f"[Быстрая проверка] Завершено. TCP-доступно: {available}/{self.quick_check_total}. Список отсортирован по пингу.\n")

    def sort_by_ping(self):
        self.sort_column, self.sort_reverse = "ping", False
        self.refresh_table()
        self.quick_ping_status.set("Сортировка: минимальный пинг сверху")

    def reload_results_from_file(self):
        """Reload the saved working-server list; this does not run or alter VLESS checks."""
        if not OUTPUT_FILE.exists():
            self.rows_by_link.clear()
            self.refresh_table()
            messagebox.showinfo("Обновление списка", "Файл fast_vless.txt ещё не создан.")
            return
        try:
            links = []
            for raw_line in OUTPUT_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
                link = raw_line.strip()
                if link and "://" in link:
                    links.append(link)
            self.rows_by_link.clear()
            for link in dict.fromkeys(links):
                self.rows_by_link[link] = {
                    "name": "Сохранённая конфигурация",
                    "protocol": link.split(":", 1)[0],
                    "ip": "",
                    "ping": 0,
                    "availability": "Не проверено",
                    "link": link,
                }
            self.refresh_table()
            self.append(f"[GUI] Список обновлён из fast_vless.txt: {len(self.rows_by_link)} записей.\n")
            self.progress_text.set(f"Список рабочих серверов обновлён: {len(self.rows_by_link)}")
        except Exception as exc:
            messagebox.showerror("Обновление списка", f"Не удалось перечитать fast_vless.txt:\n{exc}")

    def validate_settings(self):
        try:
            concurrency, target, batch = int(self.concurrency_var.get()), int(self.target_var.get()), int(self.batch_var.get())
            if not 1 <= concurrency <= 1000: raise ValueError("Количество потоков должно быть от 1 до 1000.")
            if not 1 <= target <= 10000: raise ValueError("Цель должна быть от 1 до 10000.")
            if not 1 <= batch <= 5000: raise ValueError("Размер пачки должен быть от 1 до 5000.")
            return concurrency, target, batch
        except ValueError as exc:
            messagebox.showerror("Проверьте настройки", str(exc) or "Введите целые числа.")
            return None

    def start(self, from_scheduler=False):
        if self.proc and self.proc.poll() is None: return
        values = self.validate_settings()
        if not values: return
        if not SINGBOX_EXE.is_file():
            if from_scheduler:
                self.append(f"[Расписание] Проверка пропущена: не найден внешний sing-box.exe ({SINGBOX_EXE}).\\n")
            else:
                messagebox.showerror("Не найден Sing-box", f"Не найден файл:\\n{SINGBOX_EXE}\\n\\nПоместите sing-box.exe в папку singBox рядом с программой.")
            return
        concurrency, target, batch = values
        if self.git_var.get() and not self.github_configuration_valid(show_error=not from_scheduler):
            return
        if self.git_var.get() and not from_scheduler:
            if not messagebox.askyesno("Публикация конфигураций", "fast_vless.txt может содержать UUID и другие реквизиты доступа. После публикации файл станет доступен пользователям с доступом к репозиторию. Продолжить?"):
                return
        try: self.save_ui_config()
        except Exception as exc:
            messagebox.showwarning("Настройки", f"Не удалось сохранить настройки: {exc}")
        try: LOG_FILE.write_text("", encoding="utf-8")
        except OSError: pass
        env = os.environ.copy()
        env.update({"VLESS_CONCURRENCY": str(concurrency), "VLESS_MIN_CONCURRENCY": str(min(10, concurrency)), "VLESS_MAX_CONCURRENCY": str(concurrency), "VLESS_TARGET_WORKING": str(target), "VLESS_BATCH_SIZE": str(batch), "VLESS_DISABLE_GIT": "1"})
        if getattr(sys, "frozen", False): command = [sys.executable, "--worker"]
        else: command = [sys.executable, str(Path(__file__).resolve()), "--worker"]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            worker_log = LOG_FILE.open("a", encoding="utf-8", buffering=1)
            self.proc = subprocess.Popen(command, cwd=str(APP_DIR), env=env, stdout=worker_log,
                                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                         creationflags=creationflags)
            worker_log.close()
        except Exception as exc:
            try:
                worker_log.close()
            except Exception:
                pass
            messagebox.showerror("Ошибка запуска", str(exc)); return
        self.status.set("Проверка выполняется…")
        self.start_btn.configure(state="disabled"); self.stop_btn.configure(state="normal")
        self.progress.configure(mode="indeterminate"); self.progress.start(12)
        self.progress_text.set("Сбор источников и проверка конфигураций…")
        self.append("\n=== PULSEWAVE 4.0.3: запуск проверки ===\n")
        self.reader_thread = threading.Thread(target=self.read_output, args=(self.proc,), daemon=True)
        self.reader_thread.start()


    def github_configuration_valid(self, show_error=True):
        cfg = self.load_ui_config()
        owner = cfg.get("github_owner", "").strip()
        repo = cfg.get("github_repo", "").strip()
        branch = cfg.get("github_branch", "main").strip() or "main"
        token = load_github_token()
        if not owner or not repo or not token:
            if show_error:
                messagebox.showwarning("GitHub не настроен", "Откройте «Настроить GitHub…», укажите владельца, репозиторий, ветку и GitHub token.")
            return False
        return True

    def show_settings(self):
        self.main_notebook.select(self.settings_tab)
        self.settings_home.pack(fill="both", expand=True)
        self.github_page.pack_forget()

    def open_github_settings(self):
        self.main_notebook.select(self.settings_tab)
        self.settings_home.pack_forget()
        self.github_page.pack(fill="both", expand=True)
        self.github_owner_var_page.set(self.config.get("github_owner", ""))
        self.github_repo_var_page.set(self.config.get("github_repo", ""))
        self.github_branch_var_page.set(self.config.get("github_branch", "main"))
        self.github_token_var_page.set("")

    def _github_form_values(self):
        owner = self.github_owner_var_page.get().strip()
        repo = self.github_repo_var_page.get().strip()
        branch = self.github_branch_var_page.get().strip() or "main"
        token = self.github_token_var_page.get().strip() or load_github_token()
        if not owner or not repo or not branch or not token:
            raise GitHubPublishError("Заполните владельца, репозиторий, ветку и Personal Access Token.")
        return owner, repo, branch, token

    def check_github_access(self):
        try:
            owner, repo, branch, token = self._github_form_values()
            publisher = GitHubPublisher(owner, repo, branch, token)
            info = publisher.test_connection()
            self.github_page_status.set(
                f"Доступ подтверждён: {info.get('full_name', owner + '/' + repo)} · ветка {branch} доступна."
            )
            return True
        except Exception as exc:
            self.github_page_status.set(f"Проверка не пройдена: {exc}")
            messagebox.showerror("Проверка GitHub", str(exc), parent=self.root)
            return False

    def save_github_settings(self):
        try:
            owner, repo, branch, token = self._github_form_values()
            publisher = GitHubPublisher(owner, repo, branch, token)
            publisher.test_connection()
            # Save credential first; never write the token into the JSON settings file.
            save_github_token(token)
            self.config.update({"github_owner": owner, "github_repo": repo, "github_branch": branch})
            self.save_ui_config()
            self.github_token_var_page.set("")
            self.github_status_var.set(f"GitHub: {owner}/{repo} · {branch}")
            self.github_page_status.set("Настройки сохранены. Репозиторий и ветка доступны.")
            messagebox.showinfo("GitHub сохранён", f"Доступ к {owner}/{repo} (ветка {branch}) подтверждён.", parent=self.root)
        except Exception as exc:
            self.github_page_status.set(f"Ошибка сохранения: {exc}")
            messagebox.showerror("Не удалось сохранить GitHub", str(exc), parent=self.root)

    def publish_fast_vless(self, manual=False):
        cfg = self.load_ui_config()
        token = load_github_token()
        owner, repo = cfg.get("github_owner", ""), cfg.get("github_repo", "")
        branch = cfg.get("github_branch", "main")
        if not owner or not repo or not token:
            messagebox.showwarning("GitHub не настроен", "Сначала сохраните настройки GitHub и проверьте доступ.")
            return
        if not OUTPUT_FILE.exists():
            self.append("[GitHub] fast_vless.txt не найден — публикация пропущена.\n")
            return
        content = OUTPUT_FILE.read_text(encoding="utf-8", errors="replace")
        if not content.strip():
            self.append("[GitHub] Файл результатов пуст — публикация пропущена.\n")
            return
        if manual and not messagebox.askyesno(
            "Публикация в GitHub",
            "fast_vless.txt может содержать UUID, адреса серверов и другие данные доступа. "
            "Убедитесь, что репозиторий имеет нужную приватность и публикация разрешена. Продолжить?"
        ):
            return
        def worker():
            try:
                publisher = GitHubPublisher(owner, repo, branch, token)
                result = publisher.upload_text("fast_vless.txt", content, "PULSEWAVE: update fast_vless.txt")
                self.root.after(0, lambda: self.append(f"[GitHub] Опубликовано: {result}\n"))
            except Exception as exc:
                self.root.after(0, lambda err=str(exc): self.append(f"[GitHub] Ошибка публикации: {err}\n"))
        threading.Thread(target=worker, daemon=True, name="PULSEWAVE-GitHub-Publish").start()

    def toggle_scheduler(self):
        self.scheduler_enabled = bool(self.schedule_enabled_var.get())
        if self.scheduler_enabled:
            try:
                minutes = int(self.schedule_interval_var.get())
                if minutes not in (15, 30, 60, 120, 240):
                    raise ValueError
            except ValueError:
                self.schedule_enabled_var.set(False)
                self.scheduler_enabled = False
                messagebox.showerror("Расписание", "Выбери интервал 15, 30, 60, 120 или 240 минут.")
                return
            self.next_scheduled_run = time.monotonic() + minutes * 60
            self.append(f"[Расписание] Автопроверка включена: каждые {minutes} мин.\n")
        else:
            self.next_scheduled_run = None
            self.append("[Расписание] Автопроверка отключена.\n")
        self.save_preferences()
        self.update_schedule_status()
        self.root.after(1000, self.scheduler_tick)

    def schedule_interval_changed(self, event=None):
        if self.scheduler_enabled:
            self.next_scheduled_run = time.monotonic() + int(self.schedule_interval_var.get()) * 60
        self.save_preferences()
        self.update_schedule_status()

    def _schedule_next_run(self):
        if self.scheduler_enabled:
            self.next_scheduled_run = time.monotonic() + int(self.schedule_interval_var.get()) * 60
            self.update_schedule_status()

    def scheduler_tick(self):
        if not self.root.winfo_exists():
            return
        if self.scheduler_enabled and self.next_scheduled_run is not None:
            if time.monotonic() >= self.next_scheduled_run:
                if not self.proc or self.proc.poll() is not None:
                    self.scheduled_run_active = True
                    self.next_scheduled_run = None
                    self.append("[Расписание] Запускаю плановую проверку.\n")
                    self.start(from_scheduler=True)
                    if self.proc is None:
                        self.scheduled_run_active = False
                        self._schedule_next_run()
                else:
                    self.append("[Расписание] Предыдущая проверка ещё работает; следующий запуск отложен на интервал.\n")
                    self._schedule_next_run()
            self.update_schedule_status()
        self.root.after(1000, self.scheduler_tick)

    def update_schedule_status(self):
        if not self.scheduler_enabled:
            self.schedule_status_var.set("Автопроверка выключена")
        elif self.next_scheduled_run is not None:
            remaining = max(0, int(self.next_scheduled_run - time.monotonic()))
            self.schedule_status_var.set(f"Следующая через {remaining // 60:02d}:{remaining % 60:02d}")
        elif self.scheduled_run_active:
            self.schedule_status_var.set("Плановая проверка выполняется")

    def read_output(self, process):
        position = 0
        try:
            while process.poll() is None or (LOG_FILE.exists() and position < LOG_FILE.stat().st_size):
                if LOG_FILE.exists():
                    with LOG_FILE.open("r", encoding="utf-8", errors="replace") as stream:
                        stream.seek(position); chunk = stream.read(); position = stream.tell()
                    if chunk: self.messages.put(chunk)
                threading.Event().wait(0.15)
        finally:
            code = process.wait()
            if LOG_FILE.exists():
                with LOG_FILE.open("r", encoding="utf-8", errors="replace") as stream:
                    stream.seek(position); self.messages.put(stream.read())
            self.messages.put(("__EXIT__", code))

    def process_log_chunk(self, chunk):
        self.log_buffer += chunk
        lines = self.log_buffer.splitlines(keepends=True)
        self.log_buffer = ""
        if lines and not lines[-1].endswith(("\n", "\r")):
            self.log_buffer = lines.pop()
        visible = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("@@VLESS_RESULT@@"):
                try: self.upsert_result(json.loads(stripped[len("@@VLESS_RESULT@@"):]))
                except Exception: pass
            elif stripped.startswith("@@VLESS_PROGRESS@@"):
                try:
                    data = json.loads(stripped[len("@@VLESS_PROGRESS@@"):])
                    self.metric_vars["checked"].set(str(data.get("completed", 0)))
                    self.metric_vars["active"].set(str(data.get("concurrency", 0)))
                    total = max(int(data.get("total", 0)), 1); done = int(data.get("completed", 0))
                    self.progress.stop(); self.progress.configure(mode="determinate", maximum=total, value=done)
                    self.progress_text.set(f"Пачка: {done}/{total}  •  Рабочих: {data.get('working', 0)}  •  Конкурентность: {data.get('concurrency', 0)}")
                except Exception: pass
            else:
                visible.append(line)
        if visible: self.append("".join(visible))

    def poll_messages(self):
        try:
            while True:
                item = self.messages.get_nowait()
                if isinstance(item, tuple) and item[0] == "__EXIT__":
                    self.proc = None; self.progress.stop(); self.progress.configure(mode="determinate", value=0)
                    self.start_btn.configure(state="normal"); self.stop_btn.configure(state="disabled")
                    self.status.set("Завершено" if item[1] == 0 else f"Завершено с кодом {item[1]}")
                    self.progress_text.set("Проверка завершена")
                    if self.log_buffer:
                        self.append(self.log_buffer); self.log_buffer = ""
                    self.append(f"\n=== Процесс завершён (код {item[1]}) ===\n")
                    if item[1] == 0 and self.git_var.get():
                        self.publish_fast_vless()
                    if self.scheduled_run_active:
                        self.scheduled_run_active = False
                        self._schedule_next_run()
                else: self.process_log_chunk(item)
        except queue.Empty: pass
        self.root.after(100, self.poll_messages)

    def stop(self):
        if not self.proc or self.proc.poll() is not None: return
        self.status.set("Остановка…")
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else: self.proc.terminate()
        except Exception as exc: self.append(f"\n[GUI] Не удалось остановить процесс: {exc}\n")

    def edit_sources(self):
        if not SOURCES_FILE.exists(): SOURCES_FILE.write_text("# По одной HTTP(S)-ссылке на строку\n", encoding="utf-8")
        try:
            if os.name == "nt": os.startfile(str(SOURCES_FILE))
            else: subprocess.Popen(["xdg-open", str(SOURCES_FILE)])
        except Exception as exc: messagebox.showerror("Источники", str(exc))

    def save_results_as(self):
        if not OUTPUT_FILE.exists() or not OUTPUT_FILE.read_text(encoding="utf-8", errors="replace").strip():
            messagebox.showinfo("Сохранение результата", "Файл fast_vless.txt пока пуст. Сначала выполните проверку.")
            return
        path = filedialog.asksaveasfilename(
            title="Сохранить список рабочих серверов",
            defaultextension=".txt",
            filetypes=[("Текстовый файл", "*.txt"), ("Все файлы", "*.*")],
            initialfile="fast_vless.txt",
        )
        if not path:
            return
        try:
            shutil.copy2(OUTPUT_FILE, path)
            messagebox.showinfo("Сохранено", f"Результат сохранён:\\n{path}")
        except Exception as exc:
            messagebox.showerror("Ошибка сохранения", str(exc))

    def export_csv(self):
        rows = sorted(self.rows_by_link.values(), key=lambda r: r["ping"] if r["ping"] else 10**9)
        if not rows:
            messagebox.showinfo("Экспорт", "Таблица пока пуста."); return
        path = filedialog.asksaveasfilename(title="Экспорт рабочих серверов", defaultextension=".csv", filetypes=[("CSV UTF-8", "*.csv")], initialfile="pulsewave_results.csv")
        if not path: return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=["name", "protocol", "ip", "ping_ms", "link"])
                writer.writeheader()
                for row in rows: writer.writerow({"name": row["name"], "protocol": row["protocol"], "ip": row["ip"], "ping_ms": row["ping"], "link": row["link"]})
            messagebox.showinfo("Экспорт", f"Экспортировано записей: {len(rows)}\n{path}")
        except Exception as exc: messagebox.showerror("Ошибка экспорта", str(exc))

    def sort_table(self, column):
        if self.sort_column == column: self.sort_reverse = not self.sort_reverse
        else: self.sort_column, self.sort_reverse = column, False
        self.refresh_table()

    def about(self):
        messagebox.showinfo("О программе", f"PULSEWAVE {APP_VERSION}\nPERSONAL VPN SUITE\n\nПроверка серверов и управление Sing-box TUN.\n\nОткрытый исходный код распространяется отдельно от Sing-box.\nРежим TUN требует Windows и подтверждения UAC.")

    def open_results(self):
        if not OUTPUT_FILE.exists(): messagebox.showinfo("Результаты", "Файл fast_vless.txt пока не создан."); return
        try:
            if os.name == "nt": os.startfile(str(OUTPUT_FILE))
            else: subprocess.Popen(["xdg-open", str(OUTPUT_FILE)])
        except Exception as exc: messagebox.showerror("Результаты", str(exc))

    def clear_log(self):
        self.log.configure(state="normal"); self.log.delete("1.0", "end"); self.log.configure(state="disabled")

    def selected_link(self):
        selection = self.table.selection()
        if not selection:
            return None
        values = self.table.item(selection[0], "values")
        return values[6] if len(values) > 6 else None

    def connect_selected(self):
        link = self.selected_link()
        if not link:
            messagebox.showinfo("VPN-туннель", "Сначала выберите сервер в таблице рабочих серверов.")
            return
        if os.name != "nt":
            messagebox.showerror("VPN-туннель", "Режим TUN в этой сборке поддерживается только в Windows.")
            return
        if not self.is_admin:
            if not messagebox.askyesno("Требуются права администратора", "Для создания системного TUN-интерфейса Windows запросит права администратора. Перезапустить PULSEWAVE с повышенными правами?"):
                return
            try:
                PENDING_TUNNEL.write_text(json.dumps({"link": link}, ensure_ascii=False), encoding="utf-8")
                import ctypes
                if getattr(sys, "frozen", False):
                    executable, params = sys.executable, "--connect-pending"
                else:
                    executable = sys.executable
                    params = f'"{Path(__file__).resolve()}" --connect-pending'
                result = ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, params, str(APP_DIR), 1)
                if result <= 32:
                    raise OSError(f"Windows не смог запустить процесс с повышенными правами (код {result}).")
                self.root.destroy()
            except Exception as exc:
                try: PENDING_TUNNEL.unlink(missing_ok=True)
                except Exception: pass
                messagebox.showerror("Не удалось запросить UAC", str(exc))
            return
        self._start_tunnel(link)

    def connect_pending(self):
        try:
            payload = json.loads(PENDING_TUNNEL.read_text(encoding="utf-8"))
            link = payload.get("link", "")
            PENDING_TUNNEL.unlink(missing_ok=True)
            if link:
                self._start_tunnel(link)
        except Exception as exc:
            messagebox.showerror("Автоподключение", f"Не удалось восстановить выбранный профиль:\n{exc}")

    def _start_tunnel(self, link):
        try:
            self.tunnel_status.set("Проверка конфигурации…")
            self.root.update_idletasks()
            self.tunnel.start(link)
            self.tunnel_status.set("Запуск…")
            self.connect_btn.configure(state="disabled")
            self.disconnect_btn.configure(state="normal")
            self.append("\n[VPN] Запущен Sing-box TUN. Проверяется состояние интерфейса…\n")
        except Exception as exc:
            self.tunnel_status.set("Ошибка")
            messagebox.showerror("Не удалось подключить VPN", str(exc))

    def disconnect_tunnel(self):
        try:
            self.tunnel.stop()
            self.tunnel_status.set("Отключено")
            self.connect_btn.configure(state="normal")
            self.disconnect_btn.configure(state="disabled")
            self.append("[VPN] Туннель остановлен.\n")
        except Exception as exc:
            messagebox.showerror("Отключение VPN", str(exc))

    def poll_tunnel(self):
        try:
            if self.tunnel.is_running:
                self.tunnel_status.set("Подключено")
                self.connect_btn.configure(state="disabled")
                self.disconnect_btn.configure(state="normal")
            elif self.tunnel.process is not None:
                code = self.tunnel.process.poll()
                self.tunnel.process = None
                self.tunnel_status.set(f"Остановлено (код {code})")
                self.connect_btn.configure(state="normal")
                self.disconnect_btn.configure(state="disabled")
                self.append(f"[VPN] Sing-box завершился с кодом {code}. См. vlesschecker_tunnel.log.\n")
        finally:
            if self.root.winfo_exists():
                self.root.after(1000, self.poll_tunnel)

    def on_close(self):
        if self.tray_on_close_var.get():
            if self.ensure_tray():
                self.root.withdraw()
                self.status.set("Работа в фоне — значок PULSEWAVE находится в системном трее")
                self.append("[GUI] Окно скрыто в системный трей. Проверка может продолжаться в фоне.\n")
                return
            # If tray initialization fails, keep the application visible rather than losing access.
            return
        self.exit_application()


def run_worker():
    import asyncio
    import multi_tester
    with LOG_FILE.open("w", encoding="utf-8", buffering=1) as log:
        sys.stdout = log; sys.stderr = log
        asyncio.run(multi_tester.main_async())

if __name__ == "__main__":
    if "--worker" in sys.argv: run_worker()
    else:
        window = tk.Tk(); PULSEWAVEGUI(window); window.mainloop()
