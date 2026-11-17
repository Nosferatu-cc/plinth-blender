# SPDX-License-Identifier: GPL-3.0-or-later
"""Moon (0.12): an Apollo-site look.

Reference: Apollo 15-17 surface photography. Regolith ~12% albedo, slightly
warm grey, pocked at every scale; a few fresh craters with bright ejecta and
rock fields round them; worn old craters everywhere; rounded highland massifs
on a near horizon; a rover's twin wheel tracks curving past and a line of
bootprints walking up to the product. Hard sun, black sky (stars barely show
at a sunlit exposure), and Earth fixed in the sky: real ocean and land
(NASA Blue Marble), oceans glinting, procedural weather bands of cloud, city
lights on the night side (Black Marble), a blue atmospheric limb that wraps
a little past the terminator. Time of day moves the Sun, so Earth's phase
changes; at lunar night the Earth is full and its blue light is all there is.
"""
import math

import bpy

from .. import hdri, scenemaps, scenemats as SM, scenes as S
from . import common as C

SKY = {"own": True, "dry": True, "time": "DAY", "times": ["DAWN", "DAY", "GOLDEN", "DUSK", "NIGHT"],
       "weathers": ["CLEAR"], "wind": 0.0, "fog_colour": (0.0, 0.0, 0.0)}
EARTH_DIR = (10.5, 330.0)               # elevation, azimuth (degrees): Earth never moves in the Moon's sky
# The Sun ~90 degrees from Earth: Earth half lit (terminator, city lights on its night side), the
# ground side-lit for the usual shots; at lunar night the Sun is behind the Moon and Earth is full.
SUN = {"DAWN": (3.0, 65.0), "DAY": (26.0, 245.0), "GOLDEN": (8.0, 250.0), "DUSK": (1.0, 255.0),
       "NIGHT": (-12.0, 150.0)}


