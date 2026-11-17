# SPDX-License-Identifier: GPL-3.0-or-later
"""Modern house (0.12): an architect's house at golden hour, magazine style.

Reference: contemporary residential architecture photography. Two white
rendered volumes (scanned stucco) and a dark timber-clad box above, a
cantilevered concrete roof with an LED line under it, a full-height glass
front into a furnished room glowing warm, a stacked-stone feature wall by
the door; a big-format paved drive with lit bollards; a striped lawn,
gravel beds with ornamental grass, clipped box balls, a Japanese maple by
the entrance and trees behind. Lamps and the room come on with Time of day.
"""
import math

import bpy

from .. import scenemaps, scenemats as SM, scenes as S, studio
from . import common as C, flora

SKY = {"rotation": 120.0, "cover": 0.35, "haze": 1.6, "time": "GOLDEN", "golden": 12.0, "wind": 0.2}


def build(p):
    import numpy as np
    r, u, rng, scene = p.r, p.u, p.rng, p.scene
    flat = (lambda x, y: x * 0.0)
    back = 9.0 * u
    ground = p.ground(1.5 * r, 500.0 * r, flat)

    def stripes(nt, ctx, col):
        """Mower stripes: alternate lanes, the grass leaning toward or away from you."""
        lane = nt.math("SINE", nt.math("DIVIDE", ctx.x, 0.42 * u))
        k = nt.math("MULTIPLY_ADD", nt.smooth(lane, -0.15, 0.15), 0.12, 0.94)
        return nt.mix(1.0, col, SM._grey(nt, k), "MULTIPLY")

    lawn = p.terrain("Lawn", [C.Layer("leafy_grass", tint=(0.42, 0.78, 0.26))], disp=0.02 * u,
                     pad=(1.0 * r, 1.4 * r), post=stripes, cavity=0.7, sheen=0.3)
    S._with_materials(ground, [lawn or S._layered("Lawn", (0.07, 0.15, 0.04, 1), 0.9, scale=14.0 / u)])

    paving = p.surface("Paving", "large_grey_tiles", tile=3.0, normal=1.2,
                       fallback=lambda: S._simple("Paving", (0.48, 0.47, 0.45, 1), 0.75, bump_scale=8.0, r=u))
    drive = S._Parts()
    drive.box((0.0, -1.0 * u, 0.02 * u), (9.0 * u, 16.0 * u, 0.04 * u))
    drive.box((0.0, -14.0 * u, 0.01 * u), (5.0 * u, 12.0 * u, 0.02 * u))
    for k in range(6):                                 # stepping stones across the lawn to the terrace
        drive.box((5.2 * u + k * 0.95 * u, back - 2.8 * u + rng.uniform(-0.08, 0.08) * u, 0.015 * u),
                  (0.75 * u, 0.55 * u, 0.03 * u), turn=rng.uniform(-0.04, 0.04))
    S._with_materials(S._add(scene, "Driveway", drive.mesh("Driveway")), [paving])
    gravel = S._Parts()
    gravel.box((6.0 * u, back + 0.9 * u, 0.015 * u), (10.0 * u, 1.6 * u, 0.03 * u))
    gravel.box((-7.5 * u, back - 0.6 * u, 0.015 * u), (7.0 * u, 1.2 * u, 0.03 * u))
    S._with_materials(S._add(scene, "Gravel", gravel.mesh("Gravel")),
                      [p.surface("Gravel", "gravel_floor_02", tile=2.0,
                                 fallback=lambda: S._simple("Gravel", (0.35, 0.34, 0.32, 1), 0.9, bump_scale=60.0,
                                                            r=u, bump=0.6))])

    walls = S._Parts()                                 # 0 render, 1 frames, 2 timber, 3 concrete, 4 stone
    walls.box((0.0, back + 4.0 * u, 0.25 * u), (22.0 * u, 10.0 * u, 0.5 * u), slot=3)
    walls.box((-7.5 * u, back + 4.0 * u, 2.0 * u), (7.0 * u, 8.0 * u, 3.5 * u))
    walls.box((-4.05 * u, back + 0.05 * u, 2.0 * u), (0.3 * u, 0.12 * u, 3.5 * u), slot=4)
    walls.box((-9.6 * u, back - 0.05 * u, 2.0 * u), (2.8 * u, 0.12 * u, 3.5 * u), slot=4)   # stone feature wall
    walls.box((6.0 * u, back + 6.0 * u, 2.0 * u), (10.0 * u, 4.0 * u, 3.5 * u))
    walls.box((2.0 * u, back + 3.5 * u, 4.1 * u), (20.0 * u, 11.0 * u, 0.45 * u), slot=3)
    walls.box((-7.5 * u, back + 4.0 * u, 5.9 * u), (7.4 * u, 8.4 * u, 3.0 * u), slot=2)
    walls.box((-7.5 * u, back + 4.0 * u, 7.5 * u), (7.8 * u, 8.8 * u, 0.2 * u), slot=3)
    for k in range(-6, 7):
        walls.box((6.0 * u + k * 0.72 * u, back + 1.9 * u, 2.0 * u), (0.06 * u, 0.14 * u, 3.4 * u), slot=1)
    walls.box((6.0 * u, back + 1.9 * u, 0.35 * u), (9.6 * u, 0.16 * u, 0.1 * u), slot=1)
    walls.box((6.0 * u, back + 1.9 * u, 3.7 * u), (9.6 * u, 0.16 * u, 0.1 * u), slot=1)
    walls.box((-7.5 * u, back - 0.03 * u, 1.6 * u), (1.2 * u, 0.08 * u, 2.6 * u), slot=2)       # front door
    walls.box((-6.75 * u, back - 0.1 * u, 1.2 * u), (0.04 * u, 0.06 * u, 0.5 * u), slot=1)       # its pull
    walls.box((10.95 * u, back + 6.0 * u, 2.0 * u), (0.12 * u, 0.12 * u, 3.4 * u), slot=1)       # downpipe
    house = S._add(scene, "House", walls.mesh("House"))
    if p.detail in ("HIGH", "ULTRA"):
        S._bevel(house, 0.012 * u, 3 if p.detail == "ULTRA" else 2)
    render = p.surface("Render", "white_stucco", tile=2.0, tint=(1.0, 0.98, 0.95), normal=0.7, rough=(1.0, 0.05),
                       fallback=lambda: S._layered("Render", (0.84, 0.83, 0.8, 1), 0.62, scale=55.0 / u, depth=0.18))
    frames = S._simple("Frames", (0.025, 0.025, 0.028, 1), 0.32, metallic=1.0)
    timber = p.surface("Timber", "weathered_plank_siding", tile=1.6, tint=(0.75, 0.62, 0.52),
                       fallback=lambda: S._simple("Timber", (0.2, 0.1, 0.05, 1), 0.6, bump_scale=6.0, r=u))
    concrete = p.surface("Concrete", "brushed_concrete", tile=2.5,
                         fallback=lambda: S._simple("Concrete", (0.5, 0.49, 0.46, 1), 0.75, bump_scale=8.0, r=u))
    stone = p.surface("StoneWall", "stacked_stone_wall", tile=2.0, normal=1.4,
                      fallback=lambda: S._simple("StoneWall", (0.35, 0.3, 0.26, 1), 0.85, bump_scale=6.0, r=u))
    S._with_materials(house, [render, frames, timber, concrete, stone])

    glass_parts = S._Parts()
    glass_parts.box((6.0 * u, back + 1.95 * u, 2.0 * u), (9.4 * u, 0.04 * u, 3.4 * u))
    S._with_materials(S._add(scene, "Glass", glass_parts.mesh("Glass", uv=None)), [S._window_glass()])
    _room(p, back, frames)
    _lights(p, back, frames)
    _garden(p, back)


