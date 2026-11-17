# SPDX-License-Identifier: GPL-3.0-or-later
"""Decals and liveries: images layered over any part's material.

Two kinds of layer, any number per part, drawn in order (later on top):

- **Decal** (projected): a logo, number or sticker placed on the surface. A
  projector empty (``Plinth_Decal_<n>``, in the Plinth collection, parented
  to the part so it moves with it) sits on the surface facing out; the image
  is projected along its axis. Its scale is the decal's size (x/y) and how far
  it reaches into the surface (z), so it never shows through to the far side
  of the part. No UVs needed. Drag its handle over the product to move it.
- **Livery** (UV): a full-surface image laid out on the part's UVs (a livery
  or wrap design with transparent areas), over the paint.

Each layer mixes a decal surface (the image's colour, with its own gloss and
metal) over whatever was below by the image's alpha x opacity. The records
live on the object (``obj["plinth_decals"]``, per part), and the nodes are
rebuilt from them (``rewire``) whenever the part's material changes, so
decals survive preset changes and "Original". A part with its own material
gets a private Plinth copy for its decals; the original is never touched.
"""

import bpy
from mathutils import Matrix, Vector

from . import materials, studio

KEY = "plinth_decals"           # obj[KEY] = {"<slot>": [record, ...]}
PREFIX = "Plinth_Decal"         # node names and projector empties
MAP_GROUP = "Plinth_DecalMap"
LAYER_GROUP = "Plinth_DecalLayer"
GROUP_VERSION = 1
COPY = "plinth_decal_copy"      # a Plinth copy of the part's own material, for its decals
SOURCE = "plinth_decal_source"  # copy[SOURCE] = the name of the material it was copied from
SPANS = "plinth_decal_spans"    # obj[SPANS] = {"<slot>": [decal id, ...]}: decals placed on
                                # another part that carry on over this one (0.9.1)
ID = "plinth_decal_id"          # on each layer node: the decal it draws
SIZE_RANGE = (1.0, 400.0)       # cm
DEFAULTS = {"opacity": 1.0, "gloss": 0.7, "metallic": 0.0, "size": 30.0, "rotation": 0.0, "raised": 0.0,
            "span": True}
LAYER_VERSION = 2               # 2 (0.9): Raised, the decal's shape embossed (sidewall lettering)


# --- Records ----------------------------------------------------------------------

def layers(obj, index=None):
    """The part's decal records, bottom first (read-only copies)."""
    if obj is None or obj.type != "MESH":
        return []
    i = materials._slot_index(obj, index)
    entry = obj.get(KEY, {}).get(str(i), [])
    return [r.to_dict() if hasattr(r, "to_dict") else dict(r) for r in entry]


def _save(obj, i, records):
    data = {k: v for k, v in obj.get(KEY, {}).items()}
    if records:
        data[str(i)] = records
    else:
        data.pop(str(i), None)
    obj[KEY] = data


def find(obj, decal_id, index=None):
    return next((r for r in layers(obj, index) if r["id"] == decal_id), None)


def owner(decal_id):
    """(object, slot index, record) for a decal id, or (None, None, None)."""
    for obj in bpy.data.objects:
        for slot, records in obj.get(KEY, {}).items():
            for r in records:
                if r["id"] == decal_id:
                    rec = r.to_dict() if hasattr(r, "to_dict") else dict(r)
                    return obj, int(slot), rec
    return None, None, None


def label(record):
    name = record.get("image") or "No image"
    base = name.rsplit(".", 1)[0] if "." in name else name
    return base


def _new_id():
    n = 1
    taken = {r["id"] for o in bpy.data.objects for rs in o.get(KEY, {}).values() for r in rs}
    while "%s_%d" % (PREFIX, n) in taken or bpy.data.objects.get("%s_%d" % (PREFIX, n)):
        n += 1
    return "%s_%d" % (PREFIX, n)


# --- Node groups --------------------------------------------------------------------

def _interface(ng, items):
    for name, io, kind, default in items:
        sock = ng.interface.new_socket(name, in_out=io, socket_type=kind)
        if default is not None and hasattr(sock, "default_value"):
            sock.default_value = default


