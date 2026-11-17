# SPDX-License-Identifier: GPL-3.0-or-later
"""Forest (0.12): a clearing in mixed European woodland with a stream.

Reference: beech-spruce forest on rolling ground (Central Europe), late
summer. Forest-grown trees: tall clear boles, narrow crowns, spaced by their
crowns (no two crowns inside each other), beech and spruce in stands rather
than salt-and-pepper, young trees in the gaps, saplings crowding the
clearing's edge where light reaches. A clear stream meanders past behind the
product: a channel with a river-pebble bed, wet mud and moss on its banks,
ferns thick along it, mossy boulders, a trunk fallen across it. The floor is
beech litter, needle litter under the spruces, moss in patches. Golden light
through haze.
"""
import math

import bpy

from .. import scenemaps, scenemats as SM, scenes as S
from . import common as C, flora

SKY = {"rotation": 235.0, "cover": 0.4, "haze": 2.0, "time": "DAY", "golden": 14.0, "wind": 0.3}


def build(p):
    import numpy as np
    r, u, rng = p.r, p.u, p.rng
    seeds = rng.uniform(0, 1000, 4)
    # The stream: beside the product, running away from the usual shots' side
    # of it, so a low camera looks along it (a channel across the view hides).
    view = np.array([-5.2, 6.0]) / math.hypot(5.2, 6.0)
    left = np.array([-view[1], view[0]])
    centre = left * 5.0 * r
    path = C.meander(rng, tuple(centre - view * 90 * r), tuple(centre + view * 90 * r), 160, 4.0 * r)
    pd = np.hypot(path[:, 0], path[:, 1])
    push = np.maximum(1.0, (4.2 * r) / np.maximum(pd, 1e-6))          # never under the product
    path = path * push[:, None]
    half = 1.25 * u                                    # half the channel's width
    depth = 0.6 * u
    bank = 2.6 * u

    def base(x, y):
        h = S._waves(x, y, np.random.default_rng(int(seeds[0])), 5, 25.0 * r, 0.5 * r) * S._pad(x, y, 6 * r, 20 * r)
        return h + S._mountains(x, y, np.random.default_rng(int(seeds[1])), r, 45.0, 9.0, base=70.0, sharp=1.0)

    def channel(x, y):
        d, along = C.polyline_distance(x, y, path, maxd=half * 2 + bank * 3)
        w = half * (1.0 + 0.3 * np.sin(along / (7.0 * u) + seeds[2]))
        cut = 1.0 - np.clip((d - 0.55 * w) / (w * 0.45 + bank), 0.0, 1.0)
        cut = cut * cut * (3 - 2 * cut)
        return d, w, cut

    def height(x, y):
        h = base(x, y)
        near = (np.abs(x) < 130 * r) & (np.abs(y) < 130 * r)
        if near.any():
            _d, _w, cut = channel(x[near], y[near])
            h[near] -= depth * cut
        return h

    # Trees first (the needle litter under spruces is a ground mask).
    trees = _place_trees(p, height, path, half, bank)

    def needles(x, y):
        out = np.zeros_like(x)
        cx, cy = trees["conifer"]
        if len(cx) == 0:
            return out
        grid = S._Grid(x, y, 6.0 * u)
        for tx, ty in zip(cx, cy):
            idx = grid.near(tx, ty, 5.0 * u)
            if len(idx):
                d = np.hypot(x[idx] - tx, y[idx] - ty)
                out[idx] = np.maximum(out[idx], np.clip(1.0 - d / (4.5 * u), 0.0, 1.0))
        return out

    def bed(x, y):
        d, w, _c = channel(x, y)
        return np.clip(1.0 - (d - 0.7 * w) / (0.35 * w), 0.0, 1.0)

    def banks(x, y):
        d, w, _c = channel(x, y)
        return np.clip(1.0 - np.abs(d - w) / bank, 0.0, 1.0) ** 0.7

    ground = p.ground(1.5 * r, 140.0 * r, height, attrs={"needles": needles, "bed": bed, "bank": banks})
    layers = [C.Layer("forest_leaves_03", tint=(0.85, 0.85, 0.82)),
              C.Layer("forest_leaves_04", mask=lambda c: c.attr("needles", 0.1, 0.6), sharp=4.0),
              C.Layer("brown_mud_leaves_01", mask=lambda c: c.maxof(c.patches(7.0, 0.52, 0.66, seed=3.0),
                                                                      c.mul(c.attr("bank"), 0.7)), sharp=5.0),
              C.Layer("brown_mud_02", mask=lambda c: c.attr("bank", 0.35, 0.8), sharp=6.0, tint=(0.8, 0.78, 0.74)),
              C.Layer("ganges_river_pebbles", mask=lambda c: c.attr("bed", 0.2, 0.6), sharp=8.0, lift=0.05)]
    floor = p.terrain("ForestFloor", layers, disp=0.06 * u, pad=(1.0 * r, 1.4 * r), cavity=0.55,
                      wet_mask=lambda c: c.maxof(c.attr("bank", 0.3, 0.9), c.attr("bed")), puddles=0.6)
    if floor is None:
        floor = S._layered("ForestFloor", (0.09, 0.07, 0.04, 1), 0.9, scale=2.0 / u, depth=0.7)
    S._distant(floor, r, 55.0 * r, 80.0 * r, (0.018, 0.035, 0.016, 1), 0.95)
    S._with_materials(ground, [floor])
    _stream(p, path, height, depth, half, bank)
    _scatter_trees(p, trees)
    _undergrowth(p, height, path, half, bank)


