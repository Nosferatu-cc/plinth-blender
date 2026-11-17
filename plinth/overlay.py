# SPDX-License-Identifier: GPL-3.0-or-later
"""The floating viewport UI (Phase 2): drawing and input.

What is on screen comes from hud.build() as a list of elements. This module
draws them in one POST_PIXEL handler (gpu + blf) and routes input through one
invisible 2D gizmo: Blender calls its ``test_select`` on mouse move (hover),
and a click runs ``plinth.overlay_click``, which either fires the element's
action or drags a slider. No permanently running modal operator, so autosave
and file loading behave normally.
"""

import math
import os
import time
from contextlib import contextmanager

import bpy

from . import anim, hud, motion, parity, textedit, uistate, workspace
from .hud import (MARGIN_TOP, STEP_HEIGHT, STEP_KEYS, STEP_LABELS,  # noqa: F401  (re-exported)
                  PillLayout, hit_test, overlap_insets, pill_layout)

# Design tokens (docs/DESIGN.md, canvas boards), colours as RGBA 0..1.
# v0.4 (REDESIGN.md §1): panels are solid black. Blender can't blur, and a
# translucent fill turned muddy grey over light backdrops.
GLASS = (0.0, 0.0, 0.0, 1.0)                            # all floating surfaces
# Containers have no border (a 1 px SDF edge over the viewport rendered soft and
# grey). The hairline survives only as in-card dividers, drawn as snapped rects.
HAIRLINE_DIVIDER = (1.0, 1.0, 1.0, 0.14)
# Kinds that are containers: solid fill, never a border.
OUTLINE = (1.0, 1.0, 1.0, 0.14)                         # outline buttons (editlink)
CONTAINER_KINDS = frozenset({"glass", "card", "dropdown", "lightchip", "menu"})
WHITE = (245 / 255, 245 / 255, 247 / 255, 1.0)          # #f5f5f7
BLACK = (0.0, 0.0, 0.0, 1.0)
TEXT = (180 / 255, 180 / 255, 185 / 255, 1.0)           # #b4b4b9, secondary
SOFT_TEXT = (209 / 255, 209 / 255, 214 / 255, 1.0)      # #d1d1d6
ACCENT = (229 / 255, 37 / 255, 42 / 255, 1.0)           # #e5252a, "active" only
HOVER_BG = (1.0, 1.0, 1.0, 0.08)
CHIP_BG = (1.0, 1.0, 1.0, 0.08)
CHIP_HOVER = (1.0, 1.0, 1.0, 0.16)
HELP = (142 / 255, 142 / 255, 147 / 255, 1.0)           # #8e8e93, 11 px help/notes
DANGER = (1.0, 90 / 255, 82 / 255, 1.0)                 # #ff5a52, destructive items

FONT_SIZE = hud.FONT


def fade(c, k):
    return (c[0], c[1], c[2], c[3] * k)


def mix(a, b, t):
    """Colour a -> b by t (0..1): hover and on-state cross-fades."""
    if t <= 0.0:
        return a
    if t >= 1.0:
        return b
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def on_glass(c):
    """``c`` composited over GLASS, opaque: hover fills for pills that float
    straight over the viewport (a translucent fill there would show the scene)."""
    a = c[3]
    return tuple(c[i] * a + GLASS[i] * (1 - a) for i in range(3)) + (1.0,)


# --- Context -> elements -----------------------------------------------------

def ui_scale(context):
    return context.preferences.system.ui_scale


_measure_cache = {}


def measure(text, size, font=0, spacing=0.0):
    """Drawn width of ``text`` (with letter spacing, as text() draws it)."""
    import blf
    key = (text, round(size, 2), font, round(spacing, 2))
    w = _measure_cache.get(key)
    if w is None:
        if len(_measure_cache) > 4000:
            _measure_cache.clear()
        blf.size(font, size)
        if spacing:
            w = sum(blf.dimensions(font, ch)[0] for ch in text) + spacing * max(0, len(text) - 1)
        else:
            w = blf.dimensions(font, text)[0]
        _measure_cache[key] = w
    return w


MIN_FIT = 0.86          # text shrinks to 86% of its size before it is cut with "…"
cut = set()             # texts that had to be cut to fit (audited by the GUI check)


def fit_text(t, size, width, measure_fn):
    """(text, size) that fits ``width``: as is, a little smaller, or ellipsised.

    ``measure_fn(text, size)`` gives a drawn width. Plain function, so the rule
    every button and label follows is tested headless (Phase 16)."""
    if not t or width <= 0:
        return ("" if width <= 0 else t), size
    if measure_fn(t, size) <= width:
        return t, size
    for k in (0.94, 0.9, MIN_FIT):
        if measure_fn(t, size * k) <= width:
            return t, size * k
    size *= MIN_FIT
    while t and measure_fn(t + "…", size) > width:
        t = t[:-1].rstrip()
    return (t + "…") if t else "", size


def is_visible(context):
    space = context.space_data
    scene = context.scene
    return (space is not None and space.type == "VIEW_3D" and space.show_gizmo
            and scene is not None and scene.plinth.show_overlay
            and workspace.is_active(context))


def product_objects(context):
    """Selected meshes that aren't Plinth's own, active object first."""
    col = bpy.data.collections.get("Plinth")
    own = set(col.all_objects) if col else set()
    sel = [o for o in (context.selected_objects or ()) if o.type == "MESH" and o not in own]
    act = context.active_object
    if act in sel:
        sel.remove(act)
        sel.insert(0, act)
    return sel


def _project(context, objects, key):
    """{key(obj): (x, y)} for objects visible in this region."""
    from bpy_extras import view3d_utils
    pts = {}
    region = context.region
    for obj in objects:
        p = view3d_utils.location_3d_to_region_2d(region, context.region_data,
                                                  obj.matrix_world.translation)
        if p is not None and 0 <= p.x <= region.width and 0 <= p.y <= region.height:
            pts[key(obj)] = (p.x, p.y)
    return pts


def pinned_point(perspective, point, width, height, margin, box=None):
    """Region point of a 3D point, pinned inside the view (margin from the
    edges; ``box`` = (left, bottom, right, top) of what's really visible:
    header, sidebars and the top bars cover the rest) when it's off-screen or
    behind the camera, in its direction: a handle you can always grab. Pure
    maths on the view's perspective matrix."""
    from mathutils import Vector
    co = perspective @ Vector((point[0], point[1], point[2], 1.0))
    w = co.w if abs(co.w) > 1e-9 else 1e-9
    nx, ny = co.x / abs(w), co.y / abs(w)            # behind (w < 0): still the side it lies on
    hx, hy = width / 2.0, height / 2.0
    x, y = hx + nx * hx, hy + ny * hy
    bl, bb, br, bt = box if box is not None else (0.0, 0.0, width, height)
    lo_x, hi_x, lo_y, hi_y = bl + margin, br - margin, bb + margin, bt - margin
    if w > 0 and lo_x <= x <= hi_x and lo_y <= y <= hi_y:
        return x, y
    dx, dy = x - hx, y - hy                           # along the way from the centre to it
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        dy = 1.0
    k = min((hi_x - hx) / abs(dx) if dx else float("inf"), (hi_y - hy) / abs(dy) if dy else float("inf"))
    if w > 0 and k >= 1.0:
        return x, y
    return hx + dx * k, hy + dy * k


def geo_for(context):
    region, area, space = context.region, context.area, context.space_data
    s = ui_scale(context)
    left, right, top = overlap_insets(area, region, context.preferences.system.use_region_overlap)
    nav = hud.NAV_W * s if getattr(space, "show_gizmo_navigate", False) else 0
    from . import studio
    scene = context.scene
    lights = (_project(context, studio.list_lights(scene), studio.light_label)
              if scene.plinth.step == "LIGHT" else None)
    targets = (_project(context, studio.list_targets(scene), lambda o: o.name)
               if scene.plinth.step in {"STUDIO", "LIGHT", "CAMERA"} else None)
    decal_pins = None
    if scene.plinth.step == "MATERIAL":
        from . import decals
        pins = [bpy.data.objects.get(r.get("projector", "")) for o in product_objects(context)
                for rs in o.get(decals.KEY, {}).values() for r in rs if r["mode"] == "PROJECT"]
        decal_pins = _project(context, [p for p in pins if p is not None], lambda o: o.name)
    cameras = None
    if scene.plinth.step == "CAMERA":                 # every shot but the one being looked through
        rv3d = context.region_data
        through = rv3d is not None and rv3d.view_perspective == "CAMERA"
        cameras = _project(context, [c for c in studio.list_shots(scene)
                                     if not (through and c == scene.camera)], lambda o: o.name)
    sun = None
    if scene.plinth.step == "LIGHT":                  # the clear sky's sun, draggable
        from . import hdri
        at = hdri.sun_handle(scene)
        if at is not None and context.region_data is not None:
            # Visible: below Blender's header and Plinth's top bars, clear of the sidebars.
            box = (left, 0.0, region.width - right - nav, region.height - top - hud.TOP_BARS * s)
            sun = pinned_point(context.region_data.perspective_matrix, at, region.width, region.height,
                               hud.SUN_MARGIN * s, box)
    from . import transform
    return hud.Geo(region.width, region.height, s, left, right, top, nav, measure,
                   camera_frame_rect(context), lights, None, targets, decal_pins, cameras,
                   transform.modes(space), sun)


_failed = {"msg": None}


def elements(context):
    """Everything on screen. Runs while Blender draws and hit-tests, where data
    can't be written. If building fails, log it once and show nothing: the
    sidebar still works, and clicks and navigation are never blocked."""
    if context.region is None or context.area is None or not is_visible(context):
        return []
    try:
        return _elements(context)
    except Exception as exc:
        msg = "%s: %s" % (type(exc).__name__, exc)
        if _failed["msg"] != msg:
            _failed["msg"] = msg
            import traceback
            print("Plinth: floating controls hidden after an error (sidebar still works):")
            traceback.print_exc()
        return []


def _elements(context):
    els = hud.build(context.scene, geo_for(context), product_objects(context))
    lay = compare_layout(context)
    if lay is not None:
        frame, divider = lay
        grab = 12 * ui_scale(context)
        els.insert(0, hud.El("divider", "divider", (divider - grab, frame[1], 2 * grab, frame[3]),
                             track=frame, value=context.scene.plinth.compare_split,
                             drag=lambda c, f: setattr(c.scene.plinth, "compare_split", f)))
    return els


def layout_for(context):
    """The step pill's layout on its own."""
    if context.region is None or context.area is None:
        return None
    s = ui_scale(context)
    insets = overlap_insets(context.area, context.region,
                            context.preferences.system.use_region_overlap)
    return pill_layout(context.region.width, context.region.height, STEP_LABELS,
                       lambda t: measure(t, FONT_SIZE * s), s, *insets)


# Redraw cost, CPU side: exponential moving average of the draw callback.
stats = {"avg_ms": 0.0, "samples": 0}


def _record(ms):
    stats["avg_ms"] = ms if stats["samples"] == 0 else stats["avg_ms"] * 0.9 + ms * 0.1
    stats["samples"] += 1


# --- Primitives ---------------------------------------------------------------
# Rounded rectangles: one quad per shape, a signed-distance fragment shader
# gives anti-aliased edges and a hairline border in one pass.
_shader = None
_flat = False           # True when we fell back to the builtin flat shader
_FRAG = """
float rbox(vec2 p, vec2 b, float r) {
    vec2 q = abs(p) - b + r;
    return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
}
void main() {
    vec2 half_size = rect.zw * 0.5;
    float d = rbox(p - (rect.xy + half_size), half_size, radius);
    float outer = clamp(0.5 - d, 0.0, 1.0);
    float inner = clamp(0.5 - d - border, 0.0, 1.0);
    vec4 col = mix(borderColor, fillColor, inner);
    fragColor = vec4(col.rgb, col.a * outer);
}
"""