def map_group():
    """Projector space -> (UV 0..1 across the decal, Mask 1 within its box)."""
    ng = bpy.data.node_groups.get(MAP_GROUP)
    if ng is not None and ng.get("plinth_version") == GROUP_VERSION:
        return ng
    ng = ng or bpy.data.node_groups.new(MAP_GROUP, "ShaderNodeTree")
    ng.interface.clear()
    ng.nodes.clear()
    _interface(ng, [("Vector", "INPUT", "NodeSocketVector", None),
                    ("UV", "OUTPUT", "NodeSocketVector", None), ("Mask", "OUTPUT", "NodeSocketFloat", None)])
    b = materials._Builder.__new__(materials._Builder)
    b.ng, b.nodes, b.links, b.skipped = ng, ng.nodes, ng.links, []
    gin, gout = ng.nodes.new("NodeGroupInput"), ng.nodes.new("NodeGroupOutput")
    sep = b.node("ShaderNodeSeparateXYZ")
    b.links.new(gin.outputs["Vector"], sep.inputs[0])
    x, y, z = sep.outputs["X"], sep.outputs["Y"], sep.outputs["Z"]
    inside = b.math("MULTIPLY", b.math("LESS_THAN", b.math("ABSOLUTE", x), 1.0),
                    b.math("LESS_THAN", b.math("ABSOLUTE", y), 1.0))
    mask = b.math("MULTIPLY", inside, b.math("LESS_THAN", b.math("ABSOLUTE", z), 1.0))
    comb = b.node("ShaderNodeCombineXYZ")
    b.feed(comb.inputs["X"], b.math("ADD", b.math("MULTIPLY", x, 0.5), 0.5))
    b.feed(comb.inputs["Y"], b.math("ADD", b.math("MULTIPLY", y, 0.5), 0.5))
    b.links.new(comb.outputs[0], gout.inputs["UV"])
    b.links.new(mask, gout.inputs["Mask"])
    ng["plinth_version"] = GROUP_VERSION
    return ng


def layer_group():
    """The decal's own surface mixed over the shader below."""
    ng = bpy.data.node_groups.get(LAYER_GROUP)
    if ng is not None and ng.get("plinth_version") == LAYER_VERSION:
        return ng
    ng = ng or bpy.data.node_groups.new(LAYER_GROUP, "ShaderNodeTree")
    # Only add what's missing: materials already using the group keep their links.
    have = {(item.name, item.in_out) for item in ng.interface.items_tree
            if getattr(item, "item_type", "SOCKET") == "SOCKET"}
    _interface(ng, [item for item in [("Below", "INPUT", "NodeSocketShader", None),
                                      ("Colour", "INPUT", "NodeSocketColor", (1, 1, 1, 1)),
                                      ("Alpha", "INPUT", "NodeSocketFloat", 1.0),
                                      ("Mask", "INPUT", "NodeSocketFloat", 1.0),
                                      ("Opacity", "INPUT", "NodeSocketFloat", 1.0),
                                      ("Gloss", "INPUT", "NodeSocketFloat", 0.7),
                                      ("Metallic", "INPUT", "NodeSocketFloat", 0.0),
                                      ("Raised", "INPUT", "NodeSocketFloat", 0.0),
                                      ("Shader", "OUTPUT", "NodeSocketShader", None)]
                    if (item[0], item[1]) not in have])
    ng.nodes.clear()
    b = materials._Builder.__new__(materials._Builder)
    b.ng, b.nodes, b.links, b.skipped = ng, ng.nodes, ng.links, []
    gin, gout = ng.nodes.new("NodeGroupInput"), ng.nodes.new("NodeGroupOutput")
    i = gin.outputs.get
    bsdf = b.node("ShaderNodeBsdfPrincipled")
    b.links.new(i("Colour"), bsdf.inputs["Base Color"])
    b.links.new(i("Metallic"), bsdf.inputs["Metallic"])
    b.feed(bsdf.inputs["Roughness"], b.math("SUBTRACT", 1.0, i("Gloss")))
    b.feed(bsdf.inputs["Coat Weight"], b.math("MULTIPLY", i("Gloss"), 0.8))
    bsdf.inputs["Coat Roughness"].default_value = 0.03
    shape = b.math("MULTIPLY", i("Alpha"), i("Mask"))
    # Raised: the decal's shape stands proud of the surface (moulded sidewall
    # lettering, embossed badges): a bump from its alpha, a few mm high.
    bump = b.node("ShaderNodeBump", {"Distance": 0.003})
    b.links.new(shape, bump.inputs["Height"])
    b.links.new(i("Raised"), bump.inputs["Strength"])
    b.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    fac = b.math("MULTIPLY", shape, i("Opacity"), clamp=True)
    mix = b.node("ShaderNodeMixShader")
    b.links.new(fac, mix.inputs[0])
    b.links.new(i("Below"), mix.inputs[1])
    b.links.new(bsdf.outputs["BSDF"], mix.inputs[2])
    b.links.new(mix.outputs[0], gout.inputs["Shader"])
    ng["plinth_version"] = LAYER_VERSION
    return ng


