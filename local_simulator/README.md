# B 题本地批量模拟器

用于自有策略的快速演练。Python 3.10 及以上，**仅使用标准库，无须安装依赖**。
默认模拟问题三：每局随机生成 10—16 个全向干扰源，各占一个不同频道。

## 先跑通一个批次

在本目录打开终端，运行：

```powershell
python run.py batch --cases 30 --workers 1
```

默认自动启动模拟器和附带的独立示例策略。每一局策略都在新进程中执行，自动完成
`/enter → /measure、/clear → /exit`，不需要手动点击或重启。
示例策略是七点全扫描后逐个缩小定位多边形并清除，用来验证接口，不是最优策略。

Windows 也可使用 `启动批量测试.ps1`。可传 `-PythonExe` 指定 Python；默认先找已激活虚拟环境，再查找本机 Codex Python 和系统 Python。模拟器自身无须安装 NumPy。

## 接入已有 Python 策略

兼容题目附件中的四个 POST 接口及 JSON 字段。已有程序无需使用本项目的几何或策略模块。

### 推荐：从环境变量读取服务地址和机器狗编号

在原策略的入口中，让两项配置读取如下值；其余 HTTP 请求逻辑不变：

```python
import os
BASE_URL = os.environ.get("SIM_BASE_URL", "http://127.0.0.1:2026")
ROBOT_ID = os.environ.get("SIM_ROBOT_ID", "你的参赛队号")
```

运行（把路径替换成你的实际策略入口）：

```powershell
python run.py batch --cases 30 --robot-id local --cwd "D:\我的策略" --command "{python}" "D:\我的策略\main.py"
```

`--command` 必须放在最后，其后所有内容都属于策略的启动命令。
若策略使用其他虚拟环境，直接把 `{python}` 换成该环境的 `python.exe` 绝对路径。
支持命令参数占位符：`{python}`、`{base_url}`、`{robot_id}`、`{run_dir}`。
启动使用参数数组，不经过 shell；带空格的路径应加引号。

批量入口还会设置 `SIM_RUN_DIR`。策略若自行写日志，建议写入该目录，以免多局互相覆盖。
默认 `--workers 1` 顺序运行；策略能使用不同地址、且自身输出相互隔离时，才增大 workers。
并行会产生 CPU 竞争，比较程序运行时间时请使用单 worker。

### 如果策略把地址和编号写死了

例如策略固定使用 `http://127.0.0.1:2026`、编号 `team123`：

```powershell
python run.py batch --cases 30 --port 2026 --robot-id team123 --cwd "D:\我的策略" --command "{python}" "D:\我的策略\main.py"
```

固定端口仅支持单 worker。请先关闭占用该端口的官方模拟器或其他程序；本工具不会主动关闭它们。
策略本身应能直接启动，不能等待用户按回车、弹窗确认或等待正式模拟器界面。
测试窗口从本地服务就绪开始，无登录、无倒计时、无正式测试次数消耗。

### 只启动服务，自己运行策略

```powershell
python run.py serve --port 2027 --robot-id local --seed 123
```

服务输出 Ready 后即可启动策略。每个服务只处理一局，收到 `/exit` 或超时后退出并导出结果。
多局自动测试请用 batch，而不是在一局中再次 `/enter`。

## 常用实验

```powershell
# 30 个随机场景；后续用同样 seed 和生成参数对比其他策略
python run.py batch --cases 30 --seed 100 --out results\strategy_A

# 更困难：16 个目标、接收半径均为最低 1000 米、目标集中在外缘
python run.py batch --cases 30 --count 16 --layout edge --radius-mode minimum

# 聚集分布、混合分布
python run.py batch --cases 30 --layout clustered
python run.py batch --cases 30 --layout mixed

# 4 局并行，服务使用各自自动分配的端口
python run.py batch --cases 30 --workers 4

# 复现某一失败场景；还应使用原 settings.json 中的 noise 等设置
python run.py batch --cases 1 --scenario results\某批次\case_0001\scenario.json

# 限制每局实际运行 60 秒，不改变动作的虚拟耗时
python run.py batch --cases 10 --real-limit 60
```

`--out` 必须是新的或空目录，防止覆盖之前的数据。批次第 i 局使用 `seed+i-1`。
更改策略不会影响同种子、同生成参数的目标数据和误差场。
不要将 seed 作为需要策略读取的观测；它是实验管理信息。

## 实现的物理与接口规则

- 圆域半径 1800 米；机器人允许到圆域外，坐标分量不能超过 2000000 米。
- 源数量 10—16，频道从 1—20 中无放回抽取，源位置和接收半径保持固定。
- 有效接收半径 1000—1500 米；接收范围内且距离不超过 5 米返回 `near`。
- 正常示向度按东向 0 度、逆时针递增，返回两位小数、范围 `[0,360)`。
- 同一场景、频道、位置的误差固定，重复检测不会刷新误差。
- `/measure`：直线移动距离 / 5 + 换频道 1 秒（若需要）+ 检测 5 秒。
- `/clear`：仅尝试指定频道；20 米以内成功，动作耗时 5 秒，否则 3 秒；另加移动耗时。
- `/clear` 不切换测向机频道；同一目标只能被清除一次。
- 虚拟时间以整数微秒累计；响应以秒输出，最多六位小数。
- 动作只推进虚拟时间，服务不会真实等待 5 秒；真实时间仍统计策略计算、通信和等待。
- 默认实际运行上限 1200 秒、服务开放窗口 1500 秒、虚拟活动上限 360000 秒。
- batch 另有默认 60 秒的进入前启动限制，可用 `--startup-timeout` 调整。
- 严格检查已声明字段、频道、坐标、JSON 重复键、嵌套深度、请求大小和 Content-Type。
- 非法动作不推进位置/频道/时间，不占用 request_id；accepted=false 的 virtual_time_s 是 0。
- 接受后的同 ID 同内容重试返回原响应；改内容返回 409；不同动作不应并发发送。
- 不提供 `/state`、`/targets` 或真值查询接口；源数量、位置等不会出现在策略响应中。