def build(p):
    import numpy as np
    r, u, rng = p.r, p.u, p.rng
    big = 140.0 * r
    # Craters: old and worn everywhere (power law of sizes), a few fresh ones (sharp rims, bright ejecta, rocks).
    old = [(rng.uniform(-big, big), rng.uniform(-big, big), min(14.0 * r, (rng.pareto(1.6) + 1.0) * 0.6 * r),
            0.2, 0.06) for _ in range(260)]
    n_small = 2600
    a = rng.uniform(0, 2 * math.pi, n_small)
    d = np.sqrt(rng.uniform((2.6 * r) ** 2, (45.0 * r) ** 2, n_small))
    radius = np.minimum(0.04 * r * (1.0 + rng.pareto(1.25, n_small)), 1.6 * r)
    small = [(float(dd * math.cos(aa)), float(dd * math.sin(aa)), float(cr), float(rng.uniform(0.08, 0.26)),
              float(rng.uniform(0.02, 0.07))) for aa, dd, cr in zip(a, d, radius)]
    fresh = []
    for k in range(5):
        fa = rng.uniform(0, 2 * math.pi)
        fd = rng.uniform(9.0, 60.0) * r
        fresh.append((fd * math.cos(fa), fd * math.sin(fa), rng.uniform(0.8, 3.5) * r, 0.32, 0.12))
    craters = old + small + fresh
    fine_seed, hill_seed = rng.uniform(0, 1000), rng.uniform(0, 1000)

    def height(x, y):
        h = S._waves(x, y, rng_for(1), 5, 30.0 * r, 0.25 * r)
        h += S._waves(x, y, np.random.default_rng(int(fine_seed)), 4, 1.6 * r, 0.012 * r)
        grid = S._Grid(x, y, 2.0 * r)
        for cx, cy, cr, depth, rim_h in craters:
            idx = grid.near(cx, cy, 1.8 * cr)
            if len(idx) == 0:
                continue
            dd = np.sqrt((x[idx] - cx) ** 2 + (y[idx] - cy) ** 2) / cr
            bowl = np.where(dd < 1.0, (dd * dd - 1.0) * depth * cr, 0.0)
            rim = rim_h * cr * np.exp(-((dd - 1.0) / 0.28) ** 2)
            h[idx] += bowl + rim
        h = h * S._pad(x, y, 2.2 * r, 6.0 * r)
        # Rounded highland massifs on the horizon (no wind or water: soft, not ridged).
        return h + _massifs(x, y, np.random.default_rng(int(hill_seed)), r)

    def rng_for(k):
        return np.random.default_rng(int(fine_seed) + 17 * k)

    def ejecta(x, y):
        """Fresh ejecta: bright, in rays, out to ~3 crater radii."""
        out = np.zeros_like(x)
        for cx, cy, cr, _d, _h in fresh:
            dx, dy = x - cx, y - cy
            dd = np.sqrt(dx * dx + dy * dy) / cr
            ang = np.arctan2(dy, dx)
            rays = 0.55 + 0.45 * np.abs(np.sin(ang * 7.0 + cx)) ** 3 * np.abs(np.sin(ang * 3.0 + cy))
            out = np.maximum(out, np.exp(-np.maximum(dd - 0.9, 0.0) / 0.9) * rays * (dd > 0.75))
        return np.clip(out, 0.0, 1.0)

    ground = p.ground(1.2 * r, big, height, attrs={"ejecta": ejecta})
    # The rover's two wheel tracks: an arc passing beside the product; bootprints from it to the product.
    ta = rng.uniform(0.3, 1.2) + (math.pi if rng.uniform() < 0.5 else 0.0)
    rad = 26.0 * r
    centre = ((rad + 3.4 * r) * math.cos(ta), (rad + 3.4 * r) * math.sin(ta))
    gauge = 1.83 * u                                   # wheel to wheel
    near_pt = (3.4 * r * math.cos(ta), 3.4 * r * math.sin(ta))
    track_tile = 1.6 * u
    regolith = (0.46, 0.45, 0.43)
    disturbed = (0.36, 0.355, 0.345)                  # churned regolith photographs darker

    def wheel(side):
        def uv(ctx):
            along, across = C.arc(ctx, centre[0], centre[1], rad + side * gauge / 2)
            return C.strip_uv(ctx, along, across, 0.55 * track_tile)

        def mask(ctx):
            _along, across = C.arc(ctx, centre[0], centre[1], rad + side * gauge / 2)
            return ctx.mul(C.band(ctx, across, 0.13 * u, 0.06 * u), ctx.near(60.0 * r, 75.0 * r))
        return C.Layer("moon_track_01", tile=track_tile / u, uv=uv, mask=mask, anti=False, sharp=10.0,
                       tint=disturbed, lift=0.08)

    def boots_uv(ctx):
        along, across, _t = C.line(ctx, near_pt[0], near_pt[1], 1.6 * r * math.cos(ta + 0.5),
                                   1.6 * r * math.sin(ta + 0.5))
        return C.strip_uv(ctx, along, across, 0.55 * 1.55 * u)

    def boots_mask(ctx):
        _a, across, t = C.line(ctx, near_pt[0], near_pt[1], 1.6 * r * math.cos(ta + 0.5),
                               1.6 * r * math.sin(ta + 0.5))
        return ctx.mul(C.band(ctx, across, 0.28 * u, 0.1 * u), C.between(ctx, t, 0.0, 1.0, 0.02))

    def bright(nt, ctx, col):
        lift = nt.math("MULTIPLY_ADD", ctx.attr("ejecta"), 0.7, 1.0)
        return nt.mix(1.0, col, SM._grey(nt, lift), "MULTIPLY")

    layers = [C.Layer("moon_02", tint=regolith),
              C.Layer("moon_dusted_05", tile=1.4, tint=regolith, mask=lambda c: c.patches(6.0, 0.45, 0.62), sharp=4.0),
              C.Layer("moon_meteor_02", tint=regolith, mask=lambda c: c.patches(18.0, 0.5, 0.64, seed=2.0),
                      sharp=5.0),
              wheel(-1), wheel(1),
              C.Layer("moon_footprints_02", tile=1.55, tint=disturbed, uv=boots_uv, mask=boots_mask, anti=False,
                      sharp=10.0)]
    mat = p.terrain("Regolith", layers, disp=0.05 * u, pad=(1.0 * r, 1.4 * r), cavity=0.5, macro=(0.86, 1.08),
                    specular=0.3, sheen=0.15, normal=1.4, post=bright)
    if mat is None:
        mat = S._layered("Regolith", (0.1, 0.098, 0.094, 1), 0.95, scale=3.0 / r, depth=0.8)
    S._with_materials(ground, [mat])

    # Rocks: fields round the fresh craters (ejecta), scattered blocks everywhere, pebbles near the product.
    rock_m = p.surface("MoonRock", "rock_boulder_dry", colour=_moon_rock,
                       fallback=lambda: S._simple("MoonRock", (0.07, 0.07, 0.065, 1), 0.9, bump_scale=9.0, r=r,
                                                  bump=0.8))
    fx, fy = C.clustered(rng, [(c[0], c[1]) for c in fresh], [int(260 * c[2] / r) for c in fresh],
                         [1.4 * c[2] for c in fresh])
    sx = np.concatenate([fx, rng.uniform(-90, 90, 900) * r])
    sy = np.concatenate([fy, rng.uniform(-90, 90, 900) * r])
    dd = np.hypot(sx, sy)
    ok = dd > 3.5 * r
    sx, sy = sx[ok], sy[ok]
    size = (0.05 + rng.pareto(2.3, len(sx)) * 0.12) * r
    p.scatter_lod("Rocks", "rocks", np.stack([sx, sy, height(sx, sy) - 0.18 * size], 1), rng.uniform(0, 1, len(sx)),
                  S._tumbled(p, len(sx)), size, 12 * r, fallback=lambda: S._rock_protos(p, 8, rock_m), axis=0)
    n = 16000
    pa = rng.uniform(0, 2 * math.pi, n)
    pd = 1.3 * r + rng.pareto(1.3, n) * 2.0 * r
    keep = pd < 60 * r
    px, py = pd[keep] * np.cos(pa[keep]), pd[keep] * np.sin(pa[keep])
    psize = (0.008 + rng.pareto(2.5, len(px)) * 0.02) * r
    p.scatter_lod("Pebbles", "rocks", np.stack([px, py, height(px, py) - 0.2 * psize], 1), rng.uniform(0, 1, len(px)),
                  S._tumbled(p, len(px)), psize, 8 * r, fallback=lambda: S._rock_protos(p, 8, rock_m), axis=0,
                  lods=(2, 2))
    _earth(p)
    _black_sky(p.scene)


