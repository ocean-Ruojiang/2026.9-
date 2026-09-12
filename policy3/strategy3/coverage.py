"""Verified 22-station coverage and conservative per-channel negative evidence.

The certificate covers continuous cells, not sampled source positions. Unknown
channels are absent only when every task-disk cell has a negative convex-hull
certificate. Positive discovery transfers the channel out of global search.
"""
from dataclasses import dataclass
import json
import math
from pathlib import Path
import numpy as np

ASSETS=Path(__file__).resolve().parent/'assets'


def cross(a,b,c):
    return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])


def hull(points):
    pts=sorted(set(map(tuple,points)))
    if len(pts)<3:
        return pts
    lo=[]
    for p in pts:
        while len(lo)>=2 and cross(lo[-2],lo[-1],p)<=0:
            lo.pop()
        lo.append(p)
    hi=[]
    for p in reversed(pts):
        while len(hi)>=2 and cross(hi[-2],hi[-1],p)<=0:
            hi.pop()
        hi.append(p)
    return lo[:-1]+hi[:-1]


def contains_box(poly,corners):
    return len(poly)>=3 and all(min(cross(a,b,p) for p in corners)>=1e-7
                               for a,b in zip(poly,poly[1:]+poly[:1]))


@dataclass(frozen=True)
class Station:
    id: str
    group: str
    point: tuple[float,float]


def load_verified_layout():
    layout=json.loads((ASSETS/'stations_22.json').read_text(encoding='utf-8'))
    certificate=json.loads((ASSETS/'coverage_certificate_22.json').read_text(encoding='utf-8'))
    stations=[Station(s['id'],'O' if s['group']=='origin' else s['group'],(float(s['x']),float(s['y'])))
              for s in layout['stations']]
    if len(stations)!=22 or len({s.id for s in stations})!=22:
        raise ValueError('Invalid station layout')
    positions=np.array([s.point for s in stations])
    leaves={}
    for x,y,w,ids in certificate['cells']:
        if w<=0 or not ids or any(type(i) is not int or not 0<=i<22 for i in ids):
            raise ValueError('Invalid coverage leaf')
        key=(float(x),float(y),float(w))
        if key in leaves:
            raise ValueError('Duplicate certificate leaf')
        corners=np.array([[x,y],[x+w,y],[x+w,y+w],[x,y+w]])
        selected=positions[ids]
        if np.max(np.linalg.norm(selected[:,None,:]-corners[None,:,:],axis=2))>=1000-1e-7:
            raise ValueError('Coverage certificate distance check failed')
        if not contains_box(hull(selected),corners):
            raise ValueError('Coverage certificate convex hull check failed')
        leaves[key]=ids
    # Verify that the supplied leaves tile every part of the task disk. Merely
    # validating the listed leaves would not detect a removed certificate cell.
    pending=[(-1800.,-1800.,3600.)]
    used=set()
    while pending:
        x,y,w=pending.pop()
        nx=0 if x<=0<=x+w else min(abs(x),abs(x+w))
        ny=0 if y<=0<=y+w else min(abs(y),abs(y+w))
        if math.hypot(nx,ny)>1800+1e-8:
            continue
        if (x,y,w) in leaves:
            used.add((x,y,w))
            continue
        if w<.5:
            raise ValueError('Missing coverage certificate leaf')
        h=w/2
        pending.extend(((x,y,h),(x+h,y,h),(x,y+h,h),(x+h,y+h,h)))
    if len(used)!=len(leaves):
        raise ValueError('Certificate has unused/overlapping leaves')
    return stations,list(leaves)


class Coverage:
    def __init__(self,cfg):
        self.cfg=cfg
        self.stations,boxes=load_verified_layout()
        self.by_id={s.id:s for s in self.stations}
        self.boxes=np.asarray(boxes)
        self.centers=self.boxes[:,:2]+self.boxes[:,2,None]/2
        self.half=self.boxes[:,2]/2
        self.weights=self.boxes[:,2]**2
        self.weights/=self.weights.sum()
        self.negative={c:[] for c in range(1,21)}
        self.certified={c:np.zeros(len(boxes),dtype=bool) for c in range(1,21)}
        self.measured={s.id:set() for s in self.stations}
        self.selected=None
        self.stage='O'
        self.visited=[]

    def pending_channels(self,station,world):
        return [c.channel for c in world.unknown() if c.channel not in self.measured[station.id]]

    def next_station(self,world):
        if self.selected is not None and self.pending_channels(self.selected,world):
            return self.selected
        self.selected=None
        for group in ('O','I','M','E'):
            options=[s for s in self.stations if s.group==group and self.pending_channels(s,world)]
            if options:
                self.stage=group
                self.selected=min(options,key=lambda s:(math.dist(s.point,world.position),s.id))
                return self.selected
        self.stage='DONE'
        return None

    def mark_measured(self,station_id,channel):
        if station_id not in self.by_id or not 1<=channel<=20:
            raise ValueError('Unknown station/channel')
        self.measured[station_id].add(channel)
        if station_id not in self.visited:
            self.visited.append(station_id)

    def record_no_signal(self,channel,point):
        p=tuple(map(float,point))
        previous=self.negative[channel]
        if p in previous:
            return
        previous.append(p)
        for station in self.stations:
            if math.dist(station.point,p)<1e-7:
                self.mark_measured(station.id,channel)
        if len(previous)<3:
            return
        # A new witness can only improve boxes entirely within its receive disk.
        dmax=np.abs(self.centers-p)+self.half[:,None]
        candidates=np.flatnonzero((np.sum(dmax*dmax,axis=1)<(1000-1e-6)**2)&~self.certified[channel])
        pp=np.asarray(previous)
        for index in candidates:
            x,y,w=self.boxes[index]
            dist=np.abs(pp-self.centers[index])+self.half[index]
            close=pp[np.sum(dist*dist,axis=1)<(1000-1e-6)**2]
            if len(close)<3:
                continue
            corners=((x,y),(x+w,y),(x+w,y+w),(x,y+w))
            if contains_box(hull(close),corners):
                self.certified[channel][index]=True

    def absent_certificate(self,channel):
        return bool(self.certified[channel].all())

    def exploration_gain(self,point,channel):
        # A ranking surrogate only. Half of the residual location footprint is
        # used under an initially uniform 180-degree direction prior.
        pending=~self.certified[channel]
        reach=np.linalg.norm(self.centers-point,axis=1)<=self.cfg.recv_min
        return float(.5*np.sum(self.weights[pending&reach]))

    def done(self,world):
        return not any(self.pending_channels(s,world) for s in self.stations)

    def summary(self):
        return dict(layout='staggered_22',verified_cells=len(self.boxes),stage=self.stage,
            selected_station=None if self.selected is None else self.selected.id,
            visited_stations=self.visited,
            station_channel_measurements=sum(map(len,self.measured.values())),
            channel_certified_fraction={str(c):float(self.weights[self.certified[c]].sum()) for c in range(1,21)},
            absent_certificates=[c for c in range(1,21) if self.absent_certificate(c)])