def _room(p, back, frames):
    """The room behind the glass: oak floor, a warm lit back wall, pendants,
    a sofa, a dining table, a big plant, the room light (on with the evening)."""
    u, scene, rng = p.u, p.scene, p.rng
    inside = bpy.data.lights.get(S.PREFIX + "Inside") or bpy.data.lights.new(S.PREFIX + "Inside", "AREA")
    inside.shape, inside.size, inside.size_y = "RECTANGLE", 8.0 * u, 1.6 * u
    inside.energy, inside.color = 70.0 * u * u, (1.0, 0.74, 0.47)
    studio.tune_light(inside)
    lamp = S._add(scene, "Inside", inside)
    lamp.location = (6.0 * u, back + 2.9 * u, 3.6 * u)
    lamp["plinth_lamp"], lamp["plinth_lamp_day"] = 70.0 * u * u, 0.35
    for attr in ("visible_glossy", "visible_transmission"):
        if hasattr(lamp, attr):
            setattr(lamp, attr, False)
    room = S._Parts()                                  # 0 lit wall, 1 oak, 2 pendants, 3 fabric, 4 dark
    room.box((6.0 * u, back + 3.96 * u, 2.0 * u), (9.6 * u, 0.04 * u, 3.4 * u), slot=0)
    room.box((6.0 * u, back + 2.95 * u, 0.52 * u), (9.6 * u, 2.0 * u, 0.04 * u), slot=1)
    for k in range(5):
        room.box(((2.4 + 1.8 * k) * u, back + 3.0 * u, 3.1 * u), (0.32 * u, 0.32 * u, 0.06 * u), slot=2)
        room.cylinder(((2.4 + 1.8 * k) * u, back + 3.0 * u, 3.13 * u), 0.004 * u, 0.6 * u, 4, 4)
    room.box((3.6 * u, back + 3.45 * u, 0.78 * u), (2.6 * u, 0.9 * u, 0.42 * u), slot=3)        # sofa
    room.box((3.6 * u, back + 3.8 * u, 1.05 * u), (2.6 * u, 0.22 * u, 0.6 * u), slot=3)
    room.box((7.8 * u, back + 3.0 * u, 1.28 * u), (2.0 * u, 0.9 * u, 0.05 * u), slot=1)         # dining table
    for dx in (-0.9, 0.9):
        for dy in (-0.38, 0.38):
            room.box(((7.8 + dx) * u, back + (3.0 + dy) * u, 0.9 * u), (0.05 * u, 0.05 * u, 0.74 * u), slot=4)
    room_obj = S._add(scene, "Room", room.mesh("Room"))
    oak = p.surface("Oak", "oak_veneer_01", tile=1.8,
                    fallback=lambda: S._simple("Oak", (0.45, 0.3, 0.17, 1), 0.4, bump_scale=6.0, r=u))
    fabric = S._layered("Fabric", (0.28, 0.25, 0.22, 1), 0.85, scale=90.0 / u, depth=0.3, sheen=0.6)
    S._with_materials(room_obj, [S._emissive("RoomWall", (1.0, 0.72, 0.45, 1), 0.6, (0.62, 0.55, 0.47, 1), day=0.3),
                                 oak, S._emissive("Pendant", (1.0, 0.78, 0.5, 1), 18.0, day=0.15), fabric, frames])
    atlas = scenemaps.leaves("LeafSet022") or scenemaps.leaves("LeafSet005")
    if atlas:
        leaves = SM.foliage(p, "PlantLeaves", "LeafSet005", tint=(0.8, 1.0, 0.8))
        me = flora.broadleaf(S.PREFIX + "Proto_figtree", rng, atlas["rects"], atlas["length"], 2.2, p.detail,
                             spread=0.45, limbs=(5, 8), leaf_scale=3.0, leaf_k=0.12)
        plant = S._add(scene, "Plant", me)
        S._with_materials(plant, [frames, leaves])
        plant.location = (10.0 * u, back + 3.4 * u, 0.54 * u)
        plant.scale = (u, u, u)