def _moon_rock(nt, col, s):
    """A dry boulder scan made lunar: grey, a touch warm, dust settled on top."""
    grey = C.tint(nt, col, sat=0.1, value=0.34)
    up = nt.new("ShaderNodeSeparateXYZ")
    nt.link(nt.new("ShaderNodeNewGeometry").outputs["Normal"], up.inputs[0])
    dust = nt.smooth(up.outputs["Z"], 0.45, 0.85)
    return nt.mix(nt.math("MULTIPLY", dust, 0.7), grey, (0.13, 0.125, 0.118, 1.0))


def _massifs(x, y, rng, r):
    import numpy as np
    d = np.sqrt(x * x + y * y)
    h = np.zeros_like(x)
    k, amp = 2.0 * math.pi / (90.0 * r), 1.0
    for _ in range(4):
        for _w in range(3):
            th, ph = rng.uniform(0, 2 * math.pi), rng.uniform(0, 2 * math.pi)
            h += amp * (0.5 + 0.5 * np.sin(k * (x * math.cos(th) + y * math.sin(th)) + ph)) / 3.0
        k *= 2.0
        amp *= 0.35
    t = np.clip((d - 80.0 * r) / (50.0 * r), 0.0, 1.0)
    return 9.0 * r * (t * t * (3 - 2 * t)) * h ** 2.2


