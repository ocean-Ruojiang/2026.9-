"""问题3已有实验与三阶段流程图。仅读取结果，不运行策略实验。

用法见同目录 README.md。Python >= 3.10。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Rectangle, FancyArrowPatch
import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
VERSIONS = ["01_quick", "02_quick_q2"]
LABELS = ["未接入第二问", "接入第二问"]
COLORS = ["#356795", "#D0803B", "#558778"]
METRICS = ["虚拟任务时间_s", "移动距离_m", "规划墙钟_s"]
UNITS = ["虚拟任务时间 / s", "移动距离 / m", "规划墙钟时间 / s"]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def configure_font(font_file):
    if font_file:
        path = Path(font_file).expanduser().resolve()
        require(path.is_file(), f"字体文件不存在：{path}")
        font_manager.fontManager.addfont(str(path))
        name = font_manager.FontProperties(fname=str(path)).get_name()
    else:
        available = {f.name for f in font_manager.fontManager.ttflist}
        candidates = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC",
                      "Source Han Sans SC", "PingFang SC", "SimSun"]
        name = next((s for s in candidates if s in available), None)
        require(name is not None,
                "未找到中文字体。请用 --font 指定中文 .ttf/.otf/.ttc 文件。")
    plt.rcParams.update({
        "font.family": "sans-serif", "font.sans-serif": [name, "DejaVu Sans"],
        "axes.unicode_minus": False, "font.size": 10,
        "axes.titlesize": 11, "axes.labelsize": 10,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.linewidth": 0.8, "figure.facecolor": "white",
        "savefig.facecolor": "white", "pdf.fonttype": 42,
        "ps.fonttype": 42, "svg.fonttype": "none",
    })
    return name


def save(fig, out, stem):
    for suffix in ["pdf", "svg", "png"]:
        fig.savefig(out / f"{stem}.{suffix}", dpi=400,
                    bbox_inches="tight", pad_inches=0.12)
    plt.close(fig)


def note(fig, text, bottom=0.23):
    fig.subplots_adjust(bottom=bottom, top=0.80, wspace=0.34)
    fig.text(0.5, 0.045, text, ha="center", va="bottom", fontsize=9,
             color="#444444", linespacing=1.65)


def numeric(df, columns, sheet):
    for c in columns:
        require(c in df, f"{sheet}缺少列：{c}")
        df[c] = pd.to_numeric(df[c], errors="raise")
        values = df[c].to_numpy(dtype=float)
        require(np.isfinite(values).all(), f"{sheet}的{c}有缺失或非有限值，不能按0处理。")
        require((values >= 0).all(), f"{sheet}的{c}含负数。")


def complete(df, column, sheet):
    flags = df[column].astype(str).str.strip().str.lower()
    require(flags.isin(["true", "是", "1", "1.0"]).all(),
            f"{sheet}有未确认成功的记录；当前脚本只适用于已交接的全部完整清除批次。")
    require((df["清除数"] == df["目标数"]).all(), f"{sheet}存在未完整清除记录。")
    for col in ["清除数", "目标数"]:
        require((df[col] % 1 == 0).all(), f"{sheet}的{col}不是整数。")
    require((df["目标数"] > 0).all(), f"{sheet}存在零目标记录。")


def load_data(path):
    with pd.ExcelFile(path, engine="openpyxl") as book:
        names = ["本地Q2逐局", "官方Q2逐局", "MAIN_C3_C4调试", "Q2对照汇总"]
        require(set(names).issubset(book.sheet_names), "工作表名称与已交接工作簿不符。")
        local, official, smoke, summary = [pd.read_excel(book, sheet_name=s) for s in names]
    for df, sheet in [(local, names[0]), (official, names[1])]:
        required = {"版本", "运行ID", "案例ID", "配对ID", "环境种子", "场景指纹", "核验成功"}
        require(required.issubset(df.columns), f"{sheet}缺少身份或成功核验字段。")
        require(set(df["版本"]) == set(VERSIONS), f"{sheet}版本不符，不能混入其他实验。")
        require(df.groupby("版本").size().eq(30).all(), f"{sheet}应为两版各30局。")
        require(not df.duplicated(["版本", "运行ID"]).any(), f"{sheet}存在重复运行ID。")
        require(df["场景指纹"].notna().all(), f"{sheet}缺少场景指纹。")
        numeric(df, METRICS + ["目标数", "清除数"], sheet)
        complete(df, "核验成功", sheet)

    require(local["配对ID"].notna().all(), "本地记录存在空配对ID。")
    require(not local.duplicated(["版本", "配对ID"]).any(), "本地配对ID重复。")
    numeric(local, ["环境种子"], names[0])
    a = local[local["版本"] == VERSIONS[0]].set_index("配对ID")
    b = local[local["版本"] == VERSIONS[1]].set_index("配对ID")
    require(set(a.index) == set(b.index), "本地两版的配对ID不完全相同。")
    a = a.sort_values("环境种子")
    b = b.loc[a.index]
    for c in ["环境种子", "场景指纹", "目标数"]:
        require((a[c] == b[c]).all(), f"本地配对的{c}不一致。")
    require(a["环境种子"].tolist() == list(range(20261101, 20261131)), "本地地图种子与本批次不符。")
    require(a["目标数"].sum() == 395, "本地目标总数应为395。")

    require(official["配对ID"].fillna("").astype(str).str.strip().eq("").all(),
            "官方记录出现配对ID；请先确认实验设计，不能自动按独立案例绘制。")
    for c in ["案例ID", "场景指纹"]:
        require(official[c].notna().all() and official[c].nunique() == 60,
                f"官方60局的{c}不满足独立案例身份要求。")
    require(official.groupby("版本")["目标数"].sum().reindex(VERSIONS).tolist() == [395, 391],
            "官方目标总数与已交接批次不符。")

    require({"场景", "版本", "成功"}.issubset(smoke.columns), "调试表缺少必要字段。")
    require(len(smoke) == 6 and set(smoke["版本"]) == {"MAIN", "C3", "C4"}, "调试应仅含MAIN/C3/C4共6局。")
    require(set(smoke["场景"]) == {"SMOKE_01", "SMOKE_02"}, "调试场景与本批次不符。")
    require(not smoke.duplicated(["场景", "版本"]).any(), "调试场景与版本组合重复。")
    numeric(smoke, METRICS + ["目标数", "清除数"], names[2])
    complete(smoke, "成功", names[2])
    for scene, count in [("SMOKE_01", 10), ("SMOKE_02", 16)]:
        group = smoke[smoke["场景"] == scene]
        require(len(group) == 3 and group["目标数"].eq(count).all(), f"{scene}的版本或目标数不符。")

    # 绘图值来自逐局表；此处仅用原汇总表交叉检查，避免错用文件。
    summary_cols = ["平均虚拟任务时间_s", "平均移动距离_m", "平均规划墙钟_s"]
    for env, df in [("本地", local), ("官方演练", official)]:
        for v, label in zip(VERSIONS, ["policy2未接入第二问", "policy2接入第二问"]):
            row = summary[(summary["环境"] == env) & (summary["版本"] == label)]
            require(len(row) == 1, f"汇总表中{env}/{label}行缺失或重复。")
            group = df[df["版本"] == v]
            for col, summary_col in zip(METRICS, summary_cols):
                require(np.isclose(group[col].mean(), float(row.iloc[0][summary_col]),
                                   rtol=0, atol=1e-6), f"{env}/{label}/{col}逐局均值与汇总不一致。")
    return local, official, smoke, a, b


def paired_scatter(a, b, out):
    fig, axes = plt.subplots(1, 3, figsize=(13.2, 4.9))
    fig.suptitle("第二问接入对比：本地30组同地图配对", fontsize=14)
    for ax, col, unit in zip(axes, METRICS, UNITS):
        x, y = a[col].to_numpy(), b[col].to_numpy()
        lo, hi = min(x.min(), y.min()), max(x.max(), y.max())
        pad = max((hi - lo) * .08, .01)
        lo, hi = max(0, lo - pad), hi + pad
        ax.plot([lo, hi], [lo, hi], "--", color="#888888", lw=1, label="两版相同")
        ax.scatter(x, y, s=29, color=COLORS[0], alpha=.8, edgecolors="white", linewidths=.4)
        ax.set(xlim=(lo, hi), ylim=(lo, hi), xlabel=f"未接入：{unit}", ylabel=f"接入：{unit}")
        ax.set_aspect("equal", adjustable="box")
        ax.ticklabel_format(style="plain", useOffset=False)
        ax.grid(alpha=.16)
    note(fig, "每点对应同一张地图；虚线下方表示接入后的该项数值较小。\n两版均完整清除30/30局、395/395个目标；未接入组仍为policy2。")
    save(fig, out, "01_本地Q2接入_三指标配对散点")


def paired_difference(a, b, out):
    diff = (b[METRICS[0]] - a[METRICS[0]]).to_numpy()
    pct = 100 * diff / a[METRICS[0]].to_numpy()
    fig, ax = plt.subplots(figsize=(11.5, 4.8))
    fig.suptitle("第二问接入对比：逐地图虚拟任务时间变化", fontsize=14)
    x = np.arange(len(diff))
    ax.bar(x, diff, color=np.where(diff < 0, COLORS[0], COLORS[1]), width=.72)
    ax.axhline(0, color="#333333", lw=.8)
    ax.axhline(diff.mean(), color="#666666", ls="--", lw=1,
               label=f"平均差值 {diff.mean():+.3f} s")
    ax.set_xticks(x)
    ax.set_xticklabels([str(int(s)) for s in a["环境种子"]], rotation=60, ha="right", fontsize=8)
    ax.set(xlabel="地图种子", ylabel="接入 − 未接入 / s")
    ax.grid(axis="y", alpha=.16)
    ax.set_axisbelow(True)
    ax.legend(frameon=False)
    note(fig, f"负值表示接入后更快；更快 {sum(diff < 0)} 组，更慢 {sum(diff > 0)} 组，相同 {sum(diff == 0)} 组。\n"
         "差值为同地图配对结果；未进行统计显著性检验。", bottom=.35)
    save(fig, out, "02_本地Q2接入_逐地图任务耗时差")
    pd.DataFrame({"配对ID": a.index, "环境种子": a["环境种子"].astype(int).to_numpy(),
                  "未接入任务时间_s": a[METRICS[0]].to_numpy(),
                  "接入任务时间_s": b[METRICS[0]].to_numpy(),
                  "接入减未接入_s": diff, "相对变化_pct": pct}).to_csv(
                      out / "本地配对绘图数值.csv", index=False, encoding="utf-8-sig")


def official_distribution(df, out):
    fig, axes = plt.subplots(1, 3, figsize=(12.8, 5.0))
    fig.suptitle("第二问接入对比：官方独立案例演练", fontsize=14)
    for ax, col, unit in zip(axes, METRICS, UNITS):
        groups = [df[df["版本"] == v].sort_values("案例ID")[col].to_numpy() for v in VERSIONS]
        boxes = ax.boxplot(groups, positions=[0, 1], widths=.43, patch_artist=True,
                           showfliers=False, medianprops={"color": "#222222", "linewidth": 1.4})
        for i, (values, box) in enumerate(zip(groups, boxes["boxes"])):
            box.set_facecolor(COLORS[i]); box.set_alpha(.2)
            # 固定横向偏移仅用于减少遮挡；不修改任何数据，也不表示配对。
            offset = .12 * np.sin(np.arange(len(values)) * 2.39996323)
            ax.scatter(i + offset, values, s=18, color=COLORS[i], alpha=.75, zorder=3)
            ax.scatter(i, values.mean(), marker="D", s=35, c="black", zorder=4)
            ax.text(i, 1.02, f"均值 {values.mean():.3f}", ha="center",
                    transform=ax.get_xaxis_transform(), fontsize=9)
        ax.set_xticks([0, 1]); ax.set_xticklabels(LABELS)
        ax.set_ylabel(unit); ax.grid(axis="y", alpha=.16)
        ax.ticklabel_format(axis="y", style="plain", useOffset=False)
    note(fig, "每组30个独立案例；圆点为逐局值，菱形为均值，箱内横线为中位数。\n"
         "两组均完整清除30/30局，目标数分别为395和391；组间差异不作因果解释，演练不代表三次正式测试。")
    save(fig, out, "03_官方独立演练_三指标分布")


def smoke_figures(df, out):
    order = ["MAIN", "C3", "C4"]
    scenes = ["SMOKE_01", "SMOKE_02"]
    time = df.pivot(index="场景", columns="版本", values=METRICS[0]).loc[scenes, order]
    wall = df.pivot(index="场景", columns="版本", values=METRICS[2]).loc[scenes, order]
    gain = time[["C3", "C4"]].rsub(time["MAIN"], axis=0).div(time["MAIN"], axis=0) * 100
    x = np.arange(2)
    fig, axes = plt.subplots(1, 2, figsize=(11, 5.2))
    fig.suptitle("MAIN、C3、C4：两张地图的调试任务表现", fontsize=14)
    for i, version in enumerate(order):
        bars = axes[0].bar(x + (i - 1) * .24, time[version], .23, label=version, color=COLORS[i])
        axes[0].bar_label(bars, fmt="%.1f", fontsize=8, padding=3)
    axes[0].set_ylim(0, time.to_numpy().max() * 1.17)
    axes[0].set_ylabel("虚拟任务时间 / s"); axes[0].legend(frameon=False, ncol=3)
    for i, version in enumerate(["C3", "C4"]):
        bars = axes[1].bar(x + (i - .5) * .29, gain[version], .28, label=version, color=COLORS[i + 1])
        axes[1].bar_label(bars, labels=[f"{v:+.2f}%" for v in gain[version]], fontsize=9, padding=4)
    axes[1].axhline(0, color="#444444", lw=.8)
    axes[1].set_ylabel("相对MAIN的任务时间减少率 / %")
    axes[1].set_ylim(min(-1, gain.to_numpy().min() - .8), gain.to_numpy().max() + 2)
    axes[1].legend(frameon=False)
    for ax in axes:
        ax.set_xticks(x); ax.set_xticklabels(["SMOKE_01\n10目标", "SMOKE_02\n16目标"])
        ax.grid(axis="y", alpha=.16); ax.set_axisbelow(True)
    note(fig, "减少率 = (MAIN耗时 − 对照耗时) / MAIN耗时 × 100%；正值表示对照更快。\n"
         "六局均完整清除；每种目标数仅一张调试地图，不代表30张主实验地图或总体优势。", bottom=.27)
    save(fig, out, "04_调试实验_任务时间与相对变化")

    fig, ax = plt.subplots(figsize=(8.3, 4.9))
    fig.suptitle("MAIN、C3、C4：调试规划墙钟时间", fontsize=14)
    for i, version in enumerate(order):
        bars = ax.bar(x + (i - 1) * .24, wall[version], .23, label=version, color=COLORS[i])
        ax.bar_label(bars, fmt="%.3f", padding=4, fontsize=9)
    ax.set_xticks(x); ax.set_xticklabels(["SMOKE_01（10目标）", "SMOKE_02（16目标）"])
    ax.set(ylabel="规划墙钟时间 / s", ylim=(0, wall.to_numpy().max() * 1.2))
    ax.legend(frameon=False, ncol=3)
    ax.grid(axis="y", alpha=.16); ax.set_axisbelow(True)
    note(fig, "调试期间存在其他并行任务，墙钟耗时受当时系统负载影响。\n"
         "此处为实际经过的规划时间，不是CPU时间，也不是虚拟任务时间。")
    save(fig, out, "05_调试实验_规划墙钟时间")
    df.to_csv(out / "六局调试绘图数值.csv", index=False, encoding="utf-8-sig")


def flowchart(out):
    fig, ax = plt.subplots(figsize=(15, 5.5))
    ax.set(xlim=(0, 15), ylim=(0, 5.5)); ax.axis("off")
    lefts = [.2, 5.25, 10.3]
    width, bottom, height = 4.5, 2.2, 2.25
    titles = ["初始扫描", "覆盖与定位", "清除收尾"]
    lines = [
        ["原点扫描各频道", "建立未知频道排查任务", "建立已知目标定位区域"],
        ["锁定阶段目的站", "累计预算内择优行动", "真实反馈后更新并决策", "到站优先完成必要扫描"],
        ["锁定一个未清除目标", "定位并尝试清除", "达上限或无有效候选时", "转入有限网格清除"],
    ]
    outputs = ["形成频道状态与排查任务", "实际耗用逐次扣除\n阶段内重决策不补充预算",
               "持续保存网格搜索进度\n成功清除后处理下一目标"]
    for left, title, body, output in zip(lefts, titles, lines, outputs):
        ax.text(left + width / 2, 4.8, title, ha="center", va="center",
                fontsize=19, fontweight="bold", color=COLORS[0])
        ax.add_patch(Rectangle((left, bottom), width, height, fill=False,
                               edgecolor="#424951", linewidth=1.4))
        ys = np.linspace(bottom + height - .44, bottom + .40, len(body))
        for y, text in zip(ys, body):
            ax.text(left + width / 2, y, text, ha="center", va="center", fontsize=14)
        ax.text(left + width / 2, 1.55, output, ha="center", va="center",
                fontsize=12, color="#444444", linespacing=1.6)
    for start, end in [(lefts[0] + width, lefts[1]), (lefts[1] + width, lefts[2])]:
        ax.add_patch(FancyArrowPatch((start + .03, 3.3), (end - .03, 3.3),
                                    arrowstyle="-|>", mutation_scale=16, lw=1.4, color="#424951"))
    ax.text(9.72, 4.00, "排查\n完成", ha="center", va="center", fontsize=10)
    ax.text(7.5, .66, "排查完成：全部频道已发现目标，或由实际覆盖证据确认无源。",
            ha="center", fontsize=12)
    ax.text(7.5, .16, "每次反馈后优先检查近距离清除需求与完成条件；以实际清除反馈确认成功。",
            ha="center", fontsize=12)
    save(fig, out, "06_问题三_三阶段总体流程_代码版")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xlsx", type=Path, help="已完成实验汇总与逐局表.xlsx的路径")
    parser.add_argument("--only", choices=["all", "experiments", "flow"], default="all")
    parser.add_argument("--font", help="可选：中文字体文件路径")
    parser.add_argument("--out", type=Path, default=HERE / "输出", help="输出父目录，每次运行新建子目录")
    args = parser.parse_args()
    font = configure_font(args.font)
    path, data = None, None
    if args.only != "flow":
        path = args.xlsx
        if path is None:
            nearby = HERE / "已完成实验汇总与逐局表.xlsx"
            path = nearby
        path = path.expanduser().resolve()
        require(path.is_file(), f"找不到工作簿：{path}\n请使用 --xlsx 指定路径，或把工作簿放在脚本旁。")
        data = load_data(path)
    out = args.out.expanduser().resolve() / datetime.now().strftime("绘图_%Y%m%d_%H%M%S_%f")
    out.mkdir(parents=True, exist_ok=False)
    if data is not None:
        local, official, smoke, a, b = data
        paired_scatter(a, b, out)
        paired_difference(a, b, out)
        official_distribution(official, out)
        smoke_figures(smoke, out)
        rows = []
        for env, df in [("本地配对", local), ("官方独立演练", official)]:
            for v, label in zip(VERSIONS, LABELS):
                group = df[df["版本"] == v]
                rows.append({"实验": env, "版本": label, "局数": len(group),
                             "完整清除局数": len(group), "清除目标数": int(group["清除数"].sum()),
                             **{f"平均{c}": float(group[c].mean()) for c in METRICS}})
        pd.DataFrame(rows).to_csv(out / "Q2接入绘图均值.csv", index=False, encoding="utf-8-sig")
    if args.only != "experiments":
        flowchart(out)
    manifest = {"脚本": Path(__file__).name, "绘图范围": args.only, "中文字体": font,
                "工作簿": str(path) if path else None,
                "工作簿SHA256": hashlib.sha256(path.read_bytes()).hexdigest() if path else None,
                "说明": "仅读取已有结果并绘图；不运行策略、不修改工作簿、不读取官方原生日志、不执行显著性检验。",
                "文件": sorted(p.name for p in out.iterdir())}
    (out / "绘图来源.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"完成，文件保存在：{out}")


if __name__ == "__main__":
    main()