# --- The part's material ----------------------------------------------------------

def target_material(obj, i):
    """The material the part's decals go in: its Plinth material, or a Plinth
    copy of its own material (the original stays untouched and comes back
    with Original)."""
    mat = materials.part_material(obj, i)
    if mat is not None:
        return materials._own_material(obj, i)
    slot = obj.material_slots[i]
    current = slot.material
    if current is not None and current.get(COPY):
        if current.users > 1:
            current = current.copy()
            slot.link = "OBJECT"
            slot.material = current
        return _with_nodes(current)
    materials._remember_original(obj, i)
    if current is not None:
        copy = current.copy()
        copy[SOURCE] = current.name
    else:
        copy = bpy.data.materials.new("Plinth_%s_%d" % (obj.name, i))
    _with_nodes(copy)          # 4.x: a material made without nodes has no node tree
    copy.name = "Plinth_%s_%d_decals" % (obj.name, i)
    copy[COPY] = True
    slot.link = "OBJECT"
    slot.material = copy
    return copy


def _output(tree):
    """The main ("All") output: not glass's Eevee-only one."""
    outs = [n for n in tree.nodes if n.type == "OUTPUT_MATERIAL" and n.name != materials.EEVEE_OUT]
    return next((n for n in outs if n.is_active_output), outs[0] if outs else None)


def _base_socket(mat, tree, out):
    """What the decals go on top of: the Plinth group, or what fed the output
    before decals were added (remembered on the first layer)."""
    if "plinth_preset" in mat and mat.get("plinth_type") in materials.TYPES:
        node = materials.group_node(mat)
        if node is not None:
            return node.outputs.get("Surface") or node.outputs[0]
    first = tree.nodes.get(PREFIX + "Layer_0")
    if first is not None and first.inputs["Below"].links:
        return first.inputs["Below"].links[0].from_socket
    if out is not None and out.inputs["Surface"].links:
        src = out.inputs["Surface"].links[0].from_socket
        if not src.node.name.startswith(PREFIX):
            return src
    return None


def _with_nodes(mat):
    if hasattr(mat, "use_nodes") and not mat.use_nodes:
        mat.use_nodes = True
    return mat


