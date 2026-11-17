# SPDX-License-Identifier: GPL-3.0-or-later
"""Move, rotate and scale things with Blender's own transform gizmos.

The three toggles (top left) pick one of the 3D view's object gizmos, or none.
Gizmos act on the selection, so while one is on Plinth selects what the step
is about: the light being edited (Light), the active shot (Camera). In the
other steps the gizmo works on whatever is selected (the product, or anything
clicked in the scene), and the product selection comes back when you leave
Light or Camera or switch the gizmos off.

A light or shot aims at its target, which would undo any turn: picking one to
rotate frees its aim (where it points stays put); Aim at puts it back.

Read paths (``modes``, ``subject``) are safe while drawing; ``toggle`` and
``sync`` write and run from clicks.
"""

import bpy

from . import studio, uistate

# (key, label, SpaceView3D property)
MODES = (("MOVE", "Move", "show_gizmo_object_translate"),
         ("ROTATE", "Rotate", "show_gizmo_object_rotate"),
         ("SCALE", "Scale", "show_gizmo_object_scale"))
_ATTR = {k: attr for k, _l, attr in MODES}


def _space(context):
    space = getattr(context, "space_data", None)
    if space is not None and space.type == "VIEW_3D":
        return space
    screen = getattr(context, "screen", None)
    for area in (screen.areas if screen else ()):
        if area.type == "VIEW_3D":
            return area.spaces.active
    return None


def modes(space):
    """The gizmos switched on in this 3D view, as a frozenset of keys."""
    if space is None or not getattr(space, "show_gizmo", False):
        return frozenset()
    return frozenset(k for k, _l, attr in MODES if getattr(space, attr, False))


def subject(scene):
    """What the gizmo moves in this step: the light being edited, the active
    shot, or None (the selection as it is)."""
    step = scene.plinth.step
    if step == "LIGHT":
        return studio.active_light(scene)
    if step == "CAMERA":
        cam = scene.camera
        return cam if cam is not None and "plinth_shot" in cam else None
    return None


def toggle(context, key):
    """Switch to one gizmo (one at a time: picking another swaps it; picking the
    one that's on switches it off), then select what it should act on."""
    space = _space(context)
    if space is None:
        return False
    on = not getattr(space, _ATTR[key])
    for k, attr in _ATTR.items():
        setattr(space, attr, on and k == key)
    if on:
        space.show_gizmo = True
        if key == "ROTATE":
            uistate.gizmo.pop("subject", None)           # free the subject's aim (sync)
    sync(context)
    return on


def sync(context):
    """Keep the selection on the step's subject while a gizmo is on; put the
    product selection back otherwise. Runs after every floating-UI click."""
    scene = context.scene
    on = modes(_space(context))
    subj = subject(scene) if on else None
    if subj is None:
        restore(context)
        return
    state = uistate.gizmo
    view_layer = context.view_layer
    if "saved" not in state:
        state["saved"] = [o.name for o in context.selected_objects]
        active = view_layer.objects.active
        state["active"] = active.name if active is not None else ""
    if state.get("subject") == subj.name:
        return                                           # picked already: leave clicks alone
    state["subject"] = subj.name
    for obj in context.selected_objects:
        obj.select_set(False)
    if subj.name in view_layer.objects:
        subj.select_set(True)
        view_layer.objects.active = subj
    if "ROTATE" in on:
        studio.free_aim(subj)


def restore(context):
    """Bring back the selection from before the gizmo took the light or shot."""
    state = uistate.gizmo
    saved = state.pop("saved", None)
    active = state.pop("active", "")
    state.pop("subject", None)
    if saved is None:
        return
    view_layer = context.view_layer
    for obj in context.selected_objects:
        obj.select_set(False)
    for name in saved:
        obj = bpy.data.objects.get(name)
        if obj is not None and name in view_layer.objects:
            obj.select_set(True)
    obj = bpy.data.objects.get(active)
    if obj is not None and active in view_layer.objects:
        view_layer.objects.active = obj
