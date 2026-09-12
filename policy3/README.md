# Policy 3：第四问正负反馈推断与分层巡检

适用于 B 题第四问：20 个频道、10—16 个静止干扰源，混合全向与固定 180° 定向辐射。目标是完成全部发现、定位与清除。策略只通过题目的四个 HTTP 接口获取反馈。

本目录是独立 Python 包 `strategy3`，不依赖 `policy2`。本地自制模拟器位于相邻的 `../local_simulator/`；其原有第四问支持直接使用，没有修改模拟器物理规则。

## 从这里阅读

- [数学说明](docs/数学说明.md)：写作手使用，包括假设、联合推断、覆盖证明、收益和终止条件。
- [实现指南](docs/实现指南.md)：编程手与编程 agent 使用，包括模块、数据接口、执行链路、配置和扩展点。
- [实验设计](docs/实验设计.md)：10 随机＋5 特殊场景、复现命令、指标口径。
- [本次15局验证结果](docs/验证说明.md)：15/15局全清，203/203个目标清除，含逐局虚拟时间、CPU时间与墙钟时间。
- [22 站坐标](strategy3/assets/stations_22.json)：圆心 1 站，995 米内部层 7 站，1795 米近边界层 7 站，2055 米域外层 7 站。
- [站点布局简图](docs/station_layout_22.png)：与代码中交付坐标一致，可用 [SVG](docs/station_layout_22.svg) 导出。

## 安装与运行

需要 Python 3.10 或以上、NumPy 1.24 或以上且低于 3。

```powershell
python -m pip install -r requirements.txt
python -m strategy3 verify-layout
python -m strategy3 local-sim --cases 1 --directional-count 6 --config configs/default.json --output results/first_run
```

输出目录必须不存在或为空；每局独立启动本地 HTTP 服务与策略子进程，自动进入、操作、退出。

已经单独启动模拟器时：

```powershell
python -m strategy3 run --config configs/default.json --base-url http://127.0.0.1:2027 --robot-id local --output results/manual_run
```

策略也自动读取 `SIM_BASE_URL`、`SIM_ROBOT_ID`、`SIM_RUN_DIR`。本地批量工具会设置这些变量。可在本目录 `pip install -e .` 后从其他位置使用 `python -m strategy3`；覆盖资产会随包安装。

## 执行固定 15 局验收

```powershell
python tools/run_acceptance.py --config configs/balanced.json --output results/acceptance_new --real-limit 600
```

默认单 worker，10 局随机混合源场景和 5 局特殊场景。特殊场景包括最小侦察半径下边缘朝外、朝内、掠射方向、密集双簇，以及全部定向源的哈希误差压力场景。场景由实验管理器生成；策略不获得源数、源位置、类型、朝向、环境种子或真值文件路径。

成功局必须由模拟器确认全部清除、正常 `/exit`、策略进程返回 0。失败局完整保留。源码与覆盖资产在开始和结束时计算指纹，运行期间变更不会被当作同一版本的最终验收。

## 核心机制

1. 保存每个频道全部观测记录；同一位置固定误差，不把重复测量当成独立信息。
2. 已发现频道维护位置、类型、固定半径和固定方向的联合可行假设。新正记录会重新激活旧负记录的方向约束。
3. 保守自适应网格负责不漏位置；带面积权重的有限联合样本负责收益预测。样本为空不代表无目标。
4. 圆心先扫未知频道，随后内部层、近边界层推进。在锁定下一站的前提下，用累计绕路预算允许局部探索、精化和清除。
5. 内部巡检结束后先清除已知目标，再进入域外层。外圈只检查尚未发现且未被证明不存在的频道；发现立即中断巡检并锁定清除，随后恢复未完成任务。
6. 同点执行“主操作／加已知频道／加未知频道／两者都加”四类模板。执行每条操作后立刻更新证据，再检查剩余操作是否仍应执行。
7. 定位收益的无信号分支同样筛选联合样本。几何证明成立或清除概率达到阈值时尝试清除；锁定动作达到上限后，用有限 24 米清除网格兜底。