def _lights(p, back, frames):
    u, scene = p.u, p.scene
    strip = S._Parts()
    strip.box((2.0 * u, back - 1.9 * u, 3.86 * u), (19.6 * u, 0.06 * u, 0.025 * u))
    S._with_materials(S._add(scene, "Strip", strip.mesh("Strip")), [S._emissive("LED", (1.0, 0.86, 0.68, 1), 30.0,
                                                                                 day=0.1)])
    posts, caps = S._Parts(), S._Parts()
    for side in (-1, 1):
        for k in range(4):
            x, y = side * 2.9 * u, (-19.0 + 3.0 * k) * u
            posts.cylinder((x, y, 0.0), 0.07 * u, 0.7 * u, 16, 0)
            caps.cylinder((x, y, 0.7 * u), 0.072 * u, 0.06 * u, 16, 0)
            S._lamp(scene, "Bollard%s%d" % ("L" if side < 0 else "R", k), (x, y, 0.74 * u), (1.0, 0.8, 0.55),
                    12.0 * u * u, 0.03 * u, day=0.0)
    S._with_materials(S._add(scene, "Bollards", posts.mesh("Bollards")), [frames])
    S._with_materials(S._add(scene, "BollardCaps", caps.mesh("BollardCaps")),
                      [S._emissive("BollardCap", (1.0, 0.8, 0.55, 1), 25.0, day=0.05)])
    for k, x in enumerate((-8.6, -6.4)):             # wall lights either side of the door
        S._lamp(scene, "Wall%d" % k, (x * u, back - 0.25 * u, 2.4 * u), (1.0, 0.78, 0.5), 25.0 * u * u, 0.04 * u,
                day=0.0)


