# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene detail (0.11): scatter by instancing, prototypes, and scene kits.

A scene's trees, rocks, grass and props are *instances*: one prototype mesh
each (a scanned asset from a kit, or one made in code), drawn many times by a
Geometry Nodes modifier (``Plinth_Scene_Instancer``) on a cloud of points.
Each point carries which prototype (``proto``), its rotation (``rot``) and
size (``scale``) as attributes, so a forest of a thousand pines holds one
pine's geometry. Prototypes live in ``Plinth_Scene_Protos`` (inside the
scene's collection, excluded from the view layer: they never draw
themselves).

Scene kits are content packs (item kind ``kit``): a .blend of scanned assets
(Poly Haven, CC0) for one scene, made by plinth-content's
``tools/build_kits.py``. Every object carries ``plinth_role`` (trees, rocks,
grass...), ``plinth_asset`` and ``plinth_lod`` (0 = the scan, 1 and 2
lighter), ground textures are images named ``ground_<set>_<map>``. Loading
renames everything ``Plinth_Scene_Kit_*``; clearing a scene removes it.
"""
import os

import bpy

from . import studio

PREFIX = "Plinth_Scene_"
KIT = PREFIX + "Kit_"
PROTOS = PREFIX + "Protos"
INSTANCER = "Plinth_Scene_Instancer"
INSTANCER_VERSION = 2
LOADED = "plinth_kit_path"            # on the protos collection: the kit file loaded into it

KITS = {}                             # scene key -> {"path", "label", "version", "pack", "item"} (content.py)


# --- Kits (registry filled by content.py) ----------------------------------------------------

def kit_entry(key):
    """The kit for a scene, if a pack has one ({...} or None)."""
    return KITS.get(key)


def kit_path(key):
    """The installed kit file for a scene, or None."""
    e = KITS.get(key)
    p = e.get("path") if e else None
    return p if p and os.path.exists(p) else None


# --- Collections ---------------------------------------------------------------------------

def _exclude(scene, col):
    """Keep a collection out of every view layer (its objects never draw)."""
    def find(lc):
        if lc.collection == col:
            return lc
        for child in lc.children:
            hit = find(child)
            if hit is not None:
                return hit
        return None
    for vl in scene.view_layers:
        lc = find(vl.layer_collection)
        if lc is not None and not lc.exclude:
            lc.exclude = True


def protos_collection(scene, parent):
    col = bpy.data.collections.get(PROTOS)
    if col is None:
        col = bpy.data.collections.new(PROTOS)
    if col.name not in parent.children:
        parent.children.link(col)
    _exclude(scene, col)
    return col


def group_collection(scene, parent, group):
    """Prototypes of one scatter group (Plinth_Scene_Protos_<group>)."""
    top = protos_collection(scene, parent)
    name = PROTOS + "_" + group
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
    if col.name not in top.children:
        top.children.link(col)
    return col


# --- The instancer (one node group, built once, versioned) ----------------------------------

# Live controls (0.12): each scatter object's modifier carries these, so a
# category's Amount/Size/Spread/Clearing and the Wind change instantly,
# without rebuilding the place. name: (socket type, default, min, max)
CONTROLS = (("Amount", "NodeSocketFloat", 1.0, 0.0, 1.0),        # share of the points drawn (by their 'keep')
            ("Size", "NodeSocketFloat", 1.0, 0.1, 4.0),
            ("Spread", "NodeSocketFloat", 1.0, 0.2, 4.0),        # distances beyond Inner, stretched
            ("Inner", "NodeSocketFloat", 0.0, 0.0, 1e6),         # where Spread starts (the group's nearest)
            ("Clearing", "NodeSocketFloat", 0.0, 0.0, 1e6),      # nothing nearer the product than this
            ("Wind", "NodeSocketFloat", 0.0, 0.0, 1.0),          # sway, radians at the strongest gust
            ("Gust", "NodeSocketFloat", 0.15, 0.001, 1e6))       # m (x u): how big a gust is across


def instancer():
    ng = bpy.data.node_groups.get(INSTANCER)
    if ng is not None and ng.get("plinth_version") == INSTANCER_VERSION:
        return ng
    if ng is None:
        ng = bpy.data.node_groups.new(INSTANCER, "GeometryNodeTree")
    ng.nodes.clear()
    ng.interface.clear()
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Collection", in_out="INPUT", socket_type="NodeSocketCollection")
    for name, kind, default, lo, hi in CONTROLS:
        s = ng.interface.new_socket(name, in_out="INPUT", socket_type=kind)
        s.default_value, s.min_value, s.max_value = default, lo, hi
    ng.interface.new_socket("Ground", in_out="INPUT", socket_type="NodeSocketObject")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    n, link = ng.nodes, ng.links.new
    gi, go = n.new("NodeGroupInput"), n.new("NodeGroupOutput")
    gin = {s.name: gi.outputs[i] for i, s in enumerate(
        [it for it in ng.interface.items_tree if getattr(it, "in_out", "") == "INPUT"])}

    def attr(name, kind):
        a = n.new("GeometryNodeInputNamedAttribute")
        a.data_type = kind
        a.inputs["Name"].default_value = name
        return a.outputs["Attribute"]

    def fmath(op, a, b=None):
        m = n.new("ShaderNodeMath")
        m.operation = op
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                m.inputs[i].default_value = v
            else:
                link(v, m.inputs[i])
        return m.outputs[0]

    def vmath(op, a, b=None):
        m = n.new("ShaderNodeVectorMath")
        m.operation = op
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            if isinstance(v, tuple):
                m.inputs[i].default_value = v
            else:
                link(v, m.inputs[i])
        return m.outputs["Value"] if op in ("LENGTH", "DOT_PRODUCT") else m.outputs["Vector"]

    def delete(geo, selection):
        d = n.new("GeometryNodeDeleteGeometry")
        d.domain = "POINT"
        link(geo, d.inputs["Geometry"])
        link(selection, d.inputs["Selection"])
        return d.outputs[0]

    # Amount: drop the points whose 'keep' is at or above it (a lower amount is a subset).
    geo = delete(gin["Geometry"], fmath("GREATER_THAN", attr("keep", "FLOAT"),
                                        fmath("SUBTRACT", gin["Amount"], 1e-6)))
    # Spread: push each point out (or in) from the product beyond Inner, then
    # follow the ground: z moves by the ground's rise between the old and the
    # new spot (two ray casts, only when Spread isn't 1 and there's a ground).
    pos = n.new("GeometryNodeInputPosition").outputs[0]
    sep = n.new("ShaderNodeSeparateXYZ")
    link(pos, sep.inputs[0])
    flat = n.new("ShaderNodeCombineXYZ")
    link(sep.outputs[0], flat.inputs[0])
    link(sep.outputs[1], flat.inputs[1])
    d = vmath("LENGTH", flat.outputs[0])
    grow = fmath("MULTIPLY", fmath("MAXIMUM", fmath("SUBTRACT", d, gin["Inner"]), 0.0),
                 fmath("SUBTRACT", gin["Spread"], 1.0))
    factor = fmath("DIVIDE", fmath("ADD", d, grow), fmath("MAXIMUM", d, 1e-6))
    moved = n.new("ShaderNodeCombineXYZ")
    link(fmath("MULTIPLY", sep.outputs[0], factor), moved.inputs[0])
    link(fmath("MULTIPLY", sep.outputs[1], factor), moved.inputs[1])
    ground = n.new("GeometryNodeObjectInfo")
    ground.transform_space = "RELATIVE"
    link(gin["Ground"], ground.inputs["Object"])

    def ground_z(xy):
        src = n.new("ShaderNodeCombineXYZ")
        s2 = n.new("ShaderNodeSeparateXYZ")
        link(xy, s2.inputs[0])
        link(s2.outputs[0], src.inputs[0])
        link(s2.outputs[1], src.inputs[1])
        src.inputs[2].default_value = 1e5
        ray = n.new("GeometryNodeRaycast")
        link(ground.outputs["Geometry"], ray.inputs["Target Geometry"])
        link(src.outputs[0], ray.inputs["Source Position"])
        ray.inputs["Ray Direction"].default_value = (0.0, 0.0, -1.0)
        ray.inputs["Ray Length"].default_value = 2e5
        hz = n.new("ShaderNodeSeparateXYZ")
        link(ray.outputs["Hit Position"], hz.inputs[0])
        return hz.outputs[2], ray.outputs["Is Hit"]

    z_new, hit_new = ground_z(moved.outputs[0])
    z_old, hit_old = ground_z(flat.outputs[0])
    hits = fmath("MULTIPLY", hit_new, hit_old)
    rise = fmath("MULTIPLY", fmath("SUBTRACT", z_new, z_old), hits)
    changed = n.new("FunctionNodeCompare")
    changed.data_type = "FLOAT"
    changed.operation = "NOT_EQUAL"
    link(gin["Spread"], changed.inputs[0])
    changed.inputs[1].default_value = 1.0
    sw = n.new("GeometryNodeSwitch")
    sw.input_type = "FLOAT"
    link(changed.outputs[0], sw.inputs["Switch"])
    sw.inputs["False"].default_value = 0.0
    link(rise, sw.inputs["True"])
    link(sep.outputs[2], moved.inputs[2])
    lifted = vmath("ADD", moved.outputs[0], (0.0, 0.0, 0.0))
    up = n.new("ShaderNodeCombineXYZ")
    link(sw.outputs[0], up.inputs[2])
    link(up.outputs[0], lifted.node.inputs[1])
    setp = n.new("GeometryNodeSetPosition")
    link(geo, setp.inputs["Geometry"])
    link(lifted, setp.inputs["Position"])
    # Clearing: nothing nearer the product than it.
    geo = delete(setp.outputs[0], fmath("LESS_THAN", fmath("ADD", d, grow), gin["Clearing"]))
    info = n.new("GeometryNodeCollectionInfo")
    info.transform_space = "ORIGINAL"
    info.inputs["Separate Children"].default_value = True
    info.inputs["Reset Children"].default_value = True
    put = n.new("GeometryNodeInstanceOnPoints")
    put.inputs["Pick Instance"].default_value = True
    link(gin["Collection"], info.inputs["Collection"])
    link(geo, put.inputs["Points"])
    link(info.outputs[0], put.inputs["Instance"])
    link(attr("proto", "INT"), put.inputs["Instance Index"])
    link(attr("rot", "FLOAT_VECTOR"), put.inputs["Rotation"])
    link(vmath("SCALE", attr("scale", "FLOAT_VECTOR"), None), put.inputs["Scale"])
    scale_node = put.inputs["Scale"].links[0].from_node
    link(gin["Size"], scale_node.inputs["Scale"])
    # Wind: each instance leans about its base with gusts that travel across
    # the place (4D noise: position / Gust, and time).
    t = n.new("GeometryNodeInputSceneTime")
    ipos = n.new("GeometryNodeInputPosition").outputs[0]
    noise = n.new("ShaderNodeTexNoise")
    noise.noise_dimensions = "4D"
    noise.inputs["Scale"].default_value = 1.0
    noise.inputs["Detail"].default_value = 2.0
    link(vmath("DIVIDE", ipos, None), noise.inputs["Vector"])
    div = noise.inputs["Vector"].links[0].from_node
    gust = n.new("ShaderNodeCombineXYZ")
    for i in range(3):
        link(gin["Gust"], gust.inputs[i])
    link(gust.outputs[0], div.inputs[1])
    link(fmath("MULTIPLY", t.outputs["Seconds"], 0.45), noise.inputs["W"])
    lean = vmath("SUBTRACT", noise.outputs["Color"], (0.5, 0.5, 0.5))
    flutter = n.new("ShaderNodeTexNoise")
    flutter.noise_dimensions = "4D"
    flutter.inputs["Scale"].default_value = 3.0
    link(ipos, flutter.inputs["Vector"])
    link(fmath("MULTIPLY", t.outputs["Seconds"], 2.3), flutter.inputs["W"])
    sway = vmath("ADD", vmath("MULTIPLY", lean, (2.0, 2.0, 0.0)),
                 vmath("MULTIPLY", vmath("SUBTRACT", flutter.outputs["Color"], (0.5, 0.5, 0.5)), (0.5, 0.5, 0.0)))
    turn = vmath("SCALE", sway, None)
    link(gin["Wind"], turn.node.inputs["Scale"])
    rot = n.new("GeometryNodeRotateInstances")
    link(put.outputs[0], rot.inputs["Instances"])
    link(turn, rot.inputs["Rotation"])
    if "Local Space" in rot.inputs:
        rot.inputs["Local Space"].default_value = True      # about each instance's own base
    link(rot.outputs[0], go.inputs[0])
    ng["plinth_version"] = INSTANCER_VERSION
    return ng


def set_controls(obj, **values):
    """Set a scatter object's live controls (Amount, Size, ...)."""
    mod = obj.modifiers.get("Plinth_Scatter")
    if mod is None or mod.node_group is None:
        return False
    idents = {s.name: s.identifier for s in mod.node_group.interface.items_tree
              if getattr(s, "in_out", "") == "INPUT"}
    for k, v in values.items():
        if k in idents:
            studio._set_input(mod, idents[k], v)
    obj.update_tag()
    return True


