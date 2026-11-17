# SPDX-License-Identifier: GPL-3.0-or-later
"""Desert dunes (0.12): the Namib at Sossusvlei.

Reference: transverse dunes 20-60 m high, each with a long gentle windward
back (wind ripples, ~10 degrees) and a steep smooth slipface (~32 degrees)
below a knife-sharp crest that snakes across the field; orange-red sand;
flat gravel and clay between the dunes. To one side a white clay pan (a vlei)
with the dead camelthorn trees that have stood there for centuries, black
against the sand. Sparse dry grass tufts and stones on the flats. Low sun,
deep clear sky.
"""
import math

from .. import scenemats as SM, scenes as S
from . import common as C, flora

SKY = {"rotation": 190.0, "cover": 0.08, "haze": 1.6, "time": "GOLDEN", "golden": 13.0,
       "weathers": ["CLEAR", "OVERCAST", "MIST"], "wind": 0.2}
WIND = 0.35                                            # radians: the wind's direction


def build(p):
    import numpy as np
    r, u, rng = p.r, p.u, p.rng
    big = 280.0 * r
    seeds = rng.uniform(0, 1000, 4)
    lam = 34.0 * r                                     # crest to crest
    c, s = math.cos(WIND), math.sin(WIND)
    # The vlei: a clay pan to the left of the usual shots, the dead trees on it.
    view = np.array([-5.2, 6.0]) / math.hypot(5.2, 6.0)
    left = np.array([-view[1], view[0]])
    pan_r = 8.0 * r

    warp0 = S._waves(np.zeros(1), np.zeros(1), np.random.default_rng(int(seeds[0])), 3, 70.0 * r, 9.0 * r)[0]
    shift = 0.03 * lam - warp0 - 4.0 * r                # the product at the foot of a dune's windward back

    def profile(x, y):
        """Dune height, slipface and flat masks (0..1)."""
        along = x * c + y * s + shift
        warp = S._waves(x, y, np.random.default_rng(int(seeds[0])), 3, 70.0 * r, 9.0 * r)
        ph = ((along + warp) / lam) % 1.0
        back = 0.78                                     # windward share of each dune
        rise = np.where(ph < back, (ph / back) ** 1.5, ((1.0 - ph) / (1.0 - back)) ** 1.15)
        size = 0.5 + 0.5 * S._waves(x, y, np.random.default_rng(int(seeds[1])), 3, 90.0 * r, 1.0)
        size = np.clip(size, 0.15, 1.2)
        h = 5.5 * r * size * rise
        slip = (ph >= back) & (rise > 0.08)
        flat = np.clip(1.0 - rise * size * 3.0, 0.0, 1.0)
        return h, slip.astype(float), flat

    # The pan lies in the trough nearest the usual shots' left (where the dunes are lowest).
    cand = np.stack([left * k * r + view * j * r for k in range(10, 26, 2) for j in range(-6, 14, 2)])
    hc, _sc, _fc = profile(cand[:, 0], cand[:, 1])
    pan_c = cand[int(np.argmin(hc + 0.02 * r * np.hypot(cand[:, 0], cand[:, 1])))]
    edge_seed = rng.uniform(0, 6.3)

    def pan_edge(x, y):
        a = np.arctan2(y - pan_c[1], x - pan_c[0])
        rr = pan_r * (1.0 + 0.22 * np.sin(3 * a + edge_seed) + 0.12 * np.sin(7 * a + 2 * edge_seed))
        return np.hypot(x - pan_c[0], y - pan_c[1]) - rr

    def height(x, y):
        h, _s, _f = profile(x, y)
        h = h * S._pad(x, y, 2.5 * r, 7.0 * r)
        h = h * np.clip((pan_edge(x, y) + 2.0 * r) / (8.0 * r), 0.0, 1.0) ** 1.5      # the pan is flat
        return h + S._waves(x, y, np.random.default_rng(int(seeds[2])), 3, 40.0 * r, 0.25 * r)

    def slip(x, y):
        _h, sl, _f = profile(x, y)
        return sl * S._pad(x, y, 3.0 * r, 16.0 * r)

    def flats(x, y):
        _h, _s, f = profile(x, y)
        near = 1.0 - S._pad(x, y, 3.0 * r, 16.0 * r)
        return np.maximum(f, near * 0.6)

    def pan(x, y):
        return np.clip(0.5 - pan_edge(x, y) / (1.5 * r), 0.0, 1.0)

    ground = p.ground(1.5 * r, big, height, attrs={"slip": slip, "flat": flats, "pan": pan})
    sand = (1.0, 0.66, 0.42)

    def clay(nt, col, smp):
        """Vlei clay: bone white, the cracks darker (the cracked mud scan's own relief)."""
        g = C.tint(nt, col, sat=0.12, value=1.12)
        return nt.mix(1.0, g, (0.98, 0.93, 0.86, 1.0), "MULTIPLY")

    layers = [C.Layer("red_sand", tint=sand),
              C.Layer("aerial_beach_01", tile=7.0, colour=_ripple_colour,
                      mask=lambda cx: cx.mul(cx.nt.math("SUBTRACT", 1.0, cx.attr("slip")),
                                             cx.nt.math("SUBTRACT", 1.0, cx.attr("flat", 0.3, 0.8))), sharp=3.0),
              C.Layer("gravelly_sand", tint=(1.05, 0.85, 0.7), mask=lambda cx: cx.attr("flat", 0.45, 0.9), sharp=6.0),
              C.Layer("mud_cracked_dry_03", tile=2.2, colour=clay, mask=lambda cx: cx.attr("pan", 0.3, 0.8),
                      sharp=10.0)]
    mat = p.terrain("Sand", layers, disp=0.03 * u, pad=(1.0 * r, 1.4 * r), cavity=0.72, macro=(0.88, 1.08),
                    sheen=0.3, specular=0.4)
    if mat is None:
        mat = S._layered("Sand", (0.62, 0.40, 0.22, 1), 0.92, scale=1.0 / u, depth=0.4)
    S._with_materials(ground, [mat])

    # Dead camelthorn trees on the pan: black, bare, twisted, centuries dry.
    deadwood = SM.bark(p, "Deadwood", "bark_brown_02", sat=0.2, value=0.35) or S._simple(
        "Deadwood", (0.05, 0.035, 0.025, 1), 0.85, bump_scale=30.0, r=u, bump=0.6)
    tx, ty = C.clustered(rng, [tuple(pan_c)], 9, 0.45 * pan_r)
    keep = C.spaced(tx, ty, 2.8 * u)
    tx, ty = tx[keep], ty[keep]
    if len(tx):
        made = [S._proto("dead%d" % i, flora.broadleaf(S.PREFIX + "Proto_dead%d" % i, rng, [], 0.1, 6.0, p.detail,
                                                       spread=0.7, rise=(0.05, 0.5), limbs=(4, 7), leaf_k=0.0,
                                                       droop=0.05), [deadwood, deadwood]) for i in range(3)]
        idx = rng.integers(0, len(made), len(tx))
        ext = np.array([S._size(o)[2] for o in made])
        hs = rng.uniform(3.5, 7.0, len(tx)) * u
        p.scatter("DeadTrees", made, np.stack([tx, ty, height(tx, ty) - 0.1 * u], 1), idx,
                  S._upright(p, len(tx), 0.08), (hs / ext[idx])[:, None] * np.ones((1, 3)), share=False, cat="trees")
    # Stones on the gravel flats; dry branches; grass tufts.
    stone_m = p.surface("DesertStone", "rock_boulder_dry", tint=(1.0, 0.85, 0.75),
                        fallback=lambda: S._simple("DesertStone", (0.32, 0.2, 0.12, 1), 0.85, bump_scale=8.0, r=r,
                                                   bump=0.6))
    x, y = S._ring_points(p, 2400, 2.5 * r, 70.0 * r, power=1.6)
    fl = flats(x, y) + pan(x, y) * 0.3
    keep = rng.uniform(0, 1, len(x)) < np.clip(fl, 0.05, 1.0)
    x, y = x[keep], y[keep]
    size = (0.03 + rng.pareto(2.2, len(x)) * 0.06) * r
    p.scatter_lod("Stones", "stones", np.stack([x, y, height(x, y) - 0.3 * size], 1), rng.uniform(0, 1, len(x)),
                  S._upright(p, len(x), 0.3), size, 15 * r,
                  fallback=lambda: S._rock_protos(p, 6, stone_m, "stone"), axis=0, native=rng.uniform(0.5, 1.2, len(x)))
    x, y = S._ring_points(p, 60, 7.0 * r, 60.0 * r, power=1.6)
    p.scatter_lod("Debris", "debris", np.stack([x, y, height(x, y) - 0.02 * r], 1), rng.uniform(0, 1, len(x)),
                  S._upright(p, len(x), 0.05), rng.uniform(0.4, 1.0, len(x)) * r, 15 * r,
                  fallback=lambda: [S._proto("log%d" % i, S._log_mesh(S.PREFIX + "Proto_log%d" % i, rng), [deadwood])
                                    for i in range(3)], axis=0, native=rng.uniform(0.7, 1.2, len(x)), cat="props")
    grass = S._grass_mat("DryGrass", (0.32, 0.26, 0.13, 1), (0.48, 0.4, 0.22, 1), dry=(0.62, 0.55, 0.36, 1),
                         dry_share=0.9)
    gc = np.stack(S._ring_points(p, 260, 4.0 * r, 60.0 * r, power=1.3), 1)
    gx, gy = C.clustered(rng, gc, rng.integers(2, 9, len(gc)), 1.2 * u)
    keep = flats(gx, gy) > 0.35
    gx, gy = gx[keep], gy[keep]
    p.scatter_lod("Tufts", "grass", np.stack([gx, gy, height(gx, gy) - 0.02 * u], 1), rng.uniform(0, 1, len(gx)),
                  S._upright(p, len(gx), 0.15), rng.uniform(0.2, 0.55, len(gx)) * u, 14 * r,
                  fallback=lambda: S._tuft_protos(p, 5, grass), cat="plants")


def _ripple_colour(nt, col, smp):
    """Ripples keep their light and shade; the colour is the Namib's (the scan is grey beach sand)."""
    return nt.mix(1.0, SM._grey(nt, SM._lum(nt, col)), (1.45, 0.72, 0.4, 1.0), "MULTIPLY")