def _garden(p, back):
    import numpy as np
    r, u, rng, scene = p.r, p.u, p.rng, p.scene
    frames = bpy.data.materials.get(S.PREFIX + "Frames")
    # Planters with box balls along the drive.
    planters = [(side * 5.2 * u, (-4.0 + 3.5 * k) * u) for side in (-1, 1) for k in range(3)]
    if p.assets("planters"):
        co = [(x, y, 0.0) for x, y in planters]
        p.scatter("Planters", p.assets("planters"), co, [k % len(p.assets("planters")) for k in range(len(co))],
                  [(0.0, 0.0, math.pi / 2)] * len(co), [(u, u, u)] * len(co), share=False, cat="props")
        top = 0.85 * S._size(p.assets("planters")[0])[2] * u
    else:
        box = S._Parts()
        for x, y in planters:
            box.box((x, y, 0.35 * u), (1.0 * u, 2.6 * u, 0.7 * u), slot=0)
        S._with_materials(S._add(scene, "Planters", box.mesh("Planters")),
                          [S._simple("Planter", (0.05, 0.05, 0.055, 1), 0.4, metallic=0.7)])
        top = 0.7 * u
    box_atlas = scenemaps.leaves("LeafSet005")
    if box_atlas:
        green = SM.foliage(p, "BoxLeaves", "LeafSet005", tint=(0.55, 0.75, 0.5), translucency=0.2)
        balls = [S._proto("ball%d" % i, flora.ball(S.PREFIX + "Proto_ball%d" % i, rng, box_atlas["rects"], 0.09,
                                                   {"LOW": 1200, "MEDIUM": 2200, "HIGH": 3200,
                                                    "ULTRA": 5200}[p.detail]), [frames, green]) for i in range(2)]
        bx, by = [], []
        for x, y in planters:
            for dy in (-0.75, 0.0, 0.75):
                bx.append(x)
                by.append(y + dy * u)
        for k in range(9):                              # and a row along the house
            bx.append((-11.0 + 0.9 * k) * u)
            by.append(back - 0.6 * u)
        n = len(bx)
        z = np.array([top if k < 18 else 0.0 for k in range(n)])
        sc = rng.uniform(0.62, 0.8, n) * u
        p.scatter("BoxBalls", balls, np.stack([bx, by, z], 1), rng.integers(0, 2, n), S._upright(p, n, 0.0),
                  sc[:, None] * np.ones((1, 3)), share=False, cat="plants", sway=0.01)
    # Ornamental grass in the gravel beds, a Japanese maple by the door.
    grass = S._grass_mat("OrnamentalGrass", (0.3, 0.32, 0.12, 1), (0.45, 0.42, 0.2, 1), dry=(0.7, 0.6, 0.38, 1),
                         dry_share=0.6)
    gx = np.concatenate([rng.uniform(1.4, 10.6, 22), rng.uniform(-10.8, -4.4, 12)]) * u
    gy = np.concatenate([back + rng.uniform(0.3, 1.5, 22) * u, back - rng.uniform(0.2, 1.0, 12) * u])
    p.scatter_lod("OrnamentalGrass", "grass", np.stack([gx, gy, np.full(len(gx), 0.03 * u)], 1),
                  rng.uniform(0, 1, len(gx)), S._upright(p, len(gx), 0.05), rng.uniform(0.7, 1.1, len(gx)) * u,
                  30 * r, fallback=lambda: S._tuft_protos(p, 4, grass), cat="plants")
    maple = scenemaps.leaves("LeafSet010")
    if maple:
        red = SM.foliage(p, "MapleLeaves", "LeafSet010", tint=(1.0, 0.28, 0.12), hue=0.02)
        bark = SM.bark(p, "MapleBark", "bark_brown_02", sat=0.3, value=0.6)
        made = [S._proto("maple%d" % i, flora.broadleaf(S.PREFIX + "Proto_maple%d" % i, rng, maple["rects"],
                                                        maple["length"], 4.5, p.detail, spread=0.65, rise=(0.1, 0.5),
                                                        limbs=(5, 8), leaf_scale=2.2, leaf_k=0.45, droop=0.4),
                         [bark, red]) for i in range(2)]
        co = [(-12.4 * u, back - 2.2 * u, 0.0), (11.8 * u, back - 1.4 * u, 0.0)]
        p.scatter("Maples", made, co, [0, 1], [(0.0, 0.0, 0.3), (0.0, 0.0, 2.1)],
                  [(u * 0.95, u * 0.95, u * 0.95), (u * 0.8, u * 0.8, u * 0.8)], share=False, cat="trees")
    # Lawn grass, and trees behind the house.
    keep_out = [(-4.6 * u, -20.5 * u, 4.6 * u, 7.5 * u), (-12.0 * u, back - 1.4 * u, 12.0 * u, 40.0 * u),
                (4.4 * u, -5.8 * u, 6.0 * u, 4.0 * u), (-6.0 * u, -5.8 * u, -4.4 * u, 4.0 * u)]
    gx, gy = S._plant_points(p, 70000, keep_out, 2.2 * r, 24.0 * r, power=1.6)
    lawn_m = S._grass_mat("LawnGrass", (0.07, 0.15, 0.03, 1), (0.13, 0.22, 0.05, 1), dry_share=0.05)
    p.scatter_lod("Grass", "grass", np.stack([gx, gy, np.zeros(len(gx))], 1), rng.uniform(0, 1, len(gx)),
                  S._upright(p, len(gx), 0.12), rng.uniform(0.06, 0.11, len(gx)) * u, 10 * r,
                  fallback=lambda: [S._proto("lawn%d" % i, S._tuft_mesh(S.PREFIX + "Proto_lawn%d" % i, rng, 22), [lawn_m])
                                    for i in range(4)], native=rng.uniform(0.6, 1.1, len(gx)), cat="plants")
    # Woodland round the plot: a belt behind the house and down both sides,
    # thinning toward the open front; crowns spaced, never on the lawn near the drive.
    n = 2600
    tx = rng.uniform(-75.0, 75.0, n) * r
    ty = rng.uniform(-40.0, 80.0, n) * r
    behind = ty > back + 6.0 * r
    sides = np.abs(tx) > 16.0 * r + np.maximum(0.0, -ty) * 0.6
    ok = (behind | sides) & (np.hypot(tx, ty) > S.CLEAR * r) & (np.hypot(tx, ty) < 85.0 * r)
    tx, ty = tx[ok], ty[ok]
    keep = C.spaced(tx, ty, rng.uniform(4.0, 6.5, len(tx)) * u)
    tx, ty = tx[keep], ty[keep]
    atlas = scenemaps.leaves("LeafSet016")
    if atlas:
        leaves = SM.foliage(p, "OakLeaves", "LeafSet016", tint=(0.75, 0.85, 0.6))
        bark = SM.bark(p, "OakBark", "bark_brown_02", moss=0.2, sat=0.5)
        made = [S._proto("oak%d" % i, flora.broadleaf(S.PREFIX + "Proto_oak%d" % i, rng, atlas["rects"],
                                                      atlas["length"], 14.0, p.detail, spread=0.55, rise=(0.2, 0.8),
                                                      limbs=(8, 12)), [bark, leaves]) for i in range(3)]
        far_made = [S._proto("oakfar%d" % i, flora.broadleaf(S.PREFIX + "Proto_oakfar%d" % i, rng, atlas["rects"],
                                                             atlas["length"], 14.0, p.detail, spread=0.55,
                                                             rise=(0.2, 0.8), limbs=(8, 12), far=True),
                             [bark, leaves]) for i in range(2)]
        hs = rng.uniform(10.0, 18.0, len(tx)) * u
        near = np.hypot(tx, ty) < 40.0 * r
        for tag, sel, protos, share in (("", near, made, False), ("_far", ~near, far_made, True)):
            if not sel.any():
                continue
            idx = rng.integers(0, len(protos), sel.sum())
            ext = np.array([S._size(o)[2] for o in protos])
            p.scatter("Trees" + tag, protos, np.stack([tx[sel], ty[sel], np.zeros(sel.sum())], 1), idx,
                      S._upright(p, sel.sum(), 0.02), (hs[sel] / ext[idx])[:, None] * np.ones((1, 3)), share=share,
                      cat="trees")
    # Clipped hedges along the plot's sides.
    if box_atlas:
        hx, hy = [], []
        for side in (-1, 1):
            for k in range(40):
                hx.append(side * 15.5 * u)
                hy.append((-24.0 + k * 0.9) * u)
        n = len(hx)
        p.scatter("Hedge", balls, np.stack([hx, hy, np.zeros(n)], 1), rng.integers(0, 2, n),
                  np.stack([np.zeros(n), np.zeros(n), rng.uniform(0, 6.3, n)], 1),
                  np.tile([1.5 * u, 1.5 * u, 2.1 * u], (n, 1)), share=False, cat="plants", sway=0.005)
