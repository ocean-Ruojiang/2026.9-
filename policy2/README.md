# Policy 2：全覆盖约束下的收益调度

适用于 B 题第三问：在 20 个频道中搜索、定位并清除全部 10—16 个静止全向干扰源。
策略在局部收益率评分之外，加入必须推进的覆盖主任务，避免未发现目标因短期得分低而被长期忽略。

本目录是策略2完整代码。Python 包名为 `strategy2`；独立模拟器位于相邻的 `../local_simulator/`。

## 1. 安装和第一次运行

需要 Python >=3.10，NumPy >=1.24 且 <3。在本目录运行：

```bash
python -m pip install -r requirements.txt
python -m strategy2 local-sim --cases 3
```

这会自动启动项目自制模拟器，为每局创建独立 HTTP 服务与策略进程。默认 quick 配置、uniform 场景、smooth 位置固定误差，首局种子 20260912，后续加 1。

默认结果保存在本目录 `results/local_simulator/时间戳/`。运行结果被 Git 忽略。全部成功时命令退出码为 0，存在未完成或配对不一致时为 2，结果仍保留。

## 2. 批量实验

同一批场景比较三个配置：

```bash
python -m strategy2 local-sim --configs configs/quick.json configs/quick_no_detour.json configs/quick_fixed_station.json --cases 10 --seed 100 --layout mixed
```

16 个边缘目标、最小接收半径及位置哈希误差：

```bash
python -m strategy2 local-sim --configs configs/default.json --cases 5 --count 16 --layout edge --radius-mode minimum --noise hash --seed 200
```

| 选项 | 默认值 | 用途 |
|---|---|---|
| `--configs` | configs/quick.json | 一个或多个配置文件 |
| `--cases` | 3 | 每个配置运行几局 |
| `--seed` | 20260912 | 首局环境种子 |
| `--workers` | 1 | 单个配置并行局数；并行可能影响实际计算耗时 |
| `--count` | 每局随机 10—16 | 可固定为 10—16 |
| `--layout` | uniform | uniform / edge / clustered / mixed |
| `--radius-mode` | random | random / minimum / maximum |
| `--noise` | smooth | smooth / hash / zero |
| `--noise-cell` | 20 | smooth 误差场格点间距，米 |
| `--output` | 自动时间戳 | 必须为尚不存在的输出目录 |
| `--simulator` | 相邻 local_simulator | 模拟器目录，内部需包含 run.py |
| `--real-limit` | 1200 | 环境每局现实上限，秒 |
| `--window-limit` | 1500 | 服务开放窗口，秒 |
| `--virtual-limit` | 360000 | 环境每局虚拟时间上限，秒 |
| `--startup-timeout` | 60 | 策略进入前的启动上限，秒 |
| `--robot-id` | local | 本地会话编号 |

实际截止由环境与策略配置两侧共同约束。命令固定使用动态端口及全向源，不适用于第四问定向源。

Windows 从仓库根目录也可调用：

```powershell
& .\policy2\run_local_simulator.ps1 -Configs quick,quick_budget45,quick_no_detour -Cases 10 -Seed 100 -Layout mixed
```

脚本支持 `-Python` 指定解释器。未指定时优先使用当前已激活的虚拟环境，再寻找本机 Codex Python，最后查找系统 Python。所选解释器必须已安装 NumPy。

## 3. 配置与单因素变体

| 配置 | 含义 |
|---|---|
| default.json | 256 粒子、32 预测情景，48/16 个普通/站点候选上限 |
| quick.json | 快速筛选：128 粒子、8 情景，24/8 候选上限；计算预算多项变化 |
| quick_no_detour.json | 相对 quick，仅将绕路预算由 90 秒改为 0 |
| quick_fixed_station.json | 相对 quick，仅将站点偏移由 80 米改为 0 |
| quick_budget45.json | 相对 quick，仅将绕路预算改为 45 秒 |
| quick_offset40.json | 相对 quick，仅将站点偏移改为 40 米 |
| fixed_baseline.json | 站点偏移、绕路预算、收尾附加预算均为 0；多因素结构基准 |

生成新的单因素配置：

```bash
python tools/make_variant.py --base configs/quick.json --name quick_budget60 --field detour_budget_s --value 60 --output configs/quick_budget60.json
```

不要修改题目规定的速度、目标区域、接收范围和清除半径来优化成绩。

## 4. 结果判读

