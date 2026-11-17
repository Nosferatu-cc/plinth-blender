# SPDX-License-Identifier: GPL-3.0-or-later
"""Scenes (0.10, detail 0.11): places for the product instead of the studio backdrop.

Three kinds, side by side in Studio › Scene ▾:

- **Procedural**: built here (Moon, Desert dunes, Salt flats, Neon grid):
  ground, scatter and sky made in code.
- **3D sets**: environments around the product (Modern house, Forest
  clearing, Abandoned lot), at the product's scale.
- **Photo**: a real place from an HDRI (content packs: Poly Haven, CC0) with a
  ground the product really stands on. The ground is projected from the
  photo: each point of it shows the photo in the direction it's seen from the
  camera's height, and its albedo is that colour divided by the light the
  photo throws on a floor (``ground_irradiance``), so lit by the photo it looks
  exactly like the photo, and the product's shadow and reflection fall on it
  in Eevee and Cycles alike.

Detail (0.11): every procedural scene and set is built at one of four levels
(``DETAILS``, scene[DETAIL]): ground resolution (up to ~1.3 M vertices, dense
near the product), how much of the scatter is drawn, which level of detail
scanned assets come in, and whether the ground's height map is real
displacement or only bump. Scatter is instanced (kits.py), so a forest holds
one copy of each tree. With the scene's kit installed (a content pack of
scanned CC0 assets and textures), scenes use it; without it they use
prototypes and materials made here.

Everything a scene makes lives in the ``Plinth_Scene`` collection (inside
Plinth), named ``Plinth_Scene_*``, under one root empty that turns the whole
place (Turn). A scene hides the backdrop, sets the world and dims the studio
lights by its own amount; Studio (no scene) puts all of that back exactly.
Scene materials use the ground's own UVs / object space in real units, which
turn with the place (world space would slide under a turning ground).
"""
import math
import re

import bpy
from mathutils import Vector

from . import hdri, kits, sceneenv, scenemaps, scenemats, studio
from .scenemats import (_Nodes, _cavity, _clamp, _displacement, _grey,  # noqa: F401  (shared)
                        _material)

COLLECTION = "Plinth_Scene"
PREFIX = "Plinth_Scene_"
ROOT = PREFIX + "Root"
WORLD = PREFIX + "World"
KEY = "plinth_scene"                  # bpy scene[KEY]: the scene in use ("" = the studio)
TURN = "plinth_scene_turn"
DETAIL = "plinth_scene_detail"        # bpy scene[DETAIL]: LOW / MEDIUM / HIGH / ULTRA
SAVED_POWER = "plinth_scene_power"    # light[...]: its power before a scene dimmed it
SAVED_WORLD = "plinth_scene_world"    # bpy scene[...]: the world before the scene's own
SAVED_CLIP = "plinth_scene_clip"      # camera data[...]: its clip end before a scene reached further
SAVED_SPEC = "plinth_scene_spec"      # light[...]: (specular factor, seen in reflections) before a scene
KINDS = (("PROCEDURAL", "Procedural"), ("SET", "3D sets"), ("PHOTO", "Photo"))
PHOTO_RIG = 0.1                       # the studio lights' share in a photo scene
CLEAR = 14.0                          # x r: nothing tall nearer than this (the shots stand ~7 r away)
GROUND_HEIGHT = 1.7                   # m: where photo HDRIs are shot from (Poly Haven ~1.6-2 m)
GROUND_RADIUS = 10.0                  # m: the flat ground of a photo scene (where the photo's own
                                      # ground is flat); a bowl rises from its edge to a dome that
                                      # holds every shot, so trees and walls stand instead of smearing

# Detail levels. rings x segments: the ground's polar grid (dense near the
# product); share: how much of each scatter is drawn (a lower level draws a
# prefix of the same points, so it's the same place, thinner); lod: the kit
# assets' level of detail near the product (far ones one level lighter);
# displace: the ground's height map moves the surface (else bump only).
DETAILS = (("LOW", "Low", "Light: big files, slower computers"),
           ("MEDIUM", "Medium", "Balanced"),
           ("HIGH", "High", "Dense ground and scatter, real displacement"),
           ("ULTRA", "Ultra", "Everything: the most realistic, the heaviest"))
# near: how far out (x each group's own near radius) assets come at the level's
# lod; beyond, lod 2 and only the lighter half of a role's assets (Ultra: all).
# Measured with the kits: Forest (the heaviest) ~5 M triangles drawn at Low,
# ~15 M at Medium, ~40 M at High; the others 1-20 M at High.
LEVELS = {"LOW": {"rings": 110, "segments": 256, "share": 0.12, "lod": 2, "near": 0.0, "displace": False,
                  "sub": 3},
          "MEDIUM": {"rings": 200, "segments": 512, "share": 0.3, "lod": 1, "near": 0.5, "displace": False,
                     "sub": 3},
          "HIGH": {"rings": 340, "segments": 1024, "share": 0.45, "lod": 1, "near": 1.0, "displace": True,
                   "sub": 4},
          "ULTRA": {"rings": 620, "segments": 2048, "share": 1.0, "lod": 0, "near": 1.5, "displace": True,
                    "sub": 5}}
DEFAULT_DETAIL = "HIGH"

SCENES = {}                           # key -> spec (filled at the bottom)

# Scatter groups by category (the Items tab's chips) and how much each sways in wind (radians at a full gust).
CATEGORY_WORDS = (("trees", ("tree", "sapling", "canopy", "conifer", "broadleaf")),
                  ("plants", ("grass", "cover", "weed", "plant", "shrub", "hedge", "fern", "flower", "reed",
                              "bush", "moss", "tuft", "scrub")),
                  ("rocks", ("rock", "pebble", "stone", "boulder", "gravel")))
SWAY = {"trees": 0.035, "plants": 0.16, "rocks": 0.0, "props": 0.0}


def _category(group):
    g = group.lower()
    for cat, words in CATEGORY_WORDS:
        if any(w in g for w in words):
            return cat
    return "props"


# --- Reading (safe while drawing) ------------------------------------------------------

def current(scene):
    key = scene.get(KEY, "") if scene is not None else ""
    return key if key in available() else ""


def photo_scenes():
    """Photo scenes from content packs: HDRI items with a ground."""
    return {k: v for k, v in hdri.CONTENT.items() if v.get("ground")}


def available():
    """Every scene: {key: {"label", "description", "kind"}} in menu order."""
    out = {k: v for k, v in SCENES.items()}
    for k, v in photo_scenes().items():
        out[k] = {"label": v["label"], "description": v.get("description", ""), "kind": "PHOTO",
                  "ready": bool(v.get("path"))}
    return out


def label(key):
    if not key:
        return "Studio"
    spec = available().get(key)
    return spec["label"] if spec else key


def turn(scene):
    return float(scene.get(TURN, 0.0)) if scene is not None else 0.0


def detail(scene):
    v = scene.get(DETAIL, DEFAULT_DETAIL) if scene is not None else DEFAULT_DETAIL
    return v if v in LEVELS else DEFAULT_DETAIL


def detail_label(level):
    return next((lab for k, lab, _d in DETAILS if k == level), level)


def has_detail(key):
    """Detail applies to built scenes, not to photos."""
    spec = available().get(key)
    return bool(spec) and spec["kind"] != "PHOTO"


def kit_state(key):
    """'' (no kit for it), 'ready' (installed and used) or 'missing' (its pack's file is gone)."""
    if not key or key not in SCENES:
        return ""
    if kits.kit_path(key):
        return "ready"
    return "missing" if kits.kit_entry(key) else ""


def _r(scene):
    tgt = studio.find_target(scene)
    return 0.5 * float(tgt.get("plinth_scale", 1.0)) if tgt is not None else 0.5


# --- Collection, objects, materials -------------------------------------------------------

def _collection(scene):
    parent = studio.studio_collection(scene)
    col = bpy.data.collections.get(COLLECTION)
    if col is None:
        col = bpy.data.collections.new(COLLECTION)
    if col.name not in parent.children:
        parent.children.link(col)
    return col


def _root(scene):
    col = _collection(scene)
    root = bpy.data.objects.get(ROOT)
    if root is None:
        root = bpy.data.objects.new(ROOT, None)
        root.empty_display_type = "PLAIN_AXES"
        root.hide_viewport = False
    studio._link(root, col)
    return root


def _add(scene, name, data):
    """A scene object, parented to the root (Turn rotates the place)."""
    full = PREFIX + name
    obj = bpy.data.objects.get(full)
    if obj is not None:
        bpy.data.objects.remove(obj)
    obj = bpy.data.objects.new(full, data)
    studio._link(obj, _collection(scene))
    obj.parent = _root(scene)
    obj["plinth_scene_part"] = True
    return obj


# --- Meshes (numpy: fast at a million vertices) ------------------------------------------

def _mesh_from(name, co, faces_flat, sizes, slot_index=None, uv=None):
    """A mesh from vertex coords (N x 3), flat face vertex indices and per-face
    sizes. uv: "xy" (top-down, metres: grounds) or "box" (each face projected
    on its main axis, metres: blocks, rocks, logs); None for no UVs."""
    import numpy as np
    full = PREFIX + name
    old = bpy.data.meshes.get(full)
    if old is not None and old.users == 0:
        bpy.data.meshes.remove(old)
    me = bpy.data.meshes.new(full)
    co = np.asarray(co, np.float32).reshape(-1, 3)
    faces_flat = np.asarray(faces_flat, np.int32)
    sizes = np.asarray(sizes, np.int32)
    me.vertices.add(len(co))
    me.vertices.foreach_set("co", co.ravel())
    me.loops.add(len(faces_flat))
    me.loops.foreach_set("vertex_index", faces_flat)
    me.polygons.add(len(sizes))
    starts = np.zeros(len(sizes), np.int32)
    starts[1:] = np.cumsum(sizes)[:-1]
    me.polygons.foreach_set("loop_start", starts)            # 4.x: sizes follow from the starts
    if slot_index is not None:
        me.polygons.foreach_set("material_index", np.asarray(slot_index, np.int32))
    me.update(calc_edges=True)
    me.validate(clean_customdata=False)
    me.polygons.foreach_set("use_smooth", np.ones(len(me.polygons), bool))
    if uv:
        loop_co = co[faces_flat]
        if uv == "xy":
            uvs = loop_co[:, :2]
        else:
            normals = np.empty(len(me.polygons) * 3, np.float32)
            me.polygons.foreach_get("normal", normals)
            axis = np.abs(normals.reshape(-1, 3)).argmax(axis=1)
            loop_axis = np.repeat(axis, sizes)
            uvs = np.where(loop_axis[:, None] == 2, loop_co[:, [0, 1]],
                           np.where(loop_axis[:, None] == 1, loop_co[:, [0, 2]], loop_co[:, [1, 2]]))
        layer = me.uv_layers.new(name="UVMap")
        layer.data.foreach_set("uv", np.ascontiguousarray(uvs, np.float32).ravel())
    return me


def polar_ground(name, r_in, r_out, rings, segments, height_fn):
    """A round ground, dense near the product and coarse far away (rings grow
    geometrically), heights from height_fn(x, y) (numpy arrays), UVs top-down
    in metres."""
    import numpy as np
    t = np.linspace(0.0, 1.0, rings)
    radii = np.concatenate([[0.0], r_in * (r_out / r_in) ** t])
    ang = np.linspace(0.0, 2.0 * math.pi, segments, endpoint=False)
    rr, aa = np.meshgrid(radii[1:], ang, indexing="ij")
    x, y = (rr * np.cos(aa)).ravel(), (rr * np.sin(aa)).ravel()
    xs = np.concatenate([[0.0], x])
    ys = np.concatenate([[0.0], y])
    zs = height_fn(xs, ys)
    co = np.stack([xs, ys, zs], axis=1)
    seg = np.arange(segments)
    fan = np.stack([np.zeros(segments, np.int64), 1 + seg, 1 + (seg + 1) % segments], axis=1).ravel()
    ring_ids = np.arange(rings - 1)[:, None]
    seg_ids = seg[None, :]
    a = (1 + ring_ids * segments + seg_ids).ravel()
    b = (1 + ring_ids * segments + (seg_ids + 1) % segments).ravel()
    c = (1 + (ring_ids + 1) * segments + (seg_ids + 1) % segments).ravel()
    d = (1 + (ring_ids + 1) * segments + seg_ids).ravel()
    quads = np.stack([a, d, c, b], axis=1).ravel()           # anticlockwise from above: faces up
    flat = np.concatenate([fan, quads]).astype(np.int32)
    sizes = np.concatenate([np.full(segments, 3, np.int32), np.full(len(a), 4, np.int32)])
    return _mesh_from(name, co, flat, sizes, uv="xy")


