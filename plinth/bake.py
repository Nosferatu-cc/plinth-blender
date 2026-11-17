# SPDX-License-Identifier: GPL-3.0-or-later
"""Baking for Eevee.

Lighting: Eevee's light (irradiance) volume, sized to the studio around the
product and baked in one click. It gives Eevee what it can't trace live:
bounce light, colour bleeding from the backdrop and light from Glow parts.
Cycles ignores it. The bake records a signature of everything that lights the
scene, so the UI can say when lighting changed since (``lighting_state``).
"""

import hashlib
import time

import bpy

VOLUME = "Plinth_Irradiance"
STAMP = "plinth_baked"              # volume[...] = signature at bake time
RESOLUTION = (10, 10, 6)


def volume(scene=None):
    """Plinth's light volume, or None. Read-only."""
    obj = bpy.data.objects.get(VOLUME)
    return obj if obj is not None and obj.type == "LIGHT_PROBE" else None


def _extent(scene):
    """(centre, half sizes): around the product and the floor under it."""
    from mathutils import Vector
    tgt = bpy.data.objects.get("Plinth_Target")
    centre = tgt.matrix_world.translation.copy() if tgt is not None else Vector()
    r = 0.5 * float(tgt.get("plinth_scale", 1.0)) if tgt is not None else 0.5
    floor = centre.z - r * 1.05                      # the target sits a radius above the floor
    return Vector((centre.x, centre.y, floor + 1.6 * r)), Vector((4 * r, 4 * r, 1.7 * r))


def build_volume(scene):
    """Create or fit the light volume (idempotent). Returns it."""
    from . import studio
    data = bpy.data.lightprobes.get(VOLUME) or bpy.data.lightprobes.new(VOLUME, "VOLUME")
    obj = bpy.data.objects.get(VOLUME) or bpy.data.objects.new(VOLUME, data)
    centre, half = _extent(scene)
    obj.location = centre
    obj.scale = half
    for attr, value in (("resolution_x", RESOLUTION[0]), ("resolution_y", RESOLUTION[1]),
                        ("resolution_z", RESOLUTION[2]), ("capture_world", True),
                        ("capture_indirect", True), ("capture_emission", True)):
        if hasattr(data, attr):
            setattr(data, attr, value)
    return studio._link(obj, studio.studio_collection(scene))


def signature(scene):
    """A short hash of what lights the scene: lights, world, backdrop colour,
    the volume's fit, and every visible mesh's place and materials (Glow
    brightness included). Read-only, cheap enough to call while drawing."""
    from . import hdri
    parts = []
    for obj in sorted(scene.objects, key=lambda o: o.name):
        if obj.hide_render or obj.type not in {"LIGHT", "MESH"} or obj.name == VOLUME:
            continue
        parts.append(obj.name)
        parts.append(tuple(round(v, 4) for row in obj.matrix_world for v in row))
        if obj.type == "LIGHT":
            d = obj.data
            parts.append((round(d.energy, 3), tuple(round(c, 3) for c in d.color),
                          round(getattr(d, "size", 0.0), 4), round(getattr(d, "size_y", 0.0), 4),
                          round(getattr(d, "spread", 0.0), 4), d.get("plinth_gobo", "")))
        else:
            for slot in obj.material_slots:
                mat = slot.material
                parts.append(mat.name if mat else "")
                if mat is not None and mat.node_tree is not None:
                    for node in mat.node_tree.nodes:
                        if node.type == "GROUP":
                            parts.append(tuple(_value(s) for s in node.inputs if not s.is_linked))
    st = hdri.settings(scene)
    parts.append((st["on"], st["key"], round(st["strength"], 3), round(st["rotation"], 2)))
    if scene.world is not None:
        parts.append(tuple(round(c, 3) for c in scene.world.color))
    centre, half = _extent(scene)
    parts.append((tuple(round(v, 4) for v in centre), tuple(round(v, 4) for v in half)))
    return hashlib.sha1(repr(parts).encode()).hexdigest()[:16]


def _value(sock):
    v = getattr(sock, "default_value", None)
    if v is None:
        return None
    try:
        return tuple(round(c, 4) for c in v)
    except TypeError:
        return round(v, 4) if isinstance(v, float) else v


