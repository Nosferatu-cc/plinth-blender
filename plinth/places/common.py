# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared parts for the places (0.12): paths in the ground's material
(tracks, footprints, streams), natural point patterns, and helpers."""
import math

from .. import scenemats as SM


# --- Paths in a ground material (crisp: worked out per pixel, not per vertex) -------------------

def arc(ctx, cx, cy, radius):
    """Metres along an arc (around cx, cy) and across it (+ outward)."""
    nt = ctx.nt
    dx = nt.math("SUBTRACT", ctx.x, cx)
    dy = nt.math("SUBTRACT", ctx.y, cy)
    d = nt.math("SQRT", nt.math("ADD", nt.math("MULTIPLY", dx, dx), nt.math("MULTIPLY", dy, dy)))
    along = nt.math("MULTIPLY", nt.math("ARCTAN2", dy, dx), radius)
    return along, nt.math("SUBTRACT", d, radius)


def line(ctx, x0, y0, x1, y1):
    """Metres along a straight path from (x0, y0) toward (x1, y1), across it, and t (0..1 along)."""
    nt = ctx.nt
    length = max(1e-6, math.hypot(x1 - x0, y1 - y0))
    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    px = nt.math("SUBTRACT", ctx.x, x0)
    py = nt.math("SUBTRACT", ctx.y, y0)
    along = nt.math("ADD", nt.math("MULTIPLY", px, ux), nt.math("MULTIPLY", py, uy))
    across = nt.math("SUBTRACT", nt.math("MULTIPLY", py, ux), nt.math("MULTIPLY", px, uy))
    return along, across, nt.math("DIVIDE", along, length)


def strip_uv(ctx, along, across, centre):
    """A UV vector (metres) for a texture whose feature runs along V at U =
    ``centre`` metres (e.g. 0.55 x the tile): across first, then along."""
    nt = ctx.nt
    v = nt.new("ShaderNodeCombineXYZ")
    nt.link(nt.math("ADD", across, centre), v.inputs[0])
    nt.link(along, v.inputs[1])
    return v.outputs[0]


def band(ctx, across, half, soft):
    """1 within ``half`` m of a path's middle, fading over ``soft`` m."""
    nt = ctx.nt
    return nt.smooth(nt.math("ABSOLUTE", across), half + soft, half)


def between(ctx, t, a, b, soft=0.05):
    """1 for t in [a, b] (a path's stretch), soft ends."""
    nt = ctx.nt
    return nt.math("MULTIPLY", nt.smooth(t, a - soft, a), nt.smooth(t, b + soft, b))


# --- Natural point patterns (numpy) ----------------------------------------------------------

def clustered(rng, centres, per, spread):
    """Thomas process: ``per`` (array or int) points around each centre,
    normally spread by ``spread`` (array or float). Returns x, y."""
    import numpy as np
    centres = np.asarray(centres, float).reshape(-1, 2)
    per = np.broadcast_to(np.asarray(per), (len(centres),)).astype(int)
    spread = np.broadcast_to(np.asarray(spread, float), (len(centres),))
    xs, ys = [], []
    for (cx, cy), n, s in zip(centres, per, spread):
        if n <= 0:
            continue
        xs.append(cx + rng.normal(0, s, n))
        ys.append(cy + rng.normal(0, s, n))
    if not xs:
        return np.zeros(0), np.zeros(0)
    return np.concatenate(xs), np.concatenate(ys)


def spaced(x, y, radius, order=None):
    """Keep points so no two are nearer than the larger of their radii
    (a crown never grows inside another's): greedy in ``order`` (default:
    biggest first). Returns the kept indices."""
    import numpy as np
    x, y = np.asarray(x, float), np.asarray(y, float)
    radius = np.broadcast_to(np.asarray(radius, float), x.shape)
    if order is None:
        order = np.argsort(-radius, kind="stable")
    cell = max(1e-6, float(radius.max()))
    grid = {}
    kept = []
    for i in order:
        cx, cy = int(math.floor(x[i] / cell)), int(math.floor(y[i] / cell))
        ok = True
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for j in grid.get((gx, gy), ()):
                    if (x[i] - x[j]) ** 2 + (y[i] - y[j]) ** 2 < max(radius[i], radius[j]) ** 2:
                        ok = False
                        break
                if not ok:
                    break
            if not ok:
                break
        if ok:
            grid.setdefault((cx, cy), []).append(i)
            kept.append(i)
    return np.asarray(sorted(kept), int)


def polyline_distance(px, py, path, maxd=None):
    """Distance from points to a polyline (N x 2), and the arc length of the
    nearest spot along it. With ``maxd``, points clearly farther than that
    (by a coarse first pass) just get ``maxd``: a million ground vertices stay light."""
    import numpy as np
    px, py = np.asarray(px, float), np.asarray(py, float)
    path = np.asarray(path, float)
    if maxd is not None and len(px) > 5000 and len(path) > 16:
        coarse = path[::8] if (len(path) - 1) % 8 == 0 else np.vstack([path[::8], path[-1:]])
        sag = float(np.max(np.hypot(*(path[1:] - path[:-1]).T))) * 8.0
        dc, _a = polyline_distance(px, py, coarse)
        near = dc < maxd + sag
        best = np.full(len(px), float(maxd))
        along = np.zeros(len(px))
        if near.any():
            d, a = polyline_distance(px[near], py[near], path)
            best[near] = np.minimum(d, maxd)
            along[near] = a
        return best, along
    a, b = path[:-1], path[1:]
    seg = b - a
    seg_len = np.maximum(np.hypot(seg[:, 0], seg[:, 1]), 1e-9)
    start = np.concatenate([[0.0], np.cumsum(seg_len)[:-1]])
    best = np.full(len(px), np.inf)
    along = np.zeros(len(px))
    for k in range(len(a)):
        t = ((px - a[k, 0]) * seg[k, 0] + (py - a[k, 1]) * seg[k, 1]) / (seg_len[k] ** 2)
        t = np.clip(t, 0.0, 1.0)
        qx, qy = a[k, 0] + t * seg[k, 0], a[k, 1] + t * seg[k, 1]
        d = np.hypot(px - qx, py - qy)
        better = d < best
        best = np.where(better, d, best)
        along = np.where(better, start[k] + t * seg_len[k], along)
    return best, along


def meander(rng, start, end, steps, wander, smooth=3):
    """A meandering polyline from start to end (a stream): a straight line
    bent by a random walk across it, smoothed."""
    import numpy as np
    t = np.linspace(0.0, 1.0, steps)
    sx, sy = start
    ex, ey = end
    dx, dy = ex - sx, ey - sy
    length = math.hypot(dx, dy)
    nx, ny = -dy / length, dx / length
    walk = np.cumsum(rng.normal(0.0, 1.0, steps))
    walk -= np.linspace(walk[0], walk[-1], steps)
    for _ in range(smooth):
        walk = np.convolve(np.pad(walk, 2, mode="edge"), np.ones(5) / 5.0, mode="valid")
    walk *= wander / max(1e-9, np.abs(walk).max())
    walk += wander * 0.6 * np.sin(t * math.pi * 2.3 + rng.uniform(0, 6.3))
    return np.stack([sx + dx * t + nx * walk, sy + dy * t + ny * walk], 1)


def tint(nt, colour, sat=1.0, value=1.0):
    """Saturation/value of a colour socket (a scan pushed toward the place's own colour)."""
    h = nt.new("ShaderNodeHueSaturation")
    h.inputs["Saturation"].default_value = sat
    h.inputs["Value"].default_value = value
    nt.link(colour, h.inputs["Color"])
    return h.outputs["Color"]


Layer = SM.Layer
