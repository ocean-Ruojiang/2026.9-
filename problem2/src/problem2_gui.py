"""Problem 2 interactive visualizer for the two-stage localization strategy."""

from __future__ import annotations

import json
import math
import queue
import sys
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np

_CODE_DIR = Path(__file__).resolve().parent
if str(_CODE_DIR) not in sys.path:
    sys.path.insert(0, str(_CODE_DIR))

import problem2_optimizer as p2


class Problem2App(tk.Tk):
    """Visualize Stage 1 regions and Stage 2 finite-element search."""

    def __init__(self) -> None:
        super().__init__()
        self.title("第2题：无线电干扰源第二检测点优化")
        self.geometry("1480x920")
        self.minsize(1180, 720)

        self.view = (-1950.0, 1950.0, -1950.0, 1950.0)
        self.screen_x0 = 0.0
        self.screen_y0 = 0.0
        self.scale = 1.0
        self.result: p2.FEMSearchResult | None = None
        self.display_possible_region: p2.CurvedRegion | None = None
        self.candidate_empty = False
        self._compute_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.running = False
        self.compute_started_at: float | None = None

        self.first_x_var = tk.StringVar(value="0")
        self.first_y_var = tk.StringVar(value="0")
        self.bearing_var = tk.StringVar(value="0")
        self.error_var = tk.StringVar(value="1")
        self.target_radius_var = tk.StringVar(value="1800")
        self.max_radius_var = tk.StringVar(value="1500")
        self.guaranteed_radius_var = tk.StringVar(value="1000")
        self.coarse_width_var = tk.StringVar(value="150")
        self.refine_width_var = tk.StringVar(value="40")
        self.angle_step_var = tk.StringVar(value="10")
        self.epsilon_var = tk.StringVar(value="5")
        self.metric_var = tk.StringVar(value="both")
        self.circle_samples_var = tk.StringVar(value="720")
        self.max_constraints_var = tk.StringVar(value="96")
        self.coverage_spacing_var = tk.StringVar(value="50")
        self.safety_margin_var = tk.StringVar(value="100")
        self.status_var = tk.StringVar(value="就绪")
        self.possible_stats_var = tk.StringVar(value="目标可能区域：-")
        self.candidate_stats_var = tk.StringVar(value="保证可检测区域：-")
        self.coarse_result_var = tk.StringVar(value="粗网格结果：-")
        self.final_result_var = tk.StringVar(value="细化结果：-")

        self._configure_style()
        self._build_layout()
        self.after(100, self._poll_compute_queue)
        self.after_idle(self._redraw)

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
        side = ttk.Frame(split, width=430)
        split.add(map_frame, weight=4)
        split.add(side, weight=2)

        toolbar = ttk.Frame(map_frame)
        toolbar.pack(fill=tk.X, padx=8, pady=(8, 4))
        self.run_button = ttk.Button(toolbar, text="开始计算", command=self.run_search)
        self.run_button.pack(side=tk.LEFT)
        ttk.Button(toolbar, text="适应视图", command=self.fit_view).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(toolbar, text="清除结果", command=self.clear_results).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(toolbar, text="导出JSON", command=self.export_json).pack(side=tk.LEFT, padx=(6, 0))

        self.canvas = tk.Canvas(map_frame, background="white", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self._build_sidebar(side)

        ttk.Label(self, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W, padding=(8, 4)).grid(row=1, column=0, sticky="ew")

    def _build_sidebar(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)

        stage1 = ttk.LabelFrame(parent, text="第一阶段参数", padding=8)
        stage1.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))
        stage1.columnconfigure(1, weight=1)
        rows1 = [
            ("第一检测点 x", self.first_x_var),
            ("第一检测点 y", self.first_y_var),
            ("第一示向度/°", self.bearing_var),
            ("示向度误差/°", self.error_var),
            ("目标区域半径/m", self.target_radius_var),
            ("最大检测半径/m", self.max_radius_var),
            ("保证检测半径/m", self.guaranteed_radius_var),
            ("圆周采样数", self.circle_samples_var),
            ("最大圆盘约束数", self.max_constraints_var),
            ("边界采样间距/m", self.coverage_spacing_var),
            ("安全裕量/m", self.safety_margin_var),
        ]
        for row, (label, variable) in enumerate(rows1):
            ttk.Label(stage1, text=label).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Entry(stage1, textvariable=variable, width=14).grid(row=row, column=1, sticky="ew", pady=2, padx=(6, 0))

        stage2 = ttk.LabelFrame(parent, text="第二阶段有限元", padding=8)
        stage2.grid(row=1, column=0, sticky="ew", padx=8, pady=6)
        stage2.columnconfigure(1, weight=1)
        rows2 = [
            ("粗网格宽度/m", self.coarse_width_var),
            ("细化网格宽度/m", self.refine_width_var),
            ("角度遍历步长/°", self.angle_step_var),
            ("较优阈值/m", self.epsilon_var),
        ]
        for row, (label, variable) in enumerate(rows2):
            ttk.Label(stage2, text=label).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Entry(stage2, textvariable=variable, width=14).grid(row=row, column=1, sticky="ew", pady=2, padx=(6, 0))
        ttk.Label(stage2, text="评价指标").grid(row=4, column=0, sticky="nw", pady=(8, 2))
        metric_frame = ttk.Frame(stage2)
        metric_frame.grid(row=4, column=1, sticky="ew", pady=(8, 2), padx=(6, 0))
        ttk.Radiobutton(metric_frame, text="方法一：最大直径", variable=self.metric_var, value="max").pack(anchor="w")
        ttk.Radiobutton(metric_frame, text="方法二：有效角度积分平均", variable=self.metric_var, value="mean").pack(anchor="w")
        ttk.Radiobutton(metric_frame, text="方法一 + 方法二同时对比", variable=self.metric_var, value="both").pack(anchor="w")

        results = ttk.LabelFrame(parent, text="计算结果", padding=8)
        results.grid(row=2, column=0, sticky="ew", padx=8, pady=6)
        for variable in (self.possible_stats_var, self.candidate_stats_var, self.coarse_result_var, self.final_result_var):
            ttk.Label(results, textvariable=variable, wraplength=390).pack(anchor=tk.W, pady=2)

        legend = ttk.LabelFrame(parent, text="图例", padding=8)
        legend.grid(row=3, column=0, sticky="ew", padx=8, pady=6)
        for text in (
            "蓝色点状填充：目标可能出现位置",
            "绿色密点填充：第二检测点一定能检测的区域",
            "虚线边框：圆弧边",
            "实线边框：直线边",
            "红色十字：粗网格最优点",
            "绿色圆环：二次细化最优点",
            "黄色圆点：第一检测点",
            "彩色小点：候选第二检测点，蓝优红差",
            "对比模式：红色=最大直径，蓝色=积分平均",
        ):
            ttk.Label(legend, text=text).pack(anchor=tk.W)

    def _read_float(self, variable: tk.StringVar, name: str) -> float:
        try:
            value = float(variable.get())
        except ValueError as exc:
            raise ValueError(f"{name} must be a number") from exc
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
        return value

    def run_search(self) -> None:
        if self.running:
            return
        try:
            first_x = self._read_float(self.first_x_var, "第一检测点 x")
            first_y = self._read_float(self.first_y_var, "第一检测点 y")
            bearing = self._read_float(self.bearing_var, "第一示向度")
            error = self._read_float(self.error_var, "示向度误差")
            target_radius = self._read_float(self.target_radius_var, "目标区域半径")
            max_radius = self._read_float(self.max_radius_var, "最大检测半径")
            guaranteed_radius = self._read_float(self.guaranteed_radius_var, "保证检测半径")
            coarse_width = self._read_float(self.coarse_width_var, "粗网格宽度")
            refine_width = self._read_float(self.refine_width_var, "细化网格宽度")
            angle_step = self._read_float(self.angle_step_var, "角度遍历步长")
            circle_samples = int(self.circle_samples_var.get())
            max_constraints = int(self.max_constraints_var.get())
            coverage_spacing = self._read_float(self.coverage_spacing_var, "边界采样间距")
            safety_margin = self._read_float(self.safety_margin_var, "安全裕量")
            metric = self.metric_var.get()
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))
            return

        if angle_step < 2.0 or coarse_width < 50.0 or refine_width < 10.0 or circle_samples > 1440 or max_constraints > 192:
            proceed = messagebox.askyesno(
                "计算量较大",
                "当前网格或角度设置可能产生很大计算量。\n"
                "是否仍然继续？\n\n"
                "建议：粗网格宽度≥50，细化宽度≥10，角度步长≥2。",
            )
            if not proceed:
                return

        self.running = True
        self.compute_started_at = time.time()
        self.run_button.configure(state=tk.DISABLED)
        self.status_var.set("正在计算第一阶段和第二阶段……")

        def worker() -> None:
            try:
                common = dict(
                    target_radius=target_radius,
                    maximum_detection_radius=max_radius,
                    guaranteed_radius=guaranteed_radius,
                    bearing_error_deg=error,
                    angle_step_deg=angle_step,
                    coarse_width=coarse_width,
                    refine_width=refine_width,
                    circle_samples=circle_samples,
                    maximum_constraints=max_constraints,
                    coverage_spacing=coverage_spacing,
                    safety_margin=safety_margin,
                )
                if metric == "both":
                    result = p2.finite_element_search_dual(
                        (first_x, first_y), bearing, **common
                    )
                else:
                    result = p2.finite_element_search(
                        (first_x, first_y), bearing, metric=metric, **common
                    )
                self._compute_queue.put(("ok", result))
            except p2.EmptyCandidateRegionError as exc:
                self._compute_queue.put(("empty", exc.possible_region))
            except Exception as exc:
                self._compute_queue.put(("error", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_compute_queue(self) -> None:
        try:
            kind, payload = self._compute_queue.get_nowait()
        except queue.Empty:
            if self.running and self.compute_started_at is not None:
                elapsed = time.time() - self.compute_started_at
                self.status_var.set(f"正在计算……已用 {elapsed:.1f} 秒")
            self.after(100, self._poll_compute_queue)
            return
        self.running = False
        self.compute_started_at = None
        self.run_button.configure(state=tk.NORMAL)
        if kind == "error":
            messagebox.showerror("计算失败", str(payload))
            self.status_var.set(f"计算失败：{payload}")
            self.after(100, self._poll_compute_queue)
            return
        if kind == "empty":
            self.result = None
            self.display_possible_region = payload  # type: ignore[assignment]
            self.candidate_empty = True
            self.possible_stats_var.set(
                f"目标可能区域：{len(self.display_possible_region.vertices)} 个顶点"
            )
            self.candidate_stats_var.set("保证可检测子区域：空集（正常结果）")
            self.coarse_result_var.set("粗网格结果：不可用")
            self.final_result_var.set("细化结果：不可用")
            self.status_var.set("保证可检测区域为空，仍显示第一阶段结果")
            self._redraw()
            self.after(100, self._poll_compute_queue)
            return
        self.result = payload  # type: ignore[assignment]
        self.display_possible_region = self.result.possible_region
        self.candidate_empty = False
        self.status_var.set("计算完成")
        try:
            self._update_stats()
            self.fit_view()
        except Exception as exc:
            self.status_var.set(f"计算完成，但绘图失败：{exc}")
        self.after(100, self._poll_compute_queue)

    def clear_results(self) -> None:
        self.result = None
        self.display_possible_region = None
        self.candidate_empty = False
        self.possible_stats_var.set("目标可能区域：-")
        self.candidate_stats_var.set("保证可检测区域：-")
        self.coarse_result_var.set("粗网格结果：-")
        self.final_result_var.set("细化结果：-")
        self.status_var.set("就绪")
        self._redraw()

    def _update_stats(self) -> None:
        if self.result is None:
            return
        possible_edges = self.result.possible_region.edges()
        candidate_edges = self.result.candidate_region.edges()
        arc_count = sum(edge.kind == "arc" for edge in possible_edges)
        line_count = sum(edge.kind == "line" for edge in possible_edges)
        self.possible_stats_var.set(
            f"目标可能区域：{len(self.result.possible_region.vertices)} 个顶点，"
            f"{line_count} 条直线边，{arc_count} 条圆弧边，直径={p2.region_diameter(self.result.possible_region):.3f} m"
        )
        candidate_area = self._polygon_area(self.result.candidate_region.vertices)
        self.candidate_stats_var.set(
            f"保证可检测子区域（保守）：{len(self.result.candidate_region.vertices)} 个顶点，"
            f"面积={candidate_area:,.1f} m² = {candidate_area / 1_000_000.0:.4f} km²"
        )
        epsilon = float(self.epsilon_var.get())
        near = p2.near_optimal_points(self.result, epsilon)
        if isinstance(self.result, p2.DualFEMSearchResult):
            self.coarse_result_var.set(
                f"方法一粗网格最优：({self.result.best_max_coarse.x:.3f}, {self.result.best_max_coarse.y:.3f})，"
                f"最大直径={self.result.best_max_coarse.max_value:.3f} m\n"
                f"方法二粗网格最优：({self.result.best_mean_coarse.x:.3f}, {self.result.best_mean_coarse.y:.3f})，"
                f"积分平均直径={self.result.best_mean_coarse.mean_value:.3f} m\n"
                f"法一较优点数={len(near['max'])}；法二较优点数={len(near['mean'])}"
            )
            self.final_result_var.set(
                f"方法一细化最优：({self.result.best_max.x:.3f}, {self.result.best_max.y:.3f})，"
                f"最大直径={self.result.best_max.max_value:.3f} m\n"
                f"方法二细化最优：({self.result.best_mean.x:.3f}, {self.result.best_mean.y:.3f})，"
                f"积分平均直径={self.result.best_mean.mean_value:.3f} m\n"
                f"较优阈值 ε={epsilon:g} m；未进行最近距离筛选"
            )
        else:
            metric_name = "最大直径" if self.result.metric == "max" else "有效角度积分平均直径"
            self.coarse_result_var.set(
                f"粗网格最优：({self.result.best_coarse.x:.3f}, {self.result.best_coarse.y:.3f})，"
                f"{metric_name}={self.result.best_coarse.value:.3f} m"
            )
            self.final_result_var.set(
                f"细化最优：({self.result.best.x:.3f}, {self.result.best.y:.3f})，"
                f"{metric_name}={self.result.best.value:.3f} m\n"
                f"较优点数={len(near)}，ε={epsilon:g} m；未进行最近距离筛选"
            )

    @staticmethod
    def _polygon_area(points: list[np.ndarray]) -> float:
        if len(points) < 3:
            return 0.0
        value = 0.0
        for index, point in enumerate(points):
            following = points[(index + 1) % len(points)]
            value += float(point[0] * following[1] - point[1] * following[0])
        return abs(value) * 0.5

    # ------------------------------------------------------------------
    # Coordinate transform
    # ------------------------------------------------------------------
    def _canvas_size(self) -> tuple[int, int]:
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        if width <= 1 or height <= 1:
            return 900, 700
        return width, height

    def _update_transform(self) -> None:
        width, height = self._canvas_size()
        xmin, xmax, ymin, ymax = self.view
        margin = 36.0
        self.scale = min(
            (width - 2.0 * margin) / max(1e-9, xmax - xmin),
            (height - 2.0 * margin) / max(1e-9, ymax - ymin),
        )
        center_x = 0.5 * (xmin + xmax)
        center_y = 0.5 * (ymin + ymax)
        self.screen_x0 = 0.5 * width - center_x * self.scale
        self.screen_y0 = 0.5 * height + center_y * self.scale

    def _world_to_screen(self, point: object) -> tuple[float, float]:
        array = np.asarray(point, dtype=np.float64)
        return (
            self.screen_x0 + float(array[0]) * self.scale,
            self.screen_y0 - float(array[1]) * self.scale,
        )

    def fit_view(self) -> None:
        points = [(0.0, 0.0), (1800.0, 1800.0), (-1800.0, -1800.0)]
        if self.display_possible_region is not None and not self.display_possible_region.empty:
            points.extend(self.display_possible_region.vertices)
        if self.result is not None and not self.result.candidate_region.empty:
            points.extend(self.result.candidate_region.vertices)
        array = np.vstack(points)
        xmin, ymin = np.min(array, axis=0)
        xmax, ymax = np.max(array, axis=0)
        span = max(float(xmax - xmin), float(ymax - ymin), 100.0)
        cx = 0.5 * float(xmin + xmax)
        cy = 0.5 * float(ymin + ymax)
        margin = 0.12 * span
        self.view = (cx - 0.5 * span - margin, cx + 0.5 * span + margin, cy - 0.5 * span - margin, cy + 0.5 * span + margin)
        self._redraw()

    def _draw_circle(self, center: tuple[float, float], radius: float, **kwargs: object) -> None:
        screen = self._world_to_screen(center)
        r = radius * self.scale
        self.canvas.create_oval(screen[0] - r, screen[1] - r, screen[0] + r, screen[1] + r, **kwargs)

    def _draw_region(self, region: p2.CurvedRegion, fill: str, outline: str, stipple: str) -> None:
        if region.empty:
            return
        coordinates: list[float] = []
        for point in region.vertices:
            screen = self._world_to_screen(point)
            coordinates.extend([screen[0], screen[1]])
        self.canvas.create_polygon(coordinates, fill=fill, outline="", stipple=stipple)
        for edge in region.edges(arc_tol=max(0.5, 2.0 / max(self.scale, 1e-9))):
            first = self._world_to_screen(edge.start)
            second = self._world_to_screen(edge.end)
            self.canvas.create_line(
                first[0],
                first[1],
                second[0],
                second[1],
                fill=outline,
                width=3 if edge.kind == "arc" else 1.5,
                dash=(6, 3) if edge.kind == "arc" else None,
            )

    def _redraw(self) -> None:
        self.canvas.delete("all")
        self._update_transform()
        width, height = self._canvas_size()

        for value in range(-1500, 1501, 500):
            first = self._world_to_screen((value, -1800))
            second = self._world_to_screen((value, 1800))
            self.canvas.create_line(first[0], first[1], second[0], second[1], fill="#eeeeee")
            first = self._world_to_screen((-1800, value))
            second = self._world_to_screen((1800, value))
            self.canvas.create_line(first[0], first[1], second[0], second[1], fill="#eeeeee")

        self._draw_circle((0.0, 0.0), 1800.0, outline="#455a64", width=2)
        self._draw_circle((0.0, 0.0), 1500.0, outline="#90a4ae", width=1, dash=(5, 4))
        self._draw_stage1_marks()

        if self.display_possible_region is not None:
            self._draw_region(self.display_possible_region, "#90caf9", "#1565c0", "gray25")
            self._draw_region_label(self.display_possible_region, "目标可能区域", "#0d47a1")
        if self.result is not None:
            if not self.result.candidate_region.empty:
                self._draw_region(self.result.candidate_region, "#a5d6a7", "#2e7d32", "gray50")
                self._draw_region_label(
                    self.result.candidate_region,
                    "保证可检测子区域\n第二检测点候选范围",
                    "#1b5e20",
                )
            else:
                center_screen = self._world_to_screen((0.0, 0.0))
                self.canvas.create_text(
                    center_screen[0],
                    center_screen[1] - 60,
                    text="保证可检测区域：空集",
                    fill="#c62828",
                    font=("Microsoft YaHei", 12, "bold"),
                )
            self._draw_grid_heatmap(self.result.coarse)
            epsilon = float(self.epsilon_var.get())
            self._draw_near_optima(self.result, epsilon)
            if isinstance(self.result, p2.DualFEMSearchResult):
                self._draw_cross(self.result.best_max_coarse, "#d32f2f", ring=False)
                self._draw_cross(self.result.best_mean_coarse, "#1565c0", ring=False)
                self._draw_cross(self.result.best_max, "#d32f2f", ring=True)
                self._draw_cross(self.result.best_mean, "#1565c0", ring=True)
            else:
                self._draw_cross(self.result.best_coarse, "#d32f2f", ring=False)
                self._draw_cross(self.result.best, "#00c853", ring=True)

        self.canvas.create_text(12, 12, text="第2题：两阶段示向度定位优化", anchor=tk.NW, fill="#263238", font=("Segoe UI", 11, "bold"))
        metric_text = "最大定位直径" if self.metric_var.get() == "max" else "有效角度积分平均直径"
        self.canvas.create_text(12, 34, text=f"蓝色点状：目标可能区域；绿色密点：保证可检测区域；热力图指标：{metric_text}", anchor=tk.NW, fill="#546e7a", font=("Microsoft YaHei", 9))
        if self.metric_var.get() == "both":
            marker_legend = "红叉/红圈：方法一粗/细最优\n蓝叉/蓝圈：方法二粗/细最优\n红/蓝点及虚线：阈值内多个较优点"
        else:
            marker_legend = "红叉：粗网格最优\n绿圆：细化最优\n青点及虚线：阈值内多个较优点"
        self.canvas.create_text(
            width - 12,
            12,
            text=(
                "图例\n蓝色填充：目标可能区域\n绿色填充：保证可检测区域"
                "\n黄色圆点：第一检测点\n彩色小点：候选检测点，蓝优红差\n"
                + marker_legend
            ),
            anchor=tk.NE,
            justify=tk.RIGHT,
            fill="#37474f",
            font=("Microsoft YaHei", 9),
        )

    @staticmethod
    def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        unique = sorted(set(points))
        if len(unique) <= 2:
            return unique

        def cross(o, a, b):
            return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

        lower = []
        for point in unique:
            while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
                lower.pop()
            lower.append(point)
        upper = []
        for point in reversed(unique):
            while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
                upper.pop()
            upper.append(point)
        return lower[:-1] + upper[:-1]

    def _draw_hull(self, points, color: str) -> None:
        hull = self._convex_hull([(float(item.x), float(item.y)) for item in points])
        if len(hull) < 3:
            return
        coords = []
        for point in hull:
            screen = self._world_to_screen(point)
            coords.extend([screen[0], screen[1]])
        self.canvas.create_polygon(
            coords,
            fill="",
            outline=color,
            width=2,
            dash=(6, 4),
        )

    def _draw_near_optima(self, result, epsilon: float) -> None:
        near = p2.near_optimal_points(result, epsilon)
        if isinstance(result, p2.DualFEMSearchResult):
            self._draw_hull(near["max"], "#d32f2f")
            self._draw_hull(near["mean"], "#1565c0")
            for item in near["max"]:
                screen = self._world_to_screen((item.x, item.y))
                self.canvas.create_oval(screen[0] - 2, screen[1] - 2, screen[0] + 2, screen[1] + 2, fill="#d32f2f", outline="")
            for item in near["mean"]:
                screen = self._world_to_screen((item.x, item.y))
                self.canvas.create_oval(screen[0] - 2, screen[1] - 2, screen[0] + 2, screen[1] + 2, fill="#1565c0", outline="")
        else:
            self._draw_hull(near, "#00897b")
            for item in near:
                screen = self._world_to_screen((item.x, item.y))
                self.canvas.create_oval(screen[0] - 2, screen[1] - 2, screen[0] + 2, screen[1] + 2, fill="#00897b", outline="")

    def _draw_cross(self, item: object, color: str, *, ring: bool) -> None:
        screen = self._world_to_screen((float(getattr(item, "x")), float(getattr(item, "y"))))
        if ring:
            self.canvas.create_oval(
                screen[0] - 8,
                screen[1] - 8,
                screen[0] + 8,
                screen[1] + 8,
                outline=color,
                width=3,
            )
        else:
            self.canvas.create_line(screen[0] - 9, screen[1], screen[0] + 9, screen[1], fill=color, width=3)
            self.canvas.create_line(screen[0], screen[1] - 9, screen[0], screen[1] + 9, fill=color, width=3)

    def _draw_stage1_marks(self) -> None:
        try:
            first = (float(self.first_x_var.get()), float(self.first_y_var.get()))
            bearing = math.radians(float(self.bearing_var.get()))
            error = math.radians(float(self.error_var.get()))
        except ValueError:
            return
        center = self._world_to_screen(first)
        length = 1900.0
        for angle in (bearing - error, bearing + error):
            endpoint = (first[0] + length * math.cos(angle), first[1] + length * math.sin(angle))
            target = self._world_to_screen(endpoint)
            self.canvas.create_line(
                center[0],
                center[1],
                target[0],
                target[1],
                fill="#f9a825",
                width=2,
                dash=(8, 4),
                arrow=tk.LAST,
            )
        self.canvas.create_oval(
            center[0] - 8,
            center[1] - 8,
            center[0] + 8,
            center[1] + 8,
            fill="#fdd835",
            outline="#212121",
            width=2,
        )
        self.canvas.create_text(
            center[0] + 10,
            center[1] - 10,
            text="第一检测点",
            anchor=tk.SW,
            fill="#6d4c00",
            font=("Microsoft YaHei", 10, "bold"),
        )

    @staticmethod
    def _region_centroid(region: p2.CurvedRegion) -> np.ndarray:
        return np.mean(np.vstack(region.vertices), axis=0)

    def _draw_region_label(self, region: p2.CurvedRegion, label: str, color: str) -> None:
        if region.empty:
            return
        centroid = self._region_centroid(region)
        screen = self._world_to_screen(centroid)
        self.canvas.create_text(
            screen[0],
            screen[1],
            text=label,
            fill=color,
            font=("Microsoft YaHei", 11, "bold"),
            justify=tk.CENTER,
        )

    @staticmethod
    def _heat_color(ratio: float) -> str:
        red = int(30 + 220 * ratio)
        blue = int(220 - 180 * ratio)
        green = int(100 + 70 * (1.0 - abs(ratio - 0.5) * 2.0))
        return f"#{red:02x}{green:02x}{blue:02x}"

    def _draw_grid_heatmap(self, values) -> None:
        if not values:
            return
        dual = hasattr(values[0], "max_value")
        max_values = [item.max_value if dual else item.value for item in values]
        mean_values = [item.mean_value if dual else item.value for item in values]
        max_min, max_max = min(max_values), max(max_values)
        mean_min, mean_max = min(mean_values), max(mean_values)
        max_span = max(max_max - max_min, 1e-12)
        mean_span = max(mean_max - mean_min, 1e-12)
        radius = 3
        for item in values:
            if dual:
                fill = self._heat_color((item.max_value - max_min) / max_span)
                outline = self._heat_color((item.mean_value - mean_min) / mean_span)
            else:
                fill = self._heat_color((item.value - max_min) / max_span)
                outline = "white"
            screen = self._world_to_screen((item.x, item.y))
            self.canvas.create_oval(
                screen[0] - radius,
                screen[1] - radius,
                screen[0] + radius,
                screen[1] + radius,
                fill=fill,
                outline=outline,
                width=2 if dual else 1,
            )

    def export_json(self) -> None:
        if self.result is None:
            messagebox.showwarning("无结果", "请先完成计算再导出。")
            return
        path = filedialog.asksaveasfilename(title="导出第2题计算结果", defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        payload = {
            "summary": self.result.summary(),
            "possible_region": self.result.possible_region.to_dict(),
            "candidate_region": self.result.candidate_region.to_dict(),
            "coarse": [
                {
                    "x": item.x,
                    "y": item.y,
                    **(
                        {"max_value": item.max_value, "mean_value": item.mean_value}
                        if hasattr(item, "max_value")
                        else {"value": item.value, "metric": item.metric}
                    ),
                }
                for item in self.result.coarse
            ],
            "near_optimal": (
                {
                    "method_max": [{"x": item.x, "y": item.y, "max_value": item.max_value} for item in p2.near_optimal_points(self.result, float(self.epsilon_var.get()))["max"]],
                    "method_mean": [{"x": item.x, "y": item.y, "mean_value": item.mean_value} for item in p2.near_optimal_points(self.result, float(self.epsilon_var.get()))["mean"]],
                }
                if isinstance(self.result, p2.DualFEMSearchResult)
                else [
                    {"x": item.x, "y": item.y, "value": item.value}
                    for item in p2.near_optimal_points(self.result, float(self.epsilon_var.get()))
                ]
            ),
            "refined": [
                {
                    "x": item.x,
                    "y": item.y,
                    **(
                        {"max_value": item.max_value, "mean_value": item.mean_value}
                        if hasattr(item, "max_value")
                        else {"value": item.value, "metric": item.metric}
                    ),
                }
                for item in self.result.refined
            ],
        }
        try:
            Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self.status_var.set(f"已导出：{path}")
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc))

    def run(self) -> None:
        self.mainloop()


def main() -> None:
    app = Problem2App()
    app.run()


if __name__ == "__main__":
    main()
