# 自制干扰源模拟器与可视化界面

本目录提供工作空间中的完整桌面模拟器：在地图上布置干扰源，启动本地 HTTP 服务，观察机器狗的移动、测向、清除轨迹与时间变化。

**可以单独复制本目录使用。**运行只需要 Python 3.10+；GUI 还需要 Tkinter 和桌面环境。没有 NumPy、SciPy、浏览器或官方模拟器依赖。

## 与仓库其他模块的关系

| 模块 | 用途 |
|---|---|
| 本目录 `simulator_gui/` | 手工布源、地图交互、动作可视化；也能无界面启动同一核心 |
| `local_simulator/` | 仓库已有的另一套批量实验模拟器和独立示例策略 |
| `policy2/` | 策略程序，通过 HTTP 接口调用模拟器 |
| `problem2/` | 独立第二测点求解和可视化 |

两套自制模拟器是不同实现，不能假定同一随机种子会产生相同场景或误差。本目录没有替换既有批量模拟器或策略。

## 启动 GUI

从仓库根目录执行：

```bash
python simulator_gui/run_gui.py
```

单独复制本目录后执行：

```bash
python run_gui.py
```

Windows 可双击 `start_gui.bat`。它依次查找本目录虚拟环境、仓库根目录虚拟环境和 PATH 中的 Python，不包含任何本机固定路径。

如果出现缺少 Tkinter 或无法创建窗口，先执行 `python -m tkinter` 检查当前解释器的 Tk 支持。Tkinter 是 Python 的可选组件，不是 pip 第三方依赖；无桌面的服务器可使用下述命令行入口。

## 界面功能和操作

1. 添加、选择、拖动或删除干扰源，编辑频道、坐标、全向/定向类型、接收半径及朝向。
2. 使用随机全向或混合场景按钮生成测试布局；“随机种子”同时用于随机布局及会话的测向误差。
3. 在设置中修改端口、机器人编号、速度、近距离判定、清除半径、定向覆盖角、测向误差及现实时间上限。
4. 点击启动接口，然后运行指向该地址的策略。
5. 地图显示源点、覆盖、机器人轨迹和动作；界面更新请求日志、虚拟用时及现实剩余时间。
6. 停止/复位可保留源配置并清除运行状态；导入/导出 JSON 用于保存和重放布局。

GUI 默认端口为 **2026**。如官方模拟器正在使用该端口，请在本界面改为 **2027**，并同步修改策略地址。启动失败会显示错误，不会自动结束其他程序。

GUI 需要读取真实源位置来绘图，因此启用调试真值访问；HTTP 响应不提供真实源列表。用于策略评估时，不应让策略直接读取 GUI 内存或源配置文件。

## 无界面服务与接口演示

以下命令从本目录执行：

```bash
python run_server.py --sources examples/sources.json --port 2027 --robot-id local --seed 20260912 --log results/events.jsonl
```

另开终端：

```bash
python examples/demo_client.py --base-url http://127.0.0.1:2027 --robot-id local
```

演示客户端按顺序调用进入、测量、清除和退出，使用样例已知的源坐标，仅验证接口，不是定位策略。执行后如需再跑，重启服务或在 GUI 复位、重新启动会话。

命令行默认绑定 `127.0.0.1:2027`；`--port 0` 自动选择空闲端口，实际地址在 READY 行显示。Ctrl+C 停止命令行服务。可用 `--real-limit` 修改现实秒数上限，`--problem-mode` 选择 custom、problem3 或 problem4；默认 custom 允许小型自定义样例。

`problem3` 模式要求 10—16 个全向源、频道不重复、位置及接收半径满足核心校验；本目录的两源混合样例应使用 custom。problem4 标志不代表已完整实现官方第四问全部场景约束。

## 接口

均为 UTF-8 JSON 的 POST 请求：

| 路径 | 公共字段之外的字段 | 主要结果 |
|---|---|---|
| `/enter` | 无 | 是否进入、虚拟和现实时间限制 |
| `/measure` | `position: {x, y}`、`channel` | direction / near / no_signal；direction 时含 svd_deg |
| `/clear` | `position: {x, y}`、`channel` | success / no_target_in_range |
| `/exit` | 无 | 是否结束会话 |

公共字段为 `arena_id: "default"`、`robot_id`、`request_id`。正常新动作使用新的 request_id；相同请求 ID 和相同内容会返回缓存响应，不重复计时，同 ID 不同内容返回冲突。

客户端除检查 HTTP 状态，还应检查 `accepted`。HTTP 200 不必然代表操作成功；清除是否成功应读取 clear_result。

```json
{
  "arena_id": "default",
  "robot_id": "local",
  "request_id": "measure-001",
  "position": {"x": 0, "y": 0},
  "channel": 1
}
```

源文件格式见 `examples/sources.json`：JSON 顶层为数组，每项包含 channel、x、y、kind、receive_radius_m、direction_deg 和 cleared。源配置文件不含算法、账号或令牌。

## 时间与误差

- 默认移动速度 5 m/s。
- 测量虚拟耗时：移动距离/速度 + 换频道时的 1 s + 测量 5 s。
- 清除虚拟耗时：移动距离/速度 + 成功 5 s 或失败 3 s。
- 默认现实上限 1200 s，从机器狗进入开始计时；超时拒绝后续动作，不将拒绝动作计入虚拟时间。
- 默认同频道、同位置固定测向误差；位置以 6 位小数作为缓存键。相同种子和相同操作序列可复现误差，独立误差模式保留在核心 API 中供诊断。

完整参数定义和校验以 `src/interference_simulator_core.py` 为准。

## 可安装入口

无需安装即可运行源码。若希望从任意目录使用命令：

```bash
python -m pip install .
interference-sim-gui
interference-sim-server --sources /path/to/sources.json --port 2027
```

源码运行无第三方依赖；安装构建使用 setuptools。发布的是可移植源码，不是捆绑 Python 的免安装 exe。

## 测试

```bash
python -m unittest discover -s tests -p "test_*.py" -v
python tests/gui_smoke.py
```

GUI 测试使用临时空闲端口，不占用 2026；执行后自动关闭。实际验证平台和结果见 [验证记录](docs/VALIDATION.md)。

## 目录

```text
simulator_gui/
├── src/interference_simulator_core.py  # 状态、规则、HTTP 服务
├── src/interference_simulator_gui.py   # 完整 Tk 桌面界面
├── src/simulator_cli.py                # 无界面入口
├── examples/                          # 自包含布局和接口客户端
├── tests/                             # 核心、HTTP、GUI 验证
├── docs/VALIDATION.md
├── run_gui.py
├── run_server.py
├── start_gui.bat
├── requirements.txt                   # 声明仅使用标准库
└── pyproject.toml
```

## 当前边界

这是自制实验工具，不是官方模拟器的完整替代品。官方登录、服务器校时、加密行为日志上传、正式测试次数及时间窗口均未实现。默认源布局分布属于本地假设；未完整复刻所有协议边界响应和官方源生成规则。研究结论应记录模拟器版本、配置和误差设置。

本目录不含第三问策略、强化学习环境或历史实验数据；HTTP 调用即可连接独立策略，无需引入这些模块。