def _pad(x, y, r0, r1):
    """0 on the flat pad the product stands on, rising to 1 away from it."""
    import numpy as np
    d = np.sqrt(x * x + y * y)
    t = np.clip((d - r0) / max(1e-6, r1 - r0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _waves(x, y, rng, octaves, base, amp, gain=0.5):
    """Smooth fractal height: a sum of randomly turned sine waves (no noise library)."""
    import numpy as np
    h = np.zeros_like(x)
    k, a = 2.0 * math.pi / base, amp
    for _ in range(octaves):
        for _w in range(3):
            th = rng.uniform(0.0, 2.0 * math.pi)
            ph = rng.uniform(0.0, 2.0 * math.pi)
            h += a * np.sin(k * (x * math.cos(th) + y * math.sin(th)) + ph) / 3.0
        k *= 2.03
        a *= gain
    return h


class _Parts:
    """Many small meshes merged into one object (walls, slats, panels): one draw call."""

    def __init__(self):
        self.co, self.faces, self.sizes, self.slots = [], [], [], []
        self.n = 0

    def add(self, verts, faces, slot=0):
        self.co.extend(verts)
        for f in faces:
            self.faces.extend(i + self.n for i in f)
            self.sizes.append(len(f))
            self.slots.append(slot)
        self.n += len(verts)

    def box(self, centre, size, slot=0, turn=0.0):
        cx, cy, cz = centre
        sx, sy, sz = (s / 2.0 for s in size)
        c, s = math.cos(turn), math.sin(turn)
        verts = []
        for dz in (-sz, sz):
            for dx, dy in ((-sx, -sy), (sx, -sy), (sx, sy), (-sx, sy)):
                verts.append((cx + dx * c - dy * s, cy + dx * s + dy * c, cz + dz))
        faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
        self.add(verts, faces, slot)

    def cylinder(self, base, radius, height, sides=8, slot=0, top_radius=None, tilt=(0.0, 0.0)):
        bx, by, bz = base
        tr = radius if top_radius is None else top_radius
        verts = []
        for k, (rad, z) in enumerate(((radius, 0.0), (tr, height))):
            for i in range(sides):
                a = 2.0 * math.pi * i / sides
                verts.append((bx + rad * math.cos(a) + tilt[0] * z, by + rad * math.sin(a) + tilt[1] * z, bz + z))
        faces = [(i, (i + 1) % sides, sides + (i + 1) % sides, sides + i) for i in range(sides)]
        faces.append(tuple(range(sides - 1, -1, -1)))
        if tr > 1e-6:
            faces.append(tuple(range(sides, 2 * sides)))
        self.add(verts, faces, slot)

    def strip(self, rows, slot=0):
        """A ribbon through rows of points [(left, middle, right), ...]: two quads a row (a leaf, a frond)."""
        base = len(rows)
        verts = [p for row in rows for p in row]
        faces = []
        for i in range(base - 1):
            a = 3 * i
            faces.append((a, a + 3, a + 4, a + 1))
            faces.append((a + 1, a + 4, a + 5, a + 2))
        self.add(verts, faces, slot)

    def mesh(self, name, uv="box"):
        return _mesh_from(name, self.co, self.faces, self.sizes, self.slots, uv=uv)


def _with_materials(obj, mats):
    for m in mats:
        obj.data.materials.append(m)
    return obj


# --- Prototypes made in code (when there's no kit) -------------------------------------------

def _proto(name, mesh, mats):
    obj = bpy.data.objects.new(PREFIX + "P_new_" + name, mesh)
    for m in mats:
        mesh.materials.append(m)
    return obj


def _rock_mesh(name, rng, sub, flat=0.55, cuts=7):
    """A rock: an icosphere roughened with fractal noise and chiselled by a few
    flat cuts (real rocks break along planes), flattened, sitting at z=0."""
    import bmesh
    import numpy as np
    from mathutils import noise
    bm = bmesh.new()
    try:
        bmesh.ops.create_icosphere(bm, subdivisions=sub, radius=1.0)
    except TypeError:                                     # very old name of the argument
        bmesh.ops.create_icosphere(bm, subdivisions=sub, diameter=1.0)
    off = Vector(rng.uniform(-50, 50, 3))
    for v in bm.verts:
        n = noise.fractal(v.co * 1.3 + off, 0.75, 2.1, 6)
        v.co *= 1.0 + 0.22 * n + 0.04 * noise.fractal(v.co * 6.0 + off, 0.6, 2.2, 3)
    co = np.array([v.co[:] for v in bm.verts])
    for _ in range(cuts):                                 # flat breaks
        nrm = rng.normal(size=3)
        nrm /= np.linalg.norm(nrm)
        d = rng.uniform(0.55, 0.85)
        over = co @ nrm - d
        co -= np.where(over > 0, over, 0.0)[:, None] * nrm
    co[:, 2] *= flat
    co[:, 2] -= co[:, 2].min() * 0.35                     # a third buried: it sits, not perches
    for v, c in zip(bm.verts, co):
        v.co = c
    me = bpy.data.meshes.new(PREFIX + "Proto_" + name)
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = True
    _box_uv(me)
    return me


def _box_uv(me, scale=1.0):
    """UVs by box projection in object units (x scale): each face on its main axis."""
    import numpy as np
    n = len(me.vertices)
    co = np.empty(n * 3, np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3) * scale
    vi = np.empty(len(me.loops), np.int32)
    me.loops.foreach_get("vertex_index", vi)
    nrm = np.empty(len(me.polygons) * 3, np.float32)
    me.polygons.foreach_get("normal", nrm)
    sizes = np.empty(len(me.polygons), np.int32)
    me.polygons.foreach_get("loop_total", sizes)
    axis = np.repeat(np.abs(nrm.reshape(-1, 3)).argmax(axis=1), sizes)
    lc = co[vi]
    uvs = np.where(axis[:, None] == 2, lc[:, [0, 1]], np.where(axis[:, None] == 1, lc[:, [0, 2]], lc[:, [1, 2]]))
    layer = me.uv_layers.get("UVMap") or me.uv_layers.new(name="UVMap")
    layer.data.foreach_set("uv", np.ascontiguousarray(uvs, np.float32).ravel())


def _rock_protos(p, n, mat, prefix="rock"):
    return [_proto("%s%d" % (prefix, i), _rock_mesh("%s%d" % (prefix, i), p.rng, p.level["sub"]), [mat])
            for i in range(n)]


def _conifer_mesh(name, rng, whorls, per, segs):
    """A conifer one unit tall: a tapering trunk and whorls of drooping fronds
    (ribbons with a raised spine), thinning to a spire. Slot 0 bark, 1 needles."""
    parts = _Parts()
    parts.cylinder((0.0, 0.0, -0.02), 0.016, 0.98, 10, 0, top_radius=0.002)
    golden = math.pi * (3.0 - math.sqrt(5.0))
    for w in range(whorls):
        t = 0.12 + 0.86 * (w / max(1, whorls - 1))
        z = t * 0.98
        length = 0.04 + 0.3 * (1.0 - t) ** 0.95 * rng.uniform(0.85, 1.15)
        count = max(3, int(round(per * (1.0 - 0.6 * t))))
        for f in range(count):
            a = 2.0 * math.pi * f / count + w * golden + rng.uniform(-0.25, 0.25)
            d = Vector((math.cos(a), math.sin(a), 0.0))
            side = Vector((-d.y, d.x, 0.0))
            rise = rng.uniform(0.05, 0.25)
            droop = rng.uniform(0.2, 0.45)
            rows = []
            for k in range(segs + 1):
                s = k / segs
                centre = Vector((0.0, 0.0, z)) + d * (s * length) + Vector((0.0, 0.0, (rise * s - droop * s * s) * length))
                width = length * (0.34 * (1.0 - s) ** 0.6 + 0.04)
                spine = Vector((0.0, 0.0, 0.18 * width))
                rows.append((tuple(centre - side * width), tuple(centre + spine), tuple(centre + side * width)))
            parts.strip(rows, 1)
    return parts.mesh(name)


def _conifer_protos(p, n, bark, needles):
    lv = {"LOW": (9, 6, 3), "MEDIUM": (13, 8, 4), "HIGH": (18, 11, 5), "ULTRA": (26, 15, 7)}[p.detail]
    out = []
    for i in range(n):
        me = _conifer_mesh(PREFIX + "Proto_conifer%d" % i, p.rng, *lv)
        out.append(_proto("conifer%d" % i, me, [bark, needles]))
    return out


def _tuft_mesh(name, rng, blades, segs=5):
    """A grass clump one unit tall: blades rising from a small patch, leaning
    away from its middle and arching over toward their tips, each a folded
    ribbon (a raised midrib) tapering to a point; heights vary a lot."""
    parts = _Parts()
    for _ in range(blades):
        a = rng.uniform(0, 2 * math.pi)
        rad = abs(rng.normal(0, 0.09))
        base = Vector((rad * math.cos(a), rad * math.sin(a), 0.0))
        out = Vector((math.cos(a + rng.normal(0, 0.5)), math.sin(a + rng.normal(0, 0.5)), 0.0))
        lean = rng.uniform(0.1, 0.45) + 0.6 * rad
        arch = rng.uniform(0.1, 0.5)
        h = rng.uniform(0.35, 1.0) ** 0.7
        side = Vector((-out.y, out.x, 0.0))
        w0 = rng.uniform(0.02, 0.04)
        rows = []
        for k in range(segs + 1):
            t = k / segs
            c = base + out * (lean * h * t + arch * h * t ** 3) + Vector((0.0, 0.0, h * t * (1.0 - arch * 0.55 * t * t)))
            w = w0 * (1.0 - t ** 1.6) + 0.0015
            rows.append((tuple(c - side * w), tuple(c + Vector((0.0, 0.0, 0.25 * w))), tuple(c + side * w)))
        parts.strip(rows, 0)
    return parts.mesh(name)


def _tuft_protos(p, n, mat):
    blades = {"LOW": 18, "MEDIUM": 30, "HIGH": 48, "ULTRA": 70}[p.detail]
    return [_proto("tuft%d" % i, _tuft_mesh(PREFIX + "Proto_tuft%d" % i, p.rng, blades), [mat]) for i in range(n)]


def _log_mesh(name, rng, sides=14, rings=10):
    """A fallen log one unit long along X, radius ~0.1, its bark lumpy."""
    from mathutils import noise
    off = Vector(rng.uniform(-50, 50, 3))
    verts, faces = [], []
    for k in range(rings + 1):
        x = -0.5 + k / rings
        for i in range(sides):
            a = 2.0 * math.pi * i / sides
            p = Vector((x, math.cos(a), math.sin(a)))
            rad = 0.1 * (1.0 + 0.12 * noise.fractal(p * 3.0 + off, 0.8, 2.0, 4))
            verts.append((x, rad * math.cos(a), rad * math.sin(a) + 0.09))
    for k in range(rings):
        for i in range(sides):
            a, b = k * sides + i, k * sides + (i + 1) % sides
            faces.append((a, b, b + sides, a + sides))
    faces.append(tuple(range(sides - 1, -1, -1)))
    faces.append(tuple(range(rings * sides, (rings + 1) * sides)))
    parts = _Parts()
    parts.add(verts, faces)
    return parts.mesh(name)


def _size(obj):
    """(dx, dy, dz) of a prototype's mesh, cached on the mesh."""
    me = obj.data
    if me is None:
        return (1.0, 1.0, 1.0)
    if "plinth_size" in me:
        return tuple(me["plinth_size"])
    import numpy as np
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    size = tuple(float(v) for v in (co.max(axis=0) - co.min(axis=0))) if len(co) else (1.0, 1.0, 1.0)
    me["plinth_size"] = size
    return size


# --- A scene being built -----------------------------------------------------------------------

class _Place:
    """What a builder needs: the scene, the product's radius r (u = r / 2.5 is
    one 'metre' for a car), a seeded rng, the detail level and the kit."""

    def __init__(self, scene, key, r, rng, level, kit):
        self.scene, self.key, self.r, self.rng, self.kit = scene, key, r, rng, kit
        self.detail, self.level = level, LEVELS[level]
        self.u = r / 2.5

    def ground(self, r_in, r_out, height_fn, name="Ground", res=1.0, attrs=None):
        """The place's round ground. attrs: {name: fn(x, y) -> 0..1 per vertex}
        stored as point attributes (masks the ground's material reads: wet
        banks, fresh ejecta, paths)."""
        import numpy as np
        lv = self.level
        rings, segs = max(24, int(lv["rings"] * res)), max(64, int(lv["segments"] * res))
        me = polar_ground(name, r_in, r_out, rings, segs, height_fn)
        if attrs:
            co = np.empty(len(me.vertices) * 3, np.float32)
            me.vertices.foreach_get("co", co)
            co = co.reshape(-1, 3).astype(np.float64)
            for key, fn in attrs.items():
                a = me.attributes.new(key, "FLOAT", "POINT")
                a.data.foreach_set("value", np.asarray(fn(co[:, 0], co[:, 1]), np.float32).ravel())
        return _add(self.scene, name, me)

    def terrain(self, name, layers, **kw):
        """A scanned, layered ground material (scenemats.terrain)."""
        return scenemats.terrain(self, name, layers, **kw)

    def surface(self, name, set_id, fallback=None, **kw):
        """A scanned surface for a part, else ``fallback()``."""
        m = scenemats.surface(self, name, set_id, **kw)
        return m if m is not None or fallback is None else fallback()

    def assets(self, role, lod=None):
        return kits.assets(self.kit, role, self.level["lod"] if lod is None else lod)

    def textures(self, name):
        """A scanned set {map: image} (diff, nor, rough, disp): the kit's 4K at
        Ultra, else the maps Plinth ships, else the kit's own; or {}."""
        return scenemaps.textures(name, self.kit, self.detail == "ULTRA")

    def keep(self, n):
        return n if n <= 0 else max(1, int(round(n * self.level["share"])))

    def scatter(self, group, protos, co, proto, rot, scale, share=True, cat=None, sway=None):
        """Instance protos at points (arrays in the root's space). Every point
        is kept; the level's share (and the category's Amount) is the
        instancer's Amount, drawing those whose random 'keep' is below it, so
        a lower detail is a subset of the same place."""
        import numpy as np
        import zlib
        if not protos or len(co) == 0:
            return None
        co = np.asarray(co, np.float32).reshape(-1, 3)
        keep = np.random.default_rng(zlib.crc32((self.key + group).encode())).uniform(0.0, 1.0, len(co))
        obj = kits.scatter(self.scene, _add, _collection(self.scene), group, protos, co, np.asarray(proto),
                           np.asarray(rot), np.asarray(scale), keep=keep)
        cat = cat or _category(group)
        obj["plinth_category"] = cat
        obj["plinth_share"] = self.level["share"] if share else 1.0
        obj["plinth_sway"] = SWAY.get(cat, 0.0) if sway is None else sway
        d = np.sqrt(co[:, 0] ** 2 + co[:, 1] ** 2)
        inner = float(d.min()) if len(d) else 0.0
        kits.set_controls(obj, Amount=obj["plinth_share"], Inner=inner, Gust=6.0 * self.u,
                          Ground=bpy.data.objects.get(PREFIX + "Ground"))
        return obj

    def scatter_lod(self, group, role, co, pick, rot, size, near, fallback=None, axis=2, native=None, lods=None,
                    cat=None, sway=None):
        """Scatter a kit role (or the fallback protos) with size given per point
        in metres along ``axis``: points nearer than ``near`` get the detail's
        own LOD, the rest one level lighter. ``pick`` (0..1) chooses the asset.
        native: per-point factors; kit assets then keep their own real size
        (x u x factor) and ``size`` only sizes the code-made fallback.
        lods: (near, far) kit LODs instead of the level's (tiny pebbles: (2, 2)),
        or {level: (near, far)} for some levels only."""
        import numpy as np
        co = np.asarray(co, np.float32).reshape(-1, 3)
        dist = np.sqrt(co[:, 0] ** 2 + co[:, 1] ** 2)
        if isinstance(lods, dict):
            lods = lods.get(self.detail)
        lod, far_lod = lods or (self.level["lod"], 2)
        near = near * self.level["near"]
        if self.kit and self.assets(role, lod):
            far = self.assets(role, far_lod)
            if self.detail != "ULTRA" and len(far) > 1:      # far away, the lighter half does
                far = sorted(far, key=lambda o: len(o.data.polygons))[:max(1, (len(far) + 1) // 2)]
            groups = [("", dist < near, self.assets(role, lod)),
                      ("_far", dist >= near, far)]
        else:                                              # made in code: one set near and far
            protos = fallback() if callable(fallback) else list(fallback or [])
            groups = [("", np.ones(len(co), bool), protos)]
        made = []
        for tag, sel, protos in groups:
            if not protos or not sel.any():
                continue
            idx = np.minimum((np.asarray(pick)[sel] * len(protos)).astype(np.int32), len(protos) - 1)
            if native is not None and any(o.get("plinth_kit_object") or o.get("plinth_real") for o in protos):
                s = (self.u * np.asarray(native)[sel])[:, None] * np.ones((1, 3))
            else:
                extent = np.array([max(1e-4, _size(o)[axis]) for o in protos])
                s = (np.asarray(size)[sel] / extent[idx])[:, None] * np.ones((1, 3))
            made.append(self.scatter(group + tag, protos, co[sel], idx, np.asarray(rot)[sel], s, cat=cat,
                                     sway=sway))
        return [m for m in made if m is not None]


def _ring_points(p, n, d_min, d_max, power=None, sector=None):
    """n points around the product, between d_min and d_max: even over the area,
    or crowding the inside (``power`` > 1: Pareto-like falloff). ``sector``
    (a0, a1) radians limits the angle."""
    import numpy as np
    rng = p.rng
    a = rng.uniform(*(sector or (0.0, 2.0 * math.pi)), n)
    if power is None:
        d = np.sqrt(rng.uniform(d_min ** 2, d_max ** 2, n))
    else:
        d = d_min + (d_max - d_min) * rng.uniform(0, 1, n) ** power
    return d * np.cos(a), d * np.sin(a)


def _upright(p, n, tilt=0.06):
    """Random yaw, a slight lean (Euler XYZ, radians)."""
    import numpy as np
    rng = p.rng
    return np.stack([rng.normal(0, tilt, n), rng.normal(0, tilt, n), rng.uniform(0, 2 * math.pi, n)], axis=1)


def _tumbled(p, n):
    import numpy as np
    return p.rng.uniform(0, 2 * math.pi, (n, 3))


# --- Materials: kit textures with code-made fallbacks -------------------------------------------

def _sample(nt, tex, scale, offset=(0.0, 0.0)):
    v = nt.uv(scale, offset)
    return {m: nt.image(img, v, cubic=(m == "disp")) for m, img in tex.items()}


def _textured(p, name, sets, tile, disp=0.0, tint=None, pad=None, rough=None, slot_out=None, normal=1.3,
              cavity=0.55, sheen=None):
    """A PBR material from kit texture sets, tiled every ``tile`` m: two samples
    (the second larger and shifted) blended by large noise, plus a broad
    brightness variation, so it never repeats visibly. Depth comes from four
    things together: the normal map (``normal`` strength), a finer bump from
    the height map at 3.7x the frequency, crevices darkened from the height map
    (``cavity``: how dark the lowest parts get), and real displacement at
    High/Ultra (``disp``: the height map's depth in m; ``pad`` (r0, r1) keeps
    it flat only under the product). Roughness follows the roughness map,
    worn smoother on high points. None if the kit lacks the first set."""
    tex_a = p.textures(sets[0])
    if not tex_a.get("diff"):
        return None
    tex_b = p.textures(sets[1]) if len(sets) > 1 else {}
    tex_b = tex_b if tex_b.get("diff") else tex_a
    m = _material(name)
    nt = _Nodes(m.node_tree)
    out = nt.output()
    a = _sample(nt, tex_a, 1.0 / tile)
    b = _sample(nt, tex_b, 1.0 / (tile * 2.37), (0.31, 0.57))
    pos = nt.obj_pos()
    mask = nt.smooth(nt.noise(pos, 1.0 / (6.0 * tile), 3.0).outputs["Fac"], 0.42, 0.6)

    def both(key):
        return nt.mixf(mask, a[key].outputs["Color"], (b.get(key) or a[key]).outputs["Color"])

    colour = nt.mix(mask, a["diff"].outputs["Color"], b["diff"].outputs["Color"])
    broad = nt.ramp(nt.noise(pos, 1.0 / (25.0 * tile), 3.0).outputs["Fac"],
                    [(0.3, (0.8, 0.8, 0.8, 1)), (0.7, (1.12, 1.09, 1.05, 1))])
    colour = nt.mix(1.0, colour, broad, "MULTIPLY")
    if tint is not None:
        colour = nt.mix(1.0, colour, tint, "MULTIPLY")
    if "disp" in a:
        height = both("disp")
    else:                                              # no height map: the albedo's brightness stands in
        lum = nt.new("ShaderNodeRGBToBW")
        nt.link(colour, lum.inputs[0])
        height = lum.outputs[0]
    if cavity < 1.0:
        colour = nt.mix(1.0, colour, _grey(nt, _cavity(nt, height, deep=cavity)), "MULTIPLY")
    if "rough" in a:
        rgh = both("rough")
        if rough is not None:
            rgh = nt.math("ADD", nt.math("MULTIPLY", rgh, rough[0]), rough[1])
    else:
        rgh = nt.math("ADD", 0.7, nt.math("MULTIPLY", nt.noise(pos, 1.0 / tile, 4.0).outputs["Fac"], 0.25))
    rgh = _clamp(nt, nt.math("SUBTRACT", rgh, nt.math("MULTIPLY", nt.smooth(height, 0.6, 0.95), 0.12)))
    nrm = None
    if "nor" in a:
        ncol = nt.mix(mask, a["nor"].outputs["Color"], (b.get("nor") or a["nor"]).outputs["Color"])
        nm = nt.new("ShaderNodeNormalMap", uv_map="UVMap", in_Strength=normal)
        nt.link(ncol, nm.inputs["Color"])
        nrm = nm.outputs["Normal"]
    if "disp" in a:                                    # the fine grain: the height map, smaller, as bump
        fine = nt.image(a["disp"].image, nt.uv(3.7 / tile, (0.13, 0.71)))
        bump = nt.new("ShaderNodeBump", in_Strength=0.55, in_Distance=max(1e-4, 0.08 * tile))
        nt.link(fine.outputs["Color"], bump.inputs["Height"])
        if nrm is not None:
            nt.link(nrm, bump.inputs["Normal"])
        nrm = bump.outputs["Normal"]
    bsdf = nt.principled(out, colour, rgh, normal=nrm)
    if sheen:                                          # dusty, fibrous grounds catch grazing light
        for k in ("Sheen Weight", "Sheen"):
            if k in bsdf.inputs:
                bsdf.inputs[k].default_value = sheen
                break
    if "disp" in a and disp > 0.0:
        dn = nt.new("ShaderNodeDisplacement", in_Midlevel=0.5)
        nt.link(height, dn.inputs["Height"])
        if pad is not None:
            dist = nt.new("ShaderNodeVectorMath", operation="LENGTH")
            nt.link(pos, dist.inputs[0])
            nt.link(nt.math("MULTIPLY", nt.smooth(dist.outputs["Value"], pad[0], pad[1]), disp), dn.inputs["Scale"])
        else:
            dn.inputs["Scale"].default_value = disp
        nt.link(dn.outputs[0], out.inputs["Displacement"])
    _displacement(m, p.level["displace"])
    m["plinth_bsdf"] = bsdf.name
    return m


def _layered(name, colour, rough, metallic=0.0, scale=4.0, depth=0.5, cavity=0.6, rough_var=0.18,
             tones=(0.72, 1.22), grain=True, coat=0.0, specular=0.5, sheen=0.0):
    """A code-made surface with the layers a real one has: broad and fine
    colour variation (``tones``: darkest..lightest x colour), pits and grains
    (voronoi) darkened in their hollows (``cavity``), roughness varying with
    them (``rough_var``), and relief from bumps at two real sizes (``scale``:
    features per metre; ``depth``: how strong). Object space in metres (scene
    parts are at unit scale)."""
    m = _material(name)
    nt = _Nodes(m.node_tree)
    out = nt.output()
    pos = nt.obj_pos()
    broad = nt.noise(pos, 0.12 * scale, 4.0, 0.5).outputs["Fac"]
    meso = nt.noise(pos, scale, 8.0, 0.6).outputs["Fac"]
    fine = nt.noise(pos, 9.0 * scale, 6.0, 0.65).outputs["Fac"]
    pits = nt.voronoi(pos, 2.6 * scale).outputs["Distance"] if grain else meso
    lo = tuple(min(1.0, c * tones[0]) for c in colour[:3]) + (1.0,)
    hi = tuple(min(1.0, c * tones[1]) for c in colour[:3]) + (1.0,)
    col = nt.ramp(broad, [(0.25, lo), (0.75, hi)])
    col = nt.mix(1.0, col, nt.ramp(meso, [(0.3, (0.86, 0.86, 0.86, 1)), (0.7, (1.06, 1.06, 1.06, 1))]), "MULTIPLY")
    height = nt.math("ADD", nt.math("MULTIPLY", meso, 0.6), nt.math("MULTIPLY", pits, 0.4))
    col = nt.mix(1.0, col, _grey(nt, _cavity(nt, height, 0.25, 0.6, cavity)), "MULTIPLY")
    rgh = _clamp(nt, nt.math("ADD", rough, nt.math("MULTIPLY", nt.math("SUBTRACT", meso, 0.5), 2.0 * rough_var)),
                 0.03, 1.0)
    b1 = nt.new("ShaderNodeBump", in_Strength=min(1.0, depth), in_Distance=0.35 / scale)
    nt.link(height, b1.inputs["Height"])
    b2 = nt.new("ShaderNodeBump", in_Strength=min(1.0, 0.6 * depth), in_Distance=0.06 / scale)
    nt.link(fine, b2.inputs["Height"])
    nt.link(b1.outputs["Normal"], b2.inputs["Normal"])
    pr = nt.principled(out, col, rgh, metallic=metallic, normal=b2.outputs["Normal"])
    for k, v in (("Specular IOR Level", specular), ("Coat Weight", coat), ("Sheen Weight", sheen)):
        if k in pr.inputs:
            pr.inputs[k].default_value = v
    if coat and "Coat Roughness" in pr.inputs:
        pr.inputs["Coat Roughness"].default_value = 0.08
    m["plinth_bsdf"] = pr.name
    return m


def _window_glass(name="WindowGlass", tint=(0.86, 0.92, 0.9, 1), rough=0.015):
    """Architectural glass: a thin pane, so light goes straight through (no
    refraction offset), faintly green-tinted, reflecting by Fresnel at IOR 1.52
    (~4% head-on, a mirror at grazing angles). The same shader in both
    engines; Eevee draws it Blended (tinted panes can't be Dithered)."""
    m = _material(name)
    nt = _Nodes(m.node_tree)
    out = nt.output()
    clear_ = nt.new("ShaderNodeBsdfTransparent")
    clear_.inputs["Color"].default_value = tint
    glossy = nt.new("ShaderNodeBsdfGlossy") if hasattr(bpy.types, "ShaderNodeBsdfGlossy") else nt.new(
        "ShaderNodeBsdfAnisotropic")
    glossy.inputs["Roughness"].default_value = rough
    fres = nt.new("ShaderNodeFresnel", in_IOR=1.52)
    mix = nt.new("ShaderNodeMixShader")
    nt.link(fres.outputs[0], mix.inputs["Fac"])
    nt.link(clear_.outputs[0], mix.inputs[1])
    nt.link(glossy.outputs[0], mix.inputs[2])
    nt.link(mix.outputs[0], out.inputs["Surface"])
    if hasattr(m, "surface_render_method"):
        m.surface_render_method = "BLENDED"
    elif hasattr(m, "blend_method"):
        m.blend_method = "BLEND"
    if hasattr(m, "use_transparency_overlap"):
        m.use_transparency_overlap = False
    return m


def _emissive(name, colour, strength, base=(0.9, 0.9, 0.88, 1), day=None):
    """A light-giving surface (bulbs, LED strips, lit rooms): emission over a
    pale diffuse body, so it still reads as a thing when it's off. ``day``
    (0..1): its share of full strength in daylight; the rest comes on with
    the Env group's Night (Time of day). None: always full."""
    m = _material(name)
    nt = _Nodes(m.node_tree)
    out = nt.output()
    pr = nt.principled(out, base, 0.35)
    for k, v in (("Emission Color", colour), ("Emission", colour)):
        if k in pr.inputs:
            pr.inputs[k].default_value = v
            break
    if "Emission Strength" in pr.inputs:
        pr.inputs["Emission Strength"].default_value = strength
        if day is not None:
            on = nt.math("MULTIPLY_ADD", scenemats.env(nt, "Night"), 1.0 - day, day)
            nt.link(nt.math("MULTIPLY", on, strength), pr.inputs["Emission Strength"])
    m["plinth_bsdf"] = pr.name
    return m


def _bark_needles(p):
    bark = _simple("Bark", (0.06, 0.04, 0.025, 1), 0.9, bump_scale=30.0, r=1.0, bump=0.6)
    needles = _material("Needles")
    nt = _Nodes(needles.node_tree)
    out = nt.output()
    info = nt.new("ShaderNodeObjectInfo")
    tone = nt.ramp(info.outputs["Random"], [(0.0, (0.025, 0.06, 0.025, 1)), (1.0, (0.05, 0.10, 0.035, 1))])
    fine = nt.noise(nt.obj_pos(), 140.0, 2.0)
    pr = nt.principled(out, tone, 0.6, fine.outputs["Fac"], 0.5)
    for k, v in (("Subsurface Weight", 0.15), ("Subsurface", 0.15)):
        if k in pr.inputs:
            pr.inputs[k].default_value = v
            break
    return bark, needles


def _grass_mat(name="Grass", a=(0.06, 0.11, 0.025, 1), b=(0.15, 0.2, 0.05, 1), dry=(0.38, 0.33, 0.16, 1),
               dry_share=0.35):
    """Grass blades: each clump its own green, some tips gone straw-dry in
    patches, light shining through them (translucency), a waxy sheen."""
    m = _material(name)
    nt = _Nodes(m.node_tree)
    out = nt.output()
    info = nt.new("ShaderNodeObjectInfo")
    tone = nt.mix(info.outputs["Random"], a, b)
    sep = nt.new("ShaderNodeSeparateXYZ")
    nt.link(nt.obj_pos(), sep.inputs[0])
    patch = nt.smooth(nt.noise(info.outputs["Location"], 0.05, 2.0).outputs["Fac"], 0.62 - 0.3 * dry_share, 0.7)
    tip = nt.math("MULTIPLY", nt.smooth(sep.outputs["Z"], 0.35, 0.95), patch)
    col = nt.mix(tip, tone, dry)
    col = nt.mix(nt.math("MULTIPLY", nt.smooth(sep.outputs["Z"], 0.25, 0.0), 0.5), col, (0.04, 0.035, 0.02, 1))
    pr = nt.new("ShaderNodeBsdfPrincipled")
    nt.link(col, pr.inputs["Base Color"])
    pr.inputs["Roughness"].default_value = 0.42
    scenemats._set(pr, specular=0.5, sheen=0.2)
    tr = nt.new("ShaderNodeBsdfTranslucent")
    nt.link(nt.mix(1.0, col, (1.3, 1.5, 0.7, 1), "MULTIPLY"), tr.inputs["Color"])
    mix = nt.new("ShaderNodeMixShader")
    mix.inputs["Fac"].default_value = 0.3
    nt.link(pr.outputs[0], mix.inputs[1])
    nt.link(tr.outputs[0], mix.inputs[2])
    nt.link(mix.outputs[0], out.inputs["Surface"])
    if hasattr(m, "use_backface_culling"):
        m.use_backface_culling = False
    m["plinth_bsdf"] = pr.name
    m["plinth_foliage"] = True
    return m


# --- World and lights ---------------------------------------------------------------------

def _sky(scene, elevation, rotation, haze=1.0, softness=0.55, brightness=1.0):
    """The clear sky (hdri.py), seen behind the product (no backdrop now)."""
    _restore_world(scene)
    hdri.set_image(scene, hdri.SKY)
    hdri.set_hdri(scene, strength=hdri.SKY_STRENGTH * brightness, background=True)
    hdri.set_sun(scene, elevation=elevation, rotation=rotation, haze=haze, softness=softness)


CLOUDS = PREFIX + "Cloud"


def _clouds(scene, cover=0.4, speed=0.5):
    """A cloud layer over the clear sky: fractal noise projected on a plane
    above (so clouds shrink toward the horizon), lit from the sky's own colour
    (brighter than the sky around them, greyer underneath), thinning out at the
    horizon. ``cover`` 0..1. They drift with the frame (a driver: turntables and
    animations show them move). Lives in Plinth's world; clear() takes it out."""
    world = scene.world
    tree = world.node_tree if world is not None else None
    sky = tree.nodes.get(hdri.PREFIX + "Sky") if tree is not None else None
    tint = tree.nodes.get(hdri.PREFIX + "Tint") if tree is not None else None
    if sky is None or tint is None:
        return None
    _remove_clouds(scene)
    before = {n.name for n in tree.nodes}
    nt = _Nodes(tree)

    def named(n, name):
        n.name = n.label = CLOUDS + name
        n.location = (sky.location.x + 200, sky.location.y - 300)
        return n

    d = named(nt.new("ShaderNodeTexCoord"), "Coord").outputs["Generated"]
    sep = named(nt.new("ShaderNodeSeparateXYZ"), "Sep")
    nt.link(d, sep.inputs[0])
    z = nt.math("MAXIMUM", sep.outputs["Z"], 0.03)
    plane = named(nt.new("ShaderNodeCombineXYZ"), "Plane")
    nt.link(nt.math("DIVIDE", sep.outputs["X"], z), plane.inputs[0])
    nt.link(nt.math("DIVIDE", sep.outputs["Y"], z), plane.inputs[1])
    noise = named(nt.new("ShaderNodeTexNoise"), "Noise")
    noise.noise_dimensions = "4D"
    noise.inputs["Scale"].default_value = 0.9
    noise.inputs["Detail"].default_value = 12.0
    noise.inputs["Roughness"].default_value = 0.58
    if "Distortion" in noise.inputs:
        noise.inputs["Distortion"].default_value = 0.25
    nt.link(plane.outputs[0], noise.inputs["Vector"])
    fc = noise.inputs["W"].driver_add("default_value")
    fc.driver.type = "SCRIPTED"
    fc.driver.expression = "frame * %.6f" % (0.0012 * speed)
    lo = 0.68 - 0.3 * cover
    dens = nt.smooth(noise.outputs["Fac"], lo, lo + 0.14)
    dens = nt.math("MULTIPLY", dens, nt.smooth(sep.outputs["Z"], 0.0, 0.16))
    dens = nt.math("MULTIPLY", dens, 0.96)
    under = nt.smooth(noise.outputs["Fac"], lo + 0.05, lo + 0.35)          # thick middles are greyer below
    lum = named(nt.new("ShaderNodeRGBToBW"), "Lum")
    nt.link(sky.outputs["Color"], lum.inputs[0])
    lit = nt.math("MULTIPLY", lum.outputs[0], nt.math("SUBTRACT", 2.4, nt.math("MULTIPLY", under, 1.1)))
    cloud = _grey(nt, lit)
    warm = nt.mix(0.25, cloud, sky.outputs["Color"])                       # a little of the sky's hue
    out = nt.mix(dens, sky.outputs["Color"], warm)
    # Below the horizon the world is ground, not more glowing haze: reflections
    # in glass and paint show a dark earth beyond the place's own ground.
    below = nt.math("SUBTRACT", 1.0, nt.smooth(sep.outputs["Z"], -0.06, 0.0))
    earth = _grey(nt, nt.math("MULTIPLY", lum.outputs[0], 0.18))
    out = nt.mix(below, out, earth)
    nt.link(out, next(s_ for s_ in tint.inputs if s_.identifier == "A_Color"))
    for n in list(tree.nodes):                         # tag every node made here, for clear()
        if n.name not in before and not n.name.startswith(CLOUDS):
            n.name = CLOUDS + n.name
    return out


def _remove_clouds(scene):
    world = bpy.data.worlds.get(hdri.WORLD)
    tree = world.node_tree if world is not None else None
    if tree is None:
        return
    gone = [n for n in tree.nodes if n.name.startswith(CLOUDS)]
    if not gone:
        return
    for n in gone:
        try:
            n.inputs["W"].driver_remove("default_value")
        except (KeyError, TypeError, AttributeError):
            pass
        tree.nodes.remove(n)
    sky, tint = tree.nodes.get(hdri.PREFIX + "Sky"), tree.nodes.get(hdri.PREFIX + "Tint")
    if sky is not None and tint is not None and world.get("plinth_hdri") == hdri.SKY:
        tree.links.new(sky.outputs["Color"], next(s_ for s_ in tint.inputs if s_.identifier == "A_Color"))


def _own_world(scene, build):
    """A world of the scene's own (stars, a neon gradient), remembering the one before."""
    world = scene.world
    if world is not None and world.name != WORLD and SAVED_WORLD not in scene:
        scene[SAVED_WORLD] = world.name
    hdri.sync_sun(scene)
    w = bpy.data.worlds.get(WORLD) or bpy.data.worlds.new(WORLD)
    if hasattr(w, "use_nodes") and not w.use_nodes:
        w.use_nodes = True
    w.node_tree.nodes.clear()
    nt = _Nodes(w.node_tree)
    out = nt.new("ShaderNodeOutputWorld")
    build(nt, out)
    scene.world = w
    return w


def _restore_world(scene):
    if scene.world is not None and scene.world.name == WORLD:
        before = bpy.data.worlds.get(scene.get(SAVED_WORLD, ""))
        if before is not None:
            scene.world = before
    scene.pop(SAVED_WORLD, None)


def _scene_sun(scene, elevation, rotation, strength, angle_deg=0.5, colour=(1.0, 0.98, 0.95)):
    """A sun lamp of the scene's own (the moon's hard light, the neon dusk)."""
    data = bpy.data.lights.get(PREFIX + "Sun") or bpy.data.lights.new(PREFIX + "Sun", "SUN")
    data.energy, data.color, data.angle = strength, colour, math.radians(angle_deg)
    studio.tune_light(data)
    sun = _add(scene, "Sun", data)
    sun.parent = None                                 # the sky doesn't turn with the place
    sun.rotation_mode = "QUATERNION"
    sun.rotation_quaternion = (-hdri.sun_direction(elevation, rotation)).to_track_quat("-Z", "Y")
    sun["plinth_sun_dir"] = (float(elevation), float(rotation))
    return sun


def _dim_rig(scene, factor):
    """Outdoors the studio lights only lift the product: dimmer, and gone from
    reflections (a softbox mirrored in a puddle or a car's paint gives the
    studio away; the sky and the place are what reflect)."""
    for light in studio.list_lights(scene):
        if SAVED_POWER not in light:
            light[SAVED_POWER] = light.data.energy
        light.data.energy = light[SAVED_POWER] * factor
        if SAVED_SPEC not in light:
            light[SAVED_SPEC] = [float(getattr(light.data, "specular_factor", 1.0)),
                                 float(getattr(light, "visible_glossy", True)),
                                 float(getattr(light, "visible_transmission", True))]
        if hasattr(light.data, "specular_factor"):
            light.data.specular_factor = 0.0
        for attr in ("visible_glossy", "visible_transmission"):
            if hasattr(light, attr):
                setattr(light, attr, False)


def _restore_rig(scene):
    for light in studio.list_lights(scene):
        if SAVED_POWER in light:
            light.data.energy = light[SAVED_POWER]
            del light[SAVED_POWER]
        if SAVED_SPEC in light:
            saved = list(light[SAVED_SPEC]) + [1.0]
            spec, glossy, trans = saved[:3]
            if hasattr(light.data, "specular_factor"):
                light.data.specular_factor = spec
            if hasattr(light, "visible_glossy"):
                light.visible_glossy = bool(glossy)
            if hasattr(light, "visible_transmission"):
                light.visible_transmission = bool(trans)
            del light[SAVED_SPEC]


# --- Apply / clear ------------------------------------------------------------------------

def _remove_parts(keep_kit=False):
    col = bpy.data.collections.get(COLLECTION)
    if col is None:
        return
    keep = {kits.PROTOS + "_Kit"} if keep_kit else set()
    for obj in list(col.all_objects):
        if keep_kit and obj.get("plinth_kit_object"):
            continue
        data = obj.data
        bpy.data.objects.remove(obj)
        if data is not None and data.users == 0 and getattr(data, "name", "").startswith(PREFIX) \
                and not data.name.startswith(kits.KIT):
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
            elif isinstance(data, bpy.types.Light):
                bpy.data.lights.remove(data)

    def drop(c):
        for child in list(c.children):
            drop(child)
            if child.name not in keep and child.name != kits.PROTOS:
                bpy.data.collections.remove(child)
    drop(col)
    if not keep_kit:                              # first what uses the kit's images, then the kit
        mats = [m for m in bpy.data.materials if m.name.startswith(PREFIX) and not m.name.startswith(kits.KIT)
                and m.users == 0]
        if mats:
            bpy.data.batch_remove(mats)
        kits.unload()
        protos = bpy.data.collections.get(kits.PROTOS)
        if protos is not None:
            bpy.data.collections.remove(protos)
    dead = [m for m in bpy.data.meshes if m.name.startswith(PREFIX) and not m.name.startswith(kits.KIT)
            and m.users == 0]
    if dead:
        bpy.data.batch_remove(dead)


def clear(scene, keep_kit=False):
    """Back to the studio: backdrop, world and light power as they were."""
    if not scene.get(KEY) and bpy.data.collections.get(COLLECTION) is None:
        return False
    _remove_parts(keep_kit)
    _restore_clip(scene)
    _remove_clouds(scene)
    sceneenv.remove_atmosphere(scene)
    sceneenv._moonlight(scene, False, None)
    scenemaps.unload()
    scenemats.remove_env()
    bd = bpy.data.objects.get(studio.BACKDROP)
    if bd is not None:
        bd.hide_viewport = bd.hide_render = False
    _restore_world(scene)
    if hdri.settings(scene)["on"] and scene.get(KEY):
        hdri.turn_off(scene)
    _restore_rig(scene)
    scene[KEY] = ""
    scene.pop(TURN, None)
    return True


def apply(scene, key, level=None):
    """Put the product in a scene ("" = back to the studio), at a detail level
    (default: the scene's). Returns an error message, or None."""
    if not key:
        clear(scene)
        return None
    spec = available().get(key)
    if spec is None:
        return "No scene called %s" % key
    if spec["kind"] == "PHOTO" and not photo_scenes()[key].get("path"):
        return "That scene's pack isn't installed"
    if studio.find_target(scene) is None:
        return "Build the studio first"
    if level is not None:
        if level not in LEVELS:
            return "No detail level called %s" % level
        scene[DETAIL] = level
    path = kits.kit_path(key) if spec["kind"] != "PHOTO" else None
    store = bpy.data.collections.get(kits.PROTOS + "_Kit")
    clear(scene, keep_kit=bool(path and store is not None and store.get(kits.LOADED) == path))
    bd = bpy.data.objects.get(studio.BACKDROP)
    if bd is not None:
        bd.hide_viewport = bd.hide_render = True
    import zlib
    import numpy as np
    cfg = sceneenv.config(scene, spec)
    seed = int(cfg.get("seed", 0))
    rng = np.random.default_rng(zlib.crc32((key + ("#%d" % seed if seed else "")).encode()))  # same place every time
    r = _r(scene)
    if spec["kind"] == "PHOTO":
        _build_photo(scene, key, r)
    else:
        kit = kits.load(scene, _collection(scene), path) if path else None
        _root(scene)
        place = _Place(scene, key, r, rng, detail(scene), kit)
        place.seed = seed
        spec["build"](place)
        sky = spec.get("sky")
        if sky is not None and not sky.get("own"):          # 0.12 places: the clear sky and Plinth's atmosphere
            _sky(scene, 30.0, sky.get("rotation", 200.0), haze=sky.get("haze", 1.0), softness=0.55)
            sceneenv.atmosphere(scene, sky.get("cover", 0.35), sky.get("speed", 0.5))
        _finish_materials(scene, r)
        _fit_clip(scene)
    # A photo lights its own scene; the studio rig only lifts the product a little
    # (more and its floor read brighter than the photo around it).
    _dim_rig(scene, spec.get("rig", 0.35) if spec["kind"] != "PHOTO" else PHOTO_RIG)
    scene[KEY] = key
    scene[TURN] = 0.0
    if spec["kind"] != "PHOTO":
        refresh(scene)
    return None


# --- Settings: Variation, Time of day, Weather, Wind, categories (0.12) --------------------------

def settings(scene, key=None):
    """The scene's settings (sceneenv.config), for the one in use or ``key``."""
    key = key or current(scene)
    spec = SCENES.get(key)
    return sceneenv.config(scene, spec) if spec else None


def refresh(scene):
    """Apply the saved settings to the built place: light, weather, wind, categories."""
    key = current(scene) or scene.get(KEY, "")
    spec = SCENES.get(key)
    col = bpy.data.collections.get(COLLECTION)
    if spec is None or col is None:
        return False
    cfg = sceneenv.config(scene, spec)
    r = _r(scene)
    sceneenv.apply(scene, spec, cfg, r / 2.5, col)
    sceneenv.set_wind(col, cfg["wind"])
    for cat, vals in cfg["cats"].items():
        sceneenv.set_category(col, cat, amount=vals["amount"], size=vals["size"], spread=vals["spread"],
                              clearing=cfg["clearing"] * r if cfg["clearing"] else 0.0)
    return True


def set_setting(scene, rebuild=False, **values):
    """Change settings (time, weather, wind, clearing, seed) and apply them:
    live, or by rebuilding the place (Variation)."""
    key = current(scene)
    spec = SCENES.get(key)
    if spec is None:
        return False
    cfg = sceneenv.config(scene, spec)
    times, weathers = sceneenv.allowed(spec)
    if values.get("time") is not None and values["time"] not in times:
        return False
    if values.get("weather") is not None and values["weather"] not in weathers:
        return False
    cfg.update({k: v for k, v in values.items() if v is not None})
    sceneenv.save(scene, cfg)
    if rebuild:
        t = turn(scene)
        apply(scene, key)
        if t:
            set_turn(scene, t)
        return True
    return refresh(scene)


def set_category(scene, cat, **values):
    """A category's Amount (0..2), Size (0.3..3) or Spread (0.5..3), live."""
    key = current(scene)
    spec = SCENES.get(key)
    if spec is None or cat not in dict(sceneenv.CATEGORIES):
        return False
    cfg = sceneenv.config(scene, spec)
    cfg["cats"][cat].update({k: float(v) for k, v in values.items() if k in ("amount", "size", "spread")})
    sceneenv.save(scene, cfg)
    return refresh(scene)


def shuffle(scene):
    """Variation: the same scene, another random layout (rebuilds)."""
    cfg = settings(scene)
    if cfg is None:
        return False
    return set_setting(scene, rebuild=True, seed=(int(cfg.get("seed", 0)) % 9999) + 1)


def categories_in_use(scene):
    """The categories the built place has scatter in: [(key, label)]."""
    col = bpy.data.collections.get(COLLECTION)
    have = {o.get("plinth_category") for o in col.all_objects} if col else set()
    return [(k, lab) for k, lab in sceneenv.CATEGORIES if k in have]


def _fit_clip(scene):
    """Plinth's shot cameras see the whole place (a car's Moon has its Earth
    ~800 m out; Blender's default clip end is 1000 m): their clip end grows to
    the farthest scene part, the old one saved on the camera data."""
    col = bpy.data.collections.get(COLLECTION)
    far = 0.0
    for obj in (col.all_objects if col else ()):
        if obj.type == "MESH" and obj.data is not None and len(obj.data.vertices) > 8:
            for corner in obj.bound_box:
                far = max(far, (obj.matrix_world @ Vector(corner)).length)
    need = far * 1.3
    for cam in _shot_cameras(scene):
        data = cam.data
        if data.clip_end < need:
            if SAVED_CLIP not in data:
                data[SAVED_CLIP] = data.clip_end
            data.clip_end = need


def _shot_cameras(scene):
    try:
        return [c for c in studio.list_shots(scene) if c.type == "CAMERA"]
    except Exception:                                 # noqa: BLE001  (no studio yet)
        return []


def _restore_clip(scene):
    for cam in bpy.data.objects:
        if cam.type == "CAMERA" and SAVED_CLIP in cam.data:
            cam.data.clip_end = cam.data[SAVED_CLIP]
            del cam.data[SAVED_CLIP]


def _finish_materials(scene, r):
    """No scene surface is left flat: wherever a scene material's BSDF still
    has a constant roughness or no normal, it gets roughness that varies (+-0.12
    at two scales) and fine relief, at real sizes (about 2 cm features for a
    car-sized place). Emitters, glass and the kit's own materials are left alone."""
    u = r / 2.5
    col = bpy.data.collections.get(COLLECTION)
    seen = set()
    objs = list(col.all_objects) if col else []
    for c in (bpy.data.collections.get(kits.PROTOS + "_Kit"),):
        if c is not None:
            objs += list(c.all_objects)
    for obj in objs:
        for slot in getattr(obj, "material_slots", ()):
            m = slot.material
            if m is None or m.name in seen or not m.node_tree:
                continue
            seen.add(m.name)
            scenemats.fog_wrap(m)
            if m.name.startswith(kits.KIT) or m.get("plinth_water"):
                continue
            for pr in [n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"]:
                emit = pr.inputs.get("Emission Strength")
                if emit is not None and emit.default_value > 0.0:
                    continue
                nt = _Nodes(m.node_tree)
                pos = None
                rin = pr.inputs["Roughness"]
                if not rin.is_linked and pr.inputs["Metallic"].default_value < 0.5:
                    pos = pos or nt.obj_pos()
                    v = nt.math("ADD", nt.math("MULTIPLY", nt.noise(pos, 0.8 / u, 3.0).outputs["Fac"], 0.6),
                                nt.math("MULTIPLY", nt.noise(pos, 12.0 / u, 4.0).outputs["Fac"], 0.4))
                    nt.link(_clamp(nt, nt.math("ADD", rin.default_value, nt.math("MULTIPLY",
                                                                                nt.math("SUBTRACT", v, 0.5), 0.24))),
                            rin)
                if not pr.inputs["Normal"].is_linked:
                    pos = pos or nt.obj_pos()
                    bump = nt.new("ShaderNodeBump", in_Strength=0.35, in_Distance=0.01 * u)
                    nt.link(nt.noise(pos, 45.0 / u, 6.0, 0.6).outputs["Fac"], bump.inputs["Height"])
                    nt.link(bump.outputs["Normal"], pr.inputs["Normal"])


def set_detail(scene, level):
    """Rebuild the scene in use at another detail level (the same place, its Turn kept)."""
    if level not in LEVELS:
        return False
    scene[DETAIL] = level
    key = current(scene)
    if not key or not has_detail(key):
        return True
    t = turn(scene)
    apply(scene, key)
    if t:
        set_turn(scene, t)
    return True


def set_turn(scene, degrees):
    """Turn the whole place around the product (the sky's sun turns with it)."""
    key = current(scene)
    if not key:
        return False
    old = turn(scene)
    root = bpy.data.objects.get(ROOT)
    if root is not None:
        root.rotation_euler = (0.0, 0.0, math.radians(degrees))
    if key in SCENES:                                  # built places: the sky follows via refresh()
        scene[TURN] = degrees % 360.0
        refresh(scene)                                 # the sky, or the scene's own look(), turns its sun
        return True
    st = hdri.settings(scene)
    if st["on"]:
        hdri.set_hdri(scene, rotation=(st["rotation"] + degrees - old) % 360.0)
    for name in ("PhotoGround", "PhotoDome"):
        mat = bpy.data.materials.get(PREFIX + name)
        rot = mat.node_tree.nodes.get("Turn") if mat is not None else None
        if rot is not None:
            rot.inputs["Rotation"].default_value = (0.0, 0.0, -math.radians(hdri.settings(scene)["rotation"]))
    scene[TURN] = degrees % 360.0
    return True


def _simple(name, colour, rough, metallic=0.0, bump_scale=None, r=1.0, bump=0.2):
    """A code-made surface (kept for its callers' arguments): layered, with relief
    at ``bump_scale``/r features per metre and ``bump`` depth."""
    metal = metallic >= 0.5
    return _layered(name, colour, rough, metallic, scale=(bump_scale or 4.0) / r, depth=0.3 + 0.7 * min(1.0, bump),
                    grain=not metal, rough_var=0.08 if metal else 0.18, cavity=0.75 if metal else 0.6)


def _heights(hfn, x, y):
    import numpy as np
    return hfn(np.asarray(x, float), np.asarray(y, float))


class _Grid:
    """Points binned in square cells: features (craters) touch only the points near them."""

    def __init__(self, x, y, cell):
        import numpy as np
        self.x, self.y, self.cell = x, y, cell
        ix = np.floor(x / cell).astype(np.int64)
        iy = np.floor(y / cell).astype(np.int64)
        key = ix * 1000003 + iy
        self.order = np.argsort(key, kind="stable")
        uniq, start, count = np.unique(key[self.order], return_index=True, return_counts=True)
        self.cells = dict(zip(uniq.tolist(), zip(start.tolist(), count.tolist())))

    def near(self, cx, cy, rad):
        import numpy as np
        c = self.cell
        out = []
        for ix in range(int(math.floor((cx - rad) / c)), int(math.floor((cx + rad) / c)) + 1):
            for iy in range(int(math.floor((cy - rad) / c)), int(math.floor((cy + rad) / c)) + 1):
                hit = self.cells.get(ix * 1000003 + iy)
                if hit:
                    out.append(self.order[hit[0]:hit[0] + hit[1]])
        return np.concatenate(out) if out else np.zeros(0, np.int64)


# --- Procedural: Moon ---------------------------------------------------------------------

# --- Procedural: Desert dunes ---------------------------------------------------------------

# --- Procedural: Salt flats -----------------------------------------------------------------

def _mountains(x, y, rng, r, start, height, base=60.0, sharp=1.6):
    """A ring of ridged mountains beyond ``start`` (x r), rising to ``height``
    (x r); ``base`` (x r) is the widest ridge, ``sharp`` how peaked they are."""
    import numpy as np
    d = np.sqrt(x * x + y * y)
    ridge = np.zeros_like(x)
    k, amp = 2.0 * math.pi / (base * r), 1.0
    for _ in range(6):
        for _w in range(3):
            th, ph = rng.uniform(0, 2 * math.pi), rng.uniform(0, 2 * math.pi)
            ridge += amp * (1.0 - np.abs(np.sin(k * (x * math.cos(th) + y * math.sin(th)) + ph))) / 3.0
        k *= 2.1
        amp *= 0.48
    t = np.clip((d - start * r) / (60.0 * r), 0.0, 1.0)
    return height * r * (t * t * (3 - 2 * t)) * ridge ** sharp


# --- Procedural: Neon grid ------------------------------------------------------------------

def _neon(p):
    import numpy as np
    r, u, rng = p.r, p.u, p.rng
    big = 160.0 * r
    seed = rng.uniform(0, 1000)

    def height(x, y):
        return _mountains(x, y, np.random.default_rng(int(seed)), r, 70.0, 22.0)

    ground = p.ground(1.5 * r, big, height)
    m = _material("NeonFloor")
    nt = _Nodes(m.node_tree)
    out = nt.output()
    pos = nt.obj_pos()
    sep = nt.new("ShaderNodeSeparateXYZ")
    nt.link(pos, sep.inputs[0])
    cell = 2.2 * r

    def glow_of(axis, width=0.012, halo=0.05):
        """A grid line: a hot core and a soft halo falling off around it (reads as bloom)."""
        f = nt.math("FRACT", nt.math("DIVIDE", sep.outputs[axis], cell))
        d = nt.math("SUBTRACT", 0.5, nt.math("ABSOLUTE", nt.math("SUBTRACT", f, 0.5)))      # 0 on the line
        core = nt.math("LESS_THAN", d, width)
        soft = nt.math("MULTIPLY", nt.math("EXPONENT", nt.math("DIVIDE", d, -halo)), 0.35)
        return nt.math("MAXIMUM", core, soft)

    grid = nt.math("MINIMUM", nt.math("MAXIMUM", glow_of("X"), glow_of("Y")), 1.0)
    mountain = nt.smooth(sep.outputs["Z"], 0.3 * r, 2.0 * r)
    contour = nt.math("LESS_THAN", nt.math("FRACT", nt.math("DIVIDE", sep.outputs["Z"], 1.2 * r)), 0.06)
    glow_mask = nt.mixf(mountain, grid, contour)
    glow_col = nt.mix(mountain, (1.0, 0.1, 0.7, 1), (0.0, 0.85, 1.0, 1))
    glow = nt.new("ShaderNodeEmission", in_Strength=9.0)
    nt.link(glow_col, glow.inputs["Color"])
    asphalt = p.textures("asphalt_02")
    gloss = nt.new("ShaderNodeBsdfPrincipled")
    if asphalt.get("diff"):
        smp = _sample(nt, asphalt, 1.0 / (2.5 * u))
        nt.link(nt.mix(1.0, smp["diff"].outputs["Color"], (0.06, 0.04, 0.09, 1), "MULTIPLY"), gloss.inputs["Base Color"])
        puddles = nt.smooth(nt.noise(pos, 0.15 / r, 3.0).outputs["Fac"], 0.5, 0.58)
        nt.link(nt.mixf(puddles, nt.math("MULTIPLY", smp["rough"].outputs["Color"], 0.35) if "rough" in smp else 0.2,
                        0.035), gloss.inputs["Roughness"])
        if "nor" in smp:
            nm = nt.new("ShaderNodeNormalMap", uv_map="UVMap")
            nt.link(smp["nor"].outputs["Color"], nm.inputs["Color"])
            nt.link(nm.outputs["Normal"], gloss.inputs["Normal"])
    else:
        gloss.inputs["Base Color"].default_value = (0.01, 0.005, 0.02, 1)
        gloss.inputs["Roughness"].default_value = 0.08
    mix = nt.new("ShaderNodeMixShader")
    nt.link(glow_mask, mix.inputs["Fac"])
    nt.link(gloss.outputs[0], mix.inputs[1])
    nt.link(glow.outputs[0], mix.inputs[2])
    nt.link(mix.outputs[0], out.inputs["Surface"])
    _displacement(m, False)
    _with_materials(ground, [m])

    # A striped sunset sun behind the mountains.
    sun_parts = _Parts()
    n, rad = 96, 30.0 * r
    sun_parts.add([(rad * math.cos(2 * math.pi * i / n), 0.0, rad * math.sin(2 * math.pi * i / n)) for i in range(n)],
                  [tuple(range(n))])
    disc = _add(p.scene, "SunDisc", sun_parts.mesh("SunDisc", uv=None))
    disc.location = (0.0, 140.0 * r, 14.0 * r)
    sm = _material("NeonSun")
    ns = _Nodes(sm.node_tree)
    so = ns.output()
    obj_co = ns.new("ShaderNodeTexCoord").outputs["Object"]
    ssep = ns.new("ShaderNodeSeparateXYZ")
    ns.link(obj_co, ssep.inputs[0])
    zf = ns.math("DIVIDE", ssep.outputs["Z"], rad)
    stripes = ns.math("GREATER_THAN", ns.math("FRACT", ns.math("MULTIPLY", zf, 7.0)),
                      ns.math("MULTIPLY", ns.math("SUBTRACT", 0.2, zf), 0.9))
    colour = ns.ramp(ns.math("ADD", ns.math("MULTIPLY", zf, 0.5), 0.5),
                     [(0.0, (1.0, 0.05, 0.45, 1)), (1.0, (1.0, 0.75, 0.1, 1))])
    emit = ns.new("ShaderNodeEmission", in_Strength=12.0)
    ns.link(colour, emit.inputs["Color"])
    clear_ = ns.new("ShaderNodeBsdfTransparent")
    mixs = ns.new("ShaderNodeMixShader")
    ns.link(stripes, mixs.inputs["Fac"])
    ns.link(clear_.outputs[0], mixs.inputs[1])
    ns.link(emit.outputs[0], mixs.inputs[2])
    ns.link(mixs.outputs[0], so.inputs["Surface"])
    if hasattr(sm, "surface_render_method"):
        sm.surface_render_method = "BLENDED"
    elif hasattr(sm, "blend_method"):
        sm.blend_method = "BLEND"
    _with_materials(disc, [sm])
    disc.visible_shadow = False

    def dusk(nt, out):
        coord = nt.new("ShaderNodeTexCoord").outputs["Generated"]
        sep = nt.new("ShaderNodeSeparateXYZ")
        nt.link(coord, sep.inputs[0])
        sky = nt.ramp(sep.outputs["Z"], [(0.49, (0.30, 0.02, 0.22, 1)), (0.56, (0.10, 0.01, 0.16, 1)),
                                         (0.8, (0.005, 0.0, 0.02, 1))])
        bg = nt.new("ShaderNodeBackground", in_Strength=1.0)
        nt.link(sky, bg.inputs["Color"])
        nt.link(bg.outputs[0], out.inputs["Surface"])

    _own_world(p.scene, dusk)
    _scene_sun(p.scene, 6.0, 0.0, 2.5, angle_deg=4.0, colour=(1.0, 0.35, 0.55))


# --- 3D sets: shared bits -------------------------------------------------------------------

def _lamp(scene, name, location, colour, power, radius=0.05, day=None):
    """A point light (a bulb's glow on what's around it), parented to the place.
    ``day``: its share of ``power`` in daylight (Time of day brings the rest)."""
    data = bpy.data.lights.get(PREFIX + name) or bpy.data.lights.new(PREFIX + name, "POINT")
    data.energy, data.color = power, colour
    data.shadow_soft_size = radius
    studio.tune_light(data)
    obj = _add(scene, name, data)
    obj.location = location
    if day is not None:
        obj["plinth_lamp"], obj["plinth_lamp_day"] = float(power), float(day)
    return obj


def _light_kit_bulbs(p, colour, strength):
    """Kit lamps light up: every kit material named like a bulb or lamp glass emits."""
    for m in bpy.data.materials:
        if not (m.name.startswith(kits.KIT) and re.search(r"(bulb|lamp.*glass|light_glass)", m.name.lower())):
            continue
        pr = next((n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None) if m.node_tree else None
        if pr is None:
            continue
        for k in ("Emission Color", "Emission"):
            if k in pr.inputs:
                for l in list(pr.inputs[k].links):
                    m.node_tree.links.remove(l)
                pr.inputs[k].default_value = colour
                break
        if "Emission Strength" in pr.inputs:
            pr.inputs["Emission Strength"].default_value = strength


def _bevel(obj, width, segments=2):
    """Rounded edges (no real edge is knife sharp): a render-time modifier."""
    mod = obj.modifiers.new("Plinth_Bevel", "BEVEL")
    mod.width, mod.segments = width, segments
    mod.limit_method = "ANGLE"
    if hasattr(mod, "harden_normals"):
        mod.harden_normals = True
    return mod


def _surface(p, name, sets, tile, fallback, **kw):
    """A kit texture material, else the fallback (a callable making one)."""
    m = _textured(p, name, sets, tile, **kw)
    return m if m is not None else fallback()


def _lawn(p, hfn, big, name="Lawn", colour_a=(0.05, 0.11, 0.03, 1), colour_b=(0.10, 0.19, 0.05, 1),
          sets=("leafy_grass", "sparse_grass"), tile=2.0, tint=(0.62, 0.95, 0.45, 1)):
    r, u = p.r, p.u
    ground = p.ground(1.5 * r, big, hfn)

    def code():
        m = _material(name)
        nt = _Nodes(m.node_tree)
        out = nt.output()
        pos = nt.obj_pos()
        tone = nt.ramp(nt.noise(pos, 0.25 / r, 6.0).outputs["Fac"], [(0.35, colour_a), (0.7, colour_b)])
        nt.principled(out, tone, 0.9, nt.noise(pos, 14.0 / r, 4.0).outputs["Fac"], 0.35)
        return m
    _with_materials(ground, [_surface(p, name, list(sets), tile * u, code, disp=0.03 * u, pad=(1.0 * r, 1.4 * r),
                                      tint=tint)])
    return ground


def _plant_points(p, n, keep_out, d_min, d_max, power=None, sector=None):
    """Points for plants, minus those inside any keep-out box (x0, y0, x1, y1)."""
    import numpy as np
    x, y = _ring_points(p, n, d_min, d_max, power=power, sector=sector)
    ok = np.ones(len(x), bool)
    for x0, y0, x1, y1 in keep_out:
        ok &= ~((x > x0) & (x < x1) & (y > y0) & (y < y1))
    return x[ok], y[ok]


# --- 3D sets: Modern house ------------------------------------------------------------------

# --- 3D sets: Forest clearing ---------------------------------------------------------------

# --- 3D sets: Abandoned lot -----------------------------------------------------------------

def _abandoned(p):
    import numpy as np
    r, u, rng, scene = p.r, p.u, p.rng, p.scene
    big = 120.0 * r
    seed = rng.uniform(0, 1000)

    def hfn(x, y):
        return _waves(x, y, np.random.default_rng(int(seed)), 3, 20.0 * r, 0.02 * r)

    ground = p.ground(1.5 * r, big, hfn)

    def code():
        m = _material("Asphalt")
        nt = _Nodes(m.node_tree)
        out = nt.output()
        pos = nt.obj_pos()
        cracks = nt.voronoi(pos, 0.35 / r, "DISTANCE_TO_EDGE")
        crack = nt.math("LESS_THAN", nt.math("ADD", cracks.outputs["Distance"],
                                             nt.math("MULTIPLY", nt.noise(pos, 3.0 / r, 4.0).outputs["Fac"], 0.04)),
                        0.03)
        base = nt.ramp(nt.noise(pos, 0.2 / r, 6.0).outputs["Fac"],
                       [(0.3, (0.035, 0.035, 0.037, 1)), (0.7, (0.085, 0.082, 0.08, 1))])
        colour = nt.mix(nt.math("MULTIPLY", crack, 0.8), base, (0.01, 0.01, 0.01, 1))
        puddle = nt.math("GREATER_THAN", nt.noise(pos, 0.12 / r, 3.0).outputs["Fac"], 0.6)
        rough = nt.math("ADD", nt.math("MULTIPLY", puddle, -0.8), 0.88)
        nt.principled(out, colour, rough, nt.math("SUBTRACT", nt.noise(pos, 10.0 / r, 6.0).outputs["Fac"],
                                                   nt.math("MULTIPLY", crack, 0.5)), 0.35)
        _displacement(m, False)
        return m
    from .places import common as C

    def marks(nt, ctx, col):
        """Faded parking bays (white paint, worn through by tyres), oil stains."""
        bay = nt.math("ABSOLUTE", nt.math("SUBTRACT", nt.math("FRACT", nt.math("DIVIDE", ctx.x, 2.6 * u)), 0.5))
        line = nt.math("MULTIPLY", nt.smooth(bay, 0.485, 0.495),
                       nt.math("MULTIPLY", nt.smooth(ctx.y, 5.5 * u, 5.0 * u), nt.smooth(ctx.y, 0.5 * u, 1.0 * u)))
        worn = nt.smooth(nt.noise(ctx.pos, 1.0 / (0.4 * u), 6.0).outputs["Fac"], 0.42, 0.62)
        col = nt.mix(nt.math("MULTIPLY", line, nt.math("MULTIPLY", worn, 0.75)), col, (0.62, 0.6, 0.55, 1.0))
        oil = nt.math("MULTIPLY", ctx.patches(2.2, 0.62, 0.7, seed=4.0), 0.7)
        return nt.mix(oil, col, (0.012, 0.011, 0.01, 1.0))

    layers = [C.Layer("worn_asphalt"),
              C.Layer("road_damaged", mask=lambda c: c.patches(9.0, 0.48, 0.62, seed=1.0), sharp=5.0),
              C.Layer("asphalt_02", mask=lambda c: c.patches(14.0, 0.55, 0.7, seed=2.0), sharp=4.0),
              C.Layer("cracked_concrete", tile=3.0, mask=lambda c: c.mul(c.near(30.0 * r, 34.0 * r),
                                                                          c.patches(20.0, 0.6, 0.7, seed=3.0)),
                      sharp=8.0)]
    asphalt = p.terrain("Asphalt", layers, disp=0.02 * u, pad=(1.0 * r, 1.4 * r), puddles=1.0, post=marks,
                        cavity=0.55) or code()
    _with_materials(ground, [asphalt])

    junk = _Parts()                                     # 0 concrete, 1 rust, 2 brick
    for k in range(6):                                  # shipping containers, some stacked
        x = (-16.0 + 6.5 * k) * u
        y = (20.0 + rng.uniform(-1.5, 1.5)) * u
        t = rng.uniform(-0.08, 0.08)
        junk.box((x, y, 1.3 * u), (2.45 * u, 6.1 * u, 2.6 * u), 1, turn=t)
        if rng.uniform() < 0.5:
            junk.box((x + rng.uniform(-0.3, 0.3) * u, y, 3.9 * u), (2.45 * u, 6.1 * u, 2.6 * u), 1,
                     turn=t + rng.uniform(-0.1, 0.1))
    for k in range(14):                                 # a broken brick wall, its top ragged
        x = (-24.0 + 1.6 * k) * u
        h = rng.uniform(0.8, 3.2) * u
        junk.box((x, -18.0 * u, h / 2), (1.6 * u, 0.4 * u, h), 2)
    barriers = p.assets("barriers")
    if not barriers:                                    # jersey barriers in a broken line
        for k in range(9):
            a = math.radians(-60 + 15 * k + rng.uniform(-6, 6))
            d = rng.uniform(9.0, 11.0) * u
            x, y = d * math.sin(a), d * math.cos(a)
            t = a + rng.uniform(-0.25, 0.25)
            junk.box((x, y, 0.4 * u), (3.0 * u, 0.55 * u, 0.8 * u), 0, turn=-t)
            junk.box((x, y, 0.95 * u), (3.0 * u, 0.25 * u, 0.3 * u), 0, turn=-t)
    obj = _add(scene, "Junk", junk.mesh("Junk"))
    if p.detail in ("HIGH", "ULTRA"):
        _bevel(obj, 0.02 * u)

    def rust_code():
        rust = _material("Rust")
        nr = _Nodes(rust.node_tree)
        ro = nr.output()
        rp = nr.obj_pos()
        rsep = nr.new("ShaderNodeSeparateXYZ")
        nr.link(rp, rsep.inputs[0])
        ribs = nr.math("ADD", nr.math("ABSOLUTE", nr.math("SINE", nr.math("DIVIDE", rsep.outputs["X"], 0.05 * u))),
                       nr.math("ABSOLUTE", nr.math("SINE", nr.math("DIVIDE", rsep.outputs["Y"], 0.05 * u))))
        patches = nr.noise(rp, 0.8 / u, 6.0).outputs["Fac"]
        colour = nr.ramp(patches, [(0.35, (0.05, 0.13, 0.18, 1)), (0.55, (0.22, 0.07, 0.03, 1)),
                                   (0.8, (0.35, 0.14, 0.05, 1))])
        nr.principled(ro, colour, 0.8, nr.math("ADD", nr.math("MULTIPLY", ribs, 0.6),
                                               nr.math("MULTIPLY", patches, 0.2)), 0.7)
        return rust
    weathered = _surface(p, "Weathered", ["cracked_concrete"], 2.0 * u,
                         lambda: _simple("Weathered", (0.2, 0.195, 0.18, 1), 0.85, bump_scale=6.0, r=u, bump=0.3))
    rust = _surface(p, "Rust", ["rusty_corrugated_iron", "container_side"], 2.0 * u, rust_code)
    brick = _surface(p, "Brick", ["brick_wall_001"], 2.0 * u,
                     lambda: _simple("Brick", (0.28, 0.09, 0.05, 1), 0.9, bump_scale=10.0, r=u, bump=0.5))
    _with_materials(obj, [weathered, rust, brick])

    if barriers:                                       # scanned barriers in the same broken line
        co, rot = [], []
        for k in range(9):
            a = math.radians(-60 + 15 * k + rng.uniform(-6, 6))
            d = rng.uniform(9.0, 11.0) * u
            co.append((d * math.sin(a), d * math.cos(a), 0.0))
            rot.append((0.0, 0.0, -(a + rng.uniform(-0.25, 0.25))))
        p.scatter("Barriers", barriers, co, [k % len(barriers) for k in range(9)], rot, [(u, u, u)] * 9,
                  share=False)
    # Junk: barrels, tyres, crates, a bin, a gas bottle, scattered and some knocked over.
    x, y = _ring_points(p, 90, 5.0 * u, 26.0 * u, power=1.1)
    rot = _upright(p, len(x), 0.04)
    fallen = rng.uniform(0, 1, len(x)) < 0.3
    rot[fallen, 0] = math.pi / 2
    p.scatter_lod("Debris", "junk", np.stack([x, y, np.zeros(len(x))], 1), rng.uniform(0, 1, len(x)), rot,
                  rng.uniform(0.6, 1.0, len(x)) * u, 30 * r, fallback=lambda: _junk_protos(p),
                  native=rng.uniform(0.9, 1.1, len(x)), axis=2)
    _warehouse(p)
    # A chain-link fence and power poles along the back, a street lamp.
    for role, start, end, step_m, yaw in (("fence", (-26.0, 28.0), (26.0, 28.0), None, 0.0),
                                          ("poles", (-30.0, 34.0), (30.0, 34.0), 15.0, 0.0)):
        protos = p.assets(role)
        if not protos:
            continue
        length = max(0.5, _size(protos[0])[0]) if step_m is None else step_m
        n = int((end[0] - start[0]) * u / (length * u)) + 1
        co = [((start[0] * u + i * length * u), start[1] * u, 0.0) for i in range(n)]
        p.scatter(role.title(), protos[:1], co, [0] * n, [(0.0, 0.0, yaw)] * n, [(u, u, u)] * n, share=False)
    lamps = p.assets("lamps")
    sodium = (1.0, 0.52, 0.18)
    spots = [((-12.0, 12.0), 0.4), ((14.0, -10.0), 2.6), ((-18.0, -9.0), 1.2)]
    if lamps:
        p.scatter("Lamps", lamps, [(x * u, y * u, 0.0) for (x, y), _t in spots], [0] * len(spots),
                  [(0.0, 0.0, t) for _xy, t in spots], [(u, u, u)] * len(spots), share=False)
        _light_kit_bulbs(p, sodium + (1.0,), 60.0)
        head = _size(lamps[0])[2] * u * 0.93
    else:                                               # code-made: a post, an arm and a glowing head
        post = _Parts()
        heads = _Parts()
        head = 5.6 * u
        for (x, y), t in spots:
            post.cylinder((x * u, y * u, 0.0), 0.09 * u, 5.8 * u, 12, 0, top_radius=0.06 * u)
            heads.box((x * u, y * u, head + 0.12 * u), (0.5 * u, 0.25 * u, 0.12 * u), turn=t)
        _with_materials(_add(scene, "Posts", post.mesh("Posts")), [_simple("Posts", (0.12, 0.12, 0.12, 1), 0.5,
                                                                            metallic=0.9, bump_scale=6.0, r=u)])
        _with_materials(_add(scene, "Heads", heads.mesh("Heads")), [_emissive("Sodium", sodium + (1.0,), 60.0,
                                                                              day=0.0)])
    for i, ((x, y), _t) in enumerate(spots):
        _lamp(scene, "Sodium%d" % i, (x * u, y * u, head), sodium, 2200.0 * u * u, 0.08 * u, day=0.0)
    # Weeds in the cracks, thick against the walls and barriers.
    weed = _grass_mat("Weeds", (0.06, 0.08, 0.02, 1), (0.16, 0.15, 0.05, 1))
    x, y = _ring_points(p, 6000, 2.6 * r, 40.0 * r, power=0.9)
    p.scatter_lod("Weeds", "weeds", np.stack([x, y, np.zeros(len(x))], 1), rng.uniform(0, 1, len(x)),
                  _upright(p, len(x), 0.15), rng.uniform(0.1, 0.4, len(x)) * u, 10 * r,
                  fallback=lambda: _tuft_protos(p, 4, weed), native=rng.uniform(0.6, 1.2, len(x)))
    # The sky (dusk by default: the sodium lamps on) comes from the scene's SKY spec.


def _junk_protos(p):
    """Code-made junk (no kit): an oil drum, a lying tyre, a pallet, a crate, each about 1 unit tall/across."""
    u = p.u
    rust = p.surface("Drum", "rusty_metal_02", tile=1.0, metallic=0.0,
                     fallback=lambda: _simple("Drum", (0.25, 0.1, 0.04, 1), 0.7, bump_scale=10.0, r=u))
    rubber = _layered("Rubber", (0.025, 0.025, 0.027, 1), 0.75, scale=30.0, depth=0.4, cavity=0.8)
    wood = p.surface("PalletWood", "weathered_plank_siding", tile=1.0, tint=(1.1, 1.0, 0.9),
                     fallback=lambda: _simple("PalletWood", (0.3, 0.22, 0.14, 1), 0.85, bump_scale=20.0, r=u))
    out = []
    drum = _Parts()
    drum.cylinder((0, 0, 0), 0.29, 0.88, 28, 0)
    for z in (0.29, 0.58):
        drum.cylinder((0, 0, z - 0.012), 0.297, 0.024, 28, 0)
    out.append(_proto("drum", drum.mesh("Proto_drum"), [rust]))
    tyre = _Parts()
    sides, rings = 28, 10
    verts, faces = [], []
    for i in range(sides):
        a = 2 * math.pi * i / sides
        for j in range(rings):
            b = 2 * math.pi * j / rings
            rr = 0.31 + 0.1 * math.cos(b)
            verts.append((rr * math.cos(a), rr * math.sin(a), 0.11 + 0.11 * math.sin(b)))
    for i in range(sides):
        for j in range(rings):
            a0, a1 = i * rings + j, ((i + 1) % sides) * rings + j
            faces.append((a0, a1, ((i + 1) % sides) * rings + (j + 1) % rings, i * rings + (j + 1) % rings))
    tyre.add(verts, faces)
    out.append(_proto("tyre", tyre.mesh("Proto_tyre"), [rubber]))
    pallet = _Parts()
    for k in range(7):
        pallet.box((0, -0.5 + k * 0.166, 0.13), (1.2, 0.1, 0.022))
    for k in (-0.55, 0.0, 0.55):
        pallet.box((k, 0, 0.06), (0.1, 1.0, 0.1))
    out.append(_proto("pallet", pallet.mesh("Proto_pallet"), [wood]))
    crate = _Parts()
    crate.box((0, 0, 0.3), (0.8, 0.6, 0.6))
    out.append(_proto("crate", crate.mesh("Proto_crate"), [wood]))
    for o in out:
        o["plinth_real"] = True                       # metres: scaled by the scene's unit, like a kit's scan
    return out


def _warehouse(p):
    """A derelict corrugated warehouse behind the lot: rusted walls, a dark
    row of broken high windows, a roller door."""
    u = p.u
    parts = _Parts()                                     # 0 corrugated, 1 dark glass, 2 concrete
    x0, y0 = 0.0, 34.0 * u
    parts.box((x0, y0 + 9.0 * u, 4.0 * u), (40.0 * u, 18.0 * u, 8.0 * u), slot=0)
    parts.box((x0, y0 + 9.0 * u, 8.6 * u), (41.0 * u, 19.0 * u, 1.2 * u), slot=0)
    parts.box((x0, y0, 0.6 * u), (40.4 * u, 0.4 * u, 1.2 * u), slot=2)
    for k in range(12):
        if p.rng.uniform() < 0.25:
            continue
        parts.box(((-16.5 + 3.0 * k) * u, y0 - 0.02 * u, 6.2 * u), (2.2 * u, 0.06 * u, 1.2 * u), slot=1)
    parts.box((8.0 * u, y0 - 0.03 * u, 2.6 * u), (5.0 * u, 0.06 * u, 4.4 * u), slot=0)
    obj = _add(p.scene, "Warehouse", parts.mesh("Warehouse"))
    iron = p.surface("Corrugated", "rusty_corrugated_iron", tile=2.0,
                     fallback=lambda: _simple("Corrugated", (0.25, 0.12, 0.06, 1), 0.7, bump_scale=6.0, r=u))
    dark = _layered("BrokenGlass", (0.02, 0.022, 0.025, 1), 0.08, scale=8.0 / u, depth=0.2)
    conc = p.surface("Footing", "concrete_wall_008", tile=2.7,
                     fallback=lambda: _simple("Footing", (0.4, 0.39, 0.37, 1), 0.85, bump_scale=6.0, r=u))
    _with_materials(obj, [iron, dark, conc])


def _distant(mat, r, start, end, colour, rough):
    """Far from the product a ground becomes ``colour`` (the canopy over wooded hills)."""
    bsdf = mat.node_tree.nodes.get(mat.get("plinth_bsdf", "")) if mat.get("plinth_bsdf") else None
    if bsdf is None:
        bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        return
    nt = _Nodes(mat.node_tree)
    dist = nt.new("ShaderNodeVectorMath", operation="LENGTH")
    nt.link(nt.obj_pos(), dist.inputs[0])
    far = nt.smooth(dist.outputs["Value"], start, end)
    col_in = bsdf.inputs["Base Color"]
    src = col_in.links[0].from_socket if col_in.links else tuple(col_in.default_value)
    mottled = nt.mix(nt.noise(nt.obj_pos(), 0.3 / r, 4.0).outputs["Fac"], colour,
                     tuple(min(1.0, 2.2 * c) for c in colour[:3]) + (1.0,))
    nt.link(nt.mix(far, src, mottled), col_in)
    r_in = bsdf.inputs["Roughness"]
    rsrc = r_in.links[0].from_socket if r_in.links else r_in.default_value
    nt.link(nt.mixf(far, rsrc, rough), r_in)


def _puddles(mat, r):
    """Puddles on a ground material: where large noise is high, glassy and darker."""
    bsdf = mat.node_tree.nodes.get(mat.get("plinth_bsdf", "")) if mat.get("plinth_bsdf") else None
    if bsdf is None:
        return
    nt = _Nodes(mat.node_tree)
    wet = nt.smooth(nt.noise(nt.obj_pos(), 0.12 / r, 3.0).outputs["Fac"], 0.58, 0.63)
    rough_in = bsdf.inputs["Roughness"]
    old = rough_in.links[0].from_socket if rough_in.links else None
    nt.link(nt.mixf(wet, old if old is not None else rough_in.default_value, 0.12), rough_in)
    col_in = bsdf.inputs["Base Color"]
    if col_in.links:
        nt.link(nt.mix(nt.math("MULTIPLY", wet, 0.55), col_in.links[0].from_socket, (0.0, 0.0, 0.0, 1)), col_in)
    nrm = bsdf.inputs["Normal"]
    if nrm.links:                                       # still water is flat
        flat = nt.new("ShaderNodeNewGeometry").outputs["Normal"]
        mixn = nt.new("ShaderNodeMix", data_type="VECTOR")
        sock = {s.identifier: s for s in mixn.inputs}
        nt.link(wet, sock["Factor_Float"])
        nt.link(nrm.links[0].from_socket, sock["A_Vector"])
        nt.link(flat, sock["B_Vector"])
        nt.link(next(s for s in mixn.outputs if s.identifier == "Result_Vector"), nrm)



# --- Photo scenes --------------------------------------------------------------------------

def ground_irradiance(image, max_width=256):
    """Light an upward-facing floor gets from an equirectangular HDRI at
    strength 1 (cosine-weighted upper hemisphere). Plain numpy on the pixels."""
    import numpy as np
    w, h = image.size
    if not w or not h:
        return 1.0
    px = np.empty(w * h * 4, np.float32)
    image.pixels.foreach_get(px)
    px = px.reshape(h, w, 4)[:, :, :3]
    step = max(1, w // max_width)
    px = px[::step, ::step]
    hh, ww = px.shape[:2]
    lum = px @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    lat = (np.arange(hh) + 0.5) / hh * math.pi - math.pi / 2.0      # rows bottom-up: -90..90
    cos_t = np.clip(np.sin(lat), 0.0, None)                            # angle from straight up
    d_omega = np.cos(lat) * (math.pi / hh) * (2.0 * math.pi / ww)
    return max(1e-4, float((lum * (cos_t * d_omega)[:, None]).sum()))


def _farthest_shot(scene):
    tgt = studio.find_target(scene)
    centre = tgt.location if tgt is not None else Vector()
    bpy.context.view_layer.update()                # a shot just moved has a stale matrix_world
    return max([(c.matrix_world.translation - centre).length for c in studio.list_shots(scene)] or [0.0])


def dome_radius(scene):
    dome = bpy.data.objects.get(PREFIX + "Dome")
    return float(dome["plinth_radius"]) if dome is not None and "plinth_radius" in dome else 0.0


def fit_dome(scene):
    """A photo scene's dome must hold every shot (a camera outside it saw only a
    blur of its outside): rebuild it wider when a shot goes beyond it (the flat
    ground stays: it's the photo's). Called when a shot moves and before Shoot.
    Returns the radius."""
    dome = bpy.data.objects.get(PREFIX + "Dome")
    if dome is None or "plinth_radius" not in dome:
        return 0.0
    need = 1.5 * _farthest_shot(scene)
    have = dome_radius(scene)
    if need <= have:
        return have
    old = dome.data
    dome.data = dome_mesh("Dome", float(dome["plinth_ground"]), need)
    if old.users == 0:
        bpy.data.meshes.remove(old)
    dome["plinth_radius"] = need
    return need


def dome_mesh(name, ground, radius, rings=16, segments=128):
    """Around a flat ground of radius ``ground``: a bowl rising from its edge
    (a quarter ellipse, flat where it starts) to a wall at ``radius`` and half
    as high, closed by a cap. Faces inward (seen from inside)."""
    import numpy as np
    height = 0.5 * radius
    profile = []
    for i in range(rings + 1):                                 # the bowl: edge of the ground -> wall
        p = (math.pi / 2.0) * i / rings
        profile.append((ground + (radius - ground) * math.sin(p), height * (1.0 - math.cos(p))))
    for i in range(1, rings):                                  # the cap: wall -> top
        p = (math.pi / 2.0) * i / rings
        profile.append((radius * math.cos(p), height + radius * math.sin(p)))
    co = []
    for rho, z in profile:
        for s in range(segments):
            a = 2.0 * math.pi * s / segments
            co.append((rho * math.cos(a), rho * math.sin(a), z))
    top = len(co)
    co.append((0.0, 0.0, height + radius))
    faces, sizes = [], []
    for i in range(len(profile) - 1):
        for s in range(segments):
            a, b = i * segments + s, i * segments + (s + 1) % segments
            faces += [a, a + segments, b + segments, b]
            sizes.append(4)
    last = (len(profile) - 1) * segments
    for s in range(segments):
        faces += [last + s, top, last + (s + 1) % segments]
        sizes.append(3)
    return _mesh_from(name, np.asarray(co), faces, sizes)


def _projected(nt, image, height, rotation):
    """The photo's colour at a point, seen from the camera's height above the
    middle of the ground (rotated like the world's image)."""
    pos = nt.world_pos()
    eye = nt.new("ShaderNodeVectorMath", operation="SUBTRACT")
    nt.link(pos, eye.inputs[0])
    eye.inputs[1].default_value = (0.0, 0.0, height)
    turn_ = nt.new("ShaderNodeVectorRotate", rotation_type="EULER_XYZ")
    turn_.name = "Turn"
    turn_.inputs["Rotation"].default_value = (0.0, 0.0, -math.radians(rotation))
    nt.link(eye.outputs[0], turn_.inputs["Vector"])
    env = nt.new("ShaderNodeTexEnvironment")
    env.image = image
    nt.link(turn_.outputs[0], env.inputs["Vector"])
    return env.outputs["Color"]


def _build_photo(scene, key, r):
    entry = photo_scenes()[key]
    err = hdri.set_image(scene, path=entry["path"])
    if err:
        raise RuntimeError(err)
    hdri.set_hdri(scene, strength=entry.get("strength", 1.0), rotation=entry.get("rotation", 0.0),
                  background=True)
    world = scene.world
    image = world.node_tree.nodes[hdri.PREFIX + "Image"].image
    ground = entry.get("ground") or {}
    height = float(ground.get("height", GROUND_HEIGHT))
    e_up = ground_irradiance(image)
    # Real metres (the photo's ground), and always beyond every shot: a camera
    # outside the dome saw only a blur of its outside.
    flat = max(float(ground.get("radius", GROUND_RADIUS)), 2.5 * r)    # the product and its shadow fit
    radius = max(1.5 * _farthest_shot(scene), 2.0 * flat)
    rotation = hdri.settings(scene)["rotation"]
    floor = _add(scene, "Ground", polar_ground("Ground", 0.5 * r, flat, 40, 128, lambda x, y: x * 0.0))
    floor.parent = None                                # the photo turns through its own rotation
    # The dome: the rest of the photo, standing on the ground's edge, so trees and
    # walls stand up instead of smearing over the floor. Camera-only: it never
    # lights, reflects in or shadows the product (the world does that).
    dome = _add(scene, "Dome", dome_mesh("Dome", flat, radius))
    dome.parent = None
    dome["plinth_radius"], dome["plinth_ground"] = radius, flat
    for attr in ("visible_diffuse", "visible_glossy", "visible_transmission", "visible_volume_scatter",
                 "visible_shadow"):
        if hasattr(dome, attr):
            setattr(dome, attr, False)
    dm = _material("PhotoDome")
    nd = _Nodes(dm.node_tree)
    dout = nd.output()
    nd.emission(dout, _projected(nd, image, height, rotation), float(entry.get("strength", 1.0)))
    _with_materials(dome, [dm])
    m = _material("PhotoGround")
    nt = _Nodes(m.node_tree)
    out = nt.output()
    env = _projected(nt, image, height, rotation).node
    # Two outputs, like glass: Eevee lights the floor from the world as indirect
    # light, which its presets boost (eevee.indirect_light_intensity, 1.25 for
    # Studio) while the photo seen behind isn't; measured 1.25x bright at the
    # seam. Its albedo divides that back out through a driver (a simple
    # expression: runs without Python scripts), so every preset matches.
    out.target = "CYCLES"
    eevee_out = nt.output()
    eevee_out.target = "EEVEE"
    for target, boost in ((out, False), (eevee_out, True)):
        albedo = nt.new("ShaderNodeVectorMath", operation="SCALE")
        albedo.name = "Albedo" + ("Eevee" if boost else "")
        albedo.inputs["Scale"].default_value = math.pi / e_up
        if boost and hasattr(scene.eevee, "indirect_light_intensity"):
            fc = albedo.inputs["Scale"].driver_add("default_value")
            drv = fc.driver
            drv.type = "SCRIPTED"
            var = drv.variables.new()
            var.name, var.type = "boost", "SINGLE_PROP"
            var.targets[0].id_type, var.targets[0].id = "SCENE", scene
            var.targets[0].data_path = "eevee.indirect_light_intensity"
            drv.expression = "%.6f / max(boost, 0.01)" % (math.pi / e_up)
        nt.link(env.outputs["Color"], albedo.inputs[0])
        p = nt.new("ShaderNodeBsdfPrincipled")
        p.inputs["Roughness"].default_value = float(ground.get("roughness", 0.85))
        for name in ("Specular IOR Level", "Specular"):
            if name in p.inputs:
                p.inputs[name].default_value = 0.2
                break
        nt.link(albedo.outputs[0], p.inputs["Base Color"])
        nt.link(p.outputs[0], target.inputs["Surface"])
    m["plinth_ground_irradiance"] = e_up
    _with_materials(floor, [m])


# --- The list ------------------------------------------------------------------------------

# What each scene takes from its kit (plinth-content's tools/build_kits.py makes
# objects with these roles; content_export writes them into the schema).
KIT_ROLES = {"MOON": ("rocks",), "DUNES": ("debris", "stones"), "SALT": (), "NEON": (),
             "HOUSE": ("aircon", "grass", "hedge", "lamp", "planters", "plants", "terrace", "trees"),
             "FOREST": ("cover", "debris", "litter", "rocks", "saplings", "trees"),
             "ABANDONED": ("barriers", "fence", "junk", "lamps", "poles", "weeds")}

SCENES.update({
    "MOON": {"label": "Moon", "kind": "PROCEDURAL", "build": None, "rig": 0.12,
             "description": "Grey regolith, craters and rocks under a black sky, Earth on the horizon"},
    "DUNES": {"label": "Desert dunes", "kind": "PROCEDURAL", "build": None, "rig": 0.12,
              "description": "Rippled sand dunes rolling away under a low golden sun"},
    "SALT": {"label": "Salt flats", "kind": "PROCEDURAL", "build": None, "rig": 0.22,
             "description": "A white cracked salt pan out to a ring of mountains, damp patches mirroring the sky"},
    "NEON": {"label": "Neon grid", "kind": "PROCEDURAL", "build": _neon, "rig": 0.3,
             "description": "A wet glowing grid floor, neon mountains and a striped sunset sun"},
    "HOUSE": {"label": "Modern house", "kind": "SET", "build": None, "rig": 0.2,
              "description": "A paved driveway before a glass-and-timber modern house, its lights on, a garden round it"},
    "FOREST": {"label": "Forest", "kind": "SET", "build": None, "rig": 0.2,
               "description": "A clearing in a pine forest: ferns, rocks, stumps, fallen trunks, soft hazy sun"},
    "ABANDONED": {"label": "Abandoned lot", "kind": "SET", "build": _abandoned, "rig": 0.3,
                  "description": "Broken asphalt and puddles, barriers, rusty containers, junk and weeds"},
})


# 0.12: places rethought, one module each (plinth/places/).
from .places import moon as _place_moon  # noqa: E402

SCENES["MOON"].update(build=_place_moon.build, look=_place_moon.look, sky=_place_moon.SKY,
                      description="An Apollo site: regolith, fresh craters and rock fields, rover tracks, "
                                  "Earth with its oceans, clouds and city lights")
from .places import forest as _place_forest  # noqa: E402

SCENES["FOREST"].update(build=_place_forest.build, sky=_place_forest.SKY,
                        description="A clearing in beech and spruce woodland, a clear stream behind, ferns, mossy "
                                    "boulders, a trunk fallen across the water")
from .places import dunes as _place_dunes  # noqa: E402

SCENES["DUNES"].update(build=_place_dunes.build, sky=_place_dunes.SKY,
                       description="Namib dunes: sharp crests, rippled backs, smooth slipfaces, a white clay pan "
                                   "with dead camelthorn trees")
from .places import salt as _place_salt  # noqa: E402

SCENES["SALT"].update(build=_place_salt.build, sky=_place_salt.SKY,
                      description="Salar de Uyuni: a white crust of raised salt polygons, tyre tracks, salt cones, "
                                  "far mountains; after rain, the sky's mirror")
from .places import house as _place_house  # noqa: E402

SCENES["HOUSE"].update(build=_place_house.build, sky=_place_house.SKY,
                       description="An architect's house at golden hour: white render, timber and stone, a lit room "
                                   "behind the glass, a paved drive, box balls, a Japanese maple")
SCENES["ABANDONED"].update(sky={"rotation": 250.0, "cover": 0.55, "haze": 2.6, "time": "DUSK", "wind": 0.15},
                           description="Broken asphalt with faded bays, oil stains and puddles, rusting containers, "
                                       "a ruined wall, sodium lamps coming on at dusk")
SCENES["NEON"].update(sky={"own": True, "time": "NIGHT", "times": ["NIGHT"], "weathers": ["CLEAR", "WET", "MIST"],
                           "wind": 0.0, "fog_colour": (0.22, 0.02, 0.16)})