# --- Trees -------------------------------------------------------------------------------------

def _place_trees(p, height, path, half, bank):
    """Positions per class: canopy beech, canopy spruce (stands by noise),
    understory, saplings. Crowns never inside each other; none in the stream."""
    import numpy as np
    r, u, rng = p.r, p.u, p.rng
    n = 14000
    a = rng.uniform(0, 2 * math.pi, n)
    d = np.sqrt(rng.uniform((S.CLEAR * r) ** 2, (105.0 * r) ** 2, n))
    x, y = d * np.cos(a), d * np.sin(a)
    dist, _al = C.polyline_distance(x, y, path)
    ok = dist > half + bank + 1.5 * u
    x, y = x[ok], y[ok]
    stands = S._waves(x, y, np.random.default_rng(int(rng.uniform(0, 1e6))), 3, 30.0 * r, 1.0)
    spruce = stands > 0.05
    crown = np.where(spruce, 3.4 * u, 4.6 * u) * rng.uniform(0.85, 1.15, len(x))
    keep = C.spaced(x, y, crown)
    x, y, spruce = x[keep], y[keep], spruce[keep]
    # Understory: young beech in the gaps (away from big trunks), saplings at the clearing's edge.
    m = 1400
    ua = rng.uniform(0, 2 * math.pi, m)
    ud = np.sqrt(rng.uniform((9.0 * r) ** 2, (60.0 * r) ** 2, m))
    ux, uy = ud * np.cos(ua), ud * np.sin(ua)
    far_ok = np.ones(m, bool)
    for tx, ty in zip(x, y):
        far_ok &= (ux - tx) ** 2 + (uy - ty) ** 2 > (2.2 * u) ** 2
    du, _ = C.polyline_distance(ux, uy, path)
    far_ok &= du > half + 0.8 * bank
    ux, uy = ux[far_ok], uy[far_ok]
    young = C.spaced(ux, uy, 4.5 * u)
    ux, uy = ux[young], uy[young]
    ca = rng.uniform(0, 2 * math.pi, 26)                # saplings in clumps where a gap lets light in
    cd = (S.CLEAR - 3.0) * r + rng.gamma(2.0, 2.0, 26) * r
    sx, sy = C.clustered(rng, np.stack([cd * np.cos(ca), cd * np.sin(ca)], 1), rng.integers(3, 16, 26), 1.6 * u)
    far_enough = np.hypot(sx, sy) > 7.0 * r
    sx, sy = sx[far_enough], sy[far_enough]
    ds, _ = C.polyline_distance(sx, sy, path)
    ok = ds > half + 0.6 * bank
    sx, sy = sx[ok], sy[ok]
    return {"beech": (x[~spruce], y[~spruce]), "conifer": (x[spruce], y[spruce]), "young": (ux, uy),
            "saplings": (sx, sy), "height": height}


