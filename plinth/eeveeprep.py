# SPDX-License-Identifier: GPL-3.0-or-later
"""Getting a product's own materials right for Eevee (Phase 18).

Cycles refracts any transmissive material correctly. Eevee only does when the
material asks for ray-traced refraction and says how thick the object is; left
at their defaults (as every imported car is), glass refracts a blurry probe and
reads as a grey veil. ``prepare(scene)`` sets, for every glass-like material in
use: ray-traced refraction, see-through shadows and a thickness model chosen
from the shape: a thin pane for windows (open shells and thin slabs), a solid
for lenses and crystal. These are Eevee-only settings; Cycles ignores them, so
it's safe to run whenever Plinth switches to Eevee.

Clear thin glass doesn't refract in Eevee (0.13): Eevee traces one refraction
layer, so a window in front of another (a headlight's lens over its inner
lens, a windscreen over the rear window) blurred into one grey sheet and hid
the glass behind it. Like Plinth's own glass (materials._build_glass), it
gets an Eevee-only Material Output with a see-through pane (PANE_GROUP: the
glass's colour, a Fresnel reflection, no offset); Cycles keeps the material's
own output. Frosted and solid glass still refract (the blur, the lens).
"""

import bpy

THIN = 0.05             # mean thickness under 5% of the object's size: a pane, not a solid
FROSTED = 0.12          # roughness above which glass needs Eevee's refraction (the blur)
PANE_GROUP = "Plinth_Eevee_Pane"
PANE_OUT = "Plinth_Out_Eevee"          # same name as materials.EEVEE_OUT
PANE_NODE = "Plinth_Pane"


def is_transmissive(tree, seen=None):
    """Does this node tree refract (Glass/Refraction BSDF, or Principled with
    transmission), directly or inside a group?"""
    if tree is None:
        return False
    seen = set() if seen is None else seen
    for n in tree.nodes:
        if n.type in {"BSDF_GLASS", "BSDF_REFRACTION"}:
            return True
        if n.type == "BSDF_PRINCIPLED":
            sock = n.inputs.get("Transmission Weight") or n.inputs.get("Transmission")
            if sock is not None and (sock.is_linked or sock.default_value > 0.01):
                return True
        if n.type == "GROUP" and n.node_tree is not None and n.node_tree.name not in seen:
            seen.add(n.node_tree.name)
            if is_transmissive(n.node_tree, seen):
                return True
    return False


def mean_thickness(obj):
    """(thickness, open_shell) of a mesh object in world units: 2V/A for a closed
    mesh (a slab of thickness t has V = A/2 * t), 0 for an open shell."""
    import numpy as np
    me = obj.data
    if len(me.loops) == 0:
        return 0.0, True
    idx = np.empty(len(me.loops), dtype=np.int32)
    me.loops.foreach_get("edge_index", idx)
    if bool((np.bincount(idx, minlength=len(me.edges)) == 1).any()):
        return 0.0, True
    me.calc_loop_triangles()
    co = np.empty(len(me.vertices) * 3, dtype=np.float64)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3) * np.abs(np.array(obj.matrix_world.to_scale()))
    tri = np.empty(len(me.loop_triangles) * 3, dtype=np.int32)
    me.loop_triangles.foreach_get("vertices", tri)
    tri = tri.reshape(-1, 3)
    a, b, c = co[tri[:, 0]], co[tri[:, 1]], co[tri[:, 2]]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1).sum()
    volume = abs((a * np.cross(b, c)).sum()) / 6.0
    return (float(2.0 * volume / area) if area > 0 else 0.0), False


def is_thin(obj):
    t, shell = mean_thickness(obj)
    return bool(shell or t < THIN * max(obj.dimensions))


def prepare(scene):
    """Set Eevee's refraction up on every glass-like material in use. Returns
    {material name: "thin" | "solid"} for what changed (tests, logs)."""
    users = {}
    for obj in scene.objects:
        if obj.type != "MESH":
            continue
        for slot in obj.material_slots:
            if slot.material is not None:
                users.setdefault(slot.material, []).append(obj)
    changed = {}
    for mat, objs in users.items():
        if not mat.use_nodes or not is_transmissive(mat.node_tree):
            continue
        if mat.get("plinth_type") in ("GLASS", "UDIN_WINDSHIELD"):   # ours: materials.glass_state
            continue
        thin = any(is_thin(o) for o in objs)
        pane = thin and set_pane(mat)
        if pane:
            want = {"use_raytrace_refraction": False, "use_transparent_shadow": True,
                    "thickness_mode": "SLAB", "surface_render_method": "BLENDED",
                    "show_transparent_back": True}
        else:
            want = {"use_raytrace_refraction": True, "use_transparent_shadow": True,
                    "thickness_mode": "SLAB" if thin else "SPHERE"}
            if mat.get(PANE_NODE):                   # was a pane: frosted or solid now
                want["surface_render_method"] = "DITHERED"
                clear_pane(mat)
        diff = {k: v for k, v in want.items() if hasattr(mat, k) and getattr(mat, k) != v}
        for k, v in diff.items():
            setattr(mat, k, v)
        if diff:
            changed[mat.name] = "pane" if pane else ("thin" if thin else "solid")
    return changed