def get_control(obj, name):
    mod = obj.modifiers.get("Plinth_Scatter")
    if mod is None or mod.node_group is None:
        return None
    ident = next((s.identifier for s in mod.node_group.interface.items_tree
                  if getattr(s, "in_out", "") == "INPUT" and s.name == name), None)
    return studio._get_input(mod, ident) if ident else None


def point_cloud(name, co, proto, rot, scale, keep=None):
    """A vertex-only mesh: one vertex per instance and its attributes (keep:
    0..1 per point; Amount draws those below it)."""
    import numpy as np
    full = PREFIX + name
    old = bpy.data.meshes.get(full)
    if old is not None and old.users == 0:
        bpy.data.meshes.remove(old)
    me = bpy.data.meshes.new(full)
    co = np.asarray(co, np.float32).reshape(-1, 3)
    me.vertices.add(len(co))
    me.vertices.foreach_set("co", co.ravel())
    if keep is None:
        keep = np.zeros(len(co), np.float32)
    for attr_name, kind, values in (("proto", "INT", np.asarray(proto, np.int32)),
                                    ("rot", "FLOAT_VECTOR", np.asarray(rot, np.float32).reshape(-1, 3)),
                                    ("scale", "FLOAT_VECTOR", np.asarray(scale, np.float32).reshape(-1, 3)),
                                    ("keep", "FLOAT", np.asarray(keep, np.float32))):
        a = me.attributes.new(attr_name, kind, "POINT")
        a.data.foreach_set("value" if kind in ("INT", "FLOAT") else "vector", values.ravel())
    me.update()
    return me


