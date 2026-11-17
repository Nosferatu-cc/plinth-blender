# SPDX-License-Identifier: GPL-3.0-or-later
"""Isolate (0.9.1): `/` in the Plinth workspace shows only the selected part(s)
of the product, keeping the studio: backdrop, lights, shots and the world.
Blender's own local view hides lights and the studio too, so a part lost its
lighting the moment it was isolated.

Other geometry is hidden with the view layer's hide (the eye), which renders
ignore, and tagged so `/` again (even after a save and reload) brings back
exactly what isolation hid, never what you'd hidden yourself.
"""
import bpy

TAG = "plinth_isolated"
GEOMETRY = {"MESH", "CURVE", "SURFACE", "META", "FONT", "CURVES", "POINTCLOUD", "VOLUME"}


def _studio_objects(scene):
    col = bpy.data.collections.get("Plinth")
    return set(col.all_objects) if col else set()


def is_isolated(scene):
    return any(o.get(TAG) for o in scene.objects)


def isolate(scene, view_layer, keep):
    """Hide every other visible product object. Returns how many were hidden."""
    keep = {o for o in keep if o.type in GEOMETRY}
    if not keep:
        return 0
    studio = _studio_objects(scene)
    hidden = 0
    for o in scene.objects:
        if o in keep or o in studio or o.type not in GEOMETRY:
            continue
        if o.name not in view_layer.objects or not o.visible_get(view_layer=view_layer):
            continue
        o.hide_set(True, view_layer=view_layer)
        o[TAG] = True
        hidden += 1
    return hidden


def restore(scene, view_layer):
    """Show what isolation hid. Returns how many came back."""
    shown = 0
    for o in scene.objects:
        if o.get(TAG):
            if o.name in view_layer.objects:
                o.hide_set(False, view_layer=view_layer)
            del o[TAG]
            shown += 1
    return shown


# --- Hide (0.13): get at what's underneath, then put it back ---------------------------
# H hides the selected part(s) (the part under the mouse if nothing is
# selected) to reach what is behind or inside them; H again, or the "Show"
# pill, brings back exactly those. Same view-layer hide as isolate (renders
# still see them), its own tag, so the two never undo each other.

HIDDEN = "plinth_hidden"


def hidden(scene):
    """Objects Plinth's Hide took away (in the order they were hidden)."""
    return sorted((o for o in scene.objects if o.get(HIDDEN)), key=lambda o: o.get(HIDDEN, 0))


def hide(scene, view_layer, objects):
    """Hide these product objects (never the studio). Returns how many."""
    studio = _studio_objects(scene)
    n, stamp = 0, max([int(o.get(HIDDEN, 0)) for o in scene.objects] + [0]) + 1
    for o in objects:
        if o is None or o in studio or o.type not in GEOMETRY or o.name not in view_layer.objects:
            continue
        if not o.visible_get(view_layer=view_layer):
            continue
        o.hide_set(True, view_layer=view_layer)
        o.select_set(False, view_layer=view_layer)
        o[HIDDEN] = stamp
        n += 1
    return n


def show(scene, view_layer):
    """Bring back everything Hide took away. Returns how many."""
    n = 0
    for o in hidden(scene):
        if o.name in view_layer.objects:
            o.hide_set(False, view_layer=view_layer)
        del o[HIDDEN]
        n += 1
    return n


def toggle_hide(scene, view_layer, objects):
    """H: show what Hide took away if anything is hidden, else hide these.
    Returns ("SHOWN" | "HIDDEN" | "NOTHING", count)."""
    if hidden(scene):
        return "SHOWN", show(scene, view_layer)
    n = hide(scene, view_layer, objects)
    return ("HIDDEN", n) if n else ("NOTHING", 0)


def toggle(scene, view_layer, keep):
    """`/`: isolate the selection, or show everything again. Returns
    ("ISOLATED" | "RESTORED" | "NOTHING", count)."""
    if is_isolated(scene):
        return "RESTORED", restore(scene, view_layer)
    n = isolate(scene, view_layer, keep)
    return ("ISOLATED", n) if n else ("NOTHING", 0)
