# SPDX-License-Identifier: GPL-3.0-or-later
"""Salt flats (0.12): Salar de Uyuni.

Reference: a dead-flat crust of white salt broken into polygons a metre or
two across, their rims pushed up a few centimetres into crisp ridges (fresh
white crystal), the cells between a little greyer; tyre tracks of the tour
4x4s crossing it; rows of small harvested salt cones; mountains far off,
blue-grey in the haze. After rain a film of water covers it all and the
flat becomes the sky's mirror, only the ridge tops breaking it.
"""
import math

from .. import scenemats as SM, scenes as S
from . import common as C

SKY = {"rotation": 200.0, "cover": 0.3, "haze": 0.8, "time": "DAY", "day": 34.0, "golden": 10.0, "wind": 0.0}


def build(p):
    import numpy as np
    r, u, rng = p.r, p.u, p.rng
    big = 320.0 * r
    seeds = rng.uniform(0, 1000, 3)

    def height(x, y):
        h = S._waves(x, y, np.random.default_rng(int(seeds[0])), 3, 50.0 * r, 0.02 * r)
        return h + S._mountains(x, y, np.random.default_rng(int(seeds[1])), r, 150.0, 14.0, base=110.0, sharp=2.2)

    ground = p.ground(1.5 * r, big, height)
    # Tyre tracks: two pairs of wheel ruts passing near the product.
    tracks = []
    for k in range(2):
        a = rng.uniform(0, math.pi)
        off = rng.uniform(3.2, 7.0) * r * (1 if k else -1)
        nx, ny = -math.sin(a), math.cos(a)
        cx, cy = nx * off, ny * off
        tracks.append((cx - math.cos(a) * 200 * r, cy - math.sin(a) * 200 * r,
                       cx + math.cos(a) * 200 * r, cy + math.sin(a) * 200 * r))

    def salt_white(nt, col, smp):
        """Salt: near-white crystal; the scan's light and shade kept, its colour not."""
        return nt.mix(1.0, SM._grey(nt, nt.math("MULTIPLY_ADD", SM._lum(nt, col), 1.1, 0.42)),
                      (0.93, 0.92, 0.9, 1.0), "MULTIPLY")

    def ridge_white(nt, col, smp):
        """The polygons' rims: pushed-up fresh crystal, whiter than the cells."""
        v = SM._clamp(nt, nt.math("MULTIPLY_ADD", SM._lum(nt, col), -0.9, 1.25), 0.55, 1.1)
        return nt.mix(1.0, SM._grey(nt, v), (0.93, 0.92, 0.9, 1.0), "MULTIPLY")

    def finish(nt, ctx, col):
        # Ruts: compacted, a little grey.
        rut = 0.0
        for x0, y0, x1, y1 in tracks:
            _a, across, _t = C.line(ctx, x0, y0, x1, y1)
            for side in (-0.8 * u, 0.8 * u):
                band = C.band(ctx, nt.math("SUBTRACT", across, side), 0.11 * u, 0.06 * u)
                rut = band if isinstance(rut, float) else nt.math("MAXIMUM", rut, band)
        col = nt.mix(nt.math("MULTIPLY", rut, 0.45), col, (0.55, 0.53, 0.5, 1.0))
        # Far mountains go blue-grey in the haze.
        return nt.mix(nt.math("MULTIPLY", nt.smooth(ctx.dist, 140.0 * r, 320.0 * r), 0.7), col,
                      (0.5, 0.58, 0.7, 1.0))

    layers = [C.Layer("dry_mud_field_001", tile=3.0, colour=salt_white),
              C.Layer("mud_cracked_dry_03", tile=6.5, colour=ridge_white, invert=True,
                      mask=lambda c: c.nt.math("SUBTRACT", 0.85, c.patches(30.0, 0.5, 0.75, seed=1.0)), sharp=4.0),
              C.Layer("aerial_rocks_02", tint=(0.78, 0.66, 0.58), mask=lambda c: c.height(0.6 * r, 4.0 * r),
                      sharp=3.0)]
    mat = p.terrain("Salt", layers, disp=0.02 * u, pad=(1.0 * r, 1.4 * r), cavity=0.82, macro=(0.92, 1.05),
                    specular=0.5, post=finish, sheet=True, normal=1.0)
    if mat is None:
        mat = S._layered("Salt", (0.85, 0.84, 0.81, 1), 0.7, scale=0.6 / u, depth=0.3)
    S._with_materials(ground, [mat])
    # Harvested salt: rows of cones, a working pan to one side.
    salt_cone = p.surface("SaltCone", "dry_mud_field_001", colour=salt_white, tile=1.5) or mat
    parts = S._Parts()
    a = rng.uniform(0, 2 * math.pi)
    ox, oy = 11.0 * r * math.cos(a), 11.0 * r * math.sin(a)
    for row in range(3):
        for k in range(14):
            if rng.uniform() < 0.12:
                continue
            x = ox + (k - 7) * 1.9 * u * math.cos(a + math.pi / 2) + row * 2.2 * u * math.cos(a)
            y = oy + (k - 7) * 1.9 * u * math.sin(a + math.pi / 2) + row * 2.2 * u * math.sin(a)
            hgt = rng.uniform(0.7, 1.1) * u
            parts.cylinder((x, y, height(np.array([x]), np.array([y]))[0] - 0.05 * u), rng.uniform(0.6, 0.8) * u,
                           hgt, 20, 0, top_radius=0.08 * u, tilt=(rng.normal(0, 0.03), rng.normal(0, 0.03)))
    cones = S._add(p.scene, "SaltCones", parts.mesh("SaltCones"))
    S._with_materials(cones, [salt_cone])