# --- Earth -------------------------------------------------------------------------------------

def _earth(p):
    import bmesh
    r = p.r
    bm = bmesh.new()
    bm.loops.layers.uv.new("UVMap")
    try:
        bmesh.ops.create_uvsphere(bm, u_segments=192, v_segments=96, radius=1.0, calc_uvs=True)
    except TypeError:
        bmesh.ops.create_uvsphere(bm, u_segments=192, v_segments=96, radius=1.0)
    me = bpy.data.meshes.new(S.PREFIX + "Earth")
    bm.to_mesh(me)
    bm.free()
    for poly in me.polygons:
        poly.use_smooth = True
    earth = S._add(p.scene, "Earth", me)
    direction = hdri.sun_direction(*EARTH_DIR)
    dist = 300.0 * r
    earth.location = direction * dist
    size = math.tan(math.radians(2.6)) * dist          # ~5 degrees across: a little larger than life
    earth.scale = (size,) * 3
    earth.rotation_euler = (math.radians(23.4), 0.0, math.radians(-40.0))
    S._with_materials(earth, [_earth_material(p)])
    earth.visible_shadow = False
    shell_me = me.copy()
    shell_me.name = S.PREFIX + "EarthAir"
    shell = S._add(p.scene, "EarthAir", shell_me)
    shell.location, shell.rotation_euler = earth.location, earth.rotation_euler
    shell.scale = (size * 1.018,) * 3
    S._with_materials(shell, [_air_material()])
    shell.visible_shadow = False
    look(p.scene, SM_cfg_time(p.scene), "CLEAR")


def SM_cfg_time(scene):
    cfg = S.settings(scene, "MOON")
    return cfg["time"] if cfg else "DAY"


def _sun_vector(nt, name="SunDir"):
    v = nt.new("ShaderNodeCombineXYZ")
    v.name = v.label = name
    return v