```text
实验目录/
  manifest.json                 环境参数、实际命令、配置差异
  *.config.json                 运行前冻结的完整配置
  comparison.csv / .json        每个配置的成功率及配对耗时比较
  case_comparison.json          逐局数据、场景指纹、证书与时钟核对
  summary.json                  all_runs_ok 总体结果
  01_quick/
    results.csv / results.json  模拟器真实逐局统计
    summary.json                模拟器批次统计
    case_0001/
      scenario.json             运行结束后导出的真值
      settings.json             模拟器设置
      actions.jsonl             模拟器动作日志
      summary.json              真实目标数、清除数、耗时和退出原因
      strategy.stdout.log
      strategy.stderr.log
      strategy2/
        config.json
        events.jsonl            决策、请求、反馈、预算和状态日志
        summary.json            策略可见状态、规划与实际运行耗时
```

模拟器要求真实目标全清、正常 `/exit` 且进程退出码为 0；汇总器再核对策略完成证书和清除数量。
优先看 success_rate，再看耗时。`mean_paired_delta_s` 是当前配置减去第一配置的虚拟时间，只统计场景相同且两者都成功的案例；负数表示当前配置更快。

时间字段：`virtual_time_s` 为模拟任务时间；策略 `planning_time_s` 为规划函数累计墙钟耗时；`real_runtime_s` 为主流程实际墙钟耗时，含通信和日志。后两者不等于纯 CPU 时间。

真值由模拟器在策略进程结束后导出，策略运行期间只通过题目接口获取反馈。

## 5. 算法与模块

数学定义见 [数学说明](docs/数学说明.md)。局部评分采用 `(探索收益 + 4 × 精化收益 + 8 × 清除收益) / 期望耗时`，默认平均预测、固定权重和当前模板评价。

全局机制包含七个覆盖站、80 米可移动站点、每阶段 90 秒累计绕路预算、锁定目标收尾和有限清除网格。必须完成的扫描任务不会因低得分被过滤；没有全部完成时如实报告失败或超时。

| 模块 | 职责 |
|---|---|
| config.py / model.py | 配置与状态、动作、计划、收益结构 |
| geometry.py / coverage.py | 凸外包、包围圆、严格覆盖证书与探索面积 |
| beliefs.py / gains.py | 后验采样、预测、收益和时间 |
| q2.py / candidates.py | 第二问接口与测向、清除、站点候选 |
| templates.py | 操作组合与边际收益扩展 |
| budget.py / scheduler.py | 累计预算与阶段调度 |
| fallback.py | 持续保存进度的有限网格清除 |
| client.py / state.py | 串行幂等 HTTP、反馈校验与状态提交 |
| runner.py / journal.py | 执行主循环、截止、指标与日志 |
| local_sim.py | 独立模拟器接入及多配置配对汇总 |
| simulator.py / experiments.py | 原内置合成环境的快速测试与比较 |
| __main__.py | 命令行入口 |

## 6. 第二问替换接口

当前配置使用 `q2_provider="heuristic"`，只是包围圆中心与横向偏移候选，不是第二问最坏直径最优算法。

团队算法可实现 `strategy2.q2.Q2Provider`，提供：

```python
def propose(self, channel, current, cfg) -> Q2Result:
    # channel 包含已有观测历史与凸定位外包。
    # 返回候选 points、可选 regions 和说明。
    ...

def evaluate_worst_diameter(self, polygon, candidate, halfwidth_deg) -> float:
    # 所有可行未来观测下，裁剪多边形直径的严格最坏值。
    ...
```

定义 `factory(cfg)` 返回 provider，将配置设为 `"你的模块:factory"`。模块需能由运行策略的 Python 导入。当前决策链主要调用 propose，再以情景平均收益评分；最坏直径不能直接冒充平均定位尺度。

## 7. 直接连接 HTTP 服务

如果模拟器已启动，在本目录执行：

```bash
python -m strategy2 live --config configs/quick.json --url http://127.0.0.1:2027 --robot-id local --output results/manual_session_01
```

当由独立模拟器 batch 启动时，live 自动读取 `SIM_BASE_URL`、`SIM_ROBOT_ID`，并在 `SIM_RUN_DIR/strategy2` 新建日志目录。显式参数优先于环境变量。
手工运行若省略 --url，默认地址为 127.0.0.1:2026；请明确区分本地环境与官方已启动的演练环境。

## 8. 其他运行方式和测试

```bash
python -m strategy2 simulate --config configs/quick.json --seed 11 --scenario uniform --output results/synthetic_11
python -m strategy2 benchmark --configs configs/quick.json configs/quick_no_detour.json --seeds 11,12,13 --scenarios uniform,edge,clustered --output results/synthetic_compare
python -m unittest discover -s tests -v
```

simulate/benchmark 使用包内进程内合成环境，local-sim 使用独立 HTTP 模拟器。两套环境的相同种子不是同一场景，结果不得混成同图比较。

测试涵盖几何、覆盖、预算、预测、幂等、10/16 目标、网格和失败退出，外部接入测试会自动启动相邻模拟器。验证摘要见 [验证说明](docs/验证说明.md)。
