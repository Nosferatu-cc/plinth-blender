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
"""

import bpy

THIN = 0.05             # mean thickness under 5% of the object's size: a pane, not a solid


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
        want = {"use_raytrace_refraction": True, "use_transparent_shadow": True,
                "thickness_mode": "SLAB" if thin else "SPHERE"}
        diff = {k: v for k, v in want.items() if hasattr(mat, k) and getattr(mat, k) != v}
        for k, v in diff.items():
            setattr(mat, k, v)
        if diff:
            changed[mat.name] = "thin" if thin else "solid"
    return changed