def _scatter_trees(p, trees):
    import numpy as np
    r, u, rng = p.r, p.u, p.rng
    height = trees["height"]
    beech_atlas = scenemaps.leaves("LeafSet005")
    spruce_atlas = scenemaps.leaves("LeafSet019")
    bark_b = SM.bark(p, "BeechBark", "bark_brown_02", moss=0.35, sat=0.25, value=1.25)
    bark_s = SM.bark(p, "SpruceBark", "pine_bark", moss=0.15)
    leaves_b = SM.foliage(p, "BeechLeaves", "LeafSet005", autumn=0.04)
    leaves_s = SM.foliage(p, "SpruceNeedles", "LeafSet019", tint=(0.85, 0.95, 0.85), translucency=0.2)
    old_bark, old_needles = S._bark_needles(p)
    bark_b, bark_s = bark_b or old_bark, bark_s or old_bark
    leaves_b, leaves_s = leaves_b or old_needles, leaves_s or old_needles

    def protos(kind, n, far, h):
        out = []
        for i in range(n):
            name = "%s%s%d" % (kind, "far" if far else "", i)
            if kind == "beech" and beech_atlas:
                me = flora.broadleaf(S.PREFIX + "Proto_" + name, rng, beech_atlas["rects"], beech_atlas["length"], h,
                                     p.detail, spread=0.3, rise=(0.6, 1.3), limbs=(10, 15), far=far)
                out.append(S._proto(name, me, [bark_b, leaves_b]))
            elif kind == "spruce" and spruce_atlas:
                me = flora.conifer(S.PREFIX + "Proto_" + name, rng, spruce_atlas["rects"], h, p.detail, far=far)
                out.append(S._proto(name, me, [bark_s, leaves_s]))
        return out or S._conifer_protos(p, 3, old_bark, old_needles)

    for kind, key, h, near_n, far_n, role in (("beech", "beech", 26.0, 4, 2, None),
                                              ("spruce", "conifer", 30.0, 3, 2, "trees")):
        x, y = trees[key]
        if len(x) == 0:
            continue
        z = height(x, y) - 0.25 * u
        dist = np.hypot(x, y)
        sizes = rng.uniform(0.8, 1.2, len(x)) * h * u * np.clip(0.75 + 0.25 * dist / (40 * r), 0.75, 1.15)
        rot = S._upright(p, len(x), 0.025)
        near = dist < 34.0 * r
        kit = role and p.assets(role)
        for tag, sel, far in (("", near, False), ("_far", ~near, True)):
            if not sel.any():
                continue
            if kit:
                p.scatter_lod("Spruce" + tag, role, np.stack([x[sel], y[sel], z[sel]], 1),
                              rng.uniform(0, 1, sel.sum()) ** 2.5, rot[sel], sizes[sel], 19 * r,
                              native=sizes[sel] / (h * u) * 1.0, lods={"HIGH": (2, 2), "MEDIUM": (2, 2)},
                              cat="trees")
                continue
            made = protos(kind, near_n if not far else far_n, far, h)
            idx = rng.integers(0, len(made), sel.sum())
            ext = np.array([max(1e-4, S._size(o)[2]) for o in made])
            sc = (sizes[sel] / ext[idx])[:, None] * np.ones((1, 3))
            p.scatter(kind.title() + tag, made, np.stack([x[sel], y[sel], z[sel]], 1), idx, rot[sel], sc,
                      share=far, cat="trees")             # near trees all drawn; the distant ones by detail
    # Young beech (6-12 m) in the gaps, saplings (1-3 m) at the clearing's edge.
    ux, uy = trees["young"]
    if len(ux) and beech_atlas:
        made = [S._proto("young%d" % i, flora.broadleaf(S.PREFIX + "Proto_young%d" % i, rng, beech_atlas["rects"],
                                                        beech_atlas["length"], 9.0, p.detail, spread=0.35,
                                                        rise=(0.5, 1.1), limbs=(6, 10), leaf_scale=3.2,
                                                        leaf_k=0.3),
                         [bark_b, leaves_b]) for i in range(3)]
        idx = rng.integers(0, len(made), len(ux))
        ext = np.array([S._size(o)[2] for o in made])
        hs = rng.uniform(0.6, 1.3, len(ux)) * 9.0 * u
        p.scatter("Understory", made, np.stack([ux, uy, height(ux, uy) - 0.1 * u], 1), idx,
                  S._upright(p, len(ux), 0.05), (hs / ext[idx])[:, None] * np.ones((1, 3)), cat="trees")
    sx, sy = trees["saplings"]
    if len(sx):
        def code_saplings():
            out = []
            for i in range(3):
                if i < 2 and spruce_atlas:
                    me = flora.conifer(S.PREFIX + "Proto_sap%d" % i, rng, spruce_atlas["rects"], 2.5, p.detail,
                                       width=0.28)
                    out.append(S._proto("sap%d" % i, me, [bark_s, leaves_s]))
                elif beech_atlas:
                    me = flora.broadleaf(S.PREFIX + "Proto_sap%d" % i, rng, beech_atlas["rects"],
                                         beech_atlas["length"], 2.6, p.detail, spread=0.4, limbs=(4, 7),
                                         leaf_scale=2.4, leaf_k=0.1)
                    out.append(S._proto("sap%d" % i, me, [bark_b, leaves_b]))
            return out
        p.scatter_lod("Saplings", "saplings", np.stack([sx, sy, height(sx, sy)], 1), rng.uniform(0, 1, len(sx)),
                      S._upright(p, len(sx), 0.06), rng.uniform(1.0, 3.2, len(sx)) * u, 15 * r,
                      fallback=code_saplings, native=rng.uniform(0.7, 1.3, len(sx)))