def rewire(obj, index=None):
    """Rebuild the part's decal nodes from its records and put them between
    its surface and the output. No records: the decal nodes go and the
    surface feeds the output directly again."""
    i = materials._slot_index(obj, index)
    if i >= len(obj.material_slots):
        return None
    mat = obj.material_slots[i].material
    records = _drawn(obj, i, mat)
    if not records:
        if mat is None or mat.node_tree is None:
            return None
        if not any(n.name.startswith(PREFIX) for n in mat.node_tree.nodes):
            return mat
    else:
        mat = target_material(obj, i)
        records = _drawn(obj, i, mat)            # the material may be this part's own now
    tree = mat.node_tree
    out = _output(tree)
    base = _base_socket(mat, tree, out)
    # Glass feeds an Eevee-only output too (materials.wire_outputs): decals go
    # on top of both branches, one chain each.
    eevee_out = tree.nodes.get(materials.EEVEE_OUT)
    node = materials.group_node(mat) if eevee_out is not None else None
    eevee_base = node.outputs.get("Eevee") if node is not None else None
    bases = [(out, base, "")] + ([(eevee_out, eevee_base, "E")] if eevee_base is not None else [])
    kept = [(o, (b.node, b.identifier) if b is not None else (None, None), tag) for o, b, tag in bases]
    for n in [n for n in tree.nodes if n.name.startswith(PREFIX)]:
        tree.nodes.remove(n)
    for row, (target, (base_node, base_name), tag) in enumerate(kept):
        below = next((s for s in base_node.outputs if s.identifier == base_name), None) if base_node else None
        x0, y0 = (base_node.location.x, base_node.location.y) if base_node is not None else (0, 0)
        for k, r in enumerate(records):
            below = _build_layer(tree, k, r, below, (x0 + 250 * k, y0 - 450 - 900 * row), tag)
        if target is not None:
            for link in list(target.inputs["Surface"].links):
                tree.links.remove(link)
            if below is not None:
                tree.links.new(below, target.inputs["Surface"])
    return mat


def _build_layer(tree, k, r, below, at, tag=""):
    """One decal layer over ``below``. ``tag`` names a second chain (glass's
    Eevee branch) so both chains' nodes stay distinct."""
    nodes, links = tree.nodes, tree.links
    x, y = at
    tex = nodes.new("ShaderNodeTexImage")
    tex.name = "%s%sImage_%d" % (PREFIX, tag, k)
    tex.image = bpy.data.images.get(r.get("image", ""))
    tex.extension = "CLIP" if r["mode"] == "PROJECT" else "REPEAT"
    tex.location = (x - 300, y)
    layer = nodes.new("ShaderNodeGroup")
    layer.name = "%s%sLayer_%d" % (PREFIX, tag, k)
    layer.label = "Decal: " + label(r)
    layer.node_tree = layer_group()
    layer.location = (x, y + 200)
    layer[ID] = r["id"]
    if r["mode"] == "PROJECT":
        coords = nodes.new("ShaderNodeTexCoord")
        coords.name = "%s%sCoords_%d" % (PREFIX, tag, k)
        coords.object = bpy.data.objects.get(r.get("projector", ""))
        coords.location = (x - 700, y)
        mapper = nodes.new("ShaderNodeGroup")
        mapper.name = "%s%sMap_%d" % (PREFIX, tag, k)
        mapper.node_tree = map_group()
        mapper.location = (x - 500, y)
        links.new(coords.outputs["Object"], mapper.inputs["Vector"])
        links.new(mapper.outputs["UV"], tex.inputs["Vector"])
        links.new(mapper.outputs["Mask"], layer.inputs["Mask"])
    else:
        uv = nodes.new("ShaderNodeUVMap")
        uv.name = "%s%sUV_%d" % (PREFIX, tag, k)
        uv.uv_map = r.get("uv", "")
        uv.location = (x - 500, y)
        links.new(uv.outputs["UV"], tex.inputs["Vector"])
    links.new(tex.outputs["Color"], layer.inputs["Colour"])
    links.new(tex.outputs["Alpha"], layer.inputs["Alpha"])
    if below is not None:
        links.new(below, layer.inputs["Below"])
    _set_values(layer, r)
    return layer.outputs["Shader"]


def _set_values(layer, r):
    layer.inputs["Opacity"].default_value = float(r.get("opacity", DEFAULTS["opacity"]))
    layer.inputs["Gloss"].default_value = float(r.get("gloss", DEFAULTS["gloss"]))
    layer.inputs["Metallic"].default_value = float(r.get("metallic", DEFAULTS["metallic"]))
    if "Raised" in layer.inputs:
        layer.inputs["Raised"].default_value = float(r.get("raised", DEFAULTS["raised"]))


# --- Across panels (0.9.1) ------------------------------------------------------------
# A decal placed on one panel carries on over the touching panels that wear the
# same material (door -> fender: one stripe, one logo across the shut line).
# Projection is in the projector's (world) space, so the image lines up by
# itself; each panel it reaches just has to draw the layer. Those panels are
# remembered on them (obj[SPANS]); their layers read the owner's record, so a
# change on the decal card reaches every panel at once.