def _get_shader():
    global _shader, _flat
    if _shader is None:
        import gpu
        try:
            info = gpu.types.GPUShaderCreateInfo()
            info.push_constant("MAT4", "ModelViewProjectionMatrix")
            info.push_constant("VEC4", "rect")
            info.push_constant("VEC4", "fillColor")
            info.push_constant("VEC4", "borderColor")
            info.push_constant("FLOAT", "radius")
            info.push_constant("FLOAT", "border")
            iface = gpu.types.GPUStageInterfaceInfo("plinth_rrect_iface")
            iface.smooth("VEC2", "p")
            info.vertex_in(0, "VEC2", "pos")
            info.vertex_out(iface)
            info.fragment_out(0, "VEC4", "fragColor")
            info.vertex_source(
                "void main() { p = pos;"
                " gl_Position = ModelViewProjectionMatrix * vec4(pos, 0.0, 1.0); }")
            info.fragment_source(_FRAG)
            _shader = gpu.shader.create_from_info(info)
        except Exception as exc:   # never take the viewport down over styling
            print("Plinth: rounded-rect shader unavailable, using flat rects:", exc)
            _shader = gpu.shader.from_builtin("UNIFORM_COLOR")
            _flat = True
    return _shader


_alpha = [1.0]          # opacity multiplier for everything painted (fades, page flip)


def _lin(v):
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


_gpu_cache = {}


def _a(c):
    """A token colour (sRGB, like the design canvas) as the GPU needs it.

    The 3D viewport draws overlays into a scene-linear buffer, so sRGB values
    sent as-is come out gamma-brightened (an 8% white well showed as 31% grey).
    Colours go to linear; alpha is mapped too, so a translucent fill over the
    black panels lands on the same sRGB value as on the canvas. Fades multiply
    alpha before the mapping."""
    a = c[3] * _alpha[0]
    key = (c[0], c[1], c[2], a)
    out = _gpu_cache.get(key)
    if out is None:
        if len(_gpu_cache) > 4096:
            _gpu_cache.clear()
        out = _gpu_cache[key] = (_lin(c[0]), _lin(c[1]), _lin(c[2]), a if a >= 1.0 else _lin(a))
    return out


def _px(v):
    """Nearest whole device pixel, halves up (1.5 px -> 2, 3.0 -> 3)."""
    return math.floor(v + 0.5)


def snap(rect, border):
    """An outline's rect on whole device pixels and its width in whole pixels
    (at least 1): the SDF edge then lands exactly on pixel boundaries, crisp at
    any UI scale."""
    x, y, w, h = rect
    x0, y0, x1, y1 = _px(x), _px(y), _px(x + w), _px(y + h)
    return (x0, y0, max(1, x1 - x0), max(1, y1 - y0)), max(1, _px(border))


def draw_rule(rect, color):
    """A divider: a filled rect, 1 device px (or more) thick, on whole pixels."""
    (x, y, w, h), _b = snap(rect, 1)
    draw_rrect((x, y, w, h), color, 0)


def draw_rrect(rect, fill, radius, border=0.0, border_color=None):
    import gpu
    from gpu_extras.batch import batch_for_shader
    fill = _a(fill)
    border_color = _a(border_color) if border_color is not None else None
    if border > 0:
        rect, border = snap(rect, border)
    x, y, w, h = rect
    radius = max(0.0, min(radius, w / 2, h / 2))
    shader = _get_shader()
    x0, y0, x1, y1 = x - 1, y - 1, x + w + 1, y + h + 1     # +1px for the AA fringe
    batch = batch_for_shader(shader, "TRIS", {"pos": [(x0, y0), (x1, y0), (x1, y1),
                                                      (x0, y0), (x1, y1), (x0, y1)]})
    gpu.state.blend_set("ALPHA")          # per shape: blf text drawing resets blending
    shader.bind()
    if _flat:
        shader.uniform_float("color", fill)
    else:
        shader.uniform_float("ModelViewProjectionMatrix",
                             gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix())
        shader.uniform_float("rect", rect)
        shader.uniform_float("fillColor", fill)
        shader.uniform_float("borderColor", border_color if border > 0 else fill)
        shader.uniform_float("radius", radius)
        shader.uniform_float("border", border)
    batch.draw(shader)


def draw_image(texture, rect):
    """A texture (premade linear RGBA, straight alpha) over rect, snapped to pixels."""
    import gpu
    from gpu_extras.batch import batch_for_shader
    x, y, w, h = (_px(v) for v in rect)
    shader = gpu.shader.from_builtin("IMAGE")
    batch = batch_for_shader(shader, "TRI_FAN", {
        "pos": ((x, y), (x + w, y), (x + w, y + h), (x, y + h)),
        "texCoord": ((0, 0), (1, 0), (1, 1), (0, 1))})
    gpu.state.blend_set("ALPHA")
    shader.bind()
    shader.uniform_sampler("image", texture)
    batch.draw(shader)


def draw_tris(points, color):
    import gpu
    from gpu_extras.batch import batch_for_shader
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch = batch_for_shader(shader, "TRIS", {"pos": points})
    gpu.state.blend_set("ALPHA")
    shader.bind()
    shader.uniform_float("color", _a(color))
    batch.draw(shader)


_mono = {"id": None}
_MONO_FILE = "DejaVuSansMono.woff2"


def mono_font():
    """Blender's bundled monospace (numbers, labels); falls back to the UI font."""
    if _mono.get("id") is None:
        import blf
        _mono["id"] = 0
        try:
            path = os.path.join(bpy.utils.system_resource("DATAFILES", path="fonts"), _MONO_FILE)
            if os.path.isfile(path):
                fid = blf.load(path)
                if fid >= 0:
                    _mono["id"], _mono["path"] = fid, path
        except Exception:
            pass
    return _mono["id"]


def text(t, rect, size, color, align="left", font=0, spacing=0.0, color_at=None, fit=True, pad=0.0):
    """Draw `t` vertically centred in rect. spacing > 0 letter-spaces it (labels);
    ``color_at(glyph centre x)`` colours glyph by glyph (inverted progress label).

    With ``fit`` (the default) the text never leaves its rect: it shrinks a
    little, then ends in "…" (Phase 16). ``pad`` keeps it off the rect's sides."""
    import blf
    x, y, w, h = rect
    if pad:
        x, w = x + pad, w - 2 * pad
    cap_size, base_spacing = size, spacing
    if fit and t:
        whole = t
        t, size = fit_text(t, cap_size, w,
                           lambda tt, ss: measure(tt, ss, font, base_spacing * ss / cap_size))
        spacing = base_spacing * size / cap_size
        if t != whole and len(cut) < 200:
            cut.add(whole)                          # tools/gui_check.py audits this
    blf.size(font, cap_size)
    cap = blf.dimensions(font, "S")[1]
    blf.size(font, size)
    widths = [blf.dimensions(font, ch)[0] for ch in t] if spacing else None
    tw = (sum(widths) + spacing * max(0, len(t) - 1)) if spacing else blf.dimensions(font, t)[0]
    tx = x if align == "left" else x + w - tw if align == "right" else x + (w - tw) / 2
    ty = round(y + (h - cap) / 2)
    blf.color(font, *_a(color))
    if not spacing:
        blf.position(font, round(tx), ty, 0)
        blf.draw(font, t)
        return
    for ch, cw in zip(t, widths):
        if color_at is not None:
            blf.color(font, *_a(color_at(tx + cw / 2)))
        blf.position(font, round(tx), ty, 0)
        blf.draw(font, ch)
        tx += cw + spacing


# --- Element painters ------------------------------------------------------------

MOTION = motion.Motion()
PUMP = anim.RedrawPump(MOTION.a)
CLEAR = (0.0, 0.0, 0.0, 0.0)
_menu_anchor = [(0.0, 0.0)]     # the open menu grows from this corner


@contextmanager
def _transformed(e, s, alpha=1.0):
    """Press/pop scale about the element's centre, the card's page flip and the
    menu's open animation, as a GPU matrix (blf text follows it) plus opacity."""
    import gpu
    press = MOTION.scale(e.id)
    dx = 0.0
    menu_scale = 1.0
    if e.layer == "card":
        dx, a = MOTION.flip()
        dx *= s
        alpha *= a
    elif e.layer == "menu":
        menu_scale, a = MOTION.menu_in()
        alpha *= a
    if press == 1.0 and dx == 0.0 and menu_scale == 1.0 and alpha >= 1.0:
        yield
        return
    saved = _alpha[0]
    _alpha[0] = saved * alpha
    try:
        with gpu.matrix.push_pop():
            if menu_scale != 1.0:
                ax, ay = _menu_anchor[0]
                gpu.matrix.translate((ax, ay))
                gpu.matrix.scale((menu_scale, menu_scale))
                gpu.matrix.translate((-ax, -ay))
            if dx:
                gpu.matrix.translate((dx, 0.0))
            if press != 1.0:
                cx, cy = e.rect[0] + e.rect[2] / 2, e.rect[1] + e.rect[3] / 2
                gpu.matrix.translate((cx, cy))
                gpu.matrix.scale((press, press))
                gpu.matrix.translate((-cx, -cy))
            yield
    finally:
        _alpha[0] = saved


def _coverage(rect, ind):
    """How much of `rect` the sliding indicator covers (0..1): its text colour."""
    if ind is None:
        return 0.0
    x0 = max(rect[0], ind[0])
    x1 = min(rect[0] + rect[2], ind[0] + ind[2])
    y0 = max(rect[1], ind[1])                    # a wrapped row: only the line it's on
    y1 = min(rect[1] + rect[3], ind[1] + ind[3])
    across = max(0.0, min(1.0, (x1 - x0) / max(1.0, rect[2])))
    return across * max(0.0, min(1.0, (y1 - y0) / max(1.0, rect[3])))


def _seg_radius(e, s):
    return (11 if e.kind == "seg2" else 9 if e.kind in {"tab", "segcell"} else 0) * s or e.rect[3] / 2


class _NewIds:
    """content.NEW_IDS, looked up when painting (content registers after overlay)."""
    def __contains__(self, el_id):
        from . import content
        return el_id in content.NEW_IDS


NEW_IDS = _NewIds()


def paint_all(els, s, hover):
    """Paint in order. A segmented control's indicator goes down just before its
    first cell, so it sits on the container and under the labels."""
    drawn = set()
    for e in els:
        if e.group and e.group not in drawn:
            drawn.add(e.group)
            r = MOTION.indicator(e.group)
            if r is not None:
                with _transformed(e, s):
                    draw_rrect(r, WHITE, _seg_radius(e, s))
        with _transformed(e, s):
            _paint(e, s, hover)
            if e.id in NEW_IDS:
                _new_dot(e, s)
    if MOTION.ghost:                                  # a menu that just closed, fading out
        fade_out = MOTION.menu_out()
        if fade_out > 0.0:
            saved = _alpha[0]
            _alpha[0] = fade_out
            try:
                for e in MOTION.ghost:
                    _paint(e, s, None)
            finally:
                _alpha[0] = saved


def _new_dot(e, s):
    """New from the content library: a small soft-white dot in the top-right
    corner (red stays reserved for "active")."""
    x, y, w, h = e.rect
    d = 5 * s
    draw_rrect((x + w - d - 5 * s, y + h - d - 5 * s, d, d), SOFT_TEXT, d / 2)


