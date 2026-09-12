"""Independent Q3 smoke-test strategy: seven stations, then polygon refinement.

Uses ONLY the public HTTP client. Does not read scenario files or simulator state.
This is a working integration example, not a claimed optimal contest strategy.
"""
import math
import time

from b_sim.client import Client


def clip(poly, nx, ny, bound):
    if not poly:
        return []
    result = []
    a = poly[-1]
    da = nx*a[0] + ny*a[1] - bound
    for b in poly:
        db = nx*b[0] + ny*b[1] - bound
        if (da <= 0) != (db <= 0):
            t = da/(da-db)
            result.append((a[0]+t*(b[0]-a[0]), a[1]+t*(b[1]-a[1])))
        if db <= 0:
            result.append(b)
        a, da = b, db
    return result


def disk_clip(poly, center, radius):
    for i in range(64):
        a = math.tau*i/64
        nx, ny = math.cos(a), math.sin(a)
        poly = clip(poly, nx, ny, nx*center[0]+ny*center[1]+radius)
    return poly


def update(poly, p, obs):
    if obs["measure_result"] == "near":
        return disk_clip(poly, p, 5.)
    if obs["measure_result"] != "direction":
        return poly
    poly = disk_clip(poly, p, 1500.)
    for sign in [-1, 1]:
        a = math.radians(obs["svd_deg"] + sign*1.000001)
        nx, ny = -sign*math.sin(a), sign*math.cos(a)
        poly = clip(poly, nx, ny, nx*p[0]+ny*p[1])
    return poly


def bound_circle(poly):
    # A bounding-box circle encloses every vertex; the center is not assumed to be truth.
    x = (min(p[0] for p in poly)+max(p[0] for p in poly))/2
    y = (min(p[1] for p in poly)+max(p[1] for p in poly))/2
    return (x,y), max(math.dist((x,y), p) for p in poly)


def main():
    client = Client()
    initial = client.enter()
    deadline = time.monotonic()+initial["remaining_real_duration_s"]-1
    domain = disk_clip([(-1800.,-1800.),(1800.,-1800.),(1800.,1800.),(-1800.,1800.)], (0,0),1800.)
    beliefs = {}
    position, receiver = (0.,0.), 1
    stations = [(0.,0.)]+[(1150*math.cos(k*math.pi/3),1150*math.sin(k*math.pi/3)) for k in range(6)]
    for p in stations:
        channels = [receiver]+[c for c in range(1,21) if c != receiver]
        for c in channels:
            if time.monotonic() >= deadline:
                client.exit()
                return
            obs = client.measure(*p, c)
            position, receiver = p, c
            if obs["measure_result"] != "no_signal":
                beliefs[c] = update(beliefs.get(c, list(domain)), p, obs)
    while beliefs:
        c = min(beliefs, key=lambda c: math.dist(position,bound_circle(beliefs[c])[0]))
        for _ in range(30):
            if time.monotonic() >= deadline:
                client.exit()
                return
            poly = beliefs[c]
            if not poly:
                raise RuntimeError(f"Empty belief for channel {c}")
            point, radius = bound_circle(poly)
            if radius <= 19.99:
                result = client.clear(*point,c)
                position = point
                if result["clear_result"] != "success":
                    raise RuntimeError("Guaranteed clear unexpectedly failed")
                del beliefs[c]
                break
            if radius > 1000:
                raise RuntimeError("Demo bounding circle too large; use a more general strategy")
            obs = client.measure(*point,c)
            position, receiver = point,c
            if obs["measure_result"] == "no_signal":
                raise RuntimeError("Guaranteed reception unexpectedly failed")
            beliefs[c] = update(poly,point,obs)
        else:
            raise RuntimeError("Demo refinement limit reached")
    client.exit()


if __name__ == "__main__":
    main()