# --- The stream ------------------------------------------------------------------------------------

def _stream(p, path, height, depth, half, bank):
    """The water: a ribbon along the path at its own level (a little below
    the banks), u across and v downstream in metres (the flow)."""
    import numpy as np
    u = p.u
    path = np.asarray(path)
    m = flora._Mesh()
    rows = []
    seg = np.diff(path, axis=0)
    seglen = np.hypot(seg[:, 0], seg[:, 1])
    along = np.concatenate([[0.0], np.cumsum(seglen)])
    ground_mid = height(path[:, 0].copy(), path[:, 1].copy())
    level = ground_mid + depth * 0.45
    for k in range(len(path)):
        d = seg[min(k, len(seg) - 1)]
        d = d / max(1e-9, np.hypot(*d))
        nrm = np.array([-d[1], d[0]])
        w = half + bank * 0.85                         # under the banks: the ground draws the waterline
        row = []
        for q in np.linspace(-1.0, 1.0, 9):
            x, y = path[k] + nrm * w * q
            row.append(m.vert((float(x), float(y), float(level[k]))))
        rows.append((row, along[k]))
    for (ra, va), (rb, vb) in zip(rows, rows[1:]):
        for i in range(len(ra) - 1):
            ua, ub = (i / 8.0) * 2 * (half + bank * 0.85), ((i + 1) / 8.0) * 2 * (half + bank * 0.85)
            m.face((ra[i], ra[i + 1], rb[i + 1], rb[i]), ((ua, va), (ub, va), (ub, vb), (ua, vb)), 0)
    obj = S._add(p.scene, "Stream", m.mesh(S.PREFIX + "Stream"))
    S._with_materials(obj, [SM.water(p, "Stream", colour=(0.03, 0.05, 0.035), flow=(0.0, 1.0), speed=0.45)])
    obj.visible_shadow = False


# --- Undergrowth, rocks, logs ------------------------------------------------------------------------

