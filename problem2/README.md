# 第二问：第二检测点优化与可视化

根据第一次检测的位置、示向度与误差，构建目标可能区域和第二检测点的保证可检测区域，再在候选区域进行粗网格搜索和局部细化，比较第二次测向后的定位区域直径。

本目录可单独复制到另一台机器使用，不依赖仓库的模拟器、第三问策略、原工作空间、桌面数据或 Conda 固定路径。

## 功能

- 计算目标可能区域、保证可检测区域及区域直径。
- 方法一：最小化采样有效示向角下的**最大直径**。
- 方法二：最小化采样有效示向角下的**平均直径**。
- 同时比较两种方法，展示较优候选点、粗/细网格和区域边界。
- Tkinter 桌面界面：参数编辑、实时用时、适应视图、清除结果、JSON 导出。
- 命令行单案例计算，以及按案例或网格分配进程的并行计算。

**精度说明：**圆弧、多边形约束、测向角和候选点均使用数值近似。这里的“最大直径”是离散采样角上的最大值；“平均”是有效采样角的等权平均，不是对未知目标位置的概率期望，也不是连续空间全局最优的证明。缩小网格或角度步长会增加计算量。详见 [算法与移植说明](docs/PORTABILITY.md)。

## 安装

需要 Python 3.10+、NumPy、SciPy，以及运行 GUI 所需的 Tkinter 和桌面会话。以下命令从本目录执行：

```bash
python -m venv .venv
```

Windows CMD：

```bat
.venv\Scripts\activate.bat
python -m pip install -r requirements.txt
python run_gui.py
```

Windows PowerShell 可以不激活环境：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_gui.py
```

Linux/macOS：

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
python run_gui.py
```

Tkinter 属于 Python 的可选组件，不能靠本项目的 pip 依赖补齐。先用 `python -m tkinter` 验证能否打开窗口；缺失时为所用 Python 安装匹配的 Tk 支持（例如 Debian/Ubuntu 的 `python3-tk`）。无桌面的服务器可直接使用命令行计算。

Windows 双击 `start_gui.bat` 时依次使用本目录的虚拟环境、仓库根目录的虚拟环境、PATH 中的 Python；依赖应安装在实际使用的解释器中。

## 界面操作

1. 填写第一检测点坐标、第一示向度、误差及各半径。
2. 设置粗网格、细化网格、角度步长，选择最大直径、平均直径或双方法对比。
3. 点击“开始计算”；状态栏显示已用秒数。计算运行于后台线程，完整搜索结束后更新图表。
4. 用“较优阈值”显示最优值附近的多个候选点；点击“导出JSON”保存区域、网格与结果。

距离单位为米，示向角单位为度；坐标/方向约定见源码的 `bearing_wedge_circles`。保证可检测区域为空时，界面保留目标可能区域并提示空集，不生成虚构最优点。

GUI 保留原工作空间的参数默认值。下述快速样例使用较粗精度，仅用于安装和功能验证，不应用作最终论文结果。

## 单案例与结果导出

```bash
python run_solver.py --config examples/quick.json --output results/quick.json
python run_solver.py --config examples/quick.json --mode max --output results/max.json
python run_solver.py --config examples/quick.json --mode mean --output results/mean.json
```

在 JSON 配置中设置 `first_point`、`first_bearing_deg`、`mode` 和 `params`。完整参数名和默认值见 `src/problem2_optimizer.py` 中的 `finite_element_search`、`finite_element_search_dual`。不给配置时使用计算核心默认值，可能需要较长时间。

输出包含输入、实际耗时 `elapsed_seconds`、结果摘要、两个区域及粗/细网格。状态 `empty_candidate_region` 表示几何上没有候选区域；参数或文件错误返回非零退出码。GUI 导出使用原有格式；命令行格式额外记录输入和耗时。

## 并行批处理

```bash
python run_batch.py --cases 4 --seed 20260911 --mode both --case-workers 2 --grid-workers 1 --circle-samples 72 --maximum-constraints 96 --coverage-spacing 200 --safety-margin 100 --coarse-width 400 --refine-width 200 --angle-step 30 --json results/batch.json --csv results/batch.csv
```

批处理会按种子生成独立案例，并保留失败记录。结果按输入顺序保存。`case-workers > 1` 与 `grid-workers > 1` 不可同时使用，避免嵌套进程过量占用 CPU。GUI 仍使用原来的单案例计算路径；多进程入口用于批量或网格计算。

如需安装命令行入口，执行 `python -m pip install .`，随后可使用 `problem2-gui`、`problem2-solve`、`problem2-batch`。安装后可从其他目录启动，无需手动配置 PYTHONPATH。

## 目录

```text
problem2/
├── src/                    # 完整计算核心、GUI、并行模块和单例CLI
├── examples/quick.json     # 自包含的快速验证输入
├── tests/                  # 数值、并行和GUI移植验证
├── docs/PORTABILITY.md     # 功能边界与移植改动
├── docs/VALIDATION.md      # 实际验证记录
├── requirements.txt
├── pyproject.toml          # 可安装Python项目
├── run_gui.py
├── run_solver.py
├── run_batch.py
└── start_gui.bat
```

## 验证

```bash
python -m unittest discover -s tests -p "test_*.py" -v
python tests/gui_smoke.py
```

第二条需要桌面环境，会创建测试界面、完成一次快速计算、验证JSON导出后关闭。测试结果与已验证平台见 [验证记录](docs/VALIDATION.md)。

本目录尚未连接 `policy2/strategy2/q2.py` 的策略适配器；发布独立第二问求解器不会自动更换策略2当前的 heuristic 实现。
