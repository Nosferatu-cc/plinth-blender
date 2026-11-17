# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene materials (0.12): scanned, layered, physically based.

- ``terrain``: a ground from several scanned sets (scenemaps), each tiled at
  its real size, anti-tiled (a second turned sample blended by noise, a third
  far away), stacked by *height blending* (where a layer's mask rises, its
  high points come through first: moss climbs out of the litter, pebbles
  show through mud), with macro variation, crevices darkened, wetness
  (porous darkening, glossier, puddles that flatten the relief) and real
  displacement at High/Ultra.
- ``foliage`` / ``bark``: leaf atlases (alpha, translucency, both sides,
  per-instance hue/value variation) and scanned bark.
- ``water``: a stream's flowing surface (two normal layers drifting with the
  Env group's Time, Fresnel reflection, depth darkening).
- ``env``: the ``Plinth_Scene_Env`` group every scene material reads (Wet,
  Night, Time), so Weather and Time of day change materials without a rebuild.

Object space in metres throughout (scene parts are at unit scale and turn
with the place). Names: Plinth_Scene_*.
"""
import math

import bpy

from . import scenemaps

PREFIX = "Plinth_Scene_"
ENV = PREFIX + "Env"
ENV_VERSION = 2


# --- Writing shader trees ---------------------------------------------------------------

def _material(name):
    full = PREFIX + name
    mat = bpy.data.materials.get(full)
    if mat is None:
        mat = bpy.data.materials.new(full)
    if hasattr(mat, "use_nodes") and not mat.use_nodes:
        mat.use_nodes = True
    mat.node_tree.nodes.clear()
    return mat


def _displacement(mat, true_displacement):
    """Real displacement (and bump) or bump only. 4.1+: on the material; older: Cycles'."""
    method = "BOTH" if true_displacement else "BUMP"
    for owner in (mat, getattr(mat, "cycles", None)):
        if owner is not None and hasattr(owner, "displacement_method"):
            owner.displacement_method = method
            return