def _undergrowth(p, height, path, half, bank):
    import numpy as np
    r, u, rng = p.r, p.u, p.rng
    path = np.asarray(path)
    # Ferns: thick along the banks and in the shade, clustered.
    pick = rng.integers(0, len(path), 160)
    side = rng.choice([-1.0, 1.0], 160)
    seg = np.diff(path, axis=0)
    nrm = np.stack([-seg[:, 1], seg[:, 0]], 1) / np.maximum(1e-9, np.hypot(seg[:, 0], seg[:, 1]))[:, None]
    nrm = np.vstack([nrm, nrm[-1:]])
    centres = path[pick] + nrm[pick] * (side * (half + bank * rng.uniform(0.6, 2.5, 160)))[:, None]
    cx2 = rng.uniform(-60, 60, (70, 2)) * r
    cx2 = cx2[np.hypot(cx2[:, 0], cx2[:, 1]) > 7 * r]
    fx, fy = C.clustered(rng, np.vstack([centres, cx2]), rng.integers(4, 14, len(centres) + len(cx2)), 1.3 * u)
    dd, _ = C.polyline_distance(fx, fy, path)
    ok = (dd > half + 0.4 * u) & (np.hypot(fx, fy) > 2.6 * r)
    fx, fy = fx[ok], fy[ok]
    fern_m = SM.foliage(p, "FernFronds", "LeafSet026", tint=(0.75, 0.9, 0.7), translucency=0.4)
    green = _fern_material(p)

    def ferns():
        return [S._proto("fern%d" % i, flora.fern(S.PREFIX + "Proto_fern%d" % i, p.rng, 9 + i * 2, p.detail),
                         [green, green]) for i in range(3)]
    del fern_m
    p.scatter_lod("Ferns", "cover", np.stack([fx, fy, height(fx, fy) - 0.02 * u], 1), rng.uniform(0, 1, len(fx)),
                  S._upright(p, len(fx), 0.12), rng.uniform(0.5, 1.1, len(fx)) * u, 16 * r, fallback=ferns,
                  native=rng.uniform(0.7, 1.4, len(fx)), cat="plants")
    # Grass and herbs in the clearing's light, thinning into the wood.
    ga = rng.uniform(0, 2 * math.pi, 2600)
    gd = np.sqrt(rng.uniform((1.4 * r) ** 2, (26.0 * r) ** 2, 2600))
    gx, gy = C.clustered(rng, np.stack([gd * np.cos(ga), gd * np.sin(ga)], 1), rng.integers(6, 40, 2600), 0.7 * u)
    gd = np.hypot(gx, gy)
    dg, _ = C.polyline_distance(gx, gy, path)
    keep = (dg > half + 0.3 * u) & (gd > 1.15 * r)
    keep &= rng.uniform(0, 1, len(gx)) < np.clip(np.minimum((gd - 1.15 * r) / (1.5 * r), 1.4 - gd / (16 * r)), 0.15, 1)
    gx, gy = gx[keep], gy[keep]
    cover = S._grass_mat("ForestGrass")
    p.scatter_lod("Grass", "cover", np.stack([gx, gy, height(gx, gy) - 0.02 * u], 1), rng.uniform(0, 1, len(gx)),
                  S._upright(p, len(gx), 0.12), rng.uniform(0.15, 0.45, len(gx)) * u, 14 * r,
                  fallback=lambda: S._tuft_protos(p, 6, cover), native=rng.uniform(0.7, 1.3, len(gx)), cat="plants")
    # Boulders along the stream (mossy), scattered rocks, pebbles in the bed.
    stone = p.surface("MossyRock", "mossy_rock", fallback=lambda: S._simple("Stone", (0.11, 0.11, 0.10, 1), 0.85,
                                                                            bump_scale=4.0, r=u, bump=0.5))
    pick = rng.integers(0, len(path), 90)
    side = rng.uniform(-1.4, 1.4, 90)
    bx = path[pick, 0] + nrm[pick, 0] * side * (half + bank)
    by = path[pick, 1] + nrm[pick, 1] * side * (half + bank)
    ox, oy = S._ring_points(p, 160, 4.5 * r, 50.0 * r, power=1.3)
    rx, ry = np.concatenate([bx, ox]), np.concatenate([by, oy])
    ok = np.hypot(rx, ry) > 3.0 * r
    rx, ry = rx[ok], ry[ok]
    size = (0.25 + rng.pareto(2.0, len(rx)) * 0.35) * u
    p.scatter_lod("Rocks", "rocks", np.stack([rx, ry, height(rx, ry) - 0.25 * size], 1), rng.uniform(0, 1, len(rx)),
                  S._upright(p, len(rx), 0.25), size, 15 * r, fallback=lambda: S._rock_protos(p, 6, stone), axis=0,
                  native=rng.uniform(0.6, 1.5, len(rx)))
    k = rng.integers(0, len(path), 1400)
    q = rng.uniform(-0.8, 0.8, 1400)
    px = path[k, 0] + nrm[k, 0] * q * half
    py = path[k, 1] + nrm[k, 1] * q * half
    ok = np.hypot(px, py) < 70 * r
    px, py = px[ok], py[ok]
    psize = (0.05 + rng.pareto(2.5, len(px)) * 0.06) * u
    p.scatter_lod("Pebbles", "rocks", np.stack([px, py, height(px, py) - 0.3 * psize], 1), rng.uniform(0, 1, len(px)),
                  S._tumbled(p, len(px)), psize, 10 * r, fallback=lambda: S._rock_protos(p, 6, stone, "pebble"),
                  axis=0, lods=(2, 2))
    # Fallen trunks: one across the stream, others on the floor; stumps; litter.
    logs = SM.bark(p, "LogBark", "bark_brown_02", moss=0.3, sat=0.5, value=0.9) or S._simple(
        "Logs", (0.10, 0.065, 0.04, 1), 0.9, bump_scale=8.0, r=u, bump=0.4)
    lx, ly = S._ring_points(p, 70, 6.0 * r, 40.0 * r, power=1.2)
    k = int(len(path) * 0.62)
    d = path[k + 1] - path[k]
    yaw_across = math.atan2(d[1], d[0]) + math.pi / 2 + rng.uniform(-0.3, 0.3)
    lx = np.concatenate([[path[k, 0]], lx])
    ly = np.concatenate([[path[k, 1]], ly])
    rot = S._upright(p, len(lx), 0.05)
    rot[0, 2] = yaw_across
    lsize = rng.uniform(2.0, 4.5, len(lx)) * u
    lsize[0] = 6.5 * u
    lz = height(lx, ly) - 0.03 * u
    lz[0] = height(np.array([path[k, 0] + math.cos(yaw_across) * 2.5 * u]),
                   np.array([path[k, 1] + math.sin(yaw_across) * 2.5 * u]))[0] - 0.05 * u
    p.scatter_lod("Debris", "debris", np.stack([lx, ly, lz], 1), rng.uniform(0, 1, len(lx)), rot, lsize, 15 * r,
                  fallback=lambda: [S._proto("log%d" % i, S._log_mesh(S.PREFIX + "Proto_log%d" % i, rng), [logs])
                                    for i in range(3)], axis=0, native=rng.uniform(0.8, 1.2, len(lx)), cat="props")
    tx, ty = S._ring_points(p, 2600, 1.2 * r, 30.0 * r, power=1.3)
    nl = len(tx)
    p.scatter_lod("Litter", "litter", np.stack([tx, ty, height(tx, ty) - 0.01 * u], 1), rng.uniform(0, 1, nl),
                  np.stack([rng.normal(0, 0.08, nl), rng.normal(0, 0.08, nl), rng.uniform(0, 6.3, nl)], 1),
                  rng.uniform(0.2, 0.9, nl) * u, 10 * r, axis=0, native=rng.uniform(0.7, 1.3, nl), cat="props")


