"""Paired comparison of the interleaved experiment with the original ten cases."""
import argparse
import json
from pathlib import Path
from statistics import mean


def compare(baseline, experiment, output):
    old = json.loads((baseline/'results.json').read_text(encoding='utf-8-sig'))
    new = json.loads((experiment/'results.json').read_text(encoding='utf-8-sig'))
    indexed = {r['name']: r for r in old}
    pairs = [(indexed[r['name']],r) for r in new]
    for a,b in pairs:
        assert a['seed'] == b['seed'] and a['target_count'] == b['target_count']
        paths = [root/f"case_{row['case_index']:04d}"/'scenario.json'
                 for root,row in ((baseline,a),(experiment,b))]
        scenarios = [json.loads(p.read_text(encoding='utf-8-sig')) for p in paths]
        assert scenarios[0] == scenarios[1], f"Scenario mismatch: {b['name']}"
    fields = [('virtual_time_s','平均虚拟时间（秒）'),('move_distance_m','平均移动距离（米）'),
              ('measure','平均测量次数'),('strategy_cpu_time_s','平均策略 CPU 时间（秒）'),
              ('strategy_real_runtime_s','平均策略墙钟时间（秒）')]
    lines=['# 交错巡检与原分层巡检：配对实验结果','',
           f'共 {len(pairs)} 局，相同场景文件逐一核对一致。基线为原 15 局中的前 10 局。', '',
           '仅改变圈内巡检顺序；使用 balanced 的 45 秒绕路预算。CPU 和墙钟时间受运行环境影响。','',
           '| 指标 | 原分层 | 交错版 | 变化 |','|---|---:|---:|---:|']
    for key,label in fields:
        a=mean(p[0][key] for p in pairs); b=mean(p[1][key] for p in pairs)
        lines.append(f'| {label} | {a:.2f} | {b:.2f} | {(b/a-1)*100:+.2f}% |')
    for side,label in ((0,'原分层'),(1,'交错版')):
        rows=[p[side] for p in pairs]
        lines += ['',f"{label}：{sum(bool(r['run_ok']) for r in rows)}/{len(rows)} 局全清并正常退出；清除 {sum(r['cleared_count'] for r in rows)}/{sum(r['target_count'] for r in rows)} 个源。"]
    improved=sum(b['virtual_time_s']<a['virtual_time_s'] for a,b in pairs)
    lines += ['',f'交错版有 {improved}/{len(pairs)} 局虚拟时间降低。','',
              '| 局次 | 原虚拟秒 | 新虚拟秒 | 变化 | 新 CPU 秒 | 新程序墙钟秒 | 清除/总数 |',
              '|---|---:|---:|---:|---:|---:|---:|']
    for a,b in pairs:
        lines.append(f"| {b['case_index']} | {a['virtual_time_s']:.2f} | {b['virtual_time_s']:.2f} | {(b['virtual_time_s']/a['virtual_time_s']-1)*100:+.2f}% | {b['strategy_cpu_time_s']:.2f} | {b['strategy_real_runtime_s']:.2f} | {b['cleared_count']}/{b['target_count']} |")
    summary=json.loads((experiment/'summary.json').read_text(encoding='utf-8-sig'))
    lines += ['',f"运行期间源码变更：{summary['source_changed']}；固定版本实验有效：{summary['acceptance_eligible']}。",'',
              '这 10 个随机场景的结果不能替代边缘特殊场景验证，也不能证明所有布局下均有同样的效率提升。','']
    output.write_text('\n'.join(lines),encoding='utf-8')
    print('\n'.join(lines))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--experiment',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    compare(args.baseline,args.experiment,args.output)