def _earth_material(p):
    m = SM._material("Earth")
    nt = SM._Nodes(m.node_tree)
    out = nt.output()
    ultra = p.detail == "ULTRA"
    day, water, night = (scenemaps.earth(k, p.kit, ultra) for k in ("day", "water", "night"))
    uv = nt.new("ShaderNodeUVMap", uv_map="UVMap").outputs["UV"]
    obj = nt.new("ShaderNodeTexCoord").outputs["Object"]
    geo = nt.new("ShaderNodeNewGeometry")
    sun = _sun_vector(nt)
    lit_dot = nt.vmath("DOT_PRODUCT", geo.outputs["Normal"], sun.outputs[0])
    lit = nt.smooth(lit_dot, -0.04, 0.12)
    if day is not None:
        surface = nt.image(day, uv).outputs["Color"]
        sea = nt.image(water, uv).outputs["Color"] if water is not None else None
        sea = nt.math("MULTIPLY", sea, 1.0) if sea is not None else 0.7
    else:
        land = nt.smooth(nt.noise(obj, 2.2, 8.0, 0.6).outputs["Fac"], 0.55, 0.57)
        surface = nt.mix(land, (0.012, 0.05, 0.2, 1), (0.10, 0.2, 0.06, 1))
        sea = nt.math("SUBTRACT", 1.0, land)
    # Clouds: domain-warped noise in weather bands (the ITCZ near the equator,
    # storm tracks at 45-65 degrees, clear subtropics), swirling, a few big systems.
    sep = nt.new("ShaderNodeSeparateXYZ")
    nt.link(obj, sep.inputs[0])
    lat = nt.math("ABSOLUTE", sep.outputs["Z"])
    bands = nt.math("ADD", nt.math("MULTIPLY", nt.smooth(lat, 0.18, 0.05), 0.18),
                    nt.math("MULTIPLY", nt.math("MULTIPLY", nt.smooth(lat, 0.6, 0.75), nt.smooth(lat, 0.95, 0.85)),
                            0.22))
    bands = nt.math("SUBTRACT", bands, nt.math("MULTIPLY", nt.math("MULTIPLY", nt.smooth(lat, 0.2, 0.32),
                                                                     nt.smooth(lat, 0.55, 0.42)), 0.12))
    warp = nt.noise(obj, 1.6, 4.0, 0.6)
    warped = nt.vmath("ADD", obj, nt.vmath("MULTIPLY", warp.outputs["Color"], (0.9, 0.9, 0.9)))
    cl = nt.noise(warped, 5.5, 12.0, 0.62).outputs["Fac"]
    swirl = nt.noise(nt.vmath("ADD", obj, nt.vmath("MULTIPLY", nt.noise(obj, 0.8, 2.0).outputs["Color"],
                                                   (2.0, 2.0, 2.0))), 2.2, 6.0, 0.55).outputs["Fac"]
    density = nt.math("ADD", nt.math("ADD", nt.math("MULTIPLY", cl, 0.65), nt.math("MULTIPLY", swirl, 0.35)), bands)
    cloud = nt.smooth(density, 0.6, 0.74)
    thin = nt.smooth(density, 0.53, 0.6)
    cloud = nt.math("MAXIMUM", cloud, nt.math("MULTIPLY", thin, 0.35))
    # Viewed through air: the limb is hazier and bluer (the sheen over the top).
    facing = nt.new("ShaderNodeLayerWeight", in_Blend=0.35).outputs["Facing"]
    haze = nt.math("MULTIPLY", nt.math("POWER", facing, 3.0), 0.5)
    ground_col = nt.mix(haze, surface, (0.25, 0.42, 0.85, 1.0))
    colour = nt.mix(cloud, ground_col, (0.92, 0.93, 0.95, 1.0))
    rough = nt.mixf(cloud, nt.mixf(sea, 0.82, 0.16), 0.95)             # the sun glints on open ocean
    pr = nt.principled(out, colour, rough)
    if "Specular IOR Level" in pr.inputs:
        nt.link(nt.mixf(sea, 0.3, 0.6), pr.inputs["Specular IOR Level"])
    # City lights on the night side (Black Marble: yellow lights over a blue moonlit base -> keep red minus blue).
    if night is not None:
        nc = nt.image(night, uv).outputs["Color"]
        rgb = nt.new("ShaderNodeSeparateColor")
        nt.link(nc, rgb.inputs[0])
        city = nt.smooth(nt.math("SUBTRACT", rgb.outputs[0], rgb.outputs[2]), 0.005, 0.18)
        dark = nt.math("SUBTRACT", 1.0, nt.smooth(lit_dot, -0.12, 0.05))
        glow = nt.math("MULTIPLY", nt.math("MULTIPLY", city, dark), nt.math("SUBTRACT", 1.0,
                                                                            nt.math("MULTIPLY", cloud, 0.75)))
        SM._set(pr, emission=(1.0, 0.72, 0.38, 1.0))
        nt.link(nt.math("MULTIPLY", glow, 7.0), pr.inputs["Emission Strength"])
    del lit
    m["plinth_bsdf"] = pr.name
    return m