BAKE_OP = "OBJECT_OT_lightprobe_cache_bake"


def is_running():
    """A background lighting bake is going. Blender has no job type for light
    probe bakes (``is_job_running`` doesn't know them), but the bake runs as a
    modal operator until it's done."""
    wm = getattr(bpy.context, "window_manager", None)
    for window in (wm.windows if wm else ()):
        if any(op.bl_idname == BAKE_OP for op in getattr(window, "modal_operators", ())):
            return True
    return False


_cache = {"t": 0.0, "sig": None}


def lighting_state(scene, cached=False):
    """"NONE" (never baked), "BAKED" (current), "STALE" (lighting changed since)
    or "RUNNING". ``cached``: the UI asks on every redraw; reuse the signature
    for a second rather than walk a big scene each time."""
    if is_running():
        return "RUNNING"
    obj = volume(scene)
    if obj is None or not obj.get(STAMP):
        return "NONE"
    now = time.monotonic()
    if not cached or _cache["sig"] is None or now - _cache["t"] > 1.0:
        _cache["sig"], _cache["t"] = signature(scene), now
    return "BAKED" if obj[STAMP] == _cache["sig"] else "STALE"


def _override(obj):
    return bpy.context.temp_override(active_object=obj, object=obj, selected_objects=[obj],
                                     selected_editable_objects=[obj])


def bake_lighting(scene):
    """Fit the volume and bake it (blocking: about a second for a studio; a
    background bake job started from an add-on never finished in 5.2)."""
    obj = build_volume(scene)
    obj.pop(STAMP, None)
    with _override(obj):
        result = bpy.ops.object.lightprobe_cache_bake(subset="ACTIVE")
    if "FINISHED" in result:
        obj[STAMP] = signature(scene)
    return "FINISHED" in result


def clear_lighting(scene):
    """Drop Plinth's baked lighting (the volume stays, unbaked). Other light
    volumes in the file are left alone."""
    obj = volume(scene)
    if obj is None:
        return False
    with _override(obj):
        bpy.ops.object.lightprobe_cache_free(subset="ACTIVE")
    obj.pop(STAMP, None)
    return True




# --- Materials: procedural to image textures ---------------------------------------
# A part's material is baked channel by channel on a throwaway copy of the part
# (only that part's faces, its own copy of the material and of every node group
# in it), so the user's data is never rewired. Each channel is routed through
# Emission and baked as EMIT (one sample, no lighting): base colour, metallic,
# roughness, and coat when the material has one; Glow is baked as it emits;
# normals as a tangent-space normal map. The result is a plain Principled
# material reading those images through the part's own UV map
# ("Plinth_Bake_<slot>", unwrapped on first bake). The procedural material is
# kept (fake user) and comes back with unbake().

BAKED_FROM = "plinth_baked_from"    # baked material[...] = the procedural material's name
SIZES = {"1K": 1024, "2K": 2048, "4K": 4096}
_INPUTS = {   # per shader node type: (base colour, metallic, roughness) input or constant
    "BSDF_PRINCIPLED": ("Base Color", "Metallic", "Roughness"),
    "BSDF_DIFFUSE": ("Color", 0.0, 1.0),
    "BSDF_ANISOTROPIC": ("Color", 1.0, "Roughness"),
    "BSDF_GLOSSY": ("Color", 1.0, "Roughness"),
    "BSDF_GLASS": ("Color", 0.0, "Roughness"),
    "BSDF_SHEEN": ("Color", 0.0, 1.0),
    "BSDF_TRANSLUCENT": ("Color", 0.0, 1.0),
    "SUBSURFACE_SCATTERING": ("Color", 0.0, 1.0),
}
_SHADERS = set(_INPUTS) | {"EMISSION", "BSDF_TRANSPARENT", "HOLDOUT", "BSDF_REFRACTION",
                           "BSDF_TOON", "BSDF_HAIR", "BSDF_HAIR_PRINCIPLED"}


def baked_source(mat):
    """The procedural material a baked one came from, or None."""
    if mat is None or not mat.get(BAKED_FROM):
        return None
    return bpy.data.materials.get(mat[BAKED_FROM])