def scatter(scene, add, parent, group, protos, co, proto, rot, scale, keep=None):
    """One scatter group: ``protos`` (objects) instanced at ``co`` (N x 3, in
    the scene root's space) by index ``proto``, Euler ``rot`` and ``scale``
    (N x 3 each). ``add(scene, name, data)`` makes the scene object (scenes._add).
    Each prototype is a light copy (same mesh) named in index order: Collection
    Info's separate children come sorted by name (4.2 and 5.2 alike)."""
    col = group_collection(scene, parent, group)
    for obj in list(col.objects):
        bpy.data.objects.remove(obj)
    for i, src in enumerate(protos):
        name = "%sP_%s_%03d" % (PREFIX, group, i)
        if src.name.startswith(PREFIX + "P_"):          # made for this group: use it as it is
            obj = src
            obj.name = name
        else:
            obj = src.copy()                              # a kit's asset: shares its mesh
            obj.name = name
            for key in ("plinth_kit_object",):
                if key in obj:
                    del obj[key]
        obj.location = (0.0, 0.0, 0.0)
        obj.parent = None
        col.objects.link(obj)
    points = add(scene, group, point_cloud(group, co, proto, rot, scale, keep))
    mod = points.modifiers.new("Plinth_Scatter", "NODES")
    mod.node_group = instancer()
    ident = next(s.identifier for s in mod.node_group.interface.items_tree
                 if getattr(s, "in_out", "") == "INPUT" and s.socket_type == "NodeSocketCollection")
    studio._set_input(mod, ident, col)
    points["plinth_instances"] = len(co)
    return points


