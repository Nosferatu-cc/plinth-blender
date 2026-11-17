# SPDX-License-Identifier: GPL-3.0-or-later
"""Outlines of the step's lights and shots in the viewport (0.13).

The Plinth viewport turns Blender's own light and camera wireframes off
(overlay extras: all-or-nothing, with empties and probes too), so Plinth
draws them itself, one step at a time: in Light, every
Plinth light as what it really is (an area light's panel in its shape, a
spot's cone out to its target, a bulb, a sun's disc and rays), with a faint
line to what it aims at; in Camera, every shot's frame and pyramid. The light
or shot being edited is drawn in the accent colour.

``segments()`` is plain data (pairs of world points per stroke), so it's
tested headless; ``draw()`` only paints it (POST_VIEW, no depth test, so a
light behind the product still shows).
"""

import math

import bpy
from mathutils import Vector

from . import studio

ACCENT = (229 / 255, 37 / 255, 42 / 255, 1.0)
LINE = (1.0, 1.0, 1.0, 0.7)
AIM = (1.0, 1.0, 1.0, 0.22)
CIRCLE = 32


def _ring(rx, ry, z=0.0, n=CIRCLE):
    pts = [Vector((math.cos(2 * math.pi * i / n) * rx, math.sin(2 * math.pi * i / n) * ry, z))
           for i in range(n)]
    return [(pts[i], pts[(i + 1) % n]) for i in range(n)]


def _loop(pts):
    return [(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]


def _scale(scene):
    tgt = studio.find_target(scene)
    return float(tgt.get("plinth_scale", 1.0)) if tgt is not None else 1.0


def light_shape(scene, light):
    """Local-space segments for one light: what it is, where it points."""
    data = light.data
    sc = _scale(scene)
    tgt = studio.aim_target(scene, light)
    reach = (light.location - tgt.location).length if tgt is not None else 2.0 * sc
    t = data.type
    segs = []
    if t == "AREA":
        w = data.size / 2
        h = (data.size_y if data.shape in {"RECTANGLE", "ELLIPSE"} else data.size) / 2
        if data.shape in {"DISK", "ELLIPSE"}:
            segs += _ring(w, h)
        else:
            segs += _loop([Vector((-w, -h, 0)), Vector((w, -h, 0)), Vector((w, h, 0)), Vector((-w, h, 0))])
        segs.append((Vector((0, 0, 0)), Vector((0, 0, -max(w, h, 0.15 * sc)))))      # which way it faces
    elif t == "SPOT":
        r = reach * math.tan(min(data.spot_size, math.radians(170)) / 2)
        segs += _ring(r, r, -reach)
        for i in range(4):
            a = i * math.pi / 2
            segs.append((Vector((0, 0, 0)), Vector((math.cos(a) * r, math.sin(a) * r, -reach))))
        inner = r * (1.0 - data.spot_blend)
        if 0 < inner < r:
            segs += _ring(inner, inner, -reach, 24)
    elif t == "POINT":
        r = max(data.shadow_soft_size, 0.06 * sc)
        for ring in (_ring(r, r), [(a.xzy, b.xzy) for a, b in _ring(r, r)],
                     [(a.zxy, b.zxy) for a, b in _ring(r, r)]):
            segs += ring
    else:                                                             # SUN: disc, rays, direction
        r = 0.12 * sc
        segs += _ring(r, r)
        for i in range(8):
            a = i * math.pi / 4
            c, s = math.cos(a), math.sin(a)
            segs.append((Vector((c * r * 1.4, s * r * 1.4, 0)), Vector((c * r * 2.0, s * r * 2.0, 0))))
        segs.append((Vector((0, 0, 0)), Vector((0, 0, -0.8 * sc))))
    return segs


def camera_shape(scene, cam):
    """Local-space segments for a shot: frame, pyramid and the up marker."""
    corners = [Vector(v) for v in cam.data.view_frame(scene=scene)]
    k = _scale(scene) * 0.6 / max(0.01, max(abs(c.z) for c in corners) or 1.0)
    corners = [c * k for c in corners]               # the pyramid as deep as the product is big
    segs = _loop(corners)
    segs += [(Vector((0, 0, 0)), c) for c in corners]
    top = [c for c in corners if c.y > 0]
    if len(top) == 2:
        mid = (top[0] + top[1]) / 2
        up = Vector((0, (top[0] - top[1]).length * 0.25, 0))
        segs += [(top[0].lerp(mid, 0.5), mid + up), (mid + up, top[1].lerp(mid, 0.5))]
    return segs


def segments(scene, view_camera=None):
    """[(colour, width, [(a, b), ...] in world space)] for the scene's step.
    view_camera: the shot being looked through (not drawn)."""
    step = scene.plinth.step
    out = []
    if step == "LIGHT":
        active = studio.active_light(scene)
        for light in studio.list_lights(scene):
            m = light.matrix_world
            on = light == active
            segs = [(m @ a, m @ b) for a, b in light_shape(scene, light)]
            out.append((ACCENT if on else LINE, 2.0 if on else 1.25, segs))
            tgt = studio.aim_target(scene, light)
            if tgt is not None and light.data.type != "POINT":
                out.append((AIM, 1.0, [(m.translation.copy(), tgt.matrix_world.translation.copy())]))
    elif step == "CAMERA":
        for cam in studio.list_shots(scene):
            if cam == view_camera:
                continue
            m = cam.matrix_world
            on = cam == scene.camera
            out.append((ACCENT if on else LINE, 2.0 if on else 1.25,
                        [(m @ a, m @ b) for a, b in camera_shape(scene, cam)]))
    return out


# --- Drawing ------------------------------------------------------------------------

_handle = None
_shader = {}


def draw():
    context = bpy.context
    from . import overlay
    try:
        if not overlay.is_visible(context) or not context.space_data.overlay.show_overlays:
            return
        rv3d = context.region_data
        scene = context.scene
        looking = scene.camera if rv3d is not None and rv3d.view_perspective == "CAMERA" else None
        strokes = segments(scene, looking)
    except Exception:                         # never break the viewport over an outline
        return
    if not strokes:
        return
    import gpu
    from gpu_extras.batch import batch_for_shader
    shader = _shader.get("s")
    if shader is None:
        shader = _shader["s"] = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
    region = context.region
    s = overlay.ui_scale(context)
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("NONE")
    shader.bind()
    shader.uniform_float("viewportSize", (region.width, region.height))
    for colour, width, segs in strokes:
        if not segs:
            continue
        coords = [tuple(p) for seg in segs for p in seg]
        shader.uniform_float("lineWidth", width * s)
        shader.uniform_float("color", colour)
        batch_for_shader(shader, "LINES", {"pos": coords}).draw(shader)
    gpu.state.blend_set("NONE")


def register():
    global _handle
    if _handle is None:
        _handle = bpy.types.SpaceView3D.draw_handler_add(draw, (), "WINDOW", "POST_VIEW")


def unregister():
    global _handle
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
        _handle = None
    _shader.clear()