def is_baked(obj, index=None):
    i = obj.active_material_index if index is None else index
    return i < len(obj.material_slots) and baked_source(obj.material_slots[i].material) is not None


def can_bake(obj, index=None):
    """(True, "") or (False, why) for a part."""
    from . import eeveeprep
    if obj is None or obj.type != "MESH":
        return False, "Select a part"
    i = obj.active_material_index if index is None else index
    if i >= len(obj.material_slots):
        return False, "Select a part"
    mat = obj.material_slots[i].material
    if is_baked(obj, i):
        mat = baked_source(mat)
    if mat is None or mat.node_tree is None:
        return False, "This part has no material"
    if eeveeprep.is_transmissive(mat.node_tree):
        return False, "Glass stays procedural"
    if not obj.data.polygons:
        return False, "This part has no faces"
    return True, ""


def _walk(tree, seen=None):
    """Every node tree reachable from ``tree`` through groups."""
    seen = set() if seen is None else seen
    if tree is None or tree in seen:
        return seen
    seen.add(tree)
    for node in tree.nodes:
        if node.type == "GROUP" and node.node_tree is not None:
            _walk(node.node_tree, seen)
    return seen


def _has(tree, test):
    return any(test(n) for t in _walk(tree) for n in t.nodes)


def _has_coat(tree):
    def coat(n):
        if n.type != "BSDF_PRINCIPLED" or "Coat Weight" not in n.inputs:
            return False
        s = n.inputs["Coat Weight"]
        return s.is_linked or s.default_value > 0.0
    return _has(tree, coat)


def _has_glow(tree):
    def glow(n):
        if n.type == "EMISSION":
            return True
        s = n.inputs.get("Emission Strength") if n.type == "BSDF_PRINCIPLED" else None
        return s is not None and (s.is_linked or s.default_value > 0.0)
    return _has(tree, glow)


def _localize(tree, made):
    """Point every group node in ``tree`` at a private copy (recursively)."""
    for node in tree.nodes:
        if node.type == "GROUP" and node.node_tree is not None:
            src = node.node_tree
            if src not in made:
                made[src] = src.copy()
                _localize(made[src], made)
            node.node_tree = made[src]


def _feed(tree, sock, source):
    """Put an input's value or link (or a constant) onto a colour socket."""
    if isinstance(source, (int, float)):
        sock.default_value = (source, source, source, 1.0)
        return
    if source.is_linked:
        tree.links.new(source.links[0].from_socket, sock)
        return
    v = source.default_value
    try:
        v = tuple(v)
        sock.default_value = v if len(v) == 4 else (v[0], v[1], v[2], 1.0)
    except TypeError:
        sock.default_value = (v, v, v, 1.0)


def _to_emission(tree, channel):
    """Replace every shader in ``tree`` with an Emission of one of its inputs."""
    for node in list(tree.nodes):
        if node.type not in _SHADERS or not node.outputs:
            continue
        em = tree.nodes.new("ShaderNodeEmission")
        em.location = node.location
        em.inputs["Strength"].default_value = 1.0
        colour = em.inputs["Color"]
        colour.default_value = (0.0, 0.0, 0.0, 1.0)
        spec = _INPUTS.get(node.type)
        if spec is not None:
            if channel in ("COAT", "COAT_ROUGHNESS"):
                name = {"COAT": "Coat Weight", "COAT_ROUGHNESS": "Coat Roughness"}[channel]
                source = node.inputs.get(name) if node.type == "BSDF_PRINCIPLED" else 0.0
            else:
                source = spec[{"BASE": 0, "METALLIC": 1, "ROUGHNESS": 2}[channel]]
                source = node.inputs.get(source) if isinstance(source, str) else source
            if source is not None:
                _feed(tree, colour, source)
        for link in list(node.outputs[0].links):
            tree.links.new(em.outputs[0], link.to_socket)