# --- Eevee pane for a product's own thin glass ------------------------------------

# (node type, colour socket names, roughness, IOR) of the BSDFs a pane copies.
_BSDFS = {"BSDF_GLASS": (("Color",), "Roughness", "IOR"),
          "BSDF_REFRACTION": (("Color",), "Roughness", "IOR"),
          "BSDF_PRINCIPLED": (("Base Color",), "Roughness", "IOR")}


def _glass_bsdf(tree):
    """The material's own (top-level) transmissive BSDF, or None."""
    for n in tree.nodes:
        if n.type in {"BSDF_GLASS", "BSDF_REFRACTION"}:
            return n
        if n.type == "BSDF_PRINCIPLED":
            sock = n.inputs.get("Transmission Weight") or n.inputs.get("Transmission")
            if sock is not None and (sock.is_linked or sock.default_value > 0.01):
                return n
    return None


def pane_group():
    """Tint, Roughness, IOR -> a thin pane: Fresnel mix of see-through (Tint)
    and gloss. The same pane Plinth's own glass shows in Eevee."""
    ng = bpy.data.node_groups.get(PANE_GROUP)
    if ng is not None:
        return ng
    ng = bpy.data.node_groups.new(PANE_GROUP, "ShaderNodeTree")
    io = ng.interface
    io.new_socket("Tint", in_out="INPUT", socket_type="NodeSocketColor").default_value = (1, 1, 1, 1)
    io.new_socket("Roughness", in_out="INPUT", socket_type="NodeSocketFloat").default_value = 0.0
    io.new_socket("IOR", in_out="INPUT", socket_type="NodeSocketFloat").default_value = 1.5
    io.new_socket("Shader", in_out="OUTPUT", socket_type="NodeSocketShader")
    n, ln = ng.nodes, ng.links
    gin, gout = n.new("NodeGroupInput"), n.new("NodeGroupOutput")
    fresnel = n.new("ShaderNodeFresnel")
    ln.new(gin.outputs["IOR"], fresnel.inputs["IOR"])
    see = n.new("ShaderNodeBsdfTransparent")
    ln.new(gin.outputs["Tint"], see.inputs["Color"])
    gloss = n.new("ShaderNodeBsdfGlossy")
    gloss.distribution = "GGX"
    gloss.inputs["Color"].default_value = (1, 1, 1, 1)
    ln.new(gin.outputs["Roughness"], gloss.inputs["Roughness"])
    mix = n.new("ShaderNodeMixShader")
    ln.new(fresnel.outputs[0], mix.inputs[0])
    ln.new(see.outputs[0], mix.inputs[1])
    ln.new(gloss.outputs[0], mix.inputs[2])
    ln.new(mix.outputs[0], gout.inputs["Shader"])
    return ng


def _feed(tree, src, dst):
    """dst takes src's link if it has one, else its value."""
    if src.is_linked:
        tree.links.new(src.links[0].from_socket, dst)
    else:
        value = src.default_value
        dst.default_value = tuple(value) if hasattr(value, "__len__") else value


def set_pane(mat):
    """Give a thin, clear glass material an Eevee-only pane output. Returns
    False (and leaves it to Eevee's refraction) for frosted glass, glass inside
    a node group, or a material that already has its own Eevee output."""
    tree = mat.node_tree
    bsdf = _glass_bsdf(tree)
    if bsdf is None:
        return False
    colour, rough, ior = _BSDFS[bsdf.type]
    r = bsdf.inputs[rough]
    if not r.is_linked and r.default_value > FROSTED:
        return False
    out = tree.nodes.get(PANE_OUT)
    if out is None:
        if any(o.type == "OUTPUT_MATERIAL" and o.target == "EEVEE" for o in tree.nodes):
            return False                              # the user made their own Eevee look
        main = next((o for o in tree.nodes if o.type == "OUTPUT_MATERIAL" and o.is_active_output), None)
        out = tree.nodes.new("ShaderNodeOutputMaterial")
        out.name = out.label = PANE_OUT
        out.target = "EEVEE"
        if main is not None:
            out.location = (main.location.x, main.location.y - 240)
    node = tree.nodes.get(PANE_NODE)
    if node is None:
        node = tree.nodes.new("ShaderNodeGroup")
        node.name = node.label = PANE_NODE
        node.node_tree = pane_group()
        node.location = (out.location.x - 220, out.location.y)
    _feed(tree, next(bsdf.inputs[c] for c in colour if c in bsdf.inputs), node.inputs["Tint"])
    _feed(tree, r, node.inputs["Roughness"])
    if ior in bsdf.inputs:
        _feed(tree, bsdf.inputs[ior], node.inputs["IOR"])
    if not out.inputs["Surface"].is_linked:
        tree.links.new(node.outputs[0], out.inputs["Surface"])
    mat[PANE_NODE] = True
    return True


def clear_pane(mat):
    """Take the Eevee pane out again (the glass became frosted or solid)."""
    tree = mat.node_tree
    for name in (PANE_OUT, PANE_NODE):
        node = tree.nodes.get(name)
        if node is not None:
            tree.nodes.remove(node)
    mat.pop(PANE_NODE, None)
