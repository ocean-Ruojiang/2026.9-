import math
import numpy as np
from .geometry import cell_intersects
from .config import point


class ClearGrid:
    """Persistent finite grid. No deletion based on particle mass or center alone."""
    def __init__(self, channel, cfg):
        self.cfg = cfg
        h = cfg.fallback_cell_m
        lo, hi = channel.polygon.min(axis=0), channel.polygon.max(axis=0)
        self.x0, self.y0 = math.floor(lo[0]/h), math.floor(lo[1]/h)
        self.x1, self.y1 = math.floor(hi[0]/h), math.floor(hi[1]/h)
        self.total = (self.x1-self.x0+1)*(self.y1-self.y0+1)
        self.cursor = 0
        self.skipped = 0
        self.tried = 0

    def next_point(self, channel):
        h, half = self.cfg.fallback_cell_m, self.cfg.fallback_cell_m/2
        columns = self.x1-self.x0+1
        while self.cursor < self.total:
            row, col = divmod(self.cursor, columns)
            self.cursor += 1
            if row % 2:
                col = columns-1-col
            p = point(((self.x0+col+.5)*h, (self.y0+row+.5)*h), self.cfg)
            if not cell_intersects(channel.polygon,p,half):
                self.skipped += 1
                continue
            corners = np.asarray(p)+np.array([[-half,-half],[-half,half],[half,-half],[half,half]])
            if any(np.max(np.linalg.norm(corners-failed,axis=1)) <=
                   self.cfg.clear_radius-self.cfg.geometry_eps for failed in channel.failed_clear):
                self.skipped += 1
                continue
            self.tried += 1
            return p
        return None