def _uv_layer(obj, i):
    """This part's own UV map, unwrapped (smart project) the first time.
    Returns (name, error)."""
    import math
    me = obj.data
    name = "Plinth_Bake_%d" % i
    if name in me.uv_layers:
        return name, None
    if len(me.uv_layers) >= 8:
        return None, "This mesh already has 8 UV maps"
    view_layer = bpy.context.view_layer
    prev_uv = me.uv_layers.active.name if me.uv_layers.active else None
    prev_active, prev_index = view_layer.objects.active, obj.active_material_index
    prev_select = [p.select for p in me.polygons]
    hidden = obj.hide_get()
    uv = me.uv_layers.new(name=name, do_init=False)
    me.uv_layers.active = uv
    try:
        obj.hide_set(False)
        view_layer.objects.active = obj
        obj.active_material_index = i
        with bpy.context.temp_override(active_object=obj, object=obj, edit_object=obj,
                                       selected_objects=[obj], selected_editable_objects=[obj]):
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="DESELECT")
            bpy.ops.object.material_slot_select()
            bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.004)
            bpy.ops.object.mode_set(mode="OBJECT")
    finally:
        if prev_uv in me.uv_layers:
            me.uv_layers.active = me.uv_layers[prev_uv]
        for p, sel in zip(me.polygons, prev_select):
            p.select = sel
        obj.active_material_index = prev_index
        view_layer.objects.active = prev_active
        obj.hide_set(hidden)
    return name, None


def _part_copy(scene, obj, i, uv_name):
    """A throwaway object with only this part's faces, where ``obj`` is."""
    import bmesh
    me = obj.data.copy()
    tmp = bpy.data.objects.new("Plinth_BakeTmp", me)
    tmp.matrix_world = obj.matrix_world.copy()
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.material_index != i], context="FACES")
    bm.to_mesh(me)
    bm.free()
    me.uv_layers.active = me.uv_layers[uv_name]
    me.uv_layers[uv_name].active_render = True
    scene.collection.objects.link(tmp)
    return tmp


def _image(name, size, colour, float_buffer=False):
    img = bpy.data.images.get(name)
    if img is not None and (tuple(img.size) != (size, size) or img.is_float != float_buffer):
        bpy.data.images.remove(img)
        img = None
    if img is None:
        img = bpy.data.images.new(name, size, size, alpha=False, float_buffer=float_buffer)
    img.colorspace_settings.name = colour
    return img


def _bake_channel(scene, tmp, src, channel, img, uv_name):
    """Bake one channel of ``src`` into ``img`` on the throwaway part."""
    made = {}
    mat = src.copy()
    mat.name = "Plinth_BakeTmp_" + channel
    try:
        _localize(mat.node_tree, made)
        if channel not in ("NORMAL", "GLOW"):
            for tree in [mat.node_tree] + list(made.values()):
                _to_emission(tree, channel)
        nodes = mat.node_tree.nodes
        uvn = nodes.new("ShaderNodeUVMap")
        uvn.uv_map = uv_name
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = img
        mat.node_tree.links.new(uvn.outputs[0], tex.inputs["Vector"])
        for n in nodes:
            n.select = False
        tex.select = True
        nodes.active = tex
        for slot in tmp.material_slots:
            slot.link = "OBJECT"
            slot.material = mat
        kind = "NORMAL" if channel == "NORMAL" else "EMIT"
        scene.cycles.samples = 8 if kind == "NORMAL" else 1
        with bpy.context.temp_override(active_object=tmp, object=tmp, selected_objects=[tmp],
                                       selected_editable_objects=[tmp]):
            bpy.ops.object.bake(type=kind, normal_space="TANGENT", margin=8, use_clear=True,
                                target="IMAGE_TEXTURES", use_selected_to_active=False)
    finally:
        bpy.data.materials.remove(mat)
        for tree in made.values():
            bpy.data.node_groups.remove(tree)


TARGETS = {"BASE": "Base Color", "METALLIC": "Metallic", "ROUGHNESS": "Roughness",
           "COAT": "Coat Weight", "COAT_ROUGHNESS": "Coat Roughness", "GLOW": "Emission Color"}