def look_of(mat):
    """What a material looks like, for "the same material": the saved library
    material it came from, the user's material a decal copy was made of, or a
    Plinth preset with its values; else the material itself."""
    if mat is None:
        return None
    from . import matlib
    if mat.get(matlib.TAG):
        return ("library", mat[matlib.TAG])
    if mat.get(SOURCE):
        return ("own", mat[SOURCE])
    key = mat.get("plinth_preset")
    if key in materials.PRESETS and mat.get("plinth_type") in materials.TYPES:
        node = materials.group_node(mat)
        type_key = mat["plinth_type"]
        values = []
        for name, *_ in materials.controls(type_key):
            v = materials.socket(node, type_key, name).default_value if node is not None else None
            try:
                values.append(tuple(round(float(x), 3) for x in v))
            except TypeError:
                values.append(round(float(v), 3) if v is not None else None)
        return ("preset", key, tuple(values))
    return ("own", mat.name)


def spans_of(decal_id):
    """[(object, slot)] of the other panels a decal carries on over."""
    out = []
    for o in bpy.data.objects:
        for slot, ids in o.get(SPANS, {}).items():
            if decal_id in list(ids):
                out.append((o, int(slot)))
    return out


def _set_span(obj, i, decal_id, on):
    data = {k: list(v) for k, v in obj.get(SPANS, {}).items()}
    ids = [d for d in data.get(str(i), []) if d != decal_id] + ([decal_id] if on else [])
    if ids:
        data[str(i)] = ids
    else:
        data.pop(str(i), None)
    if data:
        obj[SPANS] = data
    elif SPANS in obj:
        del obj[SPANS]


