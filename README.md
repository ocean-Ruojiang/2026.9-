# B 题：第二问优化、可视化与本地策略实验

用于无线电干扰源自动搜索、定位和清除策略的本地实验。项目按问题与用途分为以下独立部分：

| 目录 | 内容 | 使用说明 |
|---|---|---|
| `problem2/` | 第二检测点优化、Tkinter 可视化、单例与并行计算；可独立移植 | [第二问 README](problem2/README.md) |
| `simulator_gui/` | 工作空间自制模拟器及完整Tk可视化；手工布源、HTTP接口、行为轨迹，可独立移植 | [桌面模拟器 README](simulator_gui/README.md) |
| `local_simulator/` | 自制 HTTP 模拟器、批量运行器、独立示例策略与测试；仅依赖 Python 标准库 | [模拟器 README](local_simulator/README.md) |
| `policy2/` | 策略2：覆盖主任务、收益评分、可移动站点、绕路预算及锁定清除；需要 NumPy | [Policy 2 README](policy2/README.md) |

策略2支持第三问的全向干扰源。模拟器依据题目协议自行实现，尚不能替代官方演练或正式测试。

## 第二问计算与可视化

安装 Python 3.10+（GUI 需 Tkinter），在仓库根目录执行：

```bash
python -m pip install -r problem2/requirements.txt
python problem2/run_gui.py
```

无图形界面也能计算并导出结果：

```bash
python problem2/run_solver.py --config problem2/examples/quick.json --output problem2/results/quick.json
```

`problem2/` 可单独复制使用，包含完整依赖链、可安装项目、Windows 启动脚本和验证样例。参数、精度口径与移植方法见 [第二问说明](problem2/README.md)。

## 自制模拟器与可视化

```bash
python simulator_gui/run_gui.py
```

可直接下载 [模拟器可移植 ZIP](packages/simulator_gui_portable_20260912.zip)，解压后运行其中的 `simulator_gui/run_gui.py`。

仅需 Python 3.10+ 与 Tkinter；也可在 `simulator_gui/` 双击 `start_gui.bat`。地图布源、参数、接口调用、无界面服务和样例见 [桌面模拟器 README](simulator_gui/README.md)。该目录与 `local_simulator/` 是不同实现；前者用于可视化交互，后者提供已有的批量实验流程。

## 策略2快速开始

安装 Python 3.10 或更新版本，然后克隆仓库：

```bash
git clone https://github.com/ocean-Ruojiang/2026.9-.git
cd 2026.9-
python -m venv .venv
```

Windows PowerShell 激活环境：

```powershell
.\.venv\Scripts\Activate.ps1
```

Linux/macOS 激活环境：

```bash
source .venv/bin/activate
```

在仓库根目录安装策略依赖，然后运行三局。无需手工启动服务或设置端口：

```bash
python -m pip install -r policy2/requirements.txt
cd policy2
python -m strategy2 local-sim --cases 3
```

输出自动保存到 `policy2/results/local_simulator/时间戳/`。先看 `summary.json` 的 `all_runs_ok` 和 `comparison.csv` 的成功率，再比较耗时。

目录名为 `policy2`，Python 包名继续使用 `strategy2`，因此命令是 `python -m strategy2`。

## 一组场景比较多个配置

在 `policy2/` 中执行：

```bash
python -m strategy2 local-sim --configs configs/quick.json configs/quick_no_detour.json configs/quick_fixed_station.json --cases 10 --seed 100 --layout mixed
```

各配置使用相同场景种子及环境参数，运行后核对目标与误差设置是否一致；失败局保留。每个配置和每局结果分别存放，不覆盖历史实验。

Windows 也可从仓库根目录调用脚本，显式使用刚建立的环境：

```powershell
& .\policy2\run_local_simulator.ps1 -Python "$PWD\.venv\Scripts\python.exe" -Configs quick,quick_no_detour,quick_fixed_station -Cases 10 -Seed 100 -Layout mixed
```

## 只运行模拟器

在仓库根目录执行以下命令，会使用模拟器自带示例策略，无须 NumPy：

```bash
python local_simulator/run.py batch --cases 3
```

该示例策略与 policy2 是不同实现。连接其他策略、独立启动服务或重放场景的方法见模拟器 README。

## 测试

使用安装了 NumPy 的同一 Python，从仓库根目录执行：

```bash
cd policy2
python -m unittest discover -s tests -v
cd ../local_simulator
python -m unittest discover -s tests -v
cd ..
```

两套测试独立发现；策略的 HTTP 集成测试会自动启动相邻模拟器，无需预先启动服务。

## 当前边界

- `problem2/` 已提供独立的数值第二测点求解器与GUI；尚未接入策略2的 [q2.py](policy2/strategy2/q2.py)，策略2当前仍使用明确标记为 `heuristic` 的候选生成器。第二问计算采用离散近似，不宣称连续全局最优。
- 策略1及 C1—C4 全部实验版本尚未在本仓库实现；固定扫描基准不是策略1。
- 只提交源码、配置、测试和说明。运行结果、缓存、个人路径记录和原始论文未纳入仓库。
- 已保存的本地接入验证为 15/15 局成功、213/213 次目标清除；这包含同图在不同配置下的重复运行，不能证明所有场景限时全清。摘要见 [验证说明](policy2/docs/验证说明.md)。