def _baked_material(src, images, uv_name, coat):
    """A Principled material reading the baked images."""
    name = src.name + " (baked)"
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    if hasattr(mat, "use_nodes") and not mat.use_nodes:
        mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    nodes, links = tree.nodes, tree.links
    out = nodes.new("ShaderNodeOutputMaterial")
    out.location = (400, 0)
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    links.new(bsdf.outputs[0], out.inputs["Surface"])
    uvn = nodes.new("ShaderNodeUVMap")
    uvn.uv_map = uv_name
    uvn.location = (-900, 0)
    for k, (channel, img) in enumerate(images.items()):
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = img
        tex.location = (-600, 300 - 280 * k)
        links.new(uvn.outputs[0], tex.inputs["Vector"])
        if channel == "NORMAL":
            nm = nodes.new("ShaderNodeNormalMap")
            nm.uv_map = uv_name
            nm.location = (-250, 300 - 280 * k)
            links.new(tex.outputs["Color"], nm.inputs["Color"])
            links.new(nm.outputs[0], bsdf.inputs["Normal"])
        else:
            links.new(tex.outputs["Color"], bsdf.inputs[TARGETS[channel]])
    if "GLOW" in images:
        bsdf.inputs["Emission Strength"].default_value = 1.0
    if coat is not None and "COAT" not in images:          # UDIN paint: its coat is a glossy layer
        bsdf.inputs["Coat Weight"].default_value = 1.0
        bsdf.inputs["Coat Roughness"].default_value = coat
    mat[BAKED_FROM] = src.name
    mat.diffuse_color = src.diffuse_color
    return mat


def _paint_coat(src):
    """UDIN paints coat with a glossy layer the bake can't see: keep its
    Clearcoat roughness as a real coat on the baked material."""
    from . import materials
    spec = materials.TYPES.get(src.get("plinth_type"))
    if not spec or not spec.get("library") or "Clearcoat roughness" not in spec.get("sockets", {}):
        return None
    node = materials.group_node(src)
    return float(materials.socket(node, src["plinth_type"], "Clearcoat roughness").default_value) \
        if node is not None else None


def bake_material(scene, obj, index=None, size="2K", progress=None):
    """Bake a part's material to image textures and use them. Returns an error
    message, or None. A baked part is baked again from its procedural source."""
    i = obj.active_material_index if index is None else index
    ok, why = can_bake(obj, i)
    if not ok:
        return why
    slot = obj.material_slots[i]
    src = baked_source(slot.material) or slot.material
    uv_name, error = _uv_layer(obj, i)
    if error:
        return error
    px = SIZES[size]
    channels = ["BASE", "METALLIC", "ROUGHNESS"]
    if _has_coat(src.node_tree):
        channels += ["COAT", "COAT_ROUGHNESS"]
    if _has_glow(src.node_tree):
        channels.append("GLOW")
    channels.append("NORMAL")
    stem = "Plinth_Bake_%s_" % src.name
    images = {}
    saved = (scene.render.engine, scene.cycles.samples)
    tmp = _part_copy(scene, obj, i, uv_name)
    try:
        scene.render.engine = "CYCLES"
        for k, channel in enumerate(channels):
            colour = "sRGB" if channel in ("BASE", "GLOW") else "Non-Color"
            img = _image(stem + channel.lower(), px, colour, float_buffer=channel == "GLOW")
            _bake_channel(scene, tmp, src, channel, img, uv_name)
            img.file_format = "OPEN_EXR" if img.is_float else "PNG"
            img.pack()
            images[channel] = img
            if progress:
                progress((k + 1) / len(channels))
    except RuntimeError as exc:
        return str(exc).strip().splitlines()[-1]
    finally:
        scene.render.engine, scene.cycles.samples = saved
        me = tmp.data
        bpy.data.objects.remove(tmp)
        bpy.data.meshes.remove(me)
    baked = _baked_material(src, images, uv_name, _paint_coat(src))
    baked["plinth_baked_size"] = size
    src.use_fake_user = True                        # kept with the file while baked
    slot.link = "OBJECT"
    slot.material = baked
    return None


def unbake(obj, index=None):
    """Back to the procedural material. The baked one is left unused (not
    deleted: an open editor may still show it); unused data isn't saved."""
    i = obj.active_material_index if index is None else index
    if not is_baked(obj, i):
        return False
    slot = obj.material_slots[i]
    src = baked_source(slot.material)
    slot.material = src
    src.use_fake_user = False
    return True
