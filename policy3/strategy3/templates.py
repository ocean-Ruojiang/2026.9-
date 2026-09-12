"""Four same-position operation templates, executed one operation at a time."""
from dataclasses import dataclass
from .model import Action


@dataclass(frozen=True)
class Template:
    actions: tuple
    name: str = 'primary'


def four(world, primary, predictor, cfg):
    """The mandatory primary is always first; each additional channel occurs once."""
    known, unknown = [], []
    for ch in world.channels.values():
        if ch.channel == primary.channel or ch.status not in ('KNOWN', 'UNKNOWN'):
            continue
        for kind in (('measure', 'clear') if ch.status == 'KNOWN' else ('measure',)):
            action = Action(primary.point, kind, ch.channel, 'same_site_extra')
            if not predictor.valid(world, action):
                continue
            forecast = predictor.forecast(world, action)
            if kind == 'clear' and not forecast.reliable_clear and forecast.clear<cfg.clear_min_probability:
                continue
            if forecast.weighted(cfg)<=1e-12:
                continue
            item = (forecast.weighted(cfg)/(forecast.service+cfg.switch_seconds*(kind=='measure')), -ch.channel, action)
            (known if ch.status == 'KNOWN' else unknown).append(item)
    k = max(known, key=lambda a:a[:2])[2] if known else None
    u = max(unknown, key=lambda a:a[:2])[2] if unknown else None
    templates = [Template((primary,))]
    limit = getattr(cfg, 'max_template_ops', 3)
    if limit>=2:
        if k is not None:
            templates.append(Template((primary, k), 'primary+known'))
        if u is not None:
            templates.append(Template((primary, u), 'primary+unknown'))
    if limit>=3 and k is not None and u is not None:
        templates.append(Template((primary, k, u), 'primary+known+unknown'))
    return templates