def _seg_colour(e, hv, off, on):
    cov = _coverage(e.rect, MOTION.indicator(e.group)) if e.group else (1.0 if e.on else 0.0)
    return cov, mix(mix(off, WHITE, hv), on, cov)


MORE_KINDS = {"add", "addshot", "target", "sun"}     # painted by _paint_more_kinds


def _paint(e, s, hover):
    k, (x, y, w, h) = e.kind, e.rect
    hv = MOTION.hover(e.id) if (e.act is not None or e.drag is not None) else 0.0
    dim = 0.45 if e.dim else 1.0
    if k in {"glass", "card"}:
        rect = MOTION.card_rect(e.rect) if k == "card" else e.rect
        draw_rrect(rect, GLASS, e.extra["radius"])
    elif k == "well":
        draw_rrect(e.rect, CHIP_BG, e.extra["radius"])
    elif k == "seg":
        cov, col = _seg_colour(e, hv, TEXT, BLACK)
        if not e.group and e.on:
            draw_rrect(e.rect, WHITE, h / 2)
        if hv and cov < 1.0:
            draw_rrect(e.rect, fade(HOVER_BG, hv * (1.0 - cov)), h / 2)
        text(e.text, e.rect, hud.FONT * s, col, "center", pad=6 * s)
    elif k == "seg2":
        cov, col = _seg_colour(e, 0.0, SOFT_TEXT, BLACK)
        if hv and cov < 1.0:
            draw_rrect(e.rect, fade(HOVER_BG, hv * (1.0 - cov)), 11 * s)
        text(e.shown, (x, y + h * 0.52, w, h * 0.4), 9 * s, fade(col, 0.7), "center",
             mono_font(), 1.2 * s)
        text(e.text, (x, y + h * 0.12, w, h * 0.42), hud.FONT * s, col, "center")
    elif k == "dropdown":
        draw_rrect(e.rect, mix(GLASS, on_glass(CHIP_HOVER), hv), h / 2)
        text(e.text, (x + 14 * s, y, w - 44 * s, h), hud.FONT * s, WHITE)
        cx, cy, r = x + w - 18 * s, y + h / 2, 3.5 * s
        draw_tris([(cx - r, cy + r / 2), (cx + r, cy + r / 2), (cx, cy - r / 2)], WHITE)
    elif k == "lightchip":
        on = MOTION.on(e.id, 1.0 if e.on else 0.0)
        bg = mix(mix(GLASS, on_glass(CHIP_HOVER), hv), on_glass((1, 1, 1, 0.16)), on)
        draw_rrect(e.rect, bg, h / 2)                # the fill alone says "on" (no border)
        d = 10 * s                                   # the light's own colour (issue 8)
        rgb = e.extra if e.extra else SOFT_TEXT
        draw_rrect((x + 14 * s, y + (h - d) / 2, d, d), (rgb[0], rgb[1], rgb[2], 1.0), d / 2,
                   border=1.0 * s, border_color=(1, 1, 1, 0.25))
        tx = x + 14 * s + d + 8 * s
        room = _split(e.text, e.shown, x + w - 14 * s - tx, hud.FONT * s, hud.SMALL * s, mono_font())
        text(e.text, (tx, y, room, h), hud.FONT * s, WHITE)
        if e.shown:
            text(e.shown, (x, y, w - 14 * s, h), hud.SMALL * s, TEXT, "right", mono_font())
    elif k == "title":
        text(e.text, e.rect, hud.TITLE * s, WHITE)
    elif k == "tag":
        text(e.text, e.rect, hud.MONO * s, TEXT, "right", mono_font(), 1.2 * s)
    elif k == "label":
        hint = (e.extra or {}).get("hints", {}).get(hover) if isinstance(e.extra, dict) else None
        right = hint or e.shown
        text(e.text, (x, y, _split(e.text, right, w, hud.FONT * s, hud.SMALL * s), h), hud.FONT * s, WHITE)
        if right:
            text(right, (x + w * 0.4, y, w * 0.6, h), hud.SMALL * s, TEXT, "right")
    elif k == "slider":
        _slider_track(e, s, hv)                            # 14 px knob, 18 px while dragging
    elif k == "shape":
        on = MOTION.on(e.id, 1.0 if e.on else 0.0)
        draw_rrect(e.rect, mix(mix(CHIP_BG, CHIP_HOVER, hv), WHITE, on), 12 * s)
        _glyph(e.extra, x + w / 2, y + h / 2, s, mix(SOFT_TEXT, BLACK, on))
    elif k == "chip":
        on = MOTION.on(e.id, 1.0 if e.on else 0.0)
        bg = mix(mix(CHIP_BG, CHIP_HOVER, 0.0 if e.dim else hv), WHITE, on)
        draw_rrect(e.rect, fade(bg, dim), h / 2)
        text(e.text, e.rect, hud.SMALL * s, fade(mix(SOFT_TEXT, BLACK, on), dim), "center", pad=10 * s)
    elif k == "ends":
        text(e.text, e.rect, 11 * s, TEXT)
        text(e.shown, e.rect, 11 * s, TEXT, "right")
    elif k == "primary":
        draw_rrect(e.rect, fade(WHITE, 0.3) if e.dim else fade(WHITE, 1.0 - 0.1 * hv), 12 * s)
        text(e.text, e.rect, 14 * s, TEXT if e.dim else BLACK, "center", pad=12 * s)
    elif k == "primary_big":
        draw_rrect(e.rect, fade(WHITE, 0.3) if e.dim else fade(WHITE, 1.0 - 0.1 * hv), h / 2)
        t_w = measure(e.text, 15 * s)
        d = 8 * s
        x0 = x + (w - t_w - d - 10 * s) / 2
        if not e.dim:
            draw_rrect((x0, y + (h - d) / 2, d, d), ACCENT, d / 2)
        text(e.text, (x0 + d + 10 * s, y, t_w + 2, h), 15 * s, TEXT if e.dim else BLACK, fit=False)
    elif k == "more":
        draw_rrect(e.rect, fade(CHIP_BG, hv), 12 * s, border=1.0 * s, border_color=OUTLINE)
        text(e.text, e.rect, hud.FONT * s, mix(TEXT, WHITE, hv), "center")
    elif k == "shot":
        a = dim
        if e.on or hv:
            draw_rrect(e.rect, fade((1, 1, 1, 0.12 if e.on else 0.07 * hv), a), 14 * s)
        aw, ah = e.extra
        th = 34 * s
        tw = th * aw / ah
        tx, ty = x + (w - tw) / 2, y + h - 6 * s - th
        draw_rrect((tx, ty, tw, th), fade((28 / 255, 28 / 255, 30 / 255, 1), a), 5 * s,
                   border=(1.5 if e.on else 1.0) * s,
                   border_color=ACCENT if e.on else (1, 1, 1, 0.1 * a))
        bw, bh = tw * 0.22, th * 0.5
        draw_rrect((tx + (tw - bw) / 2, ty + th * 0.18, bw, bh), fade((0.55, 0.57, 0.6, 1), a), bw / 2)
        text(e.text, (x, y + 4 * s, w, 14 * s), 11 * s, fade(WHITE if e.on else TEXT, a), "center")
    elif k == "hint":
        text(e.text, e.rect, hud.SMALL * s, TEXT)
    elif k in {"status", "frame_label"}:
        text(e.text, e.rect, hud.MONO * s, TEXT, "right" if k == "status" else "left",
             mono_font(), 1.2 * s)
    elif k in MORE_KINDS:
        _paint_more_kinds(e, s, hv)
    elif k == "textfield":
        _paint_textfield(e, s)
    elif k in BAR_KINDS:
        _paint_bar(e, s, hv)
    elif k in PAGE_KINDS:
        _paint_page(e, s, hv)
    elif k in BROWSER_KINDS:
        _paint_browser(e, s, hv)
    elif k == "lightpoint":
        cx, cy = x + w / 2, y + h / 2
        r = 9 * s
        lit = max(hv, 1.0 if e.on else 0.0)
        draw_rrect((cx - 2 * r, cy - 2 * r, 4 * r, 4 * r), (1, 1, 1, 0.05 + 0.05 * lit), 2 * r)
        draw_rrect((cx - r, cy - r, 2 * r, 2 * r), WHITE, r)
        if e.on:
            draw_rrect((cx - 3 * s, cy - 3 * s, 6 * s, 6 * s), ACCENT, 3 * s)
        text(e.text, (cx - 40 * s, cy - r - 20 * s, 80 * s, 14 * s), hud.MONO * s,
             mix(TEXT, WHITE, lit), "center", mono_font(), 1.2 * s)


FIELD_BG = (28 / 255, 28 / 255, 30 / 255, 1.0)       # #1c1c1e
BAR_KINDS = {"shotcard", "tick", "dots", "vline", "segcell", "progress", "stopbtn", "donebtn",
             "shootbtn", "pager", "menu", "menuitem"}


PAGE_KINDS = {"gridchip", "presettile", "linkhint", "titledrop",
              "editlink", "back", "tab", "hline", "outline", "done", "strip", "palette",
              "targetpill", "hexvalue", "tile", "switchrow", "listgroup", "listrow", "pathbox", "note"}


BROWSER_KINDS = {"searchbox", "navitem", "closebtn", "scrollbar", "catrow", "matcurrent"}


def _preview_or_dot(key, rgb, cx, cy, d, s):
    """A material's live preview (a sphere), or its colour until that's ready."""
    from . import previews
    best = previews.best(key)
    tex = previews.texture(best) if best else None
    if tex is not None:
        draw_image(tex, (cx - d / 2, cy - d / 2, d, d))
        return
    r = d * 0.4
    draw_rrect((cx - r, cy - r, 2 * r, 2 * r), (*rgb[:3], 1.0), r, border=1.0 * s,
               border_color=(1, 1, 1, 0.22))