def drawn(obj):
    """Which of a scatter object's points its Amount draws (a bool array)."""
    import numpy as np
    if obj.type != "MESH" or obj.modifiers.get("Plinth_Scatter") is None:
        return np.zeros(0, bool)
    me = obj.data
    keep = me.attributes.get("keep") if me is not None else None
    n = len(me.vertices) if me is not None else 0
    if keep is None:
        return np.ones(n, bool)
    k = np.empty(n, np.float32)
    keep.data.foreach_get("value", k)
    amount = get_control(obj, "Amount")
    return k < (1.0 if amount is None else float(amount)) - 1e-6


def instance_count(obj):
    """How many instances a scatter object draws (before its Clearing)."""
    return int(drawn(obj).sum())


# --- Loading a kit -------------------------------------------------------------------------

def _rename(idb, prefix):
    if not idb.name.startswith(prefix):
        idb.name = prefix + idb.name


def load(scene, parent, path):
    """Append a kit's objects and images once (reused while the same file is
    loaded). Returns {"objects": {(role, asset, lod): obj}, "images": {name: image}}."""
    top = protos_collection(scene, parent)
    store = bpy.data.collections.get(PROTOS + "_Kit")
    if store is not None and store.get(LOADED) == path:
        return _index(store)
    unload()
    with bpy.data.libraries.load(path, link=False) as (src, dst):
        dst.objects = list(src.objects)
        dst.images = [n for n in src.images if n.startswith(("tex_", "tex4k_"))]
    store = bpy.data.collections.new(PROTOS + "_Kit")
    top.children.link(store)
    store[LOADED] = path
    for obj in dst.objects:
        if obj is None:
            continue
        store.objects.link(obj)
        obj["plinth_kit_object"] = True
        _rename(obj, KIT)
        if obj.data is not None:
            _rename(obj.data, KIT)
        for slot in obj.material_slots:
            if slot.material is not None:
                _rename(slot.material, KIT)
                for node in (slot.material.node_tree.nodes if slot.material.node_tree else ()):
                    if getattr(node, "image", None) is not None:
                        _rename(node.image, KIT)
    _physical(dst.objects)
    for img in dst.images:
        if img is not None:
            img["plinth_tex"] = img.name[len(KIT):] if img.name.startswith(KIT) else img.name
            _rename(img, KIT)
    return _index(store)