def _touches(obj, i, to_box):
    """Does the part (slot i of obj) have geometry inside the decal's box?
    ``to_box``: world -> projector space (the box is |x|, |y|, |z| < 1)."""
    import numpy as np
    me = obj.data
    m = to_box @ obj.matrix_world
    corners = [m @ Vector(c) for c in obj.bound_box]
    for axis in range(3):                         # cheap first: the boxes must overlap
        if min(c[axis] for c in corners) > 1.0 or max(c[axis] for c in corners) < -1.0:
            return False
    n_v, n_p = len(me.vertices), len(me.polygons)
    if not n_v or not n_p:
        return False
    co = np.empty(n_v * 3, np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    if len(obj.material_slots) > 1:
        mi = np.empty(n_p, np.int32)
        me.polygons.foreach_get("material_index", mi)
        total = np.empty(n_p, np.int32)
        me.polygons.foreach_get("loop_total", total)
        vi = np.empty(len(me.loops), np.int32)
        me.loops.foreach_get("vertex_index", vi)
        mine = np.repeat(mi == i, total)
        co = co[np.unique(vi[mine])] if mine.any() else co[:0]
    M = np.array(m, np.float32)
    p = co @ M[:3, :3].T + M[:3, 3]
    return bool((np.abs(p) < 1.0).all(axis=1).any())


def panels_for(obj, i, record):
    """The other parts a projected decal reaches that wear the same material."""
    proj = bpy.data.objects.get(record.get("projector", ""))
    if proj is None or record.get("mode") != "PROJECT":
        return []
    look = look_of(obj.material_slots[i].material if i < len(obj.material_slots) else None)
    if look is None:
        return []
    to_box = proj.matrix_world.inverted()
    col = bpy.data.collections.get(studio.COLLECTION)
    studio_objs = set(col.all_objects) if col else set()
    found = []
    for o in bpy.context.scene.objects:
        if o.type != "MESH" or o in studio_objs:
            continue
        for j, slot in enumerate(o.material_slots):
            if (o, j) == (obj, i) or slot.material is None:
                continue
            if look_of(slot.material) == look and _touches(o, j, to_box):
                found.append((o, j))
    return found


def respan(obj, i, record):
    """Bring a decal's panels up to date (after it's added, moved, resized or
    switched); only panels that change are rebuilt. Returns the panels."""
    want = panels_for(obj, i, record) if record.get("span", True) else []
    have = spans_of(record["id"])
    for o, j in have:
        if (o, j) not in want:
            _set_span(o, j, record["id"], False)
            rewire(o, j)
    for o, j in want:
        if (o, j) not in have:
            _set_span(o, j, record["id"], True)
            rewire(o, j)
    return want


def _drawn(obj, i, mat):
    """Every decal record this part's material draws: the decals of every part
    wearing that same material (a linked material is one material for all of
    them) and the decals carrying on over those parts from a neighbour."""
    parts = [(obj, i)]
    if mat is not None and mat.users > 1:
        for o in bpy.data.objects:
            if o.type == "MESH":
                for j, slot in enumerate(o.material_slots):
                    if slot.material == mat and (o, j) != (obj, i):
                        parts.append((o, j))
    out, seen = [], set()
    for o, j in parts:
        for r in layers(o, j):
            if r["id"] not in seen:
                seen.add(r["id"])
                out.append(r)
        for decal_id in o.get(SPANS, {}).get(str(j), []):
            if decal_id in seen:
                continue
            _own, _slot, r = owner(decal_id)
            if r is not None:
                seen.add(decal_id)
                out.append(r)
    return out


def _layer_nodes(obj, i, decal_id):
    """The layer nodes drawing a decal in this part's material (both glass chains)."""
    if i >= len(obj.material_slots):
        return []
    mat = obj.material_slots[i].material
    if mat is None or mat.node_tree is None:
        return []
    return [n for n in mat.node_tree.nodes if n.get(ID) == decal_id]


# --- Placing -------------------------------------------------------------------------

def _aspect(image):
    if image is None or not image.size[0] or not image.size[1]:
        return 1.0
    return image.size[0] / image.size[1]


def place(scene, obj, record, location=None, normal=None):
    """Put a projected decal's empty on the surface at `location` (world),
    facing out along `normal`, upright (the image's up follows world up where
    it can), turned by its rotation and sized by its size."""
    import math
    proj = bpy.data.objects.get(record.get("projector", ""))
    if proj is None:
        return None
    if location is None:
        location = proj.matrix_world.translation.copy()
    if normal is None:
        normal = proj.matrix_world.to_3x3() @ Vector((0.0, 0.0, 1.0))
    n = Vector(normal).normalized()
    up = Vector((0.0, 0.0, 1.0))
    if abs(n.dot(up)) > 0.95:                     # on a roof or a floor: up is "forward"
        up = Vector((0.0, 1.0, 0.0))
    yaxis = (up - n * up.dot(n)).normalized()
    xaxis = yaxis.cross(n)
    rot = Matrix((xaxis, yaxis, n)).transposed() @ Matrix.Rotation(
        math.radians(record.get("rotation", 0.0)), 3, "Z")
    size = record.get("size", DEFAULTS["size"]) / 100.0          # cm -> m: the long side
    aspect = _aspect(bpy.data.images.get(record.get("image", "")))
    half = Vector((size / 2 if aspect >= 1 else size * aspect / 2,
                   size / aspect / 2 if aspect >= 1 else size / 2, 0.0))
    half.z = max(half.x, half.y) * 0.5            # reaches half its size into the surface
    world = Matrix.LocRotScale(Vector(location), rot.to_quaternion(), half)
    proj.matrix_world = world
    return proj


def _projector(scene, obj, decal_id):
    proj = bpy.data.objects.new(decal_id, None)
    proj.empty_display_type = "SINGLE_ARROW"
    proj.empty_display_size = 1.0
    proj["plinth_decal"] = True
    studio._link(proj, studio.studio_collection(scene))
    proj.parent = obj
    proj.matrix_parent_inverse = obj.matrix_world.inverted()
    return proj


def default_spot(obj, view_origin=None):
    """Where a new decal goes: the surface facing the viewer, through the
    part's middle; else the part's middle facing the viewer."""
    centre = obj.matrix_world @ (sum((Vector(c) for c in obj.bound_box), Vector()) / 8.0)
    origin = Vector(view_origin) if view_origin is not None else centre + Vector((0.0, -10.0, 0.0))
    direction = (centre - origin).normalized()
    inv = obj.matrix_world.inverted()
    ok, loc, normal, _i = obj.ray_cast(inv @ origin, inv.to_3x3() @ direction)
    if ok:
        return obj.matrix_world @ loc, (obj.matrix_world.to_3x3().inverted().transposed() @ normal).normalized()
    return centre, -direction


def add(scene, obj, image, mode="PROJECT", index=None, location=None, normal=None, view_origin=None):
    """Add a decal (PROJECT) or livery (UV) layer on top of a part. `image`
    is a bpy Image or a file path. Returns the record."""
    if isinstance(image, str):
        image = bpy.data.images.load(image, check_existing=True)
    if not obj.material_slots:
        obj.data.materials.append(None)
    i = materials._slot_index(obj, index)
    decal_id = _new_id()
    record = dict(DEFAULTS, id=decal_id, mode=mode, image=image.name if image else "")
    if mode == "PROJECT":
        proj = _projector(scene, obj, decal_id)
        record["projector"] = proj.name
        dims = obj.dimensions
        record["size"] = round(max(5.0, min(SIZE_RANGE[1], 100.0 * max(dims) / 4.0)), 1)
        if location is None:
            location, normal = default_spot(obj, view_origin)
    else:
        uv = obj.data.uv_layers.active
        record["uv"] = uv.name if uv is not None else ""
        record["gloss"] = 0.85
    records = layers(obj, i) + [record]
    _save(obj, i, records)
    if mode == "PROJECT":
        place(scene, obj, record, location, normal)
    rewire(obj, i)
    if mode == "PROJECT":
        respan(obj, i, record)
    return record


def update(obj, decal_id, index=None, **values):
    """Change a layer: opacity, gloss, metallic (on its node, live), size and
    rotation (its projector), image."""
    i = materials._slot_index(obj, index)
    records = layers(obj, i)
    rec = next((r for r in records if r["id"] == decal_id), None)
    if rec is None:
        return False
    rec.update(values)
    _save(obj, i, records)
    parts = [(obj, i)] + spans_of(decal_id)
    nodes = [n for o, j in parts for n in _layer_nodes(o, j, decal_id)]
    if not nodes or "image" in values:
        for o, j in parts:
            rewire(o, j)
    else:
        for layer in nodes:                       # live: every panel it lies on, both glass chains
            _set_values(layer, rec)
    if rec["mode"] == "PROJECT" and ({"size", "rotation", "image"} & set(values)):
        place(bpy.context.scene, obj, rec)
    if rec["mode"] == "PROJECT" and ({"size", "rotation", "image", "span"} & set(values)):
        respan(obj, i, rec)
    return True


def move_to(scene, obj, decal_id, location, normal, index=None):
    rec = find(obj, decal_id, index)
    if rec is None or rec["mode"] != "PROJECT":
        return False
    place(scene, obj, rec, location, normal)
    respan(obj, materials._slot_index(obj, index), rec)
    return True


def remove(obj, decal_id, index=None):
    i = materials._slot_index(obj, index)
    records = layers(obj, i)
    rec = next((r for r in records if r["id"] == decal_id), None)
    if rec is None:
        return False
    records.remove(rec)
    _save(obj, i, records)
    for o, j in spans_of(decal_id):
        _set_span(o, j, decal_id, False)
        rewire(o, j)
    proj = bpy.data.objects.get(rec.get("projector", ""))
    if proj is not None and proj.get("plinth_decal"):
        bpy.data.objects.remove(proj)
    rewire(obj, i)
    return True


def drag_to(context, obj, decal_id, x, y, index=None):
    """Slide a decal over its part: where the mouse ray meets the part."""
    from bpy_extras import view3d_utils
    region, rv3d = context.region, context.region_data
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, (x, y))
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, (x, y))
    inv = obj.matrix_world.inverted()
    ok, loc, normal, _i = obj.ray_cast(inv @ origin, inv.to_3x3() @ direction)
    if not ok:
        return False
    world_n = (obj.matrix_world.to_3x3().inverted().transposed() @ normal).normalized()
    return move_to(context.scene, obj, decal_id, obj.matrix_world @ loc, world_n, index)