def _paint_browser(e, s, hv):
    """The material browser (browser.py) and the card's current-material row."""
    k, (x, y, w, h) = e.kind, e.rect
    if k == "searchbox":
        draw_rrect(e.rect, FIELD_BG, h / 2, border=1.0 * s, border_color=(1, 1, 1, 0.12 + 0.2 * hv))
        cx, cy, r = x + 18 * s, y + h / 2 + 1 * s, 5 * s           # magnifier
        draw_rrect((cx - r, cy - r, 2 * r, 2 * r), CLEAR, r, border=1.4 * s, border_color=TEXT)
        _stroke((cx + r * 0.7, cy - r * 0.7), (cx + r * 1.6, cy - r * 1.6), 1.6 * s, TEXT)
        if uistate.focus.get("id") != e.id:                          # the text field draws itself
            shown = e.text or e.shown
            text(shown, (x + 34 * s, y, w - 46 * s, h), hud.FONT * s, WHITE if e.text else HELP)
    elif k == "navitem":
        on = MOTION.on(e.id, 1.0 if e.on else 0.0)
        if on > 0.01 or hv:
            draw_rrect(e.rect, mix(fade(CHIP_BG, hv), WHITE, on), 9 * s)
        col = mix(SOFT_TEXT, BLACK, on)
        room = _split(e.text, e.shown, w - 20 * s, hud.SMALL * s, 11 * s, mono_font())
        text(e.text, (x + 10 * s, y, room, h), hud.SMALL * s, col)
        if e.shown:
            text(e.shown, (x, y, w - 10 * s, h), 11 * s, mix(TEXT, BLACK, on), "right", mono_font())
    elif k == "closebtn":
        draw_rrect(e.rect, mix(CHIP_BG, CHIP_HOVER, hv), w / 2)
        r = 5 * s
        cx, cy = x + w / 2, y + h / 2
        _stroke((cx - r, cy - r), (cx + r, cy + r), 1.6 * s, WHITE)
        _stroke((cx - r, cy + r), (cx + r, cy - r), 1.6 * s, WHITE)
    elif k == "scrollbar":
        draw_rrect(e.rect, CHIP_BG, w / 2)
        frac = max(0.08, min(1.0, (e.extra or {}).get("frac", 1.0)))
        th = h * frac
        ty = y + (h - th) * (1.0 - e.value)                          # value 0 = top
        draw_rrect((x, ty, w, th), mix(fade(WHITE, 0.45), WHITE, hv), w / 2)
    elif k == "catrow":
        on = MOTION.on(e.id, 1.0 if e.on else 0.0)
        draw_rrect(e.rect, mix(fade(CHIP_BG, 0.5 + hv), CHIP_HOVER, 0.0), 10 * s, border=2 * s,
                   border_color=mix((1, 1, 1, 0.0), WHITE, on))
        ex = e.extra or {}
        _preview_or_dot(e.shown, ex.get("rgb", (0.5, 0.5, 0.5)), x + 24 * s, y + h / 2, 34 * s, s)
        tx = x + 50 * s
        room = w - 50 * s - 12 * s
        fam = ex.get("family", "")
        fw = min(measure(fam, 10 * s, mono_font(), 0.8 * s) + 8 * s, room * 0.35) if fam else 0
        text(e.text, (tx, y + h / 2, room - fw, h / 2 - 3 * s), hud.FONT * s, WHITE)
        text(ex.get("meta", ""), (tx, y + 5 * s, room, h / 2 - 5 * s), 11 * s, HELP)
        if fam:
            text(fam.upper(), (tx, y + h / 2, room, h / 2 - 3 * s), 10 * s, TEXT, "right",
                 mono_font(), 0.8 * s)
        if e.value:
            text("★", (x, y + h / 2, w - 8 * s, h / 2 - 2 * s), 11 * s, WHITE, "right")
    elif k == "matcurrent":
        draw_rrect(e.rect, fade(CHIP_BG, 0.6 + hv), 14 * s)
        ex = e.extra or {}
        _preview_or_dot(e.shown, ex.get("rgb", (0.5, 0.5, 0.5)), x + 29 * s, y + h / 2, 46 * s, s)
        tx = x + 60 * s
        room = w - 60 * s - 30 * s
        text(e.text, (tx, y + h / 2 - 1 * s, room, h / 2 - 6 * s), hud.FONT * s, WHITE)
        text(ex.get("meta", ""), (tx, y + 8 * s, room, h / 2 - 8 * s), 11 * s, TEXT)
        text("›", (x, y, w - 14 * s, h), 18 * s, mix(TEXT, WHITE, hv), "right")


def _srgb_to_display(rgb):
    """Swatch colours are sRGB; the overlay draws in display space already."""
    return (rgb[0], rgb[1], rgb[2], 1.0)


def _slider_track(e, s, hv, colours_=None):
    """Label + mono value, then the track (plain line, or 24 colour segments)."""
    x, y, w, h = e.rect
    top = (x, y + 28 * s, w, 18 * s)
    room = _split(e.text, e.shown, w, hud.FONT * s, hud.SMALL * s, mono_font())
    text(e.text, (x, y + 28 * s, room, 18 * s), hud.FONT * s, fade(WHITE, 0.45) if e.dim else WHITE)
    text(e.shown, top, hud.SMALL * s, TEXT, "right", mono_font())
    tx, ty, tw, th = e.track
    cy = ty + th / 2
    if colours_:
        n = len(colours_)
        sw = tw / n
        # Rounded end caps reach into their neighbours; flat middle segments cover that.
        draw_rrect((tx, cy - 7 * s, sw + 7 * s, 14 * s), _srgb_to_display(colours_[0]), 7 * s)
        draw_rrect((tx + (n - 1) * sw - 7 * s, cy - 7 * s, sw + 7 * s, 14 * s),
                   _srgb_to_display(colours_[-1]), 7 * s)
        for i in range(1, n - 1):
            draw_rrect((tx + i * sw, cy - 7 * s, sw + 0.5, 14 * s), _srgb_to_display(colours_[i]), 0)
    else:
        draw_rrect((tx, cy - 1 * s, tw, 2 * s), (1, 1, 1, 0.22), 1 * s)
    if e.dim:
        return
    px = tx + tw * e.value
    draw_rrect((px - 12 * s, cy - 12 * s, 24 * s, 24 * s), (0, 0, 0, 0.45), 12 * s)
    r = (7 + hv + 2 * MOTION.grab(e.id)) * s
    draw_rrect((px - r, cy - r, 2 * r, 2 * r), WHITE, r)