def _air_material():
    """Earth's atmosphere: a shell glowing blue at the limb on the day side
    (Rayleigh), reddening at the terminator, wrapping just past it."""
    m = SM._material("EarthAir")
    nt = SM._Nodes(m.node_tree)
    out = nt.output()
    geo = nt.new("ShaderNodeNewGeometry")
    sun = _sun_vector(nt)
    ndl = nt.vmath("DOT_PRODUCT", geo.outputs["Normal"], sun.outputs[0])
    day = nt.smooth(ndl, -0.28, 0.25)
    facing = nt.new("ShaderNodeLayerWeight", in_Blend=0.5).outputs["Facing"]
    rim = nt.math("POWER", facing, 3.2)
    edge = nt.math("SUBTRACT", 1.0, nt.smooth(facing, 0.93, 1.0))         # thin outer falloff
    glow = nt.math("MULTIPLY", nt.math("MULTIPLY", rim, edge), day)
    dusk = nt.math("MULTIPLY", nt.smooth(ndl, -0.25, -0.02), nt.smooth(ndl, 0.2, 0.0))
    colour = nt.mix(dusk, (0.22, 0.48, 1.0, 1.0), (1.0, 0.45, 0.18, 1.0))
    e = nt.new("ShaderNodeEmission")
    nt.link(colour, e.inputs["Color"])
    nt.link(nt.math("MULTIPLY", glow, 9.0), e.inputs["Strength"])
    clear_ = nt.new("ShaderNodeBsdfTransparent")
    add = nt.new("ShaderNodeAddShader")
    nt.link(clear_.outputs[0], add.inputs[0])
    nt.link(e.outputs[0], add.inputs[1])
    nt.link(add.outputs[0], out.inputs["Surface"])
    if hasattr(m, "surface_render_method"):
        m.surface_render_method = "BLENDED"
    elif hasattr(m, "blend_method"):
        m.blend_method = "BLEND"
    m["plinth_glow"] = True
    return m


def _black_sky(scene):
    def stars(nt, out):
        coord = nt.new("ShaderNodeTexCoord").outputs["Generated"]
        v = nt.voronoi(coord, 900.0)
        dots = nt.math("LESS_THAN", v.outputs["Distance"], 0.04)
        twinkle = nt.math("MULTIPLY", dots, nt.math("POWER", nt.noise(coord, 300.0, 1.0).outputs["Fac"], 5.0))
        bg = nt.new("ShaderNodeBackground", in_Strength=1.0)
        nt.link(SM._grey(nt, nt.math("MULTIPLY", twinkle, 0.5)), bg.inputs["Color"])
        nt.link(bg.outputs[0], out.inputs["Surface"])
    S._own_world(scene, stars)


def look(scene, time, weather):
    """The Sun for a time of day; Earth's phase and night lights follow it;
    at lunar night Earth (full) lights the ground blue."""
    el, az = SUN.get(time, SUN["DAY"])
    S._scene_sun(scene, el, az + S.turn(scene), 7.0, angle_deg=0.53, colour=(1.0, 0.985, 0.96))
    sun_dir = hdri.sun_direction(el, az + S.turn(scene))
    for name in ("Earth", "EarthAir"):
        m = bpy.data.materials.get(S.PREFIX + name)
        node = m.node_tree.nodes.get("SunDir") if m is not None else None
        if node is not None:
            for i in range(3):
                node.inputs[i].default_value = sun_dir[i]
    earth_light = bpy.data.lights.get(S.PREFIX + "Earthlight")
    if time == "NIGHT":
        data = earth_light or bpy.data.lights.new(S.PREFIX + "Earthlight", "SUN")
        data.energy, data.color, data.angle = 0.6, (0.62, 0.74, 1.0), math.radians(2.0)
        obj = S._add(scene, "Earthlight", data)
        obj.parent = None
        obj.rotation_mode = "QUATERNION"
        obj.rotation_quaternion = (-hdri.sun_direction(EARTH_DIR[0], EARTH_DIR[1] + S.turn(scene))).to_track_quat(
            "-Z", "Y")
        obj["plinth_sun_dir"] = (EARTH_DIR[0], EARTH_DIR[1])
    else:
        obj = bpy.data.objects.get(S.PREFIX + "Earthlight")
        if obj is not None:
            bpy.data.objects.remove(obj)