def _fern_material(p):
    """Fern fronds: green, translucent where the light comes through, glossy
    upper sides, each fern a little different."""
    m = SM._material("Fern")
    nt = SM._Nodes(m.node_tree)
    out = nt.output()
    info = nt.new("ShaderNodeObjectInfo")
    tone = nt.mix(info.outputs["Random"], (0.03, 0.075, 0.018, 1), (0.075, 0.13, 0.03, 1))
    tip = nt.new("ShaderNodeSeparateXYZ")
    nt.link(nt.obj_pos(), tip.inputs[0])
    tone = nt.mix(nt.smooth(tip.outputs["Z"], 0.3, 1.0), tone, (0.11, 0.16, 0.04, 1))
    pr = nt.new("ShaderNodeBsdfPrincipled")
    nt.link(tone, pr.inputs["Base Color"])
    pr.inputs["Roughness"].default_value = 0.45
    SM._set(pr, specular=0.5)
    tr = nt.new("ShaderNodeBsdfTranslucent")
    nt.link(nt.mix(1.0, tone, (1.4, 1.6, 0.8, 1), "MULTIPLY"), tr.inputs["Color"])
    mix = nt.new("ShaderNodeMixShader", in_Fac=0.35)
    nt.link(pr.outputs[0], mix.inputs[1])
    nt.link(tr.outputs[0], mix.inputs[2])
    nt.link(mix.outputs[0], out.inputs["Surface"])
    if hasattr(m, "use_backface_culling"):
        m.use_backface_culling = False
    m["plinth_bsdf"] = pr.name
    m["plinth_foliage"] = True
    return m