def _pattern(key, rect, s):
    """Gobo tile previews from rects and circles (REDESIGN.md §2 ``tiles``)."""
    x, y, w, h = rect
    light, dark = (0.9, 0.9, 0.9, 1), (0.07, 0.07, 0.07, 1)
    draw_rrect(rect, dark if key != "NONE" else FIELD_BG, 7 * s)
    if key == "WINDOW":
        pw, ph = (w - 10 * s) / 3, (h - 9 * s) / 2
        for i in range(3):
            for j in range(2):
                draw_rrect((x + 3 * s + i * (pw + 2 * s), y + 3 * s + j * (ph + 3 * s), pw, ph), light, 1 * s)
    elif key == "BLINDS":
        for j in range(5):
            draw_rrect((x + 2 * s, y + 3 * s + j * 7.5 * s, w - 4 * s, 4.5 * s), light, 1 * s)
    elif key == "LEAVES":
        for cx, cy, r in ((0.3, 0.62, 0.22), (0.7, 0.38, 0.24), (0.62, 0.8, 0.13), (0.15, 0.25, 0.1)):
            d = r * h * 1.4
            draw_rrect((x + cx * w - d / 2, y + cy * h - d / 2, d, d), light, d / 2)
    elif key == "DOTS":
        d = 5 * s
        for i in range(int(w // (10 * s))):
            for j in range(int(h // (10 * s))):
                draw_rrect((x + 3 * s + i * 10 * s, y + 3 * s + j * 10 * s, d, d), light, d / 2)
    elif key == "STRIPES":
        for i in range(-2, int(w // (12 * s)) + 2):
            x0 = x + i * 12 * s
            pts = [(max(x, x0), y), (min(x + w, x0 + 6 * s), y),
                   (min(x + w, x0 + 6 * s + h * 0.5), y + h), (max(x, x0 + h * 0.5), y + h)]
            if pts[1][0] > pts[0][0] and pts[2][0] > pts[3][0]:
                draw_tris([pts[0], pts[1], pts[2], pts[0], pts[2], pts[3]], light)
    elif key == "SPOT":
        d = h * 0.62
        draw_rrect((x + (w - d) / 2, y + (h - d) / 2, d, d), (0.9, 0.9, 0.9, 0.35), d / 2)
        d = h * 0.5
        draw_rrect((x + (w - d) / 2, y + (h - d) / 2, d, d), light, d / 2)
    elif key in {"WATER", "CRACKLE"}:
        if key == "CRACKLE":
            draw_rrect(rect, light, 7 * s)
        ink = light if key == "WATER" else dark
        # a few irregular cells, drawn as their edges
        cells = [((0.05, 0.3), (0.3, 0.12), (0.55, 0.35), (0.42, 0.7), (0.12, 0.62)),
                 ((0.55, 0.35), (0.8, 0.15), (0.97, 0.45), (0.82, 0.75), (0.42, 0.7)),
                 ((0.12, 0.62), (0.42, 0.7), (0.5, 0.97)), ((0.42, 0.7), (0.82, 0.75), (0.75, 0.97))]
        for cell in cells:
            for a_, b_ in zip(cell, cell[1:] + cell[:1]):
                _line((x + a_[0] * w, y + a_[1] * h), (x + b_[0] * w, y + b_[1] * h),
                      (2.0 if key == "WATER" else 1.6) * s, ink)
    elif key == "CHEQUER":
        n = 4
        cw, ch = w / n, h / 3
        for i in range(n):
            for j in range(3):
                if (i + j) % 2 == 0:
                    draw_rrect((x + i * cw, y + j * ch, cw, ch), light, 0)
    elif key == "RIPPLES":
        for k, r in enumerate((0.95, 0.75, 0.55, 0.35, 0.15)):
            d = h * r
            draw_rrect((x + (w - d) / 2, y + (h - d) / 2, d, d), light if k % 2 == 0 else dark, d / 2)
    elif key in {"LATTICE", "GRILLE"}:
        draw_rrect(rect, light, 7 * s)
        if key == "LATTICE":
            step = 12 * s
            for i in range(-int(h // step) - 1, int(w // step) + 2):
                x0 = x + i * step
                for x1, sign in ((x0, 1), (x0 + h, -1)):
                    a_, b_ = (x1, y), (x1 + sign * h, y + h)
                    _line(_clip(a_, b_, rect)[0], _clip(a_, b_, rect)[1], 2.0 * s, dark)
        else:
            step = 6 * s
            for i in range(1, int(w // step) + 1):
                draw_rrect((x + i * step - 0.6 * s, y, 1.2 * s, h), dark, 0)
            for j in range(1, int(h // step) + 1):
                draw_rrect((x, y + j * step - 0.6 * s, w, 1.2 * s), dark, 0)
    elif key == "BREAKUP":
        for cx, cy, r in ((0.25, 0.4, 0.45), (0.65, 0.7, 0.4), (0.8, 0.25, 0.3), (0.45, 0.85, 0.2)):
            d = r * h * 1.6
            draw_rrect((x + cx * w - d / 2, y + cy * h - d / 2, d, d), (0.9, 0.9, 0.9, 0.45), d / 2)


def _line(a, b, width, colour):
    """A straight stroke as two triangles."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    if n < 1e-6:
        return
    ox, oy = -dy / n * width / 2, dx / n * width / 2
    p = [(a[0] + ox, a[1] + oy), (b[0] + ox, b[1] + oy), (b[0] - ox, b[1] - oy), (a[0] - ox, a[1] - oy)]
    draw_tris([p[0], p[1], p[2], p[0], p[2], p[3]], colour)


def _clip(a, b, rect):
    """Segment a-b cut to the rectangle (Liang-Barsky); a degenerate point if outside."""
    x, y, w, h = rect
    t0, t1 = 0.0, 1.0
    dx, dy = b[0] - a[0], b[1] - a[1]
    for p, q in ((-dx, a[0] - x), (dx, x + w - a[0]), (-dy, a[1] - y), (dy, y + h - a[1])):
        if p == 0:
            if q < 0:
                return a, a
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
    if t0 > t1:
        return a, a
    return (a[0] + t0 * dx, a[1] + t0 * dy), (a[0] + t1 * dx, a[1] + t1 * dy)


def _switch(x, y, s, t, hv):
    """44 x 26 switch; ``t`` 0..1 animates the thumb and the accent track."""
    w, h = 44 * s, 26 * s
    draw_rrect((x, y, w, h), mix((1, 1, 1, 0.18 + 0.06 * hv), ACCENT, max(0.0, min(1.0, t))), h / 2)
    d = 22 * s
    tx = x + 2 * s + (w - d - 4 * s) * t
    draw_rrect((tx, y + 2 * s, d, d), (1, 1, 1, 1), d / 2)


def _paint_page(e, s, hv):
    """Edit pages (REDESIGN.md §2, §3)."""
    k, (x, y, w, h) = e.kind, e.rect
    if k == "gridchip":
        draw_rrect(e.rect, fade(mix(CHIP_BG, CHIP_HOVER, hv), 0.45 if e.dim else 1.0), 10 * s)
        if e.on:                                       # the look applied last (issue 7)
            d = 6 * s
            label, size = fit_text(e.text, hud.SMALL * s, w - d - 6 * s - 12 * s, measure)
            tw = measure(label, size)
            x0 = x + (w - tw - d - 6 * s) / 2
            draw_rrect((x0, y + (h - d) / 2, d, d), ACCENT, d / 2)
            text(label, (x0 + d + 6 * s, y, tw + 2, h), size, WHITE, fit=False)
        else:
            text(e.text, e.rect, hud.SMALL * s, mix(SOFT_TEXT, WHITE, hv), "center", pad=6 * s)
    elif k == "presettile":
        on = MOTION.on(e.id, 1.0 if e.on else 0.0)
        draw_rrect(e.rect, mix(CHIP_BG, CHIP_HOVER, hv), 11 * s, border=2 * s,
                   border_color=mix((1, 1, 1, 0.0), WHITE, on))
        from . import previews
        key = previews.best(e.shown)
        tex = previews.texture(key) if key else None
        if tex is not None:                     # the preset rendered on a sphere, live
            d = 46 * s
            draw_image(tex, (x + (w - d) / 2, y + h - 1 * s - d, d, d))
        else:                                   # until it's ready: its colour
            d = 18 * s
            draw_rrect((x + (w - d) / 2, y + h - 14 * s - d, d, d), (*e.extra[:3], 1.0), d / 2,
                       border=1.0 * s, border_color=(1, 1, 1, 0.22))
        text(e.text, (x, y + 5 * s, w, 14 * s), 11 * s, mix(SOFT_TEXT, WHITE, max(on, hv)), "center",
             pad=5 * s)
        if e.value:                             # a favourite (browser)
            text("★", (x, y + h - 18 * s, w - 7 * s, 14 * s), 11 * s, WHITE, "right")
    elif k == "linkhint":
        text(e.text, e.rect, hud.SMALL * s, mix(TEXT, WHITE, hv), "right")
        tw = measure(e.text, hud.SMALL * s)
        draw_rrect((x + w - tw, y + 3 * s, tw, 1 * s), fade(TEXT, 0.5 + 0.5 * hv), 0)
    elif k == "titledrop":
        if hv:
            draw_rrect(e.rect, fade(CHIP_BG, hv), 10 * s)
        text(e.text, (x + 6 * s, y + 4 * s, w - 30 * s, h - 8 * s), hud.TITLE * s, WHITE)
        cx, cy, r = x + w - 12 * s, y + h / 2, 4 * s
        draw_tris([(cx - r, cy + r / 2), (cx + r, cy + r / 2), (cx, cy - r / 2)], mix(TEXT, WHITE, hv))
    elif k == "editlink":
        draw_rrect(e.rect, fade(CHIP_BG, hv), 12 * s, border=1.0 * s, border_color=OUTLINE)
        col = fade(TEXT, 0.6) if e.dim else mix(SOFT_TEXT, WHITE, hv)
        text(e.text, (x + 16 * s, y, w - 48 * s, h), hud.FONT * s, col)
        if not e.dim:
            text("›", (x, y, w - 16 * s, h), 18 * s, col, "right")
    elif k == "back":
        draw_rrect(e.rect, mix(CHIP_BG, CHIP_HOVER, hv), w / 2)
        text("‹", (x - 1 * s, y + 1 * s, w, h), 18 * s, WHITE, "center")
    elif k == "tab":
        cov, col = _seg_colour(e, hv, SOFT_TEXT, BLACK)
        if hv and cov < 1.0:
            draw_rrect(e.rect, fade(HOVER_BG, hv * (1.0 - cov)), 9 * s)
        text(e.text, e.rect, hud.FONT * s, col, "center", pad=6 * s)
    elif k == "hline":
        draw_rule(e.rect, HAIRLINE_DIVIDER)
    elif k == "outline":
        draw_rrect(e.rect, fade(CHIP_BG, hv), h / 2, border=1.0 * s,
                   border_color=(1, 1, 1, 0.12 if e.dim else 0.3 + 0.2 * hv))
        text(e.text, e.rect, hud.SMALL * s if h < 40 * s else 14 * s,
             fade(SOFT_TEXT, 0.45) if e.dim else mix(SOFT_TEXT, WHITE, hv), "center", pad=12 * s)
    elif k == "done":
        draw_rrect(e.rect, fade(WHITE, 1.0 - 0.1 * hv), h / 2)
        text(e.text, e.rect, 14 * s, BLACK, "center", pad=12 * s)
    elif k == "strip":
        _slider_track(e, s, hv, e.extra)
    elif k == "palette":
        ring = MOTION.ring(e.id, 1.0 if e.on else 0.0)
        if ring > 0.01:
            g = 4 * s * ring
            draw_rrect((x - g, y - g, w + 2 * g, h + 2 * g), WHITE, w / 2 + g)
            draw_rrect((x - g / 2, y - g / 2, w + g, h + g), BLACK, w / 2 + g / 2)
        draw_rrect(e.rect, _srgb_to_display(e.extra), w / 2, border=1.0 * s,
                   border_color=(1, 1, 1, 0.18 + 0.3 * hv))
    elif k == "targetpill":
        on = MOTION.on(e.id, 1.0 if e.on else 0.0)
        draw_rrect(e.rect, mix(mix(CHIP_BG, CHIP_HOVER, hv), WHITE, on), h / 2)
        d = 12 * s
        label, size = fit_text(e.text, hud.SMALL * s, w - d - 8 * s - 20 * s, measure)
        tw = measure(label, size)
        x0 = x + (w - d - 8 * s - tw) / 2
        draw_rrect((x0, y + (h - d) / 2, d, d), _srgb_to_display(e.extra), d / 2, border=1.0 * s,
                   border_color=(0.5, 0.5, 0.5, 0.6))
        text(label, (x0 + d + 8 * s, y, tw + 2, h), size, mix(SOFT_TEXT, BLACK, on), fit=False)
    elif k == "hexvalue":
        text(e.text, e.rect, 13 * s, WHITE, "left", mono_font(), 0.5 * s)
    elif k == "tile":
        on = MOTION.on(e.id, 1.0 if e.on else 0.0)
        draw_rrect(e.rect, mix(CHIP_BG, CHIP_HOVER, hv), 11 * s, border=2 * s,
                   border_color=mix((1, 1, 1, 0.0), WHITE, on))
        _pattern(e.extra, (x + 5 * s, y + h - 45 * s, w - 10 * s, 40 * s), s)
        text(e.text, (x, y + 4 * s, w, 14 * s), 11 * s, mix(SOFT_TEXT, WHITE, max(on, hv)), "center",
             pad=5 * s)
    elif k == "switchrow":
        a = 0.45 if e.dim else 1.0
        # Label and help come wrapped from hud (Phase 16): lines, top to bottom.
        lines = (e.extra or {}).get("lines") or [e.text]
        helps = (e.extra or {}).get("help") or ([e.shown] if e.shown else [])
        rows = [(t, hud.FONT * s, 18 * s, SOFT_TEXT) for t in lines] + \
               [(t, hud.HELP * s, 15 * s, HELP) for t in helps]
        total = sum(r[2] for r in rows)
        ty = y + (h + total) / 2
        for t, size, lh, col in rows:
            ty -= lh
            text(t, (x, ty, w - 56 * s, lh), size, fade(col, a))
        saved = _alpha[0]
        _alpha[0] = saved * a
        try:
            _switch(x + w - 44 * s, y + (h - 26 * s) / 2, s, MOTION.switch(e.id, 1.0 if e.on else 0.0), hv)
        finally:
            _alpha[0] = saved
    elif k == "listgroup":
        draw_rrect(e.rect, CHIP_BG, 12 * s)
    elif k == "listrow":
        ex = e.extra
        if not ex.get("first"):
            draw_rule((x, y + h - 1, w, 1), HAIRLINE_DIVIDER)
        aw, ah = ex["aspect"]
        fh = 22 * s
        fw = fh * aw / ah
        draw_rrect((x + 12 * s, y + (h - fh) / 2, fw, fh), CLEAR, 3 * s, border=1.5 * s,
                   border_color=ACCENT if ex.get("active") else (1, 1, 1, 0.3))
        tx = x + 12 * s + 28 * s
        room = x + w - 12 * s - 44 * s - 8 * s - tx
        text(e.text, (tx, y + h / 2, room, h / 2 - 3 * s), hud.FONT * s, WHITE)
        text(e.shown, (tx, y + 5 * s, room, h / 2 - 5 * s), 10 * s, TEXT, "left", mono_font(), 0.8 * s)
        _switch(x + w - 12 * s - 44 * s, y + (h - 26 * s) / 2, s,
                MOTION.switch(e.id, 1.0 if e.on else 0.0), hv)
    elif k == "pathbox":
        draw_rrect(e.rect, FIELD_BG, 8 * s)          # the field's grey reads on black
        size = 12 * s
        shown = e.text
        if measure(shown, size, mono_font()) > w - 20 * s:        # keep the end: the folder's name
            while len(shown) > 1 and measure("…" + shown, size, mono_font()) > w - 20 * s:
                shown = shown[1:]
            shown = "…" + shown
        text(shown, (x + 10 * s, y, w - 20 * s, h), size, SOFT_TEXT, "left", mono_font(), fit=False)
    elif k == "note":
        lines = e.extra or [e.text]
        lh = 16 * s
        for i, line in enumerate(lines):
            text(line, (x, y + h - (i + 1) * lh, w, lh), hud.HELP * s, HELP)


def _stroke(a, b, width, color):
    import math
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy) or 1.0
    ox, oy = -dy / n * width / 2, dx / n * width / 2
    p = [(a[0] + ox, a[1] + oy), (b[0] + ox, b[1] + oy), (b[0] - ox, b[1] - oy), (a[0] - ox, a[1] - oy)]
    draw_tris([p[0], p[1], p[2], p[0], p[2], p[3]], color)


def _dashed(rect, radius, width, color, s, dash=5, gap=4):
    """A dashed rounded outline: solid outline, then gaps cut with the bar's own
    black (the bar is opaque, so this reads as a dashed line)."""
    x, y, w, h = rect
    draw_rrect(rect, CLEAR, radius, border=width, border_color=color)
    step = (dash + gap) * s
    for i in range(int((w - 2 * radius) // step) + 1):
        gx = x + radius + i * step + dash * s
        if gx + gap * s < x + w - radius:
            draw_rrect((gx, y - 1, gap * s, width + 2), GLASS, 0)
            draw_rrect((gx, y + h - width - 1, gap * s, width + 2), GLASS, 0)
    for i in range(int((h - 2 * radius) // step) + 1):
        gy = y + radius + i * step + dash * s
        if gy + gap * s < y + h - radius:
            draw_rrect((x - 1, gy, width + 2, gap * s), GLASS, 0)
            draw_rrect((x + w - width - 1, gy, width + 2, gap * s), GLASS, 0)


def _ellipsis(t, size, width, font=0, spacing=0.0):
    if measure(t, size, font, spacing) <= width:
        return t
    while t and measure(t + "…", size, font, spacing) > width:
        t = t[:-1]
    return t + "…"


def _split(left, right, w, size_l, size_r, font_r=0, spacing_r=0.0, gap=8.0):
    """Width for a left label that shares its row with a right-aligned value."""
    rw = measure(right, size_r, font_r, spacing_r) if right else 0.0
    return max(0.0, w - rw - (gap if right else 0.0))


def _paint_bar(e, s, hv):
    """Shot bar v2 and menus (REDESIGN.md §2, §4)."""
    k, (x, y, w, h) = e.kind, e.rect
    if k == "shotcard":
        ex = e.extra
        a = 0.5 if e.dim else 1.0
        if e.on:
            draw_rrect(e.rect, (1, 1, 1, 0.12), 14 * s, border=1.5 * s, border_color=WHITE)
        elif hv:
            draw_rrect(e.rect, fade((1, 1, 1, 0.07), hv), 14 * s)
        collapsed = ex.get("collapsed")
        bw, bh = 44 * s, 36 * s
        bx = x + (w - bw) / 2
        by = y + (h - bh) / 2 if collapsed else y + h - 8 * s - bh
        aw, ah = ex["aspect"]
        fw, fh = 40 * s, 34 * s
        if aw / ah > fw / fh:
            fh = fw * ah / aw
        else:
            fw = fh * aw / ah
        fx, fy = bx + (bw - fw) / 2, by + (bh - fh) / 2
        draw_rrect((fx, fy, fw, fh), fade(FIELD_BG, a), 4 * s, border=1.5 * s,
                   border_color=fade(WHITE if e.on else SOFT_TEXT, a * (1.0 if e.on else 0.6)))
        lens = ex.get("lens", 50.0)
        k_fill = 0.8 if lens >= 100 else 0.55 if lens >= 70 else 0.4      # framing hint
        d = min(fw, fh) * k_fill
        draw_rrect((fx + (fw - d) / 2, fy + (fh - d) / 2, d, d), fade((0.62, 0.63, 0.66, 1), a), d / 2)
        if not collapsed or hv:
            size = 11 * s
            ny = y + 7 * s if not collapsed else y - 18 * s
            if collapsed:                                  # name on hover, under the card
                name = _ellipsis(e.text, size, 140 * s)
                tw = measure(name, size) + 12 * s
                draw_rrect((x + (w - tw) / 2, ny - 2 * s, tw, 18 * s), GLASS, 9 * s)
                text(name, (x + (w - tw) / 2, ny, tw, 14 * s), size, fade(WHITE if e.on else SOFT_TEXT, a),
                     "center")
            else:
                text(e.text, (x + (w - 76 * s) / 2, ny, 76 * s, 14 * s), size,
                     fade(WHITE if e.on else SOFT_TEXT, a), "center")
    elif k == "tick":
        # Phase 14: included = a red dot (the accent means "on"), not a tick mark.
        run = (e.extra or {}).get("run")
        cx, cy = x + w / 2, y + h / 2
        d = (10 + 2 * hv) * s
        dot = (cx - d / 2, cy - d / 2, d, d)
        if hv:
            draw_rrect(e.rect, fade(CHIP_HOVER, hv), w / 2)
        if run == "current":                         # rendering now: a ring around the dot
            draw_rrect(e.rect, CLEAR, w / 2, border=1.5 * s, border_color=ACCENT)
            draw_rrect(dot, ACCENT, d / 2)
        elif run == "done":                          # saved: dot with a white ring
            draw_rrect(dot, ACCENT, d / 2, border=2 * s, border_color=WHITE)
        elif e.on:
            draw_rrect(dot, ACCENT, d / 2)
        else:                                        # left out: an empty ring
            draw_rrect(dot, CLEAR, d / 2, border=1.5 * s, border_color=(1, 1, 1, 0.4 + 0.4 * hv))
    elif k == "dots":
        draw_rrect(e.rect, fade(CHIP_HOVER, 0.6 + 0.4 * hv), w / 2)
        d = 2.6 * s
        for i in (-1, 0, 1):
            draw_rrect((x + w / 2 + i * 4.2 * s - d / 2, y + h / 2 - d / 2, d, d), WHITE, d / 2)
    elif k == "vline":
        draw_rule(e.rect, HAIRLINE_DIVIDER)
    elif k == "segcell":
        cov, col = _seg_colour(e, hv, SOFT_TEXT, BLACK)
        if hv and cov < 1.0:
            draw_rrect(e.rect, fade(HOVER_BG, hv * (1.0 - cov)), 7 * s)
        text(e.text, e.rect, hud.SMALL * s, col if not e.dim else fade(col, 0.45), "center", pad=6 * s)
    elif k == "progress":
        draw_rrect(e.rect, CHIP_BG, h / 2)
        f = MOTION.fill(e.id, e.value)
        fill_w = max(h, w * f) if f > 0 else 0
        if fill_w:
            draw_rrect((x, y, fill_w, h), WHITE, h / 2)
        # Inverted label: each glyph black over the fill, white over the rest.
        font, size = mono_font(), hud.MONO * s
        split = x + fill_w
        text(e.text, e.rect, size, WHITE, "center", font, 0.6 * s,
             color_at=lambda gx: BLACK if gx < split else WHITE, pad=12 * s)
    elif k == "stopbtn":
        draw_rrect(e.rect, mix(CHIP_BG, CHIP_HOVER, hv), h / 2)
        c, r = (x + w / 2, y + h / 2), 5 * s
        _stroke((c[0] - r, c[1] - r), (c[0] + r, c[1] + r), 1.6 * s, WHITE)
        _stroke((c[0] - r, c[1] + r), (c[0] + r, c[1] - r), 1.6 * s, WHITE)
    elif k == "donebtn":
        draw_rrect(e.rect, fade(CHIP_BG, hv), h / 2, border=1.0 * s, border_color=(1, 1, 1, 0.5))
        text(e.text, e.rect, 14 * s, WHITE, "center", pad=14 * s)
    elif k == "shootbtn":
        draw_rrect(e.rect, fade(WHITE, 0.3) if e.dim else fade(WHITE, 1.0 - 0.1 * hv), h / 2)
        d = 8 * s
        dot = 0 if e.dim else d + 9 * s
        label, size = fit_text(e.text, 14 * s, w - dot - 28 * s, measure)
        t_w = measure(label, size)
        x0 = x + (w - t_w - dot) / 2
        if not e.dim:
            draw_rrect((x0, y + (h - d) / 2, d, d), ACCENT, d / 2)
            x0 += dot
        text(label, (x0, y, t_w + 2, h), size, TEXT if e.dim else BLACK, fit=False)
    elif k == "pager":
        draw_rrect(e.rect, fade(CHIP_BG, 0.0 if e.dim else 1.0 + hv), 14 * s)
        text(e.text, e.rect, 20 * s, fade(WHITE, 0.3 if e.dim else 1.0), "center")
    elif k == "menu":
        draw_rrect(e.rect, GLASS, 14 * s)
    elif k == "menuitem":
        if hv:
            draw_rrect(e.rect, fade(CHIP_HOVER, hv), 9 * s)
        col = DANGER if (e.extra or {}).get("danger") else WHITE
        col = fade(col, 0.35) if e.dim else col
        room = _split(e.text, e.shown, w - 20 * s, hud.FONT * s, hud.MONO * s, mono_font())
        text(e.text, (x + 10 * s, y, room, h), hud.FONT * s, col)
        if e.shown:
            text(e.shown, (x + 10 * s, y, w - 20 * s, h), hud.MONO * s, fade(TEXT, 0.35 if e.dim else 1.0),
                 "right", mono_font())
CARET_BLINK = 0.53


def _paint_textfield(e, s):
    """REDESIGN.md §5: field, selection highlight, text and a blinking caret."""
    model = e.extra
    x, y, w, h = e.rect
    draw_rrect(e.rect, FIELD_BG, 7 * s, border=1.0 * s, border_color=WHITE)
    size = hud.FONT * s
    pad = 8 * s
    tx = x + pad
    sel = model.selection
    if sel is not None:
        a = measure(model.text[:sel[0]], size)
        b = measure(model.text[:sel[1]], size)
        draw_rrect((tx + a, y + 4 * s, max(1.0, b - a), h - 8 * s), (1, 1, 1, 0.28), 2 * s)
    text(model.text, (tx, y, w - 2 * pad, h), size, WHITE, fit=False)
    since = uistate.focus.get("since", 0.0)
    if sel is None and int((time.monotonic() - since) / CARET_BLINK) % 2 == 0:
        cx = tx + measure(model.text[:model.caret], size)
        draw_rrect((round(cx), y + 6 * s, max(1.0, 1 * s), h - 12 * s), WHITE, 0)


def _paint_more_kinds(e, s, hv):
    k, (x, y, w, h) = e.kind, e.rect
    if k == "add":                       # + chip under the lights (design: dashed circle)
        draw_rrect(e.rect, fade(CHIP_HOVER, hv), h / 2, border=1.0 * s,
                   border_color=(1, 1, 1, 0.22))
        text("+", e.rect, 18 * s, mix(TEXT, WHITE, hv), "center")
    elif k == "addshot":                 # dashed "+ From view" card at the end of the bar
        if hv:
            draw_rrect(e.rect, fade(CHIP_BG, hv), 14 * s)
        _dashed(e.rect, 14 * s, 1.5 * s, mix((1, 1, 1, 0.25), (1, 1, 1, 0.5), hv), s)
        text("+", (x, y + h * 0.45, w, h * 0.4), 20 * s, mix(SOFT_TEXT, WHITE, hv), "center")
        text(e.text, (x, y + h * 0.16, w, h * 0.3), 11 * s, mix(TEXT, WHITE, hv), "center")
    elif k == "sun":                     # 0.9.1: the clear sky's sun (a disc with rays)
        cx, cy = x + w / 2, y + h / 2
        lit = max(hv, 1.0 if e.on else 0.0)
        col = mix((1.0, 0.78, 0.25, 1.0), (1.0, 0.88, 0.45, 1.0), lit)
        halo = 20 * s                                  # dark halo: reads on a white backdrop too
        draw_rrect((cx - halo, cy - halo, 2 * halo, 2 * halo), (0.0, 0.0, 0.0, 0.35 + 0.15 * lit), halo)
        r = 7.5 * s
        draw_rrect((cx - r, cy - r, 2 * r, 2 * r), col, r, border=1.5 * s, border_color=(1, 1, 1, 0.9))
        for i_ in range(8):
            a_ = i_ * math.pi / 4
            ux, uy = math.cos(a_), math.sin(a_)
            draw_rrect((cx + ux * 13 * s - 1.7 * s, cy + uy * 13 * s - 1.7 * s, 3.4 * s, 3.4 * s), col, 1.7 * s)
        if e.on:
            d = 3 * s
            draw_rrect((cx - d / 2, cy - d / 2, d, d), ACCENT, d / 2)
        text(e.text, (cx + 24 * s, cy - 7 * s, 120 * s, 14 * s), hud.MONO * s,
             mix(TEXT, WHITE, lit), "left", mono_font(), 1.2 * s)
    elif k == "target":                  # crosshair icon (design: the Highlight marker)
        cx, cy = x + w / 2, y + h / 2
        lit = max(hv, 1.0 if e.on else 0.0)
        col = mix(fade(WHITE, 0.75), WHITE, lit)
        r = 6 * s
        draw_rrect((cx - r, cy - r, 2 * r, 2 * r), CLEAR, r, border=1.4 * s, border_color=col)
        for dx, dy, ww, hh in ((-13, -0.6, 7, 1.2), (6, -0.6, 7, 1.2), (-0.6, -13, 1.2, 7), (-0.6, 6, 1.2, 7)):
            draw_rrect((cx + dx * s, cy + dy * s, ww * s, hh * s), col, 0)
        d = 3 * s
        draw_rrect((cx - d / 2, cy - d / 2, d, d), ACCENT if e.on else col, d / 2)
        text(e.text, (cx + 16 * s, cy - 7 * s, 120 * s, 14 * s), hud.MONO * s,
             mix(TEXT, WHITE, lit), "left", mono_font(), 1.2 * s)


def _glyph(key, cx, cy, s, color):
    """Light type and shape tiles: what each light looks like."""
    if key in {"SOFTBOX", "AREA"}:
        draw_rrect((cx - 11 * s, cy - 8 * s, 22 * s, 16 * s), color, 3 * s)
    elif key == "STRIP":
        draw_rrect((cx - 3 * s, cy - 11 * s, 6 * s, 22 * s), color, 3 * s)
    elif key == "OVAL":
        draw_rrect((cx - 5 * s, cy - 11 * s, 10 * s, 22 * s), color, 5 * s)
    elif key == "SPOT":
        draw_tris([(cx - 9 * s, cy - 9 * s), (cx + 9 * s, cy - 9 * s), (cx, cy + 9 * s)], color)
    elif key == "POINT":
        draw_rrect((cx - 5 * s, cy - 5 * s, 10 * s, 10 * s), color, 5 * s)
        draw_rrect((cx - 10 * s, cy - 10 * s, 20 * s, 20 * s), fade(color, 0.0), 10 * s,
                   border=1.5 * s, border_color=fade(color, 0.5))
    elif key == "SUN":
        draw_rrect((cx - 5 * s, cy - 5 * s, 10 * s, 10 * s), color, 5 * s)
        for i in range(8):
            a = i * math.pi / 4
            c, n = math.cos(a), math.sin(a)
            _line((cx + c * 8 * s, cy + n * 8 * s), (cx + c * 12 * s, cy + n * 12 * s), 2 * s, color)
    else:   # RING: the disc
        draw_rrect((cx - 7 * s, cy - 7 * s, 14 * s, 14 * s), fade(color, 0.0), 7 * s,
                   border=3 * s, border_color=color)


# --- Compare swipe (Phase 5) ---------------------------------------------------
# A Cycles render of the scene camera, laid over the live Eevee viewport inside
# the camera frame. Left of the divider is Cycles, right is the live view.

def compare_image(context):
    """The Cycles render to compare against, if it matches the scene camera."""
    s, cam = context.scene.plinth, context.scene.camera
    img = bpy.data.images.get(parity.COMPARE_IMAGE)
    if not s.compare or cam is None or img is None or img.get("plinth_camera") != cam.name:
        return None
    return img


def camera_frame_rect(context):
    """The camera frame in region pixels (x, y, w, h), in camera view only."""
    from bpy_extras import view3d_utils
    rv3d, region, scene = context.region_data, context.region, context.scene
    if rv3d is None or rv3d.view_perspective != "CAMERA" or scene.camera is None:
        return None
    cam = scene.camera
    pts = [view3d_utils.location_3d_to_region_2d(region, rv3d, cam.matrix_world @ v)
           for v in cam.data.view_frame(scene=scene)]
    if any(p is None for p in pts):
        return None
    xs, ys = [p.x for p in pts], [p.y for p in pts]
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def compare_layout(context):
    """(frame, divider_x) when the compare swipe should show, else None."""
    if compare_image(context) is None:
        return None
    frame = camera_frame_rect(context)
    if frame is None:
        return None
    cycles_rect, _u = parity.swipe_split(frame, context.scene.plinth.compare_split)
    return frame, cycles_rect[0] + cycles_rect[2]


def _draw_compare(context, scale):
    import gpu
    from gpu_extras.batch import batch_for_shader
    lay = compare_layout(context)
    if lay is None:
        return
    frame, divider = lay
    (x, y, w, h), u = parity.swipe_split(frame, context.scene.plinth.compare_split)
    if w > 0:
        tex = gpu.texture.from_image(compare_image(context))
        shader = gpu.shader.from_builtin("IMAGE")
        batch = batch_for_shader(shader, "TRIS", {
            "pos": [(x, y), (x + w, y), (x + w, y + h), (x, y), (x + w, y + h), (x, y + h)],
            "texCoord": [(0, 0), (u, 0), (u, 1), (0, 0), (u, 1), (0, 1)]})
        gpu.state.blend_set("ALPHA")
        shader.bind()
        shader.uniform_sampler("image", tex)
        batch.draw(shader)
    fx, fy, fw, fh = frame
    draw_rrect((divider - 1 * scale, fy, 2 * scale, fh), WHITE, 1 * scale)
    knob = 22 * scale
    draw_rrect((divider - knob / 2, fy + fh / 2 - knob / 2, knob, knob), WHITE, knob / 2)
    ly, lh = fy + fh - 30 * scale, 20 * scale
    text("CYCLES", (divider - 90 * scale, ly, 80 * scale, lh), hud.MONO * scale, WHITE,
         "right", mono_font(), 1.2 * scale)
    text("EEVEE", (divider + 10 * scale, ly, 80 * scale, lh), hud.MONO * scale, WHITE,
         "left", mono_font(), 1.2 * scale)


# --- Draw handler --------------------------------------------------------------------

def reduce_motion(context):
    """Plinth preference: tweens become instant (only short fades remain)."""
    addon = context.preferences.addons.get(__package__)
    return bool(addon is not None and getattr(addon.preferences, "reduce_motion", False))


def hovered(context):
    """Id of the element under the mouse, if Blender says our gizmo is highlighted."""
    key = context.region.as_pointer()
    grp = _groups.get(key)
    if grp is None:
        return None
    try:
        if not any(gz.is_highlight for gz in grp.gizmos):
            return None
    except ReferenceError:
        _groups.pop(key, None)
        return None
    return _hover.get(key)


def draw_callback():
    t0 = time.perf_counter()
    context = bpy.context
    if not is_visible(context):
        return
    import gpu
    s = ui_scale(context)
    gpu.state.blend_set("ALPHA")
    _draw_compare(context, s)
    hover = hovered(context)
    els = elements(context)
    MOTION.a.reduce_motion = reduce_motion(context)
    MOTION.update(els, hover, _dragging.get("id"))
    menu = next((e for e in els if e.kind == "menu"), None)
    if menu is not None:
        _menu_anchor[0] = (menu.extra or {}).get("anchor", (menu.rect[0], menu.rect[1]))
    paint_all(els, s, hover)
    gpu.state.blend_set("NONE")
    if MOTION.active and not PUMP.running:
        PUMP.kick()
    _record((time.perf_counter() - t0) * 1000.0)


# --- Input ------------------------------------------------------------------------------

_groups = {}            # region pointer -> gizmo group, so the painter can read hover
_dragging = {}          # {"id": slider being dragged}: its knob grows
_hover = {}             # region pointer -> id of the element under the mouse


class PLINTH_GT_hit(bpy.types.Gizmo):
    """Invisible hit area covering every floating control; drawing is draw_callback's."""
    bl_idname = "PLINTH_GT_hit"

    def draw(self, context):
        pass

    def test_select(self, context, location):
        el = hud.hit(elements(context), *location)
        key = context.region.as_pointer()
        new = el.id if el is not None else None
        if _hover.get(key) != new:
            _hover[key] = new
            context.area.tag_redraw()
            PUMP.kick()                              # hover fades need a few frames
        return -1 if el is None else 0


class PLINTH_GGT_overlay(bpy.types.GizmoGroup):
    bl_idname = "PLINTH_GGT_overlay"
    bl_label = "Plinth floating controls"
    bl_space_type = "VIEW_3D"
    bl_region_type = "WINDOW"
    bl_options = {"PERSISTENT"}          # no "3D" flag: a 2D, pixel-space group

    @classmethod
    def poll(cls, context):
        return is_visible(context)

    def setup(self, context):
        gz = self.gizmos.new(PLINTH_GT_hit.bl_idname)
        gz.target_set_operator("plinth.overlay_click")

    def draw_prepare(self, context):
        _groups[context.region.as_pointer()] = self


def _after_click(context):
    from . import props, transform
    transform.sync(context)                     # the gizmo follows the light / shot picked
    props.tag_viewports(context)


class PLINTH_OT_overlay_click(bpy.types.Operator):
    """Click or drag a floating control"""
    bl_idname = "plinth.overlay_click"
    bl_label = "Plinth Control"
    bl_options = {"INTERNAL", "UNDO"}

    def invoke(self, context, event):
        x, y = event.mouse_region_x, event.mouse_region_y
        el = hud.hit(elements(context), x, y)
        if el is None:
            return {"CANCELLED"}
        if el.edit is not None and (_double_click(el.id) or (el.act is None and el.drag is None
                                                             and el.kind != "valuehit")):
            start_edit(context, el)
            return {"FINISHED"}
        if el.act is not None or el.drag is not None or el.drag_xy is not None:
            MOTION.press(el.id)
            PUMP.kick()
        if el.drag_xy is not None:              # click it, or drag it across surfaces
            self.drag, self.el, self.press, self.moved = None, el, (x, y), False
            context.window_manager.modal_handler_add(self)
            return {"RUNNING_MODAL"}
        if el.drag is not None:
            self.drag, self.track, self.start, self.el = el.drag, el.track, el.value, None
            self.offset = hud.grab_offset(el, x, ui_scale(context))
            _dragging["id"] = el.id
            self._apply(context, x)
            context.window_manager.modal_handler_add(self)
            return {"RUNNING_MODAL"}
        if el.act is None:
            return {"CANCELLED"}                # a card or dock: swallow the click
        try:
            el.act(context)
        except RuntimeError as exc:             # an operator refused (e.g. nothing selected)
            self.report({"WARNING"}, str(exc).strip().splitlines()[-1])
            return {"CANCELLED"}
        _after_click(context)
        return {"FINISHED"}

    def _apply(self, context, x):
        self.drag(context, hud.drag_fraction(hud.El("", "", self.track, track=self.track), x,
                                             getattr(self, "offset", 0.0)))
        context.area.tag_redraw()

    def modal(self, context, event):
        x, y = event.mouse_region_x, event.mouse_region_y
        if self.el is not None:                 # target icon
            if event.type == "MOUSEMOVE":
                if self.moved or abs(x - self.press[0]) + abs(y - self.press[1]) > 4:
                    self.moved = True
                    self.el.drag_xy(context, x, y)
                    context.area.tag_redraw()
            elif event.type == "LEFTMOUSE" and event.value == "RELEASE":
                if not self.moved and self.el.act is not None:
                    self.el.act(context)
                    _after_click(context)
                context.area.tag_redraw()
                return {"FINISHED"}
            elif event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
                return {"CANCELLED"}
            return {"RUNNING_MODAL"}
        if event.type == "MOUSEMOVE":
            self._apply(context, x)
        elif event.type == "LEFTMOUSE" and event.value == "RELEASE":
            _dragging.clear()
            PUMP.kick()
            return {"FINISHED"}
        elif event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self.drag(context, self.start)
            _dragging.clear()
            context.area.tag_redraw()
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}


class PLINTH_OT_menu(bpy.types.Operator):
    """An open floating menu: closes on Esc or a click outside, and runs its
    items' shortcuts (F2, Shift D, Delete). Runs only while a menu is open"""
    bl_idname = "plinth.menu"
    bl_label = "Plinth Menu"
    bl_options = {"INTERNAL"}

    def invoke(self, context, event):
        if not uistate.menu:
            return {"CANCELLED"}
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if not uistate.menu or uistate.focus:
            return {"FINISHED", "PASS_THROUGH"}
        if event.value != "PRESS":
            return {"PASS_THROUGH"}
        els = elements(context) if context.region is not None else []
        if event.type == "ESC":
            uistate.close_menu()
            context.area.tag_redraw()
            return {"FINISHED"}
        if event.type in {"LEFTMOUSE", "RIGHTMOUSE"}:
            x, y = event.mouse_region_x, event.mouse_region_y
            box = next((e.rect for e in els if e.kind == "menu"), None)
            if box is not None and hud.contains(box, x, y):
                return {"PASS_THROUGH"}                 # the gizmo clicks the item
            uistate.close_menu()
            context.area.tag_redraw()
            other = hud.hit(els, x, y)
            # A click on another floating control still works; on the scene it only closes.
            return {"FINISHED", "PASS_THROUGH"} if other is not None and event.type == "LEFTMOUSE" \
                else {"FINISHED"}
        key = event.type if event.type != "DEL" else "BACK_SPACE"
        for e in els:
            if e.kind == "menuitem" and e.act is not None and (e.extra or {}).get("key") == (key, event.shift):
                e.act(context)
                from . import props
                props.tag_viewports(context)
                return {"FINISHED"}
        return {"RUNNING_MODAL"} if event.type in {"F2", "D", "BACK_SPACE", "X"} else {"PASS_THROUGH"}


class PLINTH_OT_page(bpy.types.Operator):
    """An open edit page: Esc goes back to the front card. Runs only while a
    page is open; every other event passes straight through"""
    bl_idname = "plinth.page"
    bl_label = "Plinth Edit Page"
    bl_options = {"INTERNAL"}

    def invoke(self, context, event):
        if not uistate.page:
            return {"CANCELLED"}
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if not uistate.page:
            return {"FINISHED", "PASS_THROUGH"}
        if event.type == "ESC" and event.value == "PRESS" and not uistate.menu and not uistate.focus:
            uistate.close_page()
            context.area.tag_redraw()
            return {"FINISHED"}
        return {"PASS_THROUGH"}


class PLINTH_OT_browser(bpy.types.Operator):
    """The open material browser: the wheel scrolls it, right-click opens a
    material's menu, typing searches, Esc closes it. Runs only while it's open;
    every other event passes through"""
    bl_idname = "plinth.browser"
    bl_label = "Plinth Material Browser"
    bl_options = {"INTERNAL"}

    def invoke(self, context, event):
        from . import browser
        if not browser.is_open():
            return {"CANCELLED"}
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        from . import browser
        if not browser.is_open() or context.scene.plinth.step != "MATERIAL":
            uistate.close_browser()
            return {"FINISHED", "PASS_THROUGH"}
        if context.area is None or context.region is None or uistate.focus:
            return {"PASS_THROUGH"}
        x, y = event.mouse_region_x, event.mouse_region_y
        els = elements(context)
        sheet = next((e for e in els if e.id == "sheet"), None)
        inside = sheet is not None and hud.contains(sheet.rect, x, y)
        if event.type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"} and inside and not uistate.menu:
            row = uistate.browser_state()["row"] + (1 if event.type == "WHEELDOWNMOUSE" else -1)
            uistate.browser["row"] = max(0, row)
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}
        if event.value != "PRESS":
            return {"PASS_THROUGH"}
        if event.type == "ESC" and not uistate.menu:
            uistate.close_browser()
            context.area.tag_redraw()
            return {"FINISHED"}
        if event.type == "RIGHTMOUSE" and inside:
            el = hud.hit(els, x, y)
            if el is not None and el.id.startswith("item_"):
                hud.open_menu(context, "catalog", el.id[5:], el.id)
                context.area.tag_redraw()
            return {"RUNNING_MODAL"}
        if inside and not uistate.menu and event.unicode and event.unicode.isprintable() \
                and not (event.ctrl or event.oskey or event.alt):
            field = next((e for e in els if e.id == "browser_search"), None)
            if field is not None:                    # type anywhere on the sheet to search
                start_edit(context, field, width=0)
                uistate.focus["model"].insert(event.unicode)
                context.area.tag_redraw()
                return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}


DOUBLE_CLICK = 0.4


def _double_click(el_id):
    """True on the second click on the same element within 0.4 s. (The hit
    gizmo starts on press, so Blender's own DOUBLE_CLICK events don't reach it.)"""
    now = time.monotonic()
    last = uistate.clicks
    double = last.get("id") == el_id and now - last.get("time", -1.0) < DOUBLE_CLICK
    uistate.clicks.clear()
    if not double:
        uistate.clicks.update(id=el_id, time=now)
    return double


def start_edit(context, el, **options):
    """Focus the overlay text field on ``el`` (its ``edit`` = (text, commit))."""
    current, commit = el.edit
    uistate.focus.clear()
    uistate.focus.update(id=el.id, model=textedit.TextModel(current, select_all=True),
                         commit=commit, since=time.monotonic(),
                         align="right" if el.kind == "valuehit" else "left",
                         width=options.get("width", 96 if el.kind == "valuehit" else 72))
    uistate.close_menu()
    PUMP.hold("caret")
    bpy.ops.plinth.text_field("INVOKE_DEFAULT")


def end_edit(context, commit):
    """Leave the field; commit (Enter, click outside) or cancel (Esc)."""
    f = dict(uistate.focus)
    uistate.focus.clear()
    PUMP.release("caret")
    ok = None
    if commit and f.get("commit") is not None:
        try:
            ok = f["commit"](context, f["model"].text)
        except Exception as exc:                       # never leave the field stuck
            print("Plinth: couldn't apply %r: %s" % (f["model"].text, exc))
            ok = False
    from . import props
    props.tag_viewports(context)
    return ok


def _field_rect(context):
    el = next((e for e in elements(context) if e.kind == "textfield"), None)
    return el.rect if el is not None else None


_PASS = ("MOUSEMOVE", "INBETWEEN", "TIMER", "WHEEL", "TRACKPAD", "NDOF", "WINDOW", "NONE",
         "MOUSESMARTZOOM", "MOUSEROTATE", "MOUSEPAN", "MOUSEZOOM")


class PLINTH_OT_text_field(bpy.types.Operator):
    """Typing into the floating UI. Runs only while a field has focus: it takes
    key events so Blender shortcuts don't fire (no stray G/R/S while naming)"""
    bl_idname = "plinth.text_field"
    bl_label = "Plinth Text Field"
    bl_options = {"INTERNAL", "UNDO"}

    def invoke(self, context, event):
        if not uistate.focus:
            return {"CANCELLED"}
        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if not uistate.focus:                          # focus moved away (file load, reload)
            PUMP.release("caret")
            return {"CANCELLED"}
        model = uistate.focus["model"]
        if event.type in {"LEFTMOUSE", "RIGHTMOUSE", "MIDDLEMOUSE"} and event.value == "PRESS":
            rect = _field_rect(context)
            x, y = event.mouse_region_x, event.mouse_region_y
            if rect is not None and hud.contains(rect, x, y) and event.type == "LEFTMOUSE":
                model.move(len(model.text))
                context.area.tag_redraw()
                return {"RUNNING_MODAL"}
            end_edit(context, commit=True)             # click outside commits
            return {"FINISHED", "PASS_THROUGH"}
        if event.value != "PRESS" or event.type.startswith(_PASS):
            return {"PASS_THROUGH"}                    # mouse moves, wheel, timers, releases
        result = textedit.handle_key(model, event.type, event.unicode,
                                     ctrl=event.ctrl or event.oskey, shift=event.shift,
                                     clipboard=context.window_manager.clipboard)
        if event.type == "C" and (event.ctrl or event.oskey) and model.selection:
            a, b = model.selection
            context.window_manager.clipboard = model.text[a:b]
        uistate.focus["since"] = time.monotonic()      # caret stays solid while typing
        context.area.tag_redraw()
        if result == "commit":
            end_edit(context, commit=True)
            return {"FINISHED"}
        if result == "cancel":
            end_edit(context, commit=False)
            return {"CANCELLED"}
        if result == "native":
            # An input method is composing (CJK): Blender's own field handles it.
            commit = uistate.focus.get("commit")
            current = model.text
            end_edit(context, commit=False)
            _native["commit"] = commit
            bpy.ops.plinth.text_native("INVOKE_DEFAULT", value=current)
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}


_native = {}


class PLINTH_OT_text_native(bpy.types.Operator):
    """Type the value in Blender's own text field (input methods, IME)"""
    bl_idname = "plinth.text_native"
    bl_label = "Type a value"
    bl_options = {"INTERNAL", "UNDO"}

    value: bpy.props.StringProperty(name="Value")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        commit = _native.pop("commit", None)
        if commit is not None:
            commit(context, self.value)
        from . import props
        props.tag_viewports(context)
        return {"FINISHED"}


BAKE_LABELS = {"NONE": "lighting not baked", "BAKED": "lighting baked",
               "STALE": "lighting changed since the bake", "RUNNING": "baking lighting…"}


class PLINTH_MT_engine(bpy.types.Menu):
    """The Engine sheet: presets by what they're for, plus the hybrid switches."""
    bl_idname = "PLINTH_MT_engine"
    bl_label = "How should it render?"

    def draw(self, context):
        s = context.scene.plinth
        layout = self.layout
        layout.row().prop(s, "engine", expand=True)
        layout.separator()
        layout.props_enum(s, "eevee_preset" if s.engine == "EEVEE" else "cycles_preset")
        layout.separator()
        layout.prop(s, "hybrid")
        layout.prop(s, "auto_preset")
        layout.operator("plinth.compare", icon="SPLIT_VERTICAL")
        from . import bake
        layout.separator()
        layout.label(text="Eevee: " + BAKE_LABELS[bake.lighting_state(context.scene, cached=True)])
        row = layout.row(align=True)
        row.operator_context = "EXEC_DEFAULT"
        row.operator("plinth.bake_lighting", icon="LIGHTPROBE_VOLUME")
        row.operator("plinth.clear_lighting", text="Clear", icon="X")
        obj = context.active_object
        if obj is not None and obj.type == "MESH":
            layout.label(text="Selected part: " + ("baked to textures" if bake.is_baked(obj)
                                                    else "procedural"))
            row = layout.row(align=True)
            for k in bake.SIZES:
                row.operator("plinth.bake_material", text="Bake " + k).size = k
            layout.operator("plinth.unbake_material", icon="LOOP_BACK")


_handle = None
classes = (PLINTH_GT_hit, PLINTH_GGT_overlay, PLINTH_OT_overlay_click, PLINTH_OT_text_field,
           PLINTH_OT_text_native, PLINTH_OT_menu, PLINTH_OT_page, PLINTH_OT_browser, PLINTH_MT_engine)


def register():
    global _handle
    for c in classes:
        bpy.utils.register_class(c)
    _handle = bpy.types.SpaceView3D.draw_handler_add(draw_callback, (), "WINDOW", "POST_PIXEL")


def unregister():
    global _handle, _shader, _flat
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
        _handle = None
    _groups.clear()
    _hover.clear()
    _dragging.clear()
    uistate.reset()
    _native.clear()
    PUMP.shutdown()
    MOTION.__init__(MOTION.a)
    MOTION.a.clear()
    _measure_cache.clear()
    _shader, _flat = None, False
    if _mono.get("path"):
        import blf
        try:
            blf.unload(_mono["path"])
        except Exception:
            pass
    _mono.clear()
    _mono["id"] = None
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