`b_sim/client.py` 是可选的策略端客户端，也可以继续使用原来的 urllib/requests 代码。
它提供 `enter()`、`measure(x,y,channel)`、`clear(x,y,channel)`、`exit()`，网络重试复用原 ID。

## 自定义假设与官方模拟器的区别

这是**依据公开协议自行实现的演练环境，不是官方模拟器的复刻或替代品**。
官方没有公布随机生成器、误差空间相关性和部分底层舍入细节，因此不能承诺相同分数。

1. 默认 `uniform` 是圆域面积均匀分布；目标数量在 10—16 中均匀抽取，接收半径连续均匀抽取。
   `edge` 在 1600—1800 米环域按面积均匀采样；`clustered` 是三个随机中心附近的截断高斯采样；
   `mixed` 混合前三种。它们都是本地实验假设。
2. 默认 `smooth` 误差由每频道固定的随机格点场平滑插值得到，幅度不超过 1 度；
   `--noise-cell 20` 表示格点间距 20 米，不是题目提供的参数。
   `hash` 是位置哈希误差、微小移动也可能改变误差，适合另一种压力测试；`zero` 用于排错。
   所有模式都保证同点重复误差固定。最终两位小数读数也限制在真实方位 +/-1 度内；
   官方量化细节未知，正式策略仍宜保留适当数值容差。
3. 取消账号、服务器时间校验、5 秒倒计时、正式测试次数和加密上传流程。
   不实现官方的流量保护、幂等记录数量上限或日志加密。输出仅用于本地实验。
4. 对终止前接受过的请求，核心引擎可以返回缓存响应；新的动作在终止后断开连接。
   独立服务退出后接口关闭，客户端不能依赖事后查询或重试成功。
5. 可用 `--directional-count N` 生成额外的第四问场景，方向覆盖为 +/-90 度。
   附带七点示例仅适用于第三问，不可作为第四问保证策略。

规则依据为 B 题题目及其附件1、附件2；原始赛题和论文不包含在本源码仓库中。
最终仍应在官方演练环境验证策略，正式日志只能由官方模拟器生成。

## 输出与评价

批次目录：

- `results.csv`：Excel 可直接打开，逐局完整统计。
- `results.json`：同一数据的结构化版本。
- `summary.json`：清除比例、整局成功率、成功局耗时均值、全部局合并指标等。
- 每个 `case_XXXX/`：该局 `summary.json`、`scenario.json`、`settings.json`、`actions.jsonl`、
  策略标准输出和错误日志。

每局统计包括：目标总数、发现数、清除数、清除比例、虚拟总时间、每个已清除目标的平均时间、
实际运行时间、移动距离、测量次数、切换次数、失败清除次数、结束原因。
清除数为零时平均时间为 null，不能用 0 冒充好成绩。
`run_ok` 要求目标全部清除、策略正常 `/exit` 且子进程退出码为 0。
失败/超时/未找到全部目标的局都保留；不能只看成功局速度而忽略完整率。
退出码 0 表示批次全部正常完成；1 表示至少一局失败，结果仍已保存。

真值由批量管理器在策略子进程结束后写出，用于诊断。策略不要读取这些文件。
这是同一台电脑上的自用工具，不是隔离恶意策略的安全沙箱。

## 检查实现

```powershell
python -m unittest discover -s tests -v
```

测试覆盖附件计时示例、5/20/1000 米边界、频道隔离、定向覆盖、固定误差、幂等、非法输入、
真实/虚拟截止、HTTP 格式，以及独立策略子进程的完整批量清除和失败导出。

## 已接入的策略2（2026-09-12）

仓库的 `policy2/` 已提供可运行入口，并已通过本模拟器的 HTTP 联调。先使用同一个 Python 安装 `policy2/requirements.txt`，再在仓库根目录运行：

```powershell
& ".\policy2\run_local_simulator.ps1" -Cases 3
```

或者在 `policy2/` 目录直接运行：

```powershell
python ../local_simulator/run.py batch --cases 3 --out results/native_batch --cwd . --command "{python}" -m strategy2 live --config configs/quick.json
```

策略2会自动读取 SIM_BASE_URL、SIM_ROBOT_ID，在 SIM_RUN_DIR/strategy2 下保存策略日志。
运行它需要 NumPy，模拟器自身仍只依赖 Python 标准库。批量对比多个配置的方法见 [Policy 2 README](../policy2/README.md)。
这次接入未修改本工具的物理引擎、计时与批量管理器。

## 代码目录

| 文件 | 作用 |
|---|---|
| run.py | serve/batch 入口、子进程管理、结果导出与汇总 |
| b_sim/core.py | 场景生成、物理反馈、微秒计时、会话与请求幂等 |
| b_sim/server.py | HTTP 服务与请求校验 |
| b_sim/client.py | 提供给外部策略的可选客户端 |
| examples/coverage_strategy.py | 独立的七点覆盖示例；不等于 policy2 |
| tests/test_simulator.py | 协议、计时、边界、失败导出与批量运行测试 |
| 启动批量测试.ps1 | Windows 通用批量启动脚本 |

结果默认写入 results，已在 .gitignore 排除；不提交每局隐藏目标、输出日志或 Python 缓存。
