# SPDX-License-Identifier: GPL-3.0-or-later
"""What the viewport shows when you change step (REDESIGN §6, issue 6).

The first time, Camera and Shoot look through the active shot and the other
steps centre and fit the product. After that the studio remembers: each step
comes back the way you left it (orbiting, or looking through the shot), and
the orbit view is the one you last had, wherever you had it. Building the
studio again starts over.

The memory is per session (uistate.views), like an open menu: it never goes
in the .blend.
"""

from . import studio, uistate

SHOT_STEPS = {"CAMERA", "SHOOT"}


def _rv3d(context):
    rv3d = getattr(context, "region_data", None)
    return rv3d if rv3d is not None and hasattr(rv3d, "view_perspective") else None


def _copy(v):
    return v.copy() if hasattr(v, "copy") else v


def orbit_state(rv3d):
    return {k: _copy(getattr(rv3d, k)) for k in
            ("view_rotation", "view_location", "view_distance", "view_perspective")
            if hasattr(rv3d, k)}


def set_orbit_state(rv3d, state):
    for k, v in state.items():
        setattr(rv3d, k, _copy(v))


def remember(context, step=None):
    """Note how the viewport looks now: the orbit view (if orbiting) and, for
    `step`, whether it was orbiting or looking through the shot."""
    rv3d = _rv3d(context)
    if rv3d is None:
        return
    shot = rv3d.view_perspective == "CAMERA"
    if not shot and uistate.views.get("framed"):
        uistate.views["orbit"] = orbit_state(rv3d)
    if step is not None:
        uistate.views.setdefault("modes", {})[step] = "SHOT" if shot else "ORBIT"


def forget():
    """A new studio: frame it afresh."""
    uistate.views.clear()


def to_orbit(context):
    """Leave the shot for the orbit view you had, or frame the product the
    first time. False without a studio or a viewport."""
    rv3d = _rv3d(context)
    if rv3d is None:
        return False
    saved = uistate.views.get("orbit")
    if saved and uistate.views.get("framed"):
        set_orbit_state(rv3d, saved)
        if rv3d.view_perspective == "CAMERA":
            rv3d.view_perspective = "PERSP"
        return True
    region = getattr(context, "region", None)
    lens = getattr(getattr(context, "space_data", None), "lens", 50.0)
    aspect = region.width / max(1, region.height) if region is not None else 1.0
    ok = studio.frame_view(rv3d, context.scene, lens, aspect)
    if ok:
        uistate.views["framed"] = True
        uistate.views["orbit"] = orbit_state(rv3d)
    return ok


def to_shot(context):
    """Look through the active Plinth shot. False if there isn't one."""
    rv3d = _rv3d(context)
    cam = context.scene.camera
    if rv3d is None or cam is None or "plinth_shot" not in cam:
        return False
    remember(context)
    rv3d.view_perspective = "CAMERA"
    return True


def enter(context, step):
    """Show `step` the way it was left, or its first-time default."""
    if _rv3d(context) is None:
        return False
    mode = uistate.views.get("modes", {}).get(step) or ("SHOT" if step in SHOT_STEPS else "ORBIT")
    if mode == "SHOT" and to_shot(context):
        return True
    if step in SHOT_STEPS and mode == "SHOT":
        return False                     # no shot yet: leave the view alone
    return to_orbit(context)
