"""干扰源模拟器图形界面。

功能：
- 在 1800 米圆形目标区域中点击或拖动设置干扰源。
- 设置频道、全向/定向类型、有效检测距离和定向朝向。
- 一键启动 127.0.0.1:2026 HTTP 接口，供机器狗程序调用。
- 显示 /enter、/measure、/clear、/exit 请求日志和会话状态。
"""

from __future__ import annotations

import json
import math
import queue
import sys
import threading
import time
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

_CODE_DIR = Path(__file__).resolve().parent
if str(_CODE_DIR) not in sys.path:
    sys.path.insert(0, str(_CODE_DIR))

from interference_simulator_core import (
    CHANNEL_MAX,
    CHANNEL_MIN,
    DEFAULT_PORT,
    TARGET_RADIUS_M,
    InterferenceSource,
    SimulatorConfig,
    SimulatorState,
    make_server,
    load_sources_json,
    save_sources_json,
)


TYPE_TO_LABEL = {"omni": "全向", "directional": "定向"}
LABEL_TO_TYPE = {value: key for key, value in TYPE_TO_LABEL.items()}


class InterferenceSimulatorApp(tk.Tk):
    """可交互配置并运行本地干扰源模拟器。"""

    POINT_PIXELS = 13.0

    def __init__(self) -> None:
        super().__init__()
        self.title("干扰源模拟器")
        self.geometry("1460x900")
        self.minsize(1180, 720)

        self.sources: list[InterferenceSource] = []
        self.selected_index: int | None = None
        self._drag_mode: str | None = None
        self._drag_history: list[list[InterferenceSource]] = []
        self._view = (-1950.0, 1950.0, -1950.0, 1950.0)
        self._screen_x0 = 0.0
        self._screen_y0 = 0.0
        self._scale = 1.0
        self.event_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.server = None
        self.server_thread: threading.Thread | None = None
        self.state: SimulatorState | None = None
        self._behavior_signature = None

        self.port_var = tk.StringVar(value=str(DEFAULT_PORT))
        self.robot_id_var = tk.StringVar(value="")
        self.speed_var = tk.StringVar(value="5")
        self.near_var = tk.StringVar(value="5")
        self.clear_radius_var = tk.StringVar(value="20")
        self.coverage_var = tk.StringVar(value="180")
        self.svd_error_var = tk.StringVar(value="1")
        self.max_real_var = tk.StringVar(value="1200")
        self.random_seed_var = tk.StringVar(value="20260911")
        self.server_status_var = tk.StringVar(value="接口未启动")
        self.session_status_var = tk.StringVar(value="未进入")
        self.virtual_time_var = tk.StringVar(value="虚拟时间：0 s")
        self.remaining_time_var = tk.StringVar(value="现实剩余：未开始")

        self.channel_var = tk.StringVar(value="1")
        self.kind_var = tk.StringVar(value="全向")
        self.x_var = tk.StringVar(value="0")
        self.y_var = tk.StringVar(value="0")
        self.radius_var = tk.StringVar(value="1200")
        self.direction_var = tk.StringVar(value="0")
        self.selected_var = tk.StringVar(value="未选择干扰源")

        self._configure_style()
        self._build_layout()
        self._bind_events()
        self._refresh_source_tree()
        self.after(100, self._poll_events)
        self.after_idle(self._redraw_map)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=24)
        style.configure("Toolbar.TButton", padding=(8, 4))

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        split = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        split.grid(row=0, column=0, sticky="nsew")
        map_frame = ttk.Frame(split)
        side = ttk.Frame(split, width=500)
        split.add(map_frame, weight=4)
        split.add(side, weight=2)

        toolbar = ttk.Frame(map_frame)
        toolbar.pack(fill=tk.X, padx=8, pady=(8, 4))
        for index, (text, command) in enumerate(
            [
                ("新增", self.add_source),
                ("删除", self.delete_selected),
                ("清空", self.clear_sources),
                ("P3 Random", self.random_sources), ("P4 Mixed", self.random_mixed_sources),
                ("适应视图", self.fit_view),
            ]
        ):
            ttk.Button(toolbar, text=text, command=command, style="Toolbar.TButton").grid(
                row=0, column=index, padx=(0, 5), sticky="ew"
            )

        self.canvas = tk.Canvas(map_frame, background="white", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        self._build_sidebar(side)
        ttk.Label(
            self,
            text="地图：点击空白新增干扰源，拖动干扰源修改位置。",
            relief=tk.SUNKEN,
            anchor=tk.W,
            padding=(8, 4),
        ).grid(row=1, column=0, sticky="ew")

    def _build_sidebar(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        server_box = ttk.LabelFrame(parent, text="HTTP 接口", padding=8)
        server_box.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))
        for column in range(4):
            server_box.columnconfigure(column, weight=1)

        ttk.Label(server_box, text="端口").grid(row=0, column=0, sticky="w")
        ttk.Entry(server_box, textvariable=self.port_var, width=8).grid(row=0, column=1, sticky="ew")
        ttk.Label(server_box, text="机器狗ID").grid(row=0, column=2, sticky="e")
        ttk.Entry(server_box, textvariable=self.robot_id_var, width=14).grid(row=0, column=3, sticky="ew", padx=(4, 0))
        ttk.Label(
            server_box,
            text="机器狗ID留空表示接受任意非空 robot_id。",
            foreground="#666666",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(2, 6))

        self.start_button = ttk.Button(server_box, text="启动接口", command=self.start_server)
        self.start_button.grid(row=2, column=0, columnspan=2, sticky="ew", padx=(0, 2))
        self.stop_button = ttk.Button(server_box, text="停止接口", command=self.stop_server, state=tk.DISABLED)
        self.stop_button.grid(row=2, column=2, columnspan=2, sticky="ew", padx=(2, 0))

        ttk.Button(server_box, text="重置场地（保留干扰源）", command=self.reset_field).grid(
            row=3, column=0, columnspan=4, sticky="ew", pady=(6, 0)
        )
        ttk.Label(server_box, textvariable=self.server_status_var, foreground="#0d47a1").grid(
            row=4, column=0, columnspan=4, sticky="w", pady=(6, 0)
        )
        ttk.Label(server_box, textvariable=self.session_status_var).grid(
            row=5, column=0, columnspan=4, sticky="w"
        )
        ttk.Label(server_box, textvariable=self.virtual_time_var).grid(
            row=6, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(server_box, textvariable=self.remaining_time_var).grid(
            row=6, column=2, columnspan=2, sticky="w"
        )

        notebook = ttk.Notebook(parent)
        notebook.grid(row=1, column=0, sticky="nsew", padx=8, pady=6)

        source_tab = ttk.Frame(notebook)
        settings_tab = ttk.Frame(notebook)
        log_tab = ttk.Frame(notebook)
        notebook.add(source_tab, text="干扰源")
        notebook.add(settings_tab, text="参数")
        notebook.add(log_tab, text="请求日志")
        self._build_source_tab(source_tab)
        self._build_settings_tab(settings_tab)
        self._build_log_tab(log_tab)

    def _build_source_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        table_box = ttk.Frame(parent)
        table_box.grid(row=0, column=0, sticky="nsew")
        table_box.columnconfigure(0, weight=1)
        table_box.rowconfigure(0, weight=1)
        columns = ("channel", "kind", "x", "y", "radius", "direction", "cleared")
        self.source_tree = ttk.Treeview(table_box, columns=columns, show="headings", selectmode="browse")
        headings = {
            "channel": "频道",
            "kind": "类型",
            "x": "x",
            "y": "y",
            "radius": "检测距离",
            "direction": "朝向",
            "cleared": "状态",
        }
        widths = {"channel": 46, "kind": 54, "x": 68, "y": 68, "radius": 74, "direction": 60, "cleared": 52}
        for column in columns:
            self.source_tree.heading(column, text=headings[column])
            self.source_tree.column(column, width=widths[column], anchor=tk.CENTER)
        self.source_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(table_box, orient=tk.VERTICAL, command=self.source_tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.source_tree.configure(yscrollcommand=scrollbar.set)

        editor = ttk.LabelFrame(parent, text="选中干扰源", padding=8)
        editor.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        for column in range(2):
            editor.columnconfigure(column, weight=1)
        ttk.Label(editor, textvariable=self.selected_var).grid(row=0, column=0, columnspan=2, sticky="w")

        fields = [
            ("频道", self.channel_var),
            ("类型", self.kind_var),
            ("x", self.x_var),
            ("y", self.y_var),
            ("有效检测距离/m", self.radius_var),
            ("定向朝向/°", self.direction_var),
        ]
        for row, (label, variable) in enumerate(fields, start=1):
            ttk.Label(editor, text=label).grid(row=row, column=0, sticky="w", pady=2)
            if label == "类型":
                widget = ttk.Combobox(editor, textvariable=variable, values=["全向", "定向"], state="readonly", width=14)
            else:
                widget = ttk.Entry(editor, textvariable=variable, width=14)
            widget.grid(row=row, column=1, sticky="ew", pady=2, padx=(6, 0))
        ttk.Button(editor, text="应用修改", command=self.apply_editor).grid(
            row=7, column=0, columnspan=2, sticky="ew", pady=(8, 0)
        )
        ttk.Label(
            editor,
            text="修改后按 Enter 或点击“应用修改”；运行中也会立即生效。",
            foreground="#666666",
            wraplength=360,
        ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(4, 0))

        io_box = ttk.Frame(parent)
        io_box.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        for column in range(3):
            io_box.columnconfigure(column, weight=1)
        ttk.Button(io_box, text="导入JSON", command=self.import_sources).grid(row=0, column=0, sticky="ew", padx=2)
        ttk.Button(io_box, text="导出JSON", command=self.export_sources).grid(row=0, column=1, sticky="ew", padx=2)
        ttk.Button(io_box, text="接口自检", command=self.self_test).grid(row=0, column=2, sticky="ew", padx=2)

    def _build_settings_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        rows = [
            ("机器狗速度/(m/s)", self.speed_var),
            ("近距离阈值/m", self.near_var),
            ("清除半径/m", self.clear_radius_var),
            ("定向覆盖角/°", self.coverage_var),
            ("示向度误差/±°", self.svd_error_var),
            ("现实时间上限/s", self.max_real_var),
            ("Random seed", self.random_seed_var),
        ]
        box = ttk.LabelFrame(parent, text="会话参数", padding=10)
        box.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        box.columnconfigure(1, weight=1)
        for row, (label, variable) in enumerate(rows):
            ttk.Label(box, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Entry(box, textvariable=variable, width=16).grid(row=row, column=1, sticky="ew", pady=3)
        ttk.Label(
            parent,
            text="目标区域半径固定 1800 m；虚拟世界上限固定 360000 s。",
            wraplength=430,
            foreground="#666666",
        ).grid(row=1, column=0, sticky="w", padx=10, pady=(0, 8))

    def _build_log_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        self.log_text = tk.Text(parent, wrap="word", state=tk.DISABLED, font=("Consolas", 9))
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def _bind_events(self) -> None:
        self.source_tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.bind_all("<Return>", lambda _event: self.apply_editor())
        self.canvas.bind("<Button-1>", self._on_map_press)
        self.canvas.bind("<B1-Motion>", self._on_map_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_map_release)
        self.canvas.bind("<Button-3>", self._on_map_right_click)
        self.canvas.bind("<Configure>", lambda _event: self._redraw_map())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _server_is_running(self) -> bool:
        return self.server is not None

    def _log(self, kind: str, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        line = f"[{stamp}] {kind}: {message}\n"
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, line)
        if int(self.log_text.index("end-1c").split(".")[0]) > 1000:
            self.log_text.delete("1.0", "200.0")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _poll_events(self) -> None:
        try:
            while True:
                kind, message = self.event_queue.get_nowait()
                self._log(kind, message)
        except queue.Empty:
            pass
        if self.state is not None:
            status = self.state.status()
            self.session_status_var.set(
                "已进入" if status.entered else ("测试已结束" if status.ended else "等待 /enter")
            )
            self.virtual_time_var.set(f"虚拟时间：{status.virtual_time_s:g} s")
            self.remaining_time_var.set(f"现实剩余：{status.remaining_real_duration_s:.1f} s")
            latest_sources = list(self.state.sources())
            if tuple(item.to_dict() for item in latest_sources) != tuple(
                item.to_dict() for item in self.sources
            ):
                self.sources = latest_sources
                self._refresh_source_tree()
                self._select_index(self.selected_index)
                self._redraw_map()
            history = self.state.behavior_history()
            signature = tuple(
                (
                    event.get("step"),
                    event.get("action"),
                    event.get("result"),
                    event.get("channel"),
                    None
                    if not event.get("position")
                    else (
                        event["position"].get("x"),
                        event["position"].get("y"),
                    ),
                )
                for event in history
            )
            if signature != self._behavior_signature:
                self._behavior_signature = signature
                self._redraw_map()
            if self.server is not None and not status.active:
                self._stop_server(stop_state=False)
        self.after(200, self._poll_events)

    def _config_from_ui(self) -> SimulatorConfig:
        expected_robot_id = self.robot_id_var.get().strip() or None
        return SimulatorConfig(
            speed_mps=float(self.speed_var.get()),
            near_distance_m=float(self.near_var.get()),
            clear_radius_m=float(self.clear_radius_var.get()),
            coverage_angle_deg=float(self.coverage_var.get()),
            svd_error_deg=float(self.svd_error_var.get()),
            max_virtual_duration_s=360000.0,
            max_real_duration_s=float(self.max_real_var.get()),
            target_radius_m=TARGET_RADIUS_M,
            expected_robot_id=expected_robot_id,
            random_seed=self._random_seed(),
            measurement_error_mode="fixed_location",
            problem_mode=(
                "problem3"
                if 10 <= len(self.sources) <= 16
                and all(item.kind == "omni" for item in self.sources)
                else "custom"
            ),
            debug_expose_sources=True,
        )

    def start_server(self) -> None:
        if self._server_is_running():
            return
        if not self.sources:
            messagebox.showwarning("没有干扰源", "请先添加至少一个干扰源。")
            return
        channels = [item.channel for item in self.sources]
        if len(channels) != len(set(channels)):
            messagebox.showerror("频道重复", "每个频道最多只能配置一个干扰源。")
            return
        try:
            port = int(self.port_var.get())
            if not 1 <= port <= 65535:
                raise ValueError("端口必须在 1..65535 内")
            config = self._config_from_ui()
            self.state = SimulatorState(
                config,
                self.sources,
                event_sink=lambda kind, message: self.event_queue.put((kind, message)),
            )
            self.server = make_server("127.0.0.1", port, self.state)
            self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.server_thread.start()
            self.state.start_session()
            self._behavior_signature = None
            self.sources = list(self.state.sources())
            self._refresh_source_tree()
            self._select_index(self.selected_index)
        except Exception as exc:
            if self.server is not None:
                self.server.server_close()
            self.server = None
            self.server_thread = None
            self.state = None
            messagebox.showerror("启动失败", str(exc))
            return
        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        self.server_status_var.set(f"接口运行中：http://127.0.0.1:{port}")
        self._log("system", f"接口已启动，端口 {port}")
        self._redraw_map()

    def stop_server(self) -> None:
        self._stop_server(stop_state=True)

    def _stop_server(self, *, stop_state: bool) -> None:
        if self.state is not None and stop_state:
            self.state.stop_session("manual_stop")
        server = self.server
        self.server = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if self.server_thread is not None and self.server_thread.is_alive():
            self.server_thread.join(timeout=2.0)
        self.server_thread = None
        self.start_button.configure(state=tk.NORMAL)
        self.stop_button.configure(state=tk.DISABLED)
        self.server_status_var.set("接口未启动")
        self._redraw_map()

    def _sync_sources_to_active_state(self) -> None:
        if self.state is not None and self.server is not None:
            self.state.replace_sources(self.sources)

    def reset_field(self) -> None:
        """停止当前测试并复位场地，但保留干扰源配置。"""

        if self._server_is_running():
            self._stop_server(stop_state=True)
        self.state = None
        self._behavior_signature = None
        self.sources = [
            InterferenceSource(
                item.channel,
                item.x,
                item.y,
                item.kind,
                item.receive_radius_m,
                item.direction_deg,
                False,
            )
            for item in self.sources
        ]
        self.selected_index = None if not self.sources else min(self.selected_index or 0, len(self.sources) - 1)
        self.server_status_var.set("接口未启动")
        self.session_status_var.set("未进入")
        self.virtual_time_var.set("虚拟时间：0 s")
        self.remaining_time_var.set("现实剩余：未开始")
        self._refresh_source_tree()
        self._select_index(self.selected_index)
        self._redraw_map()
        self._log("system", "场地已重置，干扰源配置保留，清除状态和机器狗轨迹已清空")

    def _next_channel(self) -> int:
        used = {item.channel for item in self.sources}
        for channel in range(CHANNEL_MIN, CHANNEL_MAX + 1):
            if channel not in used:
                return channel
        raise ValueError("频道 1..20 已全部使用")

    def _within_target(self, x: float, y: float) -> tuple[float, float]:
        radius = math.hypot(x, y)
        if radius <= TARGET_RADIUS_M:
            return x, y
        ratio = TARGET_RADIUS_M / radius
        return x * ratio, y * ratio

    def _push_history(self) -> None:
        self._drag_history.append([item.clone() for item in self.sources])
        if len(self._drag_history) > 100:
            self._drag_history.pop(0)

    def add_source(self, x: float | None = None, y: float | None = None) -> None:
        try:
            channel = self._next_channel()
        except ValueError as exc:
            messagebox.showerror("无法新增", str(exc))
            return
        position = self._within_target(0.0 if x is None else x, 0.0 if y is None else y)
        self._push_history()
        self.sources.append(InterferenceSource(channel, position[0], position[1], "omni", 1200.0, 0.0))
        self.selected_index = len(self.sources) - 1
        self._sync_sources_to_active_state()
        self._refresh_source_tree()
        self._select_index(self.selected_index)
        self._redraw_map()

    def delete_selected(self) -> None:
        if self.selected_index is None:
            return
        self._push_history()
        del self.sources[self.selected_index]
        self.selected_index = None
        self._sync_sources_to_active_state()
        self._refresh_source_tree()
        self._select_index(None)
        self._redraw_map()

    def clear_sources(self) -> None:
        if not self.sources:
            return
        self._push_history()
        self.sources.clear()
        self.selected_index = None
        self._sync_sources_to_active_state()
        self._refresh_source_tree()
        self._select_index(None)
        self._redraw_map()

    def _random_seed(self) -> int:
        try:
            return int(self.random_seed_var.get().strip())
        except ValueError:
            self.random_seed_var.set("20260911")
            return 20260911

    def random_sources(self) -> None:
        """Generate a problem-3-compatible case: omni, 10..16, inside 1800 m."""
        import random

        rng = random.Random(self._random_seed())
        self._push_history()
        self.sources.clear()
        count = rng.randint(10, 16)
        channels = rng.sample(range(1, 21), count)
        for channel in sorted(channels):
            angle = rng.uniform(0.0, 2.0 * math.pi)
            radius = math.sqrt(rng.random()) * TARGET_RADIUS_M
            self.sources.append(
                InterferenceSource(
                    channel,
                    radius * math.cos(angle),
                    radius * math.sin(angle),
                    "omni",
                    rng.uniform(1000.0, 1500.0),
                    0.0,
                )
            )
        self.selected_index = None
        self._sync_sources_to_active_state()
        self._refresh_source_tree()
        self._select_index(None)
        self._redraw_map()

    def random_mixed_sources(self) -> None:
        """Generate a problem-4-style mixed case for manual testing."""
        import random

        rng = random.Random(self._random_seed() + 1)
        self._push_history()
        self.sources.clear()
        count = rng.randint(10, 16)
        channels = rng.sample(range(1, 21), count)
        for channel in sorted(channels):
            angle = rng.uniform(0.0, 2.0 * math.pi)
            radius = math.sqrt(rng.random()) * TARGET_RADIUS_M
            kind = "omni" if rng.random() < 0.5 else "directional"
            self.sources.append(
                InterferenceSource(
                    channel,
                    radius * math.cos(angle),
                    radius * math.sin(angle),
                    kind,
                    rng.uniform(1000.0, 1500.0),
                    rng.uniform(0.0, 360.0),
                )
            )
        self.selected_index = None
        self._sync_sources_to_active_state()
        self._refresh_source_tree()
        self._select_index(None)
        self._redraw_map()

    def _on_tree_select(self, _event=None) -> None:
        selection = self.source_tree.selection()
        if selection:
            self._select_index(int(selection[0]))

    def _select_index(self, index: int | None) -> None:
        self.selected_index = index
        if index is None:
            self.selected_var.set("未选择干扰源")
            return
        if not 0 <= index < len(self.sources):
            return
        item = self.sources[index]
        self.selected_var.set(f"干扰源频道 {item.channel}")
        self.channel_var.set(str(item.channel))
        self.kind_var.set(TYPE_TO_LABEL[item.kind])
        self.x_var.set(f"{item.x:.10g}")
        self.y_var.set(f"{item.y:.10g}")
        self.radius_var.set(f"{item.receive_radius_m:.10g}")
        self.direction_var.set(f"{item.direction_deg:.10g}")

    def _refresh_source_tree(self) -> None:
        selected = self.selected_index
        for item_id in self.source_tree.get_children():
            self.source_tree.delete(item_id)
        for index, item in enumerate(self.sources):
            self.source_tree.insert(
                "",
                tk.END,
                iid=str(index),
                values=(
                    item.channel,
                    TYPE_TO_LABEL[item.kind],
                    f"{item.x:.6g}",
                    f"{item.y:.6g}",
                    f"{item.receive_radius_m:.6g}",
                    f"{item.direction_deg:.6g}",
                    "已清除" if item.cleared else "未清除",
                ),
            )
        if selected is not None and 0 <= selected < len(self.sources):
            self.source_tree.selection_set(str(selected))

    def apply_editor(self) -> None:
        if self.selected_index is None:
            messagebox.showwarning("未选择", "请先选择一个干扰源。")
            return
        try:
            channel = int(self.channel_var.get())
            if not CHANNEL_MIN <= channel <= CHANNEL_MAX:
                raise ValueError("频道必须在 1..20 内")
            if any(i != self.selected_index and source.channel == channel for i, source in enumerate(self.sources)):
                raise ValueError("该频道已被其他干扰源使用")
            x = float(self.x_var.get())
            y = float(self.y_var.get())
            x, y = self._within_target(x, y)
            kind = LABEL_TO_TYPE[self.kind_var.get()]
            radius = float(self.radius_var.get())
            direction = float(self.direction_var.get())
            updated = InterferenceSource(channel, x, y, kind, radius, direction)
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        self._push_history()
        self.sources[self.selected_index] = updated
        self._sync_sources_to_active_state()
        self._refresh_source_tree()
        self._select_index(self.selected_index)
        self._redraw_map()

    def _canvas_size(self) -> tuple[int, int]:
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        if width <= 1 or height <= 1:
            return 900, 700
        return width, height

    def _update_transform(self) -> None:
        width, height = self._canvas_size()
        xmin, xmax, ymin, ymax = self._view
        margin = 35.0
        self._scale = min(
            (width - 2.0 * margin) / max(1e-9, xmax - xmin),
            (height - 2.0 * margin) / max(1e-9, ymax - ymin),
        )
        center_x = 0.5 * (xmin + xmax)
        center_y = 0.5 * (ymin + ymax)
        self._screen_x0 = 0.5 * width - center_x * self._scale
        self._screen_y0 = 0.5 * height + center_y * self._scale

    def _world_to_screen(self, point: tuple[float, float] | list[float]) -> tuple[float, float]:
        return (
            self._screen_x0 + float(point[0]) * self._scale,
            self._screen_y0 - float(point[1]) * self._scale,
        )

    def _screen_to_world(self, x: float, y: float) -> tuple[float, float]:
        return (
            (float(x) - self._screen_x0) / self._scale,
            (self._screen_y0 - float(y)) / self._scale,
        )

    def fit_view(self) -> None:
        self._view = (-1950.0, 1950.0, -1950.0, 1950.0)
        self._redraw_map()

    def _redraw_map(self) -> None:
        self.canvas.delete("all")
        self._update_transform()
        for value in range(-1500, 1501, 500):
            first = self._world_to_screen((value, -1800))
            second = self._world_to_screen((value, 1800))
            self.canvas.create_line(first[0], first[1], second[0], second[1], fill="#eeeeee")
            first = self._world_to_screen((-1800, value))
            second = self._world_to_screen((1800, value))
            self.canvas.create_line(first[0], first[1], second[0], second[1], fill="#eeeeee")

        center = self._world_to_screen((0.0, 0.0))
        radius_px = TARGET_RADIUS_M * self._scale
        self.canvas.create_oval(
            center[0] - radius_px,
            center[1] - radius_px,
            center[0] + radius_px,
            center[1] + radius_px,
            outline="#455a64",
            width=2,
        )
        self.canvas.create_oval(center[0] - 4, center[1] - 4, center[0] + 4, center[1] + 4, fill="#263238")

        for index, source in enumerate(self.sources):
            self._draw_source(index, source)

        self._draw_behavior()

        robot_text = "机器狗：未进入"
        if self.state is not None:
            status = self.state.status()
            if status.current_position is not None:
                robot_text = (
                    f"机器狗：({status.current_position[0]:.1f}, {status.current_position[1]:.1f})"
                    f"  频道 {status.current_channel}  虚拟时间 {status.virtual_time_s:g} s"
                )
        self.canvas.create_text(
            12,
            12,
            text=f"目标区域半径 {TARGET_RADIUS_M:.0f} m   |   接口 {'运行中' if self._server_is_running() else '未启动'}",
            anchor=tk.NW,
            fill="#37474f",
            font=("Segoe UI", 10, "bold"),
        )
        self.canvas.create_text(
            12,
            34,
            text=robot_text,
            anchor=tk.NW,
            fill="#b71c1c",
            font=("Segoe UI", 10, "bold"),
        )

    def _draw_behavior(self) -> None:
        """绘制机器狗轨迹、检测方向和中转事件。"""

        if self.state is None:
            return
        history = self.state.behavior_history()
        points: list[tuple[float, float]] = []
        for event in history:
            position = event.get("position")
            if isinstance(position, dict) and position.get("x") is not None:
                points.append((float(position["x"]), float(position["y"])))

        if len(points) >= 2:
            coords: list[float] = []
            for point in points:
                screen = self._world_to_screen(point)
                coords.extend([screen[0], screen[1]])
            self.canvas.create_line(
                coords,
                fill="#455a64",
                width=2,
                dash=(7, 5),
                arrow=tk.LAST,
                arrowshape=(10, 12, 4),
            )

        for event in history:
            position = event.get("position")
            if not isinstance(position, dict) or position.get("x") is None:
                continue
            point = (float(position["x"]), float(position["y"]))
            screen = self._world_to_screen(point)
            action = str(event.get("action", ""))
            result = str(event.get("result", ""))

            if action == "measure" and result == "direction" and event.get("svd_deg") is not None:
                angle = math.radians(float(event["svd_deg"]))
                length = 260.0
                endpoint = (
                    point[0] + length * math.cos(angle),
                    point[1] + length * math.sin(angle),
                )
                end_screen = self._world_to_screen(endpoint)
                self.canvas.create_line(
                    screen[0],
                    screen[1],
                    end_screen[0],
                    end_screen[1],
                    fill="#f57c00",
                    width=2,
                    arrow=tk.LAST,
                    arrowshape=(10, 12, 4),
                )

            if action == "enter":
                color = "#263238"
                radius = 5
            elif action == "clear" and result == "success":
                color = "#2e7d32"
                radius = 7
            elif action == "clear":
                color = "#757575"
                radius = 6
            elif result == "near":
                color = "#d32f2f"
                radius = 6
            elif result == "direction":
                color = "#1565c0"
                radius = 6
            else:
                color = "#9e9e9e"
                radius = 5

            self.canvas.create_oval(
                screen[0] - radius,
                screen[1] - radius,
                screen[0] + radius,
                screen[1] + radius,
                fill=color,
                outline="white",
                width=1,
            )
            self.canvas.create_text(
                screen[0] + 8,
                screen[1] + 8,
                text=f"{event.get('step')}.{action[:1].upper()}",
                anchor=tk.NW,
                fill=color,
                font=("Segoe UI", 8),
            )

        status = self.state.status()
        if status.current_position is not None:
            current = self._world_to_screen(status.current_position)
            self.canvas.create_oval(
                current[0] - 10,
                current[1] - 10,
                current[0] + 10,
                current[1] + 10,
                fill="#d32f2f",
                outline="#212121",
                width=2,
            )
            self.canvas.create_text(
                current[0],
                current[1],
                text="狗",
                fill="white",
                font=("Microsoft YaHei", 8, "bold"),
            )

    def _draw_source(self, index: int, source: InterferenceSource) -> None:
        center = self._world_to_screen(source.position)
        color = "#9e9e9e" if source.cleared else ("#1565c0" if source.kind == "omni" else "#ef6c00")
        radius_px = source.receive_radius_m * self._scale
        self.canvas.create_oval(
            center[0] - radius_px,
            center[1] - radius_px,
            center[0] + radius_px,
            center[1] + radius_px,
            outline=color,
            dash=(5, 4),
            width=1,
        )
        if source.kind == "directional" and not source.cleared:
            half = 90.0
            points = [center]
            for step in range(25):
                angle = math.radians(source.direction_deg - half + step * (2.0 * half / 24.0))
                points.extend([center[0] + radius_px * math.cos(angle), center[1] - radius_px * math.sin(angle)])
            self.canvas.create_polygon(points, fill="#ffe0b2", outline="", stipple="gray25")
        selected = index == self.selected_index
        point_radius = 8 if selected else 6
        if source.cleared:
            self.canvas.create_line(center[0] - 6, center[1] - 6, center[0] + 6, center[1] + 6, fill=color, width=3)
            self.canvas.create_line(center[0] - 6, center[1] + 6, center[0] + 6, center[1] - 6, fill=color, width=3)
        else:
            self.canvas.create_oval(
                center[0] - point_radius,
                center[1] - point_radius,
                center[0] + point_radius,
                center[1] + point_radius,
                fill=color,
                outline="white",
                width=2 if selected else 1,
            )
        self.canvas.create_text(
            center[0] + 9,
            center[1] - 9,
            text=f"CH{source.channel}",
            anchor=tk.SW,
            fill=color,
            font=("Segoe UI", 9, "bold"),
        )

    def _find_source_near(self, x: float, y: float) -> int | None:
        for index, source in enumerate(self.sources):
            screen = self._world_to_screen(source.position)
            if math.hypot(screen[0] - x, screen[1] - y) <= self.POINT_PIXELS:
                return index
        return None

    def _on_map_press(self, event) -> None:
        index = self._find_source_near(event.x, event.y)
        if index is not None:
            self._select_index(index)
            if not self._server_is_running():
                self._push_history()
                self._drag_mode = "move"
            self._redraw_map()
            return
        if self._server_is_running():
            return
        world = self._screen_to_world(event.x, event.y)
        self.add_source(world[0], world[1])

    def _on_map_motion(self, event) -> None:
        if self._drag_mode != "move" or self.selected_index is None:
            return
        world = self._screen_to_world(event.x, event.y)
        x, y = self._within_target(world[0], world[1])
        item = self.sources[self.selected_index]
        self.sources[self.selected_index] = InterferenceSource(
            item.channel,
            x,
            y,
            item.kind,
            item.receive_radius_m,
            item.direction_deg,
            item.cleared,
        )
        self._redraw_map()

    def _on_map_release(self, _event) -> None:
        if self._drag_mode is None:
            return
        self._drag_mode = None
        self._refresh_source_tree()
        self._select_index(self.selected_index)
        self._redraw_map()

    def _on_map_right_click(self, event) -> None:
        index = self._find_source_near(event.x, event.y)
        if index is not None:
            self.selected_index = index
            self.delete_selected()

    def import_sources(self) -> None:
        path = filedialog.askopenfilename(title="导入干扰源", filetypes=[("JSON", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            loaded = load_sources_json(path)
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc))
            return
        self._push_history()
        self.sources = loaded
        self.selected_index = None
        self._sync_sources_to_active_state()
        self._refresh_source_tree()
        self._select_index(None)
        self._redraw_map()

    def export_sources(self) -> None:
        path = filedialog.asksaveasfilename(title="导出干扰源", defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            save_sources_json(path, self.sources)
            self._log("system", f"已导出干扰源配置：{path}")
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))

    def self_test(self) -> None:
        if not self._server_is_running() or self.state is None:
            messagebox.showwarning("接口未启动", "请先启动接口。")
            return
        try:
            port = int(self.port_var.get())
            from urllib.request import Request, urlopen

            def post(path: str, payload: dict[str, object]):
                request = Request(
                    f"http://127.0.0.1:{port}{path}",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=3) as response:
                    return json.loads(response.read().decode("utf-8"))

            base = {"arena_id": "default", "robot_id": self.robot_id_var.get().strip() or "self-test", "request_id": "self-enter"}
            enter = post("/enter", base)
            self._log("self-test", f"/enter 响应：{enter}")
        except Exception as exc:
            messagebox.showerror("自检失败", str(exc))

    def _on_close(self) -> None:
        try:
            self._stop_server(stop_state=True)
        finally:
            self.destroy()

    def run(self) -> None:
        self.mainloop()


def main() -> None:
    app = InterferenceSimulatorApp()
    app.run()


if __name__ == "__main__":
    main()