class _Nodes:
    """Tiny helper for writing shader trees."""

    def __init__(self, tree):
        self.tree, self.nodes, self.links = tree, tree.nodes, tree.links

    def new(self, idname, **values):
        n = self.nodes.new(idname)
        for k, v in values.items():
            if k.startswith("in_"):
                n.inputs[k[3:].replace("_", " ")].default_value = v
            else:
                setattr(n, k, v)
        return n

    def link(self, a, b):
        self.links.new(a, b)
        return b

    def math(self, op, a, b=None, c=None):
        n = self.new("ShaderNodeMath", operation=op)
        for i, v in enumerate((a, b, c)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                n.inputs[i].default_value = v
            else:
                self.link(v, n.inputs[i])
        return n.outputs[0]

    def vmath(self, op, a, b=None, scale=None):
        n = self.new("ShaderNodeVectorMath", operation=op)
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            if isinstance(v, (tuple, list)):
                n.inputs[i].default_value = v
            else:
                self.link(v, n.inputs[i])
        if scale is not None:
            n.inputs["Scale"].default_value = scale
        out = n.outputs["Value"] if op in ("LENGTH", "DOT_PRODUCT", "DISTANCE") else n.outputs["Vector"]
        return out

    def mix(self, fac, a, b, blend="MIX"):
        n = self.new("ShaderNodeMix", data_type="RGBA", blend_type=blend)
        sock = {s.identifier: s for s in n.inputs}
        for ident, v in (("Factor_Float", fac), ("A_Color", a), ("B_Color", b)):
            if isinstance(v, (int, float, tuple)):
                sock[ident].default_value = v
            else:
                self.link(v, sock[ident])
        return next(s for s in n.outputs if s.identifier == "Result_Color")

    def mixf(self, fac, a, b):
        n = self.new("ShaderNodeMix", data_type="FLOAT")
        sock = {s.identifier: s for s in n.inputs}
        for ident, v in (("Factor_Float", fac), ("A_Float", a), ("B_Float", b)):
            if isinstance(v, (int, float)):
                sock[ident].default_value = v
            else:
                self.link(v, sock[ident])
        return next(s for s in n.outputs if s.identifier == "Result_Float")

    def mixv(self, fac, a, b):
        n = self.new("ShaderNodeMix", data_type="VECTOR")
        sock = {s.identifier: s for s in n.inputs}
        for ident, v in (("Factor_Float", fac), ("A_Vector", a), ("B_Vector", b)):
            if isinstance(v, (int, float, tuple)):
                sock[ident].default_value = v
            else:
                self.link(v, sock[ident])
        return next(s for s in n.outputs if s.identifier == "Result_Vector")

    def ramp(self, fac, stops):
        n = self.new("ShaderNodeValToRGB")
        els = n.color_ramp.elements
        while len(els) > 1:
            els.remove(els[-1])
        els[0].position, els[0].color = stops[0]
        for pos, col in stops[1:]:
            e = els.new(pos)
            e.color = col
        self.link(fac, n.inputs["Fac"])
        return n.outputs["Color"]

    def smooth(self, value, lo, hi):
        """0 below lo, 1 above hi, smooth between (lo > hi: the reverse)."""
        n = self.new("ShaderNodeMapRange", interpolation_type="SMOOTHSTEP")
        if lo > hi:
            n.inputs["From Min"].default_value, n.inputs["From Max"].default_value = hi, lo
            n.inputs["To Min"].default_value, n.inputs["To Max"].default_value = 1.0, 0.0
        else:
            n.inputs["From Min"].default_value, n.inputs["From Max"].default_value = lo, hi
        self.link(value, n.inputs["Value"])
        return n.outputs["Result"]

    def world_pos(self):
        return self.new("ShaderNodeNewGeometry").outputs["Position"]

    def obj_pos(self):
        """Object space: real units on a scene's parts (unit scale), turning with the place."""
        return self.new("ShaderNodeTexCoord").outputs["Object"]

    def uv(self, scale, offset=(0.0, 0.0), turn=0.0, vector=None):
        """The part's UVs (metres) scaled to one texture tile, optionally turned."""
        if vector is None:
            vector = self.new("ShaderNodeUVMap", uv_map="UVMap").outputs["UV"]
        if turn:
            rot = self.new("ShaderNodeVectorRotate", rotation_type="Z_AXIS")
            rot.inputs["Angle"].default_value = turn
            self.link(vector, rot.inputs["Vector"])
            vector = rot.outputs["Vector"]
        v = self.vmath("MULTIPLY", vector, (scale, scale, 1.0))
        if offset == (0.0, 0.0):
            return v
        return self.vmath("ADD", v, (offset[0], offset[1], 0.0))

    def image(self, img, vector, cubic=False):
        n = self.new("ShaderNodeTexImage")
        n.image = img
        n.interpolation = "Cubic" if cubic else "Linear"
        self.link(vector, n.inputs["Vector"])
        return n

    def noise(self, vector, scale, detail=4.0, rough=0.55, dims=None):
        n = self.new("ShaderNodeTexNoise", in_Scale=scale, in_Detail=detail, in_Roughness=rough)
        if dims:
            n.noise_dimensions = dims
        self.link(vector, n.inputs["Vector"])
        return n

    def voronoi(self, vector, scale, feature="F1"):
        n = self.new("ShaderNodeTexVoronoi", feature=feature, in_Scale=scale)
        self.link(vector, n.inputs["Vector"])
        return n

    def attr(self, name, kind="GEOMETRY"):
        n = self.new("ShaderNodeAttribute", attribute_name=name)
        if kind != "GEOMETRY":
            n.attribute_type = kind
        return n

    def principled(self, out_node, colour, rough, bump=None, bump_strength=0.3, metallic=0.0, normal=None):
        p = self.new("ShaderNodeBsdfPrincipled")
        if isinstance(colour, tuple):
            p.inputs["Base Color"].default_value = colour
        else:
            self.link(colour, p.inputs["Base Color"])
        if isinstance(rough, (int, float)):
            p.inputs["Roughness"].default_value = rough
        else:
            self.link(rough, p.inputs["Roughness"])
        p.inputs["Metallic"].default_value = metallic
        if bump is not None:
            b = self.new("ShaderNodeBump", in_Strength=bump_strength)
            self.link(bump, b.inputs["Height"])
            if normal is not None:
                self.link(normal, b.inputs["Normal"])
            self.link(b.outputs["Normal"], p.inputs["Normal"])
        elif normal is not None:
            self.link(normal, p.inputs["Normal"])
        self.link(p.outputs[0], out_node.inputs["Surface"])
        return p

    def output(self):
        return self.new("ShaderNodeOutputMaterial")

    def emission(self, out_node, colour, strength):
        e = self.new("ShaderNodeEmission", in_Strength=strength)
        if isinstance(colour, tuple):
            e.inputs["Color"].default_value = colour
        else:
            self.link(colour, e.inputs["Color"])
        self.link(e.outputs[0], out_node.inputs["Surface"])
        return e


def _set(bsdf, **values):
    """Set Principled inputs by name, skipping ones this Blender lacks (4.x/5.x names)."""
    aliases = {"sheen": ("Sheen Weight", "Sheen"), "subsurface": ("Subsurface Weight", "Subsurface"),
               "transmission": ("Transmission Weight", "Transmission"), "coat": ("Coat Weight", "Coat"),
               "specular": ("Specular IOR Level", "Specular"), "emission": ("Emission Color", "Emission"),
               "emission_strength": ("Emission Strength",), "coat_roughness": ("Coat Roughness",),
               "sheen_roughness": ("Sheen Roughness",), "ior": ("IOR",), "alpha": ("Alpha",)}
    for key, v in values.items():
        for name in aliases.get(key, (key,)):
            if name in bsdf.inputs:
                bsdf.inputs[name].default_value = v
                break


def _cavity(nt, height, lo=0.2, hi=0.62, deep=0.55):
    """Darken what lies low (crevices, cracks, the gaps between stones): the
    shading the eye reads as depth, which a renderer's own small-scale
    occlusion misses. Returns a factor deep..1."""
    return nt.math("ADD", deep, nt.math("MULTIPLY", nt.smooth(height, lo, hi), 1.0 - deep))


def _grey(nt, value):
    c = nt.new("ShaderNodeCombineColor")
    for i in range(3):
        nt.link(value, c.inputs[i])
    return c.outputs[0]


def _clamp(nt, value, lo=0.02, hi=1.0):
    c = nt.new("ShaderNodeClamp")
    nt.link(value, c.inputs["Value"])
    c.inputs["Min"].default_value, c.inputs["Max"].default_value = lo, hi
    return c.outputs[0]


# --- The Env group: weather and time of day, read by every scene material ------------------

ENV_VALUES = (("Wet", 0.0), ("Night", 0.0), ("Time", 0.0), ("Snow", 0.0), ("Fog", 0.0),
              ("FogR", 0.8), ("FogG", 0.85), ("FogB", 0.9))


def env_group():
    """``Plinth_Scene_Env``: a shader group with no inputs whose outputs are
    the scene's Wet (0 dry .. 1 soaked), Night (0 day .. 1 lamps full),
    Time (seconds, from the frame: water flows, leaves flutter)."""
    ng = bpy.data.node_groups.get(ENV)
    if ng is not None and ng.get("plinth_version") == ENV_VERSION:
        return ng
    if ng is None:
        ng = bpy.data.node_groups.new(ENV, "ShaderNodeTree")
    ng.nodes.clear()
    ng.interface.clear()
    out = ng.nodes.new("NodeGroupOutput")
    for i, (name, value) in enumerate(ENV_VALUES):
        ng.interface.new_socket(name, in_out="OUTPUT", socket_type="NodeSocketFloat")
        v = ng.nodes.new("ShaderNodeValue")
        v.name = v.label = name
        v.outputs[0].default_value = value
        v.location = (-200, -100 * i)
        ng.links.new(v.outputs[0], out.inputs[i])
    fc = ng.nodes["Time"].outputs[0].driver_add("default_value")
    fc.driver.type = "SCRIPTED"
    fc.driver.expression = "frame / 24.0"
    ng["plinth_version"] = ENV_VERSION
    return ng


def env(nt, name):
    """An Env output in a material tree (one group node per tree)."""
    node = nt.nodes.get("PlinthEnv")
    if node is None:
        node = nt.new("ShaderNodeGroup")
        node.node_tree = env_group()
        node.name = node.label = "PlinthEnv"
    return node.outputs[name]


def set_env(**values):
    ng = env_group()
    for k, v in values.items():
        node = ng.nodes.get(k)
        if node is not None and k != "Time":
            node.outputs[0].default_value = float(v)


def get_env(name):
    ng = bpy.data.node_groups.get(ENV)
    node = ng.nodes.get(name) if ng is not None else None
    return float(node.outputs[0].default_value) if node is not None else 0.0


def fog_wrap(mat):
    """Aerial perspective: the light coming off a surface is replaced, with
    distance from the camera, by the haze's own (Env Fog: 1/visibility,
    FogR/G/B: its radiance). Applied around every output's Surface; leaf cut-
    outs and alpha-blended scans stay see-through where they're clear.
    Idempotent (tagged)."""
    if mat is None or not mat.node_tree or mat.get("plinth_fog"):
        return False
    nt = _Nodes(mat.node_tree)
    outs = [n for n in mat.node_tree.nodes if n.type == "OUTPUT_MATERIAL"]
    targets = []
    cut = mat.node_tree.nodes.get("PlinthCut")
    if cut is not None:
        targets.append(cut.inputs[2])
    else:
        targets = [o.inputs["Surface"] for o in outs if o.inputs["Surface"].is_linked]
    if not targets:
        return False
    dist = nt.new("ShaderNodeCameraData").outputs["View Distance"]
    thick = nt.math("SUBTRACT", 1.0, nt.math("EXPONENT", nt.math("MULTIPLY", nt.math("MULTIPLY", dist, -1.0),
                                                                     env(nt, "Fog"))))
    colour = nt.new("ShaderNodeCombineColor")
    for i, k in enumerate(("FogR", "FogG", "FogB")):
        nt.link(env(nt, k), colour.inputs[i])
    glow = nt.new("ShaderNodeEmission")
    nt.link(colour.outputs[0], glow.inputs["Color"])
    fog_shader = glow.outputs[0]
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED" and n.inputs["Alpha"].is_linked), None)
    if bsdf is not None and cut is None:                  # a scan's alpha: haze only where it's solid
        clear_ = nt.new("ShaderNodeBsdfTransparent")
        a = nt.new("ShaderNodeMixShader")
        nt.link(bsdf.inputs["Alpha"].links[0].from_socket, a.inputs["Fac"])
        nt.link(clear_.outputs[0], a.inputs[1])
        nt.link(glow.outputs[0], a.inputs[2])
        fog_shader = a.outputs[0]
    for sock in targets:
        src = sock.links[0].from_socket
        mix = nt.new("ShaderNodeMixShader")
        nt.link(thick, mix.inputs["Fac"])
        nt.link(src, mix.inputs[1])
        nt.link(fog_shader, mix.inputs[2])
        nt.link(mix.outputs[0], sock)
    mat["plinth_fog"] = True
    return True


def remove_env():
    ng = bpy.data.node_groups.get(ENV)
    if ng is not None and ng.users == 0:
        bpy.data.node_groups.remove(ng)


# --- Masks (each returns a socket 0..1) -----------------------------------------------------

class Ctx:
    """What masks may read: the tree, object position, metres per unit (u), r."""

    def __init__(self, nt, u, r):
        self.nt, self.u, self.r = nt, u, r
        self.pos = nt.obj_pos()
        sep = nt.new("ShaderNodeSeparateXYZ")
        nt.link(self.pos, sep.inputs[0])
        self.x, self.y, self.z = sep.outputs["X"], sep.outputs["Y"], sep.outputs["Z"]
        xy = nt.new("ShaderNodeCombineXYZ")
        nt.link(self.x, xy.inputs[0])
        nt.link(self.y, xy.inputs[1])
        self.dist = nt.vmath("LENGTH", xy.outputs[0])

    def patches(self, size, lo=0.45, hi=0.6, detail=4.0, seed=0.0):
        """Patches about ``size`` m across (x u)."""
        p = self.pos if not seed else self.nt.vmath("ADD", self.pos, (seed * 13.1, seed * 7.7, 0.0))
        return self.nt.smooth(self.nt.noise(p, 1.0 / (size * self.u), detail, 0.6).outputs["Fac"], lo, hi)

    def slope(self, lo=0.75, hi=0.92):
        """1 on steep ground (world normal's z below lo), 0 on the flat."""
        sep = self.nt.new("ShaderNodeSeparateXYZ")
        self.nt.link(self.nt.new("ShaderNodeNewGeometry").outputs["Normal"], sep.inputs[0])
        return self.nt.smooth(sep.outputs["Z"], hi, lo)

    def height(self, lo, hi):
        return self.nt.smooth(self.z, lo, hi)

    def near(self, lo, hi):
        """1 near the product (closer than lo), 0 beyond hi."""
        return self.nt.smooth(self.dist, hi, lo)

    def attr(self, name, lo=0.0, hi=1.0):
        a = self.nt.attr(name).outputs["Fac"]
        return a if (lo, hi) == (0.0, 1.0) else self.nt.smooth(a, lo, hi)

    def mul(self, a, b):
        return self.nt.math("MULTIPLY", a, b)

    def maxof(self, a, b):
        return self.nt.math("MAXIMUM", a, b)


# --- Sampling a scanned set ------------------------------------------------------------------

def _turned_normal(nt, colour, angle):
    """A tangent-space normal map colour sampled through UVs turned by
    ``angle``: turn its XY back so the relief faces the right way."""
    v = nt.vmath("MULTIPLY_ADD", colour, (2.0, 2.0, 2.0))
    n = v.node
    n.inputs[2].default_value = (-1.0, -1.0, -1.0)
    rot = nt.new("ShaderNodeVectorRotate", rotation_type="Z_AXIS")
    rot.inputs["Angle"].default_value = -angle
    nt.link(v, rot.inputs["Vector"])
    back = nt.new("ShaderNodeVectorMath", operation="MULTIPLY_ADD")
    nt.link(rot.outputs["Vector"], back.inputs[0])
    back.inputs[1].default_value = (0.5, 0.5, 0.5)
    back.inputs[2].default_value = (0.5, 0.5, 0.5)
    return back.outputs["Vector"]


def sample(nt, ctx, maps, tile, seed=0, uv=None, far=True, anti=True):
    """A set's maps at ``tile`` m, never visibly repeating: a second sample
    turned 63 degrees at 1.37x the size, blended by noise; far off (beyond ~20
    tiles) a third at 6x for the colour, so the horizon doesn't show a grid.
    Returns {"col", "h", "rough", "ncol"} sockets (missing maps: None)."""
    turn = 1.1 + 0.37 * seed
    a = nt.uv(1.0 / tile, (0.17 * seed, 0.31 * seed), vector=uv)
    b = nt.uv(1.0 / (tile * 1.37), (0.53 + 0.11 * seed, 0.29), turn=turn, vector=uv)
    blend = nt.smooth(nt.noise(ctx.pos, 1.0 / (3.2 * tile), 3.0, 0.5,).outputs["Fac"], 0.4, 0.6)
    out = {}
    for m in ("diff", "disp", "rough", "nor"):
        img = maps.get(m)
        if img is None:
            out[m] = None
            continue
        sa = nt.image(img, a, cubic=(m == "disp")).outputs["Color"]
        if not anti:
            out[m] = sa
            continue
        sb = nt.image(img, b, cubic=(m == "disp")).outputs["Color"]
        if m == "nor":
            sb = _turned_normal(nt, sb, turn)
            out[m] = nt.mixv(blend, sa, sb)
        elif m == "diff":
            out[m] = nt.mix(blend, sa, sb)
        else:
            out[m] = nt.mixf(blend, sa, sb)
    if far and out["diff"] is not None:
        c = nt.uv(1.0 / (tile * 6.1), (0.71, 0.13), turn=0.5, vector=uv)
        sc = nt.image(maps["diff"], c).outputs["Color"]
        out["diff"] = nt.mix(nt.math("MULTIPLY", nt.smooth(ctx.dist, 18.0 * tile, 70.0 * tile), 0.6),
                             out["diff"], sc)
    return {"col": out["diff"], "h": out["disp"], "rough": out["rough"], "ncol": out["nor"]}


# --- Terrain: layered scanned sets ----------------------------------------------------------

class Layer:
    """One scanned set in a terrain: ``tile`` m (default: the set's real size),
    ``tint`` (multiply, linear RGB), ``mask(ctx)`` -> socket 0..1 (None: the
    base), ``sharp`` (height-blend contrast), ``rough`` (mul, add), ``normal``
    strength, ``wet`` how porous (darkening when wet), ``lift`` raises its
    heights in the stack (pebbles above mud)."""

    def __init__(self, set_id, tile=None, tint=None, mask=None, sharp=6.0, rough=None, normal=1.0, wet=0.5,
                 lift=0.0, colour=None, uv=None, anti=True, invert=False):
        self.set_id, self.tile, self.tint, self.mask = set_id, tile, tint, mask
        self.sharp, self.rough, self.normal, self.wet, self.lift = sharp, rough, normal, wet, lift
        self.colour = colour                 # a function (nt, col, sample) -> col socket (salt's white)
        self.uv, self.anti = uv, anti        # uv(ctx) -> metres along/across a path (tracks); no anti-tiling
        self.invert = invert                 # heights turned upside down: cracks become raised ridges (salt)


def _blend_factor(nt, mask, h_layer, h_below, sharp):
    """Height blending: the layer shows where mask*2-1 + (its height - the
    height below) is positive, its high points first."""
    v = nt.math("ADD", nt.math("MULTIPLY_ADD", mask, 2.0, -1.0),
                nt.math("MULTIPLY", nt.math("SUBTRACT", h_layer, h_below), 1.6))
    return _clamp(nt, nt.math("MULTIPLY_ADD", v, sharp, 0.5), 0.0, 1.0)


def terrain(p, name, layers, disp=0.0, pad=None, macro=(0.84, 1.1), cavity=0.6, sheen=0.0, wet_mask=None,
            puddles=0.0, normal=1.2, specular=0.5, u=None, post=None, sheet=False):
    """A ground from scanned layers (see the module doc). ``p`` is the scene's
    _Place (its detail, kit, u). ``disp``: height depth in m (High/Ultra
    displace, else bump); ``pad`` (r0, r1) keeps it flat under the product.
    ``wet_mask(ctx)``: where it's wet even when dry weather (stream banks);
    ``puddles``: how much of the ground pools in rain (0..1). Returns the
    material, or None when the base set has no maps."""
    u = p.u if u is None else u
    ultra = p.detail == "ULTRA"
    maps = [scenemaps.textures(L.set_id, p.kit, ultra) for L in layers]
    if not maps or not maps[0].get("diff"):
        return None
    m = _material(name)
    nt = _Nodes(m.node_tree)
    out = nt.output()
    ctx = Ctx(nt, u, p.r)
    col = h = rgh = ncol = None
    for i, (L, mp) in enumerate(zip(layers, maps)):
        if not mp.get("diff"):
            continue
        tile = (L.tile if L.tile is not None else scenemaps.size(L.set_id)[0]) * u
        s = sample(nt, ctx, mp, tile, seed=i, far=(i == 0 and L.uv is None), anti=L.anti,
                   uv=L.uv(ctx) if L.uv is not None else None)
        c = s["col"]
        if L.colour is not None:
            c = L.colour(nt, c, s)
        if L.tint is not None:
            c = nt.mix(1.0, c, tuple(L.tint[:3]) + (1.0,), "MULTIPLY")
        hh = s["h"] if s["h"] is not None else _lum(nt, c)
        if L.invert:
            hh = nt.math("SUBTRACT", 1.0, hh)
            if s["ncol"] is not None:
                s["ncol"] = nt.vmath("MULTIPLY_ADD", s["ncol"], (-1.0, -1.0, 1.0))
                s["ncol"].node.inputs[2].default_value = (1.0, 1.0, 0.0)
        if L.lift:
            hh = nt.math("ADD", hh, L.lift)
        rr = s["rough"] if s["rough"] is not None else 0.8
        if L.rough is not None and not isinstance(rr, float):
            rr = nt.math("MULTIPLY_ADD", rr, L.rough[0], L.rough[1])
        nn = s["ncol"] if s["ncol"] is not None else (0.5, 0.5, 1.0)
        if L.normal != 1.0 and not isinstance(nn, tuple):
            flat = (0.5, 0.5, 1.0)
            nn = nt.mixv(L.normal, flat, nn) if L.normal < 1.0 else nn
        if col is None:
            col, h, rgh, ncol = c, hh, rr, nn
            continue
        f = _blend_factor(nt, L.mask(ctx) if L.mask else 1.0, hh, h, L.sharp)
        col = nt.mix(f, col, c)
        h = nt.mixf(f, h, hh)
        rgh = nt.mixf(f, rgh, rr)
        ncol = nt.mixv(f, ncol, nn)
    # Macro variation: whole metres of ground lighter or darker, a little warmer or cooler.
    broad = nt.noise(ctx.pos, 1.0 / (22.0 * u), 3.0, 0.5).outputs["Fac"]
    col = nt.mix(1.0, col, nt.ramp(broad, [(0.3, (macro[0],) * 3 + (1,)), (0.7, (macro[1],) * 3 + (1,))]),
                 "MULTIPLY")
    hue = nt.new("ShaderNodeHueSaturation")
    hue.inputs["Hue"].default_value = 0.5
    nt.link(nt.math("MULTIPLY_ADD", nt.noise(ctx.pos, 1.0 / (40.0 * u), 2.0).outputs["Fac"], 0.04, 0.48),
            hue.inputs["Hue"])
    nt.link(col, hue.inputs["Color"])
    col = hue.outputs["Color"]
    if cavity < 1.0:
        col = nt.mix(1.0, col, _grey(nt, _cavity(nt, h, deep=cavity)), "MULTIPLY")
    if post is not None:
        col = post(nt, ctx, col)
    rgh = nt.math("SUBTRACT", rgh, nt.math("MULTIPLY", nt.smooth(h, 0.65, 0.95), 0.1))   # worn high points
    # Wet: the weather's Wet everywhere, plus what's always wet (banks); rain pools in the lows.
    wet = env(nt, "Wet")
    if wet_mask is not None:
        wet = nt.math("MAXIMUM", wet, wet_mask(ctx))
    if sheet:                                          # a flood: a film over all but the highest ridges
        pool = nt.math("MULTIPLY", wet, nt.smooth(h, 0.78, 0.6))
    elif puddles:
        pool = nt.math("MULTIPLY", nt.math("MULTIPLY", wet, nt.smooth(h, 0.42, 0.3)),
                       nt.math("MULTIPLY", ctx.patches(9.0, 0.35, 0.6), min(1.0, puddles) * 1.5))
        pool = _clamp(nt, pool, 0.0, 1.0)
    else:
        pool = 0.0
    col, rgh, n_strength = _wet(nt, col, rgh, wet, pool, normal)
    nm = nt.new("ShaderNodeNormalMap", uv_map="UVMap")
    nt.link(n_strength, nm.inputs["Strength"])
    nt.link(ncol, nm.inputs["Color"])
    bsdf = nt.principled(out, col, _clamp(nt, rgh), normal=nm.outputs["Normal"])
    _set(bsdf, specular=specular, sheen=sheen)
    if disp > 0.0:
        dn = nt.new("ShaderNodeDisplacement", in_Midlevel=0.5)
        nt.link(h, dn.inputs["Height"])
        scale = nt.math("MULTIPLY", nt.math("SUBTRACT", 1.0, nt.math("MULTIPLY", pool, 0.7)), disp)
        if pad is not None:
            scale = nt.math("MULTIPLY", nt.smooth(ctx.dist, pad[0], pad[1]), scale)
        nt.link(scale, dn.inputs["Scale"])
        nt.link(dn.outputs[0], out.inputs["Displacement"])
    _displacement(m, p.level["displace"])
    m["plinth_bsdf"] = bsdf.name
    m["plinth_terrain"] = True
    return m


def _lum(nt, colour):
    lum = nt.new("ShaderNodeRGBToBW")
    nt.link(colour, lum.inputs[0])
    return lum.outputs[0]


def _wet(nt, col, rgh, wet, pool, normal):
    """Physically: a wet porous surface darkens (light trapped in the water
    film, ~ albedo squared) and saturates; its gloss rises; standing water
    is a mirror that hides the relief under it. Returns (colour, roughness,
    normal strength)."""
    darker = nt.mix(1.0, col, col, "MULTIPLY")                       # ~ albedo^2
    darker = nt.mix(0.35, darker, col)
    col = nt.mix(nt.math("MULTIPLY", wet, 0.85), col, darker)
    rgh = nt.mixf(nt.math("MULTIPLY", wet, 0.55), rgh, nt.math("MULTIPLY", rgh, 0.35))
    rgh = nt.mixf(pool, rgh, 0.03)
    strength = nt.math("MULTIPLY", normal, nt.math("SUBTRACT", 1.0, nt.math("MULTIPLY", pool, 0.92)))
    return col, rgh, strength


def surface(p, name, set_id, tile=None, tint=None, normal=1.0, rough=None, cavity=0.7, metallic=0.0,
            specular=0.5, uv=None, coat=0.0, wet=True, colour=None):
    """A scanned set on a part (walls, slabs, rocks): one layer of terrain,
    UV-mapped (box UVs in metres), with wetness. None without maps."""
    L = Layer(set_id, tile=tile, tint=tint, rough=rough, colour=colour)
    maps = scenemaps.textures(set_id, p.kit, p.detail == "ULTRA")
    if not maps.get("diff"):
        return None
    m = _material(name)
    nt = _Nodes(m.node_tree)
    out = nt.output()
    ctx = Ctx(nt, p.u, p.r)
    t = (tile if tile is not None else scenemaps.size(set_id)[0]) * p.u
    s = sample(nt, ctx, maps, t, far=False)
    col = s["col"] if L.colour is None else L.colour(nt, s["col"], s)
    if tint is not None:
        col = nt.mix(1.0, col, tuple(tint[:3]) + (1.0,), "MULTIPLY")
    h = s["h"] if s["h"] is not None else _lum(nt, col)
    if cavity < 1.0:
        col = nt.mix(1.0, col, _grey(nt, _cavity(nt, h, deep=cavity)), "MULTIPLY")
    rgh = s["rough"] if s["rough"] is not None else 0.7
    if rough is not None and not isinstance(rgh, float):
        rgh = nt.math("MULTIPLY_ADD", rgh, rough[0], rough[1])
    n_strength = normal
    if wet:
        col, rgh, n_strength = _wet(nt, col, rgh, nt.math("MULTIPLY", env(nt, "Wet"), 0.8), 0.0, normal)
    bsdf = nt.principled(out, col, _clamp(nt, rgh) if not isinstance(rgh, float) else rgh, metallic=metallic)
    if s["ncol"] is not None:
        nm = nt.new("ShaderNodeNormalMap", uv_map="UVMap")
        if isinstance(n_strength, float):
            nm.inputs["Strength"].default_value = n_strength
        else:
            nt.link(n_strength, nm.inputs["Strength"])
        nt.link(s["ncol"], nm.inputs["Color"])
        nt.link(nm.outputs["Normal"], bsdf.inputs["Normal"])
    else:
        b = nt.new("ShaderNodeBump", in_Strength=0.4, in_Distance=0.01 * p.u)
        nt.link(h, b.inputs["Height"])
        nt.link(b.outputs["Normal"], bsdf.inputs["Normal"])
    _set(bsdf, specular=specular, coat=coat)
    m["plinth_bsdf"] = bsdf.name
    return m


# --- Foliage, bark, water --------------------------------------------------------------------

def foliage(p, name, atlas, tint=(1.0, 1.0, 1.0), hue=0.03, value=0.18, translucency=0.35, autumn=0.0):
    """Leaves from an atlas: colour and opacity (alpha clip in Eevee, real
    transparency in Cycles), the atlas normal, light through the leaf
    (translucency: backlit leaves glow), a waxy sheen, and per-instance
    variation (hue, value: no two trees, no two leaves the same)."""
    maps = scenemaps.textures(atlas, p.kit, p.detail == "ULTRA")
    if not maps.get("diff"):
        return None
    m = _material(name)
    nt = _Nodes(m.node_tree)
    out = nt.output()
    uv = nt.new("ShaderNodeUVMap", uv_map="UVMap").outputs["UV"]
    diff = nt.image(maps["diff"], uv)
    info = nt.new("ShaderNodeObjectInfo")
    leaf_rand = nt.attr("leaf_rand").outputs["Fac"]
    rand = nt.math("FRACT", nt.math("ADD", info.outputs["Random"], nt.math("MULTIPLY", leaf_rand, 0.37)))
    hsv = nt.new("ShaderNodeHueSaturation")
    nt.link(nt.math("MULTIPLY_ADD", info.outputs["Random"], 2.0 * hue, 0.5 - hue), hsv.inputs["Hue"])
    nt.link(nt.math("MULTIPLY_ADD", rand, 2.0 * value, 1.0 - value), hsv.inputs["Value"])
    hsv.inputs["Saturation"].default_value = 1.0
    nt.link(diff.outputs["Color"], hsv.inputs["Color"])
    col = nt.mix(1.0, hsv.outputs["Color"], tuple(tint) + (1.0,), "MULTIPLY")
    if autumn:
        col = nt.mix(nt.math("MULTIPLY", nt.smooth(leaf_rand, 1.0 - autumn, 1.0), 0.8), col,
                     (0.32, 0.12, 0.02, 1.0))
    rgh = nt.image(maps["rough"], uv).outputs["Color"] if maps.get("rough") else 0.45
    pr = nt.new("ShaderNodeBsdfPrincipled")
    nt.link(col, pr.inputs["Base Color"])
    if isinstance(rgh, float):
        pr.inputs["Roughness"].default_value = rgh
    else:
        nt.link(nt.math("MULTIPLY_ADD", rgh, 0.8, 0.05), pr.inputs["Roughness"])
    if maps.get("nor"):
        nm = nt.new("ShaderNodeNormalMap", uv_map="UVMap", in_Strength=0.8)
        nt.link(nt.image(maps["nor"], uv).outputs["Color"], nm.inputs["Color"])
        nt.link(nm.outputs["Normal"], pr.inputs["Normal"])
    _set(pr, specular=0.5, sheen=0.15)
    trans = nt.new("ShaderNodeBsdfTranslucent")
    nt.link(nt.mix(1.0, col, (1.0, 1.0, 0.6, 1.0), "MULTIPLY"), trans.inputs["Color"])
    leaf = nt.new("ShaderNodeMixShader")
    leaf.inputs["Fac"].default_value = translucency
    nt.link(pr.outputs[0], leaf.inputs[1])
    nt.link(trans.outputs[0], leaf.inputs[2])
    clear_ = nt.new("ShaderNodeBsdfTransparent")
    cut = nt.new("ShaderNodeMixShader")
    cut.name = "PlinthCut"
    nt.link(nt.math("GREATER_THAN", diff.outputs["Alpha"], 0.5), cut.inputs["Fac"])
    nt.link(clear_.outputs[0], cut.inputs[1])
    nt.link(leaf.outputs[0], cut.inputs[2])
    nt.link(cut.outputs[0], out.inputs["Surface"])
    _alpha_clip(m)
    m["plinth_bsdf"] = pr.name
    m["plinth_foliage"] = True
    return m


def _alpha_clip(m):
    if hasattr(m, "surface_render_method"):
        m.surface_render_method = "DITHERED"
    elif hasattr(m, "blend_method"):
        m.blend_method = "CLIP"
    if hasattr(m, "use_backface_culling"):
        m.use_backface_culling = False
    if hasattr(m, "shadow_method"):
        m.shadow_method = "CLIP"


def bark(p, name, set_id, tint=None, moss=0.0, lichen=0.0, sat=1.0, value=1.0):
    """Scanned bark on a trunk's own UVs (u around in metres, v up), its
    furrows darker, moss on the shaded, wetter side lower down, per-instance
    tone. None without maps."""
    maps = scenemaps.textures(set_id, p.kit, p.detail == "ULTRA")
    if not maps.get("diff"):
        return None
    m = _material(name)
    nt = _Nodes(m.node_tree)
    out = nt.output()
    ctx = Ctx(nt, p.u, p.r)
    tile = scenemaps.size(set_id)[0] * p.u * 0.6
    s = sample(nt, ctx, maps, tile, far=False)
    col = s["col"]
    if sat != 1.0 or value != 1.0:
        hs = nt.new("ShaderNodeHueSaturation")
        hs.inputs["Saturation"].default_value, hs.inputs["Value"].default_value = sat, value
        nt.link(col, hs.inputs["Color"])
        col = hs.outputs["Color"]
    info = nt.new("ShaderNodeObjectInfo")
    col = nt.mix(1.0, col, _grey(nt, nt.math("MULTIPLY_ADD", info.outputs["Random"], 0.3, 0.85)), "MULTIPLY")
    if tint is not None:
        col = nt.mix(1.0, col, tuple(tint) + (1.0,), "MULTIPLY")
    h = s["h"] if s["h"] is not None else _lum(nt, col)
    col = nt.mix(1.0, col, _grey(nt, _cavity(nt, h, deep=0.5)), "MULTIPLY")
    rgh = s["rough"] if s["rough"] is not None else 0.85
    if moss:
        # Moss: low on the trunk and on one side (north-ish: -Y in object space), in the furrows first.
        side = nt.smooth(nt.vmath("DOT_PRODUCT", nt.new("ShaderNodeNewGeometry").outputs["Normal"],
                                  (0.3, -1.0, 0.0)), -0.2, 0.7)
        low = nt.smooth(ctx.z, 2.5 * p.u, 0.2 * p.u)
        mask = nt.math("ADD", nt.math("MULTIPLY", nt.math("MAXIMUM", side, low), 1.6),
                       nt.math("MULTIPLY", nt.math("SUBTRACT", 0.5, h), 1.4))
        mask = nt.math("MULTIPLY", _clamp(nt, nt.math("SUBTRACT", mask, 0.5), 0.0, 1.0), moss)
        mtone = nt.mix(nt.noise(ctx.pos, 4.0 / p.u, 3.0).outputs["Fac"], (0.035, 0.07, 0.012, 1),
                       (0.10, 0.16, 0.03, 1))
        col = nt.mix(mask, col, mtone)
        rgh = nt.mixf(mask, rgh, 0.9)
    col, rgh, ns = _wet(nt, col, rgh, nt.math("MULTIPLY", env(nt, "Wet"), 0.9), 0.0, 1.0)
    bsdf = nt.principled(out, col, _clamp(nt, rgh) if not isinstance(rgh, float) else rgh)
    if s["ncol"] is not None:
        nm = nt.new("ShaderNodeNormalMap", uv_map="UVMap")
        nt.link(ns, nm.inputs["Strength"])
        nt.link(s["ncol"], nm.inputs["Color"])
        nt.link(nm.outputs["Normal"], bsdf.inputs["Normal"])
    _set(bsdf, specular=0.4)
    m["plinth_bsdf"] = bsdf.name
    return m


def water(p, name, colour=(0.035, 0.05, 0.035), clarity=0.6, flow=(0.0, 1.0), speed=0.35, ripple=1.0):
    """A stream or puddle: clear water over its bed. Fresnel reflection at IOR
    1.33 (2% head-on, a mirror at grazing angles), two layers of ripples
    drifting downstream with the Env group's Time (``flow``: the direction in
    UV space; the stream mesh's v runs downstream), a little absorption.
    Blended over the bed in Eevee (no refraction offset); Cycles refracts."""
    m = _material(name)
    nt = _Nodes(m.node_tree)
    out = nt.output()
    u = p.u
    time = env(nt, "Time")
    uvn = nt.new("ShaderNodeUVMap", uv_map="UVMap").outputs["UV"]

    def drift(scale, k):
        off = nt.new("ShaderNodeCombineXYZ")
        nt.link(nt.math("MULTIPLY", time, flow[0] * speed * k), off.inputs[0])
        nt.link(nt.math("MULTIPLY", time, -flow[1] * speed * k), off.inputs[1])
        nt.link(time, off.inputs[2])
        v = nt.vmath("ADD", uvn, off.outputs[0])
        stretch = nt.vmath("MULTIPLY", v, (1.0 / (scale * u), 0.45 / (scale * u), 0.35))
        return nt.noise(stretch, 1.0, 6.0, 0.55, dims="3D").outputs["Fac"]
    h = nt.math("ADD", nt.math("ADD", nt.math("MULTIPLY", drift(0.9, 1.0), 0.5), nt.math("MULTIPLY", drift(0.22, 1.6), 0.35)),
                nt.math("MULTIPLY", drift(0.06, 2.4), 0.15))
    bump = nt.new("ShaderNodeBump", in_Strength=0.7 * ripple, in_Distance=0.012 * u)
    nt.link(h, bump.inputs["Height"])
    glass = nt.new("ShaderNodeBsdfPrincipled")
    glass.inputs["Base Color"].default_value = (0.9, 0.95, 0.92, 1.0)
    glass.inputs["Roughness"].default_value = 0.02
    _set(glass, transmission=1.0, ior=1.333, specular=0.5)
    nt.link(bump.outputs["Normal"], glass.inputs["Normal"])
    # Eevee: a thin tinted film over the bed (Blended), reflecting by Fresnel.
    mix = nt.new("ShaderNodeMixShader")
    fres = nt.new("ShaderNodeLayerWeight", in_Blend=0.12)
    nt.link(bump.outputs["Normal"], fres.inputs["Normal"])
    tinted = nt.new("ShaderNodeBsdfTransparent")
    tinted.inputs["Color"].default_value = tuple(min(1.0, c / max(1e-3, max(colour)) * clarity) for c in colour) + (1.0,)
    gloss = nt.new("ShaderNodeBsdfPrincipled")
    gloss.inputs["Base Color"].default_value = tuple(colour) + (1.0,)
    gloss.inputs["Roughness"].default_value = 0.02
    nt.link(bump.outputs["Normal"], gloss.inputs["Normal"])
    nt.link(fres.outputs["Fresnel"], mix.inputs["Fac"])
    nt.link(tinted.outputs[0], mix.inputs[1])
    nt.link(gloss.outputs[0], mix.inputs[2])
    eevee = nt.new("ShaderNodeOutputMaterial")
    eevee.target = "EEVEE"
    nt.link(mix.outputs[0], eevee.inputs["Surface"])
    out.target = "CYCLES"
    nt.link(glass.outputs[0], out.inputs["Surface"])
    if hasattr(m, "surface_render_method"):
        m.surface_render_method = "BLENDED"
    elif hasattr(m, "blend_method"):
        m.blend_method = "BLEND"
    m["plinth_water"] = True
    return m


def scanned_or(p, fallback, *args, **kw):
    """surface(p, ...) or the fallback()."""
    m = surface(p, *args, **kw)
    return m if m is not None else fallback()
