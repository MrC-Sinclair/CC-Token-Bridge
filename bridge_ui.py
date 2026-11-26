"""Windows interface for managing local Token scanners."""

import copy
import queue
import sqlite3
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import bridge_app
from bridge_history import read_history
from desktop_presets import can_sync, source_note


BG = "#f4f6f9"
SURFACE = "#ffffff"
INK = "#19283b"
MUTED = "#51637a"
LINE = "#cbd5df"
BLUE = "#194ca9"
PALE = "#e8effb"
FONT = "Microsoft YaHei UI"
INTERVAL = 10 * 60 * 1000


def install_styles(root):
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", font=(FONT, 10), foreground=INK, background=BG)
    style.configure("TButton", background=SURFACE, bordercolor=LINE, borderwidth=1,
                    lightcolor=SURFACE, darkcolor=SURFACE, padding=(10, 5))
    style.map("TButton", background=[("active", PALE)])
    style.configure("Accent.TButton", background=BLUE, foreground="white", bordercolor=BLUE,
                    lightcolor=BLUE, darkcolor=BLUE, padding=(15, 6))
    style.map("Accent.TButton", background=[("disabled", "#d5dce6"), ("active", "#103e90")],
              foreground=[("disabled", MUTED), ("!disabled", "white")])
    style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, bordercolor=LINE,
                    rowheight=32, font=(FONT, 10))
    style.configure("Treeview.Heading", font=(FONT, 9, "bold"), background="#eaf0f5",
                    foreground=MUTED, padding=(6, 6))
    style.map("Treeview", background=[("selected", PALE)], foreground=[("selected", INK)])
    style.configure("TEntry", padding=(4, 5), fieldbackground=SURFACE, bordercolor=LINE)


def label(parent, text, *, size=10, color=INK, bold=False, **kwargs):
    return tk.Label(parent, text=text, font=(FONT, size, "bold" if bold else "normal"),
                    fg=color, bg=parent.cget("background"), anchor="w", **kwargs)


