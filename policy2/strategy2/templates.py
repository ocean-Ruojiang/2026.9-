from dataclasses import replace
from .model import Op, Plan, Status


def ordered(ops, radio, primary_first=None):
    seq = list(dict.fromkeys(ops))
    first = []
    if primary_first is not None and primary_first in seq:
        seq.remove(primary_first)
        first = [primary_first]
        if primary_first.kind == "measure":
            radio = primary_first.channel
    measures = sorted((o for o in seq if o.kind == "measure"),
                      key=lambda o: (o.channel != radio, o.channel))
    clears = sorted((o for o in seq if o.kind == "clear"), key=lambda o: o.channel)
    return tuple(first + measures + clears)


class Templates:
    def __init__(self, cfg, predictor):
        self.cfg, self.predictor = cfg, predictor

    def pool(self, world, p, excluded=()):
        known, unknown = [], []
        for ch in world.channels.values():
            if ch.channel in excluded or ch.status in (Status.CLEARED, Status.ABSENT):
                continue
            options = [Op("measure", ch.channel)]
            if ch.status == Status.KNOWN:
                options.append(Op("clear", ch.channel))
            for op in options:
                if self.predictor.valid(world,p,op):
                    f = self.predictor.atom(world,p,op)
                    if f.gain.weighted(self.cfg) > 0:
                        (unknown if ch.status == Status.UNKNOWN else known).append(op)
        def rank(op):
            f = self.predictor.atom(world,p,op)
            return (-f.gain.weighted(self.cfg)/(f.service+1), op.channel, op.kind)
        # Prune only optional operations; mandatory station scans bypass this pool.
        known.sort(key=rank)
        unknown.sort(key=rank)
        return known[:self.cfg.optional_pool_cap], unknown[:self.cfg.optional_pool_cap]

    def extend(self, world, base, pool, allowed=lambda p: True, primary_first=False):
        best = base
        score = self.predictor.evaluate(world,best).score
        while len(best.ops) < self.cfg.max_ops_per_template:
            used = {o.channel for o in best.ops}
            proposals = []
            for op in pool:
                if op.channel in used:
                    continue
                p = replace(best, ops=ordered(best.ops+(op,), world.radio,
                            best.primary if primary_first else None))
                if not allowed(p):
                    continue
                value = self.predictor.evaluate(world,p).score
                if value > score+self.cfg.score_eps:
                    proposals.append((value,p))
            if not proposals:
                break
            score, best = max(proposals, key=lambda row: row[0])
        return best

    def four(self, world, candidate, mode="optional", allowed=lambda p: True,
             primary_first=False):
        p, op = candidate.point, candidate.primary
        if not self.predictor.valid(world,p,op):
            return []
        base = Plan(p,(op,),candidate.source,mode,op)
        if not allowed(base):
            return []
        known, unknown = self.pool(world,p,(op.channel,))
        results = [base]
        for pool in (known, unknown, known+unknown):
            results.append(self.extend(world,base,pool,allowed,primary_first))
        unique = {}
        for plan in results:
            unique[plan.ops] = plan
        return list(unique.values())