未知频道的不存在证据来自所有历史无信号点对连续空间的凸包覆盖，而非抽样目标未被发现。程序启动会复核 22 站对应的 1848 个连续方格证书，包括距离、凸包和整个任务圆覆盖完整性。

## 配置

新增交错巡检实验配置 `configs/interleaved.json`：在 balanced 的 45 秒预算上，设置 `patrol_order="interleaved"`，使圈内按 I1→M1→I2→M2→…→I7→M7 推进。原 layered 配置仍保留。运行前 10 个固定随机场景可使用 `python tools/run_acceptance.py --cases 10 --config configs/interleaved.json --output results/interleaved_new`。见 [实验说明](docs/交错巡检实验.md)。

| 配置 | 含义 |
|---|---|
| `configs/default.json` | 初始基线：90 秒绕路预算、192 联合样本、40→5 米自适应格、最多 1200 格 |
| `configs/balanced.json` | 相对基线仅把绕路预算改为 45 秒；用于压低短期局部操作开销 |

固定权重为探索 1、精化 4、清除 8。局部候选上限 18，预测示向度代表数 16，每个同点模板最多 3 条操作。锁定目标最多 10 次普通主操作后进入持久清除网格；附加频道操作另有每目标 30 秒预算。

这些是可用初始配置，不代表已完成全面参数寻优。题目规定的物理常数不能作为调参项修改。

## 模块划分

| 模块 | 职责 |
|---|---|
| `config.py`、`model.py` | 校验配置；频道、观测、世界和动作类型 |
| `geometry.py` | 正反馈保守多边形、测向裁剪、包围圆 |
| `angles.py`、`grid.py` | 开闭圆周区间、自适应网格与整格安全排除 |
| `beliefs.py` | 全历史联合可行性与位置／类型／半径／方向后验近似 |
| `coverage.py`、`assets/` | 22 站连续证书、未知频道负反馈排查账本 |
| `q2.py`、`candidates.py` | 第二问扩展接口与任务候选位置 |
| `gains.py`、`templates.py` | 正负结果预测、收益率与同点操作组合 |
| `scheduler.py`、`fallback.py` | 分层推进、累计预算、中断恢复、有限清除兜底 |
| `client.py`、`state.py` | 串行幂等请求、响应校验和状态提交 |
| `runner.py`、`journal.py` | 在线循环、时限、日志、CPU 与墙钟统计 |
| `local_sim.py`、`tools/run_acceptance.py` | 原本地模拟器接入、场景与完整实验汇总 |

## 输出和局限

每局保存模拟器 `actions.jsonl`、`scenario.json`、`settings.json`、`summary.json`，以及策略 `strategy3/events.jsonl`、`config.json`、`summary.json`。动作日志包含实际移动坐标、频道及操作，可用于后续轨迹可视化。真值在策略子进程结束后导出。

虚拟时间由模拟器计费；`strategy_cpu_time_s` 是策略进程 CPU 时间；`real_runtime_s` 是策略启动、计算、通信、日志的墙钟时间；`planning_time_s` 包含决策和观测推断等状态处理，不能当作纯 CPU 时间。

团队的第二问最优观察点算法尚未提供实际代码。当前 `q2_provider="heuristic"` 是可运行的横向观察启发式；已保留 `module:factory` 接口，不能在论文中将它写成已调用第二问的精确最坏直径最优解。具体替换见实现指南。

静态布局的证明保障发现覆盖。全部清除还依赖有效观测、定位／有限清除执行及足够的活动时限。22 站不声称全局最少，也不声称比其他布局总行程最短。

## 检查代码

```powershell
python -m unittest discover -s tests -v
```

## 15 局交互回放

双击本目录的 open_replay.cmd 打开。支持测试局、频道、操作筛选和逐步播放，另可叠加检查站与真实定向发射范围。完整说明见 [回放使用说明](docs/回放使用说明.md)。

