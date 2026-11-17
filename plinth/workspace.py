# SPDX-License-Identifier: GPL-3.0-or-later
"""The Plinth workspace: one full-window viewport where all of Plinth's UI lives.

Blender has no API to make a workspace from nothing, so we duplicate the
current one and strip it down: every area but the biggest closes, that one
becomes a 3D viewport set up for product work. The workspace is tagged with a
custom property, so it's found again even if renamed, and opening it twice
never makes a second one. Plinth's floating controls and panels only show in
this workspace; elsewhere the sidebar just offers to open it.

Screen changes happen one step per event-loop tick (bpy.app.timers), each
with a full window/screen/area override and fresh lookups. Changing a freshly
duplicated screen in the same call crashed Blender 5.2 (region visibility
update with no active window: WM_window_get_active_workspace on NULL).
"""

import bpy

from . import engine

NAME = "Plinth"
TAG = "plinth_workspace"

# The studio viewport: the product is the interface, so Blender's chrome steps
# back. Applied defensively (engine.apply_settings), paths relative to the space.
VIEW_SETTINGS = {
    "shading.type": "MATERIAL",
    # Material preview normally lights and reflects everything with a built-in
    # HDRI. Use the scene's world and lights instead, so glass and paint show
    # the real studio (cove, Plinth lights, the reflection probe) and react live.
    "shading.use_scene_world": True,
    "shading.use_scene_lights": True,
    "shading.use_scene_world_render": True,
    "shading.use_scene_lights_render": True,
    "clip_end": 5000.0,                    # scenes (0.12) reach a kilometre or more out for a car
    "show_region_toolbar": False,
    "show_region_ui": False,
    "show_region_tool_header": False,
    "overlay.show_floor": False,
    "overlay.show_axis_x": False,
    "overlay.show_axis_y": False,
    "overlay.show_cursor": False,
    "overlay.show_relationship_lines": False,
    "overlay.show_extras": False,          # camera/light wireframes: Plinth draws its own labels
    "show_gizmo": True,                    # the floating controls need gizmos on
    "show_gizmo_navigate": True,
}


def find(workspaces=None):
    """The Plinth workspace in this file, or None (tag first, then name)."""
    workspaces = bpy.data.workspaces if workspaces is None else workspaces
    tagged = [ws for ws in workspaces if ws.get(TAG)]
    if tagged:
        return tagged[0]
    return next((ws for ws in workspaces if ws.name == NAME), None)


def is_active(context):
    ws = getattr(context, "workspace", None)
    return ws is not None and bool(ws.get(TAG))


def areas_to_close(areas):
    """Every area except the one to keep: the biggest 3D viewport, else the biggest."""
    areas = list(areas)
    if not areas:
        return []
    views = [a for a in areas if a.type == "VIEW_3D"] or areas
    keep = max(views, key=lambda a: a.width * a.height)
    return [a for a in areas if a is not keep]


def setup_view(space):
    """Configure a SpaceView3D for the studio. Returns skipped settings.
    On a live screen, call it under a window/screen/area override."""
    return engine.apply_settings(space, VIEW_SETTINGS)


# --- Building (needs a real window) ---------------------------------------------

def _plinth_window():
    wm = bpy.context.window_manager
    return next((w for w in (wm.windows if wm else ()) if w.workspace.get(TAG)), None)


_waits = {"n": 0}


def _close_areas():
    """Tick 2: close every area but the one we keep. Window workspace switches
    land on a later tick, so wait (briefly) until the window shows ours."""
    window = _plinth_window()
    if window is None:
        _waits["n"] += 1
        return 0.05 if _waits["n"] < 40 else None
    _waits["n"] = 0
    screen = window.screen
    for area in areas_to_close(screen.areas):
        with bpy.context.temp_override(window=window, screen=screen, area=area):
            bpy.ops.screen.area_close()
    bpy.app.timers.register(_configure_view, first_interval=0.05)
    return None


def _configure_view():
    """Tick 3: turn the remaining area into the studio viewport."""
    window = _plinth_window()
    if window is None:
        return None
    screen = window.screen
    area = max(screen.areas, key=lambda a: a.width * a.height)
    if area.type != "VIEW_3D":
        area.type = "VIEW_3D"
    region = next((r for r in area.regions if r.type == "WINDOW"), None)
    with bpy.context.temp_override(window=window, screen=screen, area=area, region=region):
        setup_view(area.spaces.active)
    area.tag_redraw()
    return None


def build(context):
    """Tick 1: duplicate the current workspace, name and tag the copy. The
    rest of the setup follows on the next ticks.

    The window only switches to the copy on a later tick, so the copy is found
    by what's new in bpy.data.workspaces, never via window.workspace (which
    would still be the original, e.g. the user's Layout)."""
    window = context.window
    before = set(bpy.data.workspaces)
    with context.temp_override(window=window, screen=window.screen):
        bpy.ops.workspace.duplicate()
    new = [ws for ws in bpy.data.workspaces if ws not in before]
    if len(new) != 1:
        raise RuntimeError("Couldn't create the Plinth workspace")
    ws = new[0]
    window.workspace = ws
    ws.name = NAME
    ws[TAG] = True
    if hasattr(ws, "object_mode"):
        ws.object_mode = "OBJECT"
    bpy.app.timers.register(_close_areas, first_interval=0.05)
    return ws


# Bump when VIEW_SETTINGS gains something existing studio views must pick up.
VIEW_VERSION = 3
LOOK_SETTINGS = {k: v for k, v in VIEW_SETTINGS.items() if k.startswith("shading.use_scene") or k == "clip_end"}


def upgrade_views(ws):
    """Bring an older Plinth workspace's viewports up to VIEW_VERSION (once),
    touching only the lighting settings, never the user's layout choices."""
    if ws.get("plinth_view_version", 1) >= VIEW_VERSION:
        return
    for screen in ws.screens:
        for area in screen.areas:
            if area.type == "VIEW_3D":
                engine.apply_settings(area.spaces.active, LOOK_SETTINGS)
    ws["plinth_view_version"] = VIEW_VERSION


def shading_for(engine_key):
    """Phase 17: Cycles shows live in the viewport (Rendered shading); Eevee
    keeps the studio's Material Preview, which already uses the scene's
    world and lights."""
    return "RENDERED" if engine_key == "CYCLES" else VIEW_SETTINGS["shading.type"]


def follow_engine(engine_key, workspaces=None):
    """Put every viewport of the Plinth workspace in the shading that shows this
    engine. Returns how many viewports changed. Other workspaces are left alone."""
    ws = find(workspaces)
    if ws is None:
        return 0
    want = shading_for(engine_key)
    changed = 0
    for screen in ws.screens:
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            shading = area.spaces.active.shading
            if shading.type != want:
                shading.type = want
                changed += 1
            area.tag_redraw()
    return changed


def activate(context):
    """Switch to the Plinth workspace, creating it the first time."""
    ws = find()
    if ws is None:
        ws = build(context)
        ws["plinth_view_version"] = VIEW_VERSION
        return ws
    ws[TAG] = True
    upgrade_views(ws)
    follow_engine(context.scene.plinth.engine)
    context.window.workspace = ws
    return ws