def _physical(objects):
    """Every dielectric reflects about 4% head-on (Principled's Specular IOR
    Level 0.5); some scans ship 0-0.02, which reads as dull and flat. Raise
    those, on Plinth's copies only."""
    seen = set()
    for obj in objects:
        for slot in (obj.material_slots if obj is not None else ()):
            m = slot.material
            if m is None or m.name in seen or not m.node_tree:
                continue
            seen.add(m.name)
            for n in m.node_tree.nodes:
                if n.type == "BSDF_PRINCIPLED" and "Specular IOR Level" in n.inputs:
                    sock = n.inputs["Specular IOR Level"]
                    if not sock.is_linked and sock.default_value < 0.3:
                        sock.default_value = 0.5


def _index(store):
    objs = {}
    for obj in store.objects:
        objs[(obj.get("plinth_role", ""), obj.get("plinth_asset", obj.name), int(obj.get("plinth_lod", 0)))] = obj
    images = {img["plinth_tex"]: img for img in bpy.data.images if "plinth_tex" in img}
    return {"objects": objs, "images": images}


def assets(kit, role, lod):
    """One prototype per asset of a role, at the LOD asked for (or the nearest
    it has; between two as near, the more detailed)."""
    if not kit:
        return []
    out = {}
    for (r, asset, level), obj in kit["objects"].items():
        if r != role:
            continue
        best = out.get(asset)
        have = int(best.get("plinth_lod", 0)) if best is not None else None
        if best is None or (abs(level - lod), level) < (abs(have - lod), have):     # a tie: the richer
            out[asset] = obj
    return [out[a] for a in sorted(out)]


def unload():
    """Remove a loaded kit's data (objects, meshes, materials, images)."""
    store = bpy.data.collections.get(PROTOS + "_Kit")
    if store is not None:
        bpy.data.collections.remove(store)
    doomed = [o for o in bpy.data.objects if o.get("plinth_kit_object")]
    for o in doomed:
        bpy.data.objects.remove(o)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.node_groups):
        dead = [d for d in coll if d.name.startswith(KIT) and d.users == 0]
        if dead:
            bpy.data.batch_remove(dead)