def edit_dialog(parent, existing, save, preset=None):
    win = tk.Toplevel(parent)
    win.withdraw()
    win.title("编辑应用" if existing else "添加应用")
    win.transient(parent)
    win.configure(bg=SURFACE)
    win.resizable(True, False)
    form = tk.Frame(win, bg=SURFACE, padx=22, pady=18)
    form.pack(fill="both", expand=True)
    form.columnconfigure(0, weight=1)
    current = existing or preset or {}
    builtin = current.get("kind") == "codebuddy"
    desktop = current.get("kind") == "desktop"
    values = {key: tk.StringVar(win, current.get(key, "")) for key in ("name", "root", "script")}
    entries = {}

    def field(row, key, title, browse=None):
        label(form, title, bold=True).grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 4))
        entry = ttk.Entry(form, textvariable=values[key], width=48)
        entry.grid(row=row + 1, column=0, columnspan=1 if browse else 2, sticky="ew")
        entries[key] = entry
        if browse:
            ttk.Button(form, text="选择…", command=browse).grid(row=row + 1, column=1, padx=(8, 0))

    def browse_root():
        path = Path(values["root"].get())
        folder = filedialog.askdirectory(parent=win, title="选择日志目录", mustexist=True,
                                         initialdir=str(path) if path.is_dir() else None)
        if folder:
            values["root"].set(folder)

    def browse_script():
        script = filedialog.askopenfilename(parent=win, title="选择本地扫描脚本",
                                            filetypes=[("扫描脚本", "*.py *.ps1"), ("所有文件", "*.*")])
        if script:
            values["script"].set(script)

    field(0, "name", "应用名称")
    hint = (f"CC 请求日志中的应用标识为 {current['app_type']}。"
            if current else "这个名称也会显示在 CC 的请求日志里。")
    label(form, hint, size=9, color=MUTED).grid(row=2, column=0, columnspan=2, sticky="w", pady=(3, 0))
    field(3, "root", "日志目录", browse_root)
    if builtin:
        label(form, "使用内置 CodeBuddy IDE 扫描器。", color=MUTED).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(13, 0))
    else:
        field(5, "script", "自定义扫描脚本（可选）" if desktop else "扫描脚本", browse_script)
        info = (source_note(dict(current, script="")) if desktop
                else "同步时会在本机运行所选的 Python 或 PowerShell 脚本。")
        label(form, info, size=9, color=MUTED, wraplength=430, justify="left").grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(3, 0))

    def submit():
        name, folder, script = (values[k].get().strip() for k in ("name", "root", "script"))
        try:
            if not name:
                raise ValueError("请填写应用名称。")
            if not folder or not Path(folder).is_dir():
                raise ValueError("请选择一个存在的日志目录。")
            if not builtin and (script or not desktop) and (not Path(script).is_file() or Path(script).suffix.lower() not in (".py", ".ps1")):
                raise ValueError("请选择一个存在的 .py 或 .ps1 扫描脚本。")
            root_path = str(Path(folder).resolve())
            script_path = str(Path(script).resolve()) if script and not builtin else ""
            if current:
                source = dict(current, name=name, root=root_path, script=script_path)
            else:
                source = bridge_app.new_source(name, name, root_path, script_path)
            save(source)
        except Exception as exc:
            messagebox.showerror("无法保存应用", str(exc), parent=win)
            return
        win.destroy()

    actions = tk.Frame(form, bg=SURFACE)
    actions.grid(row=8, column=0, columnspan=2, sticky="e", pady=(22, 0))
    ttk.Button(actions, text="取消", command=win.destroy).pack(side="left", padx=(0, 8))
    ttk.Button(actions, text="保存应用", style="Accent.TButton", command=submit).pack(side="left")
    win.bind("<Escape>", lambda _e: win.destroy())
    win.bind("<Return>", lambda _e: submit())
    win.update_idletasks()
    width, height = win.winfo_reqwidth(), win.winfo_reqheight()
    x = max(0, parent.winfo_rootx() + (parent.winfo_width() - width) // 2)
    y = max(0, parent.winfo_rooty() + (parent.winfo_height() - height) // 2)
    win.geometry(f"+{x}+{y}")
    win.deiconify()
    win.grab_set()
    entries["name"].focus_set()
    return win


class BridgeWindow:
    def __init__(self, root, config):
        self.root, self.config = root, config
        self.messages = queue.Queue()
        self.running = False
        self.closing = False
        self.last_status = {}
        self.results = {}
        self.history = {}
        self.history_error = ""
        self.timer = None
        self.auto = tk.BooleanVar(root, config.get("auto_sync", False))
        self.db_path = tk.StringVar(root, config["db_path"])
        self.root.configure(bg=BG)
        self.root.title("CC 用量助手")
        self.root.geometry("900x695")
        self.root.minsize(720, 540)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        body = tk.Frame(root, bg=BG, padx=22, pady=17)
        body.grid(sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(3, weight=1)

        label(body, "CC 用量助手", bold=True, size=21).grid(row=0, column=0, sticky="w")
        label(body, "扫描本地应用日志，并将 Token 用量同步到 CC Switch。", color=MUTED).grid(
            row=1, column=0, sticky="w", pady=(0, 12))
        toolbar = tk.Frame(body, bg=BG)
        toolbar.grid(row=2, column=0, sticky="ew", pady=(0, 7))
        label(toolbar, "应用", bold=True, size=13).pack(side="left")
        self.add = ttk.Button(toolbar, text="添加应用", command=self.add_source)
        self.add.pack(side="right")
        self.add_menu = tk.Menu(self.add, tearoff=False)
        self.remove = ttk.Button(toolbar, text="移除", command=self.remove_source)
        self.remove.pack(side="right", padx=(0, 7))
        self.edit_btn = ttk.Button(toolbar, text="编辑", command=lambda: self.edit(self.selected()))
        self.edit_btn.pack(side="right", padx=(0, 7))

        table = tk.Frame(body, bg=BG)
        table.grid(row=3, column=0, sticky="nsew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        columns = ("application", "requests", "tokens", "status")
        self.tree = ttk.Treeview(table, columns=columns, show="headings", selectmode="browse", height=7)
        for key, title, width in zip(columns, ("应用名称", "CC 累计请求", "CC 累计 Token", "同步状态"), (145, 135, 190, 225)):
            self.tree.heading(key, text=title, anchor="w")
            self.tree.column(key, width=width, minwidth=85, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.refresh_selection())
        self.tree.bind("<Double-Button-1>", self.edit_row)
        self.detail = label(body, "", color=MUTED, size=9, wraplength=660, justify="left")
        self.detail.grid(row=4, column=0, sticky="ew", pady=(5, 10))

        report = tk.Frame(body, bg=SURFACE, highlightbackground=LINE, highlightthickness=1, padx=15, pady=9)
        report.grid(row=5, column=0, sticky="ew")
        self.report_title = label(report, "同步结果", color=MUTED, size=9, bold=True)
        self.report_title.grid(row=0, column=0, columnspan=4, sticky="w")
        self.counts = []
        for i, title in enumerate(("CC 累计请求", "CC 累计 Token", "本次新增", "本次已存在")):
            report.columnconfigure(i, weight=1, uniform="stat")
            count = label(report, "—", bold=True, size=16)
            count.grid(row=1, column=i, sticky="w", padx=(0, 12))
            label(report, title, color=MUTED, size=9).grid(row=2, column=i, sticky="w")
            self.counts.append(count)
        self.message = label(body, "", color=MUTED, size=9, wraplength=660, justify="left")
        self.message.grid(row=6, column=0, sticky="ew", pady=(6, 7))

        controls = tk.Frame(body, bg=BG)
        controls.grid(row=7, column=0, sticky="ew")
        self.sync = ttk.Button(controls, text="同步到 CC", style="Accent.TButton",
                               command=lambda: self.start_sync([self.selected()]))
        self.sync.pack(side="left")
        self.sync_all = ttk.Button(controls, text="同步全部", command=lambda: self.start_sync(self.config["sources"]))
        self.sync_all.pack(side="left", padx=8)
        self.auto_button = ttk.Checkbutton(controls, text="运行时每 10 分钟自动同步", variable=self.auto,
                                           command=self.set_auto)
        self.auto_button.pack(side="right")
        ttk.Separator(body).grid(row=8, column=0, sticky="ew", pady=(12, 9))
        database = tk.Frame(body, bg=BG)
        database.grid(row=9, column=0, sticky="ew")
        database.columnconfigure(1, weight=1)
        label(database, "CC 数据库", size=9, color=MUTED).grid(row=0, column=0, padx=(0, 9))
        ttk.Entry(database, textvariable=self.db_path, state="readonly").grid(row=0, column=1, sticky="ew")
        self.db_button = ttk.Button(database, text="更换…", command=self.change_database)
        self.db_button.grid(row=0, column=2, padx=(8, 0))
        ttk.Button(database, text="退出助手", command=self.exit_app).grid(row=0, column=3, padx=(12, 0))
        self.fill_tree()
        self.schedule_auto()
        root.protocol("WM_DELETE_WINDOW", root.iconify)

    def selected(self):
        selected = self.tree.selection()
        if selected:
            return next((s for s in self.config["sources"] if s["id"] == selected[0]), None)
        return None

    def fill_tree(self, selected_id=None):
        try:
            self.history = read_history(self.config["db_path"], self.config["sources"])
            self.history_error = ""
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.history = {}
            self.history_error = f"无法读取 CC 历史用量。{exc}"
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        for src in self.config["sources"]:
            ready = can_sync(src)
            history = self.history.get(src["id"])
            requests, tokens = "—", "—"
            if history and history.first_day is not None:
                requests, tokens = f"{history.requests:,}", f"{history.tokens:,}"
            status = "可同步" if ready else "暂无用量来源"
            if ready and history:
                status = "CC 已有记录" if history.first_day is not None else "CC 暂无记录"
            if self.history_error:
                status = "CC 历史读取失败"
            status = self.last_status.get(src["id"], status)
            self.tree.insert("", "end", iid=src["id"], values=(
                src["name"], requests, tokens, status))
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(selected_id if selected_id in children else children[0])
        self.refresh_selection()

    def refresh_selection(self):
        src = self.selected()
        for button in (self.add, self.db_button):
            button.state(["disabled"] if self.running else ["!disabled"])
        for button in (self.edit_btn, self.remove):
            button.state(["!disabled"] if src and not self.running else ["disabled"])
        self.sync.state(["!disabled"] if src and can_sync(src) and not self.running else ["disabled"])
        available = any(can_sync(s) for s in self.config["sources"])
        self.sync_all.state(["!disabled"] if available and not self.running else ["disabled"])
        self.detail.configure(text=f"日志目录   {src['root']}" if src else "添加应用后即可选择扫描脚本。")
        note = source_note(src) if src else ""
        source_id = src["id"] if src else ""
        history = self.history.get(source_id)
        result, title, message = self.results.get(source_id, ({}, "历史用量", note))
        if history and history.first_day is not None:
            dates = history.first_day if history.first_day == history.last_day else f"{history.first_day} 至 {history.last_day}"
            message = f"CC 记录日期　{dates}。{message}"
        if self.history_error:
            message = f"{self.history_error} {message}"
        self.report_title.configure(text=title)
        self.message.configure(text=message, fg="#a73530" if self.history_error or title == "同步失败" else MUTED)
        history_values = (history.requests, history.tokens) if history and history.first_day is not None else (None, None)
        for count, value in zip(self.counts, (*history_values, result.get("inserted"), result.get("skipped"))):
            count.configure(text=f"{value:,}" if value is not None else "—")

    def persist(self, **changes):
        config = dict(self.config, **changes)
        bridge_app.save_config(config)
        self.config = config

    def edit_row(self, event):
        row = self.tree.identify_row(event.y)
        if row and not self.running:
            self.tree.selection_set(row)
            self.edit(self.selected())

    def add_source(self):
        existing = {s["app_type"].casefold() for s in self.config["sources"]}
        missing = [s for s in bridge_app.default_config()["sources"] if s["app_type"].casefold() not in existing]
        if not missing:
            self.edit(None)
        else:
            self.add_menu.delete(0, "end")
            self.add_menu.add_command(label="自定义扫描脚本…", command=lambda: self.edit(None))
            self.add_menu.add_separator()
            for source in missing:
                self.add_menu.add_command(label=source["name"] + "…", command=lambda s=source: self.edit(None, preset=s))
            try:
                self.add_menu.tk_popup(self.add.winfo_rootx(), self.add.winfo_rooty() + self.add.winfo_height())
            finally:
                self.add_menu.grab_release()

    def edit(self, existing, preset=None):
        if self.running:
            return

        def save(source):
            sources = list(self.config["sources"])
            if existing:
                sources = [source if src["id"] == existing["id"] else src for src in sources]
            else:
                sources.append(source)
            self.persist(sources=sources)
            self.results.pop(source["id"], None)
            self.last_status.pop(source["id"], None)
            self.fill_tree(source["id"])

        edit_dialog(self.root, existing, save, preset=preset)

    def remove_source(self):
        src = self.selected()
        if not src or self.running:
            return
        if not messagebox.askyesno("移除应用", f"从同步工具移除「{src['name']}」？CC 中已同步的用量会保留。",
                                   parent=self.root):
            return
        try:
            self.persist(sources=[s for s in self.config["sources"] if s["id"] != src["id"]])
        except Exception as exc:
            messagebox.showerror("无法移除应用", str(exc), parent=self.root)
            return
        self.fill_tree()

    def change_database(self):
        path = Path(self.db_path.get())
        picked = filedialog.askopenfilename(parent=self.root, title="选择 CC Switch 数据库",
                                            initialdir=str(path.parent) if path.parent.is_dir() else None,
                                            filetypes=[("CC Switch 数据库", "*.db"), ("所有文件", "*.*")])
        if not picked:
            return
        try:
            self.persist(db_path=picked)
        except Exception as exc:
            messagebox.showerror("无法保存数据库路径", str(exc), parent=self.root)
            return
        self.db_path.set(picked)
        self.results.clear()
        self.last_status.clear()
        self.fill_tree()

    def schedule_auto(self):
        if self.timer is not None:
            self.root.after_cancel(self.timer)
            self.timer = None
        if self.auto.get() and not self.closing:
            self.timer = self.root.after(INTERVAL, self.auto_tick)

    def auto_tick(self):
        self.timer = None
        self.start_sync(self.config["sources"])
        self.schedule_auto()

    def set_auto(self):
        try:
            self.persist(auto_sync=self.auto.get())
        except Exception as exc:
            self.auto.set(self.config["auto_sync"])
            messagebox.showerror("无法保存自动同步设置", str(exc), parent=self.root)
        self.schedule_auto()

    def start_sync(self, sources):
        sources = [copy.copy(src) for src in sources if src and can_sync(src)]
        if self.running or not sources or self.closing:
            return
        self.running = True
        database = self.config["db_path"]
        for src in sources:
            self.last_status[src["id"]] = "等待同步"
            self.tree.set(src["id"], "status", "等待同步")
        self.refresh_selection()

        def worker():
            for src in sources:
                self.messages.put(("start", src["id"], None))
                try:
                    result = bridge_app.sync_source(database, src)
                except Exception as exc:
                    self.messages.put(("error", src["id"], str(exc)))
                else:
                    self.messages.put(("done", src["id"], result))
            self.messages.put(("finished", None, None))

        threading.Thread(target=worker, name="token-sync", daemon=False).start()
        self.root.after(60, self.poll)

    def poll(self):
        while True:
            try:
                action, source_id, data = self.messages.get_nowait()
            except queue.Empty:
                break
            if action == "finished":
                self.running = False
                if self.closing:
                    self.root.destroy()
                    return
                selection = self.tree.selection()
                self.fill_tree(selection[0] if selection else None)
                break
            if action == "start":
                status = "正在同步…"
                report = ({}, "正在扫描日志…", "")
            elif action == "error":
                status = "同步失败"
                report = ({}, status, data)
            else:
                when = datetime.now().strftime("%H:%M:%S")
                status = f"{when}  ·  新增 {data['inserted']:,} 条"
                src = next(s for s in self.config["sources"] if s["id"] == source_id)
                note = source_note(src) or "在 CC 的「用量统计 → 全部 → 请求日志」查看。"
                if data.get("archive_pending_turns"):
                    note += f" 另有 {data['archive_pending_turns']:,} 个历史回合的请求次数待核对，其 {data['archive_pending_tokens']:,} Token 已计入。"
                if data.get("archive_unresolved_turns"):
                    note += f" 有 {data['archive_unresolved_turns']:,} 个历史回合的计数与已有记录无法核对，已跳过。"
                report = (data, f"同步完成  ·  {when}", note)
                if src["kind"] == "desktop" and data["seen"] == 0:
                    status = "本次扫描无有效计数"
                    report = (data, status, source_note(src) or "当前记录中未读到可导入的 Token 数。")
            self.last_status[source_id] = status
            self.results[source_id] = report
            self.tree.set(source_id, "status", status)
        self.refresh_selection()
        if self.running:
            self.root.after(100, self.poll)

    def exit_app(self):
        self.closing = True
        self.schedule_auto()
        if self.running:
            self.root.withdraw()
        else:
            self.root.destroy()


def launch():
    root = tk.Tk()
    root.withdraw()
    try:
        config = bridge_app.load_config()
        install_styles(root)
        BridgeWindow(root, config)
    except Exception as exc:
        messagebox.showerror("CC 用量助手无法启动", str(exc), parent=root)
        root.destroy()
        return
    root.deiconify()
    root.mainloop()
