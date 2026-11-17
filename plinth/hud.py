# SPDX-License-Identifier: GPL-3.0-or-later
"""The floating viewport UI as data: a list of elements (rect, look, action).

overlay.py draws these and routes clicks to them; everything here is plain
Python on the scene and a ``Geo`` (region size, scale, insets, text measure),
so the whole UI can be built and clicked in headless tests.

Layout follows the design canvas (docs/DESIGN.md): step pill top-centre,
engine switch top-right, light chips left (Light step), inspector card right,
shot dock + Shoot bottom-centre, status bottom-right. Groups are placed in
priority order and a group that would overlap Blender's UI or an earlier
group is left out, so a small viewport shows less rather than a pile-up.

Rects are (x, y, w, h) in region pixels, origin bottom-left (Blender's).
"""

import math
from collections import namedtuple

import bpy

from . import engine, hdri, library, materials, props, studio, textedit, uistate

# --- Design tokens (px at UI scale 1) -------------------------------------------
STEP_HEIGHT, STEP_PAD_X, PILL_PAD, GAP, MARGIN_TOP = 36, 16, 4, 2, 12
EDGE = 24                     # distance from the viewport's visible edge
CARD_W, CARD_PAD, CARD_GAP = 300, 20, 18
FONT, SMALL, TITLE = 13, 12, 20
MONO, HELP = 11, 11           # mono tags/status/labels; help notes (REDESIGN.md §1)
NAV_W, NAV_H = 96, 250        # Blender's navigation gizmo, top-right

STEP_KEYS = [k for k, _l, _d in props.STEPS]
STEP_LABELS = [l for _k, l, _d in props.STEPS]

PillLayout = namedtuple("PillLayout", "container steps hits")
SUN_MARGIN = 40          # px: the sun icon is pinned this far inside the view when off it
TOP_BARS = 90            # px: the step pill and Orbit/Move bars along the top of the view

Geo = namedtuple("Geo", "w h scale left right top nav measure frame lights view targets decals cameras gizmo sun")
Geo.__new__.__defaults__ = (0, 0, 0, 0, None, None, None, None, None, None, None, None, None)


class El:
    """One thing on screen. ``act(context)`` runs on click; ``drag(context, f)``
    runs while dragging with f = 0..1 across ``track``; ``drag_xy(context, x, y)``
    gets the mouse position (target icons), and a press without movement is a
    click. Kinds with none of these still block clicks if ``block`` (cards,
    docks), else let them through.

    ``group`` names a segmented control (its "on" cell gets one sliding
    indicator, motion.py); ``layer`` is "card" for card contents (page flip)
    and "menu" for an open menu. ``edit`` opens the overlay text field on a
    double-click: (current text, commit(context, text))."""
    __slots__ = ("id", "kind", "rect", "text", "on", "act", "drag", "track", "value",
                 "shown", "extra", "hit", "block", "dim", "drag_xy", "group", "layer", "edit")

    def __init__(self, id, kind, rect, text="", on=False, act=None, drag=None, track=None,
                 value=0.0, shown="", extra=None, hit=None, block=None, dim=False, drag_xy=None,
                 group=None, layer=None, edit=None):
        self.id, self.kind, self.rect, self.text, self.on = id, kind, rect, text, on
        self.act, self.drag, self.track, self.value, self.shown = act, drag, track, value, shown
        self.extra, self.hit, self.dim, self.drag_xy = extra, hit or rect, dim, drag_xy
        self.group, self.layer, self.edit = group, layer, edit
        self.block = block if block is not None else (
            act is not None or drag is not None or drag_xy is not None)

    def __repr__(self):
        return "El(%s, %s)" % (self.id, self.kind)


def contains(rect, x, y):
    rx, ry, rw, rh = rect
    return rx <= x < rx + rw and ry <= y < ry + rh


def overlaps(a, b, eps=1e-3):
    """Rects share area (edges that merely touch, to a float's error, don't)."""
    return (a[0] < b[0] + b[2] - eps and b[0] < a[0] + a[2] - eps
            and a[1] < b[1] + b[3] - eps and b[1] < a[1] + a[3] - eps)


def bbox(els):
    xs = [e.rect[0] for e in els] + [e.rect[0] + e.rect[2] for e in els]
    ys = [e.rect[1] for e in els] + [e.rect[1] + e.rect[3] for e in els]
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def hit(els, x, y):
    """Topmost element under (x, y) that takes the click, or None."""
    for e in reversed(els):
        if e.block and contains(e.hit, x, y):
            return e
    return None


# --- Step pill (kept as plain functions; the spike's tests use them) -----------

def pill_layout(region_w, region_h, labels, measure, scale=1.0,
                inset_left=0, inset_right=0, inset_top=0):
    """Place the pill top-centre in the visible part of the region.

    ``measure(text)`` returns the text width in pixels at this scale. Returns
    None when the pill doesn't fit. ``hits`` widen each step to cover gaps and
    padding, so no click falls through between steps."""
    s = scale
    widths = [measure(t) + 2 * STEP_PAD_X * s for t in labels]
    pad, gap, step_h = PILL_PAD * s, GAP * s, STEP_HEIGHT * s
    w = sum(widths) + gap * (len(labels) - 1) + 2 * pad
    h = step_h + 2 * pad
    free_w = region_w - inset_left - inset_right
    top = region_h - inset_top - MARGIN_TOP * s
    if w + 2 * MARGIN_TOP * s > free_w or top - h < 0:
        return None
    x = round(inset_left + (free_w - w) / 2)
    y = round(top - h)
    container = (x, y, round(w), round(h))
    steps, hits = [], []
    cx = x + pad
    for i, sw in enumerate(widths):
        steps.append((round(cx), round(y + pad), round(sw), round(step_h)))
        left = x if i == 0 else cx - gap / 2
        right = x + w if i == len(widths) - 1 else cx + sw + gap / 2
        hits.append((left, y, right - left, h))
        cx += sw + gap
    return PillLayout(container, steps, hits)


def hit_test(layout, x, y):
    """Index of the step under region pixel (x, y), or None."""
    if layout is None:
        return None
    for i, r in enumerate(layout.hits):
        if contains(r, x, y):
            return i
    return None


def overlap_insets(area, region, use_overlap=True):
    """Pixels hidden by overlapping toolbar/sidebar/header: (left, right, top)."""
    left = right = top = 0
    if not use_overlap:
        return left, right, top
    rx0, ry0 = region.x, region.y
    rx1, ry1 = rx0 + region.width, ry0 + region.height
    for r in area.regions:
        if r.type not in {"UI", "TOOLS", "HEADER", "TOOL_HEADER"} or r.width <= 1 or r.height <= 1:
            continue
        x0, y0, x1, y1 = r.x, r.y, r.x + r.width, r.y + r.height
        if x1 <= rx0 or x0 >= rx1 or y1 <= ry0 or y0 >= ry1:
            continue
        if r.type in {"HEADER", "TOOL_HEADER"}:
            if (y0 + y1) / 2 > (ry0 + ry1) / 2:
                top = max(top, ry1 - y0)
        elif (x0 + x1) / 2 > (rx0 + rx1) / 2:
            right = max(right, rx1 - x0)
        else:
            left = max(left, x1 - rx0)
    return left, right, top


# --- Actions --------------------------------------------------------------------

def _op(idname, *args, **kwargs):
    """Run an operator if it can run here; return its result or None."""
    mod, name = idname.split(".")
    op = getattr(getattr(bpy.ops, mod), name)
    if not op.poll(*args[:1]):
        return None
    return op(*args, **kwargs)


def _set(data, attr, value):
    return lambda context: setattr(data, attr, value)


def _lerp(lo, hi, f):
    return lo + (hi - lo) * max(0.0, min(1.0, f))


def _frac(lo, hi, v):
    return 0.0 if hi == lo else max(0.0, min(1.0, (v - lo) / (hi - lo)))


# --- Inspector card contents ------------------------------------------------------
# Each step gives (title, tag, rows). Rows:
#   ("slider", id, label, frac, shown, setter(context, f)[, num])
#       num = (lo, hi, "lin" | "log"): the number shown maps to f that way, so
#       double-clicking the value can type it (overlay text field)
#   ("shapes", [(key, label, on, act)][, heading, id prefix])     # glyph tiles
#   ("chips", heading, [(id, label, on, act)])
#   ("scale", heading, hint, [(id, small, label, on, act)])       # Shoot quality
#   ("primary", id, label, act)
#   ("buttons", id, heading, hint, [(id, label, act)])            # outline buttons in a row
#   ("more", label)

def _rig_scale(scene):
    tgt = bpy.data.objects.get(studio.TARGET)
    return float(tgt.get("plinth_scale", 1.0)) if tgt else 1.0


def card_studio(scene, products):
    s = scene.plinth
    rows = []
    bd = bpy.data.objects.get(studio.BACKDROP)
    if bd is None:
        return onboarding(scene, products)
    r = 0.5 * _rig_scale(scene)
    mod = bd.modifiers.get(studio.COVE_GN)
    idents = {k: i for k, _l, i in studio.backdrop_inputs(bd)}
    form = studio.current_form(bd)                # 0.9: the studio's shape, every form in a menu
    from . import scenes
    place = scenes.current(scene)                 # 0.10: or a whole place (moon, forest, a photo)
    rows.append(("dropdown", "scene", "Scene", scenes.label(place),
                 lambda c: open_menu(c, "scenes", c.scene.name, "scene")))
    if place:
        t = scenes.turn(scene)
        rows.append(("slider", "scene_turn", "Turn", t / 360.0, "%d°" % round(t),
                     lambda c, f: scenes.set_turn(c.scene, _lerp(0.0, 360.0, f)), (0, 360, "lin")))
        if scenes.has_detail(place):              # 0.11: how dense the place is (photos have none)
            rows.append(("dropdown", "scene_detail", "Detail", scenes.detail_label(scenes.detail(scene)),
                         lambda c: open_menu(c, "detail", c.scene.name, "scene_detail")))
    else:
        rows.append(("dropdown", "form", "Shape", studio.FORMS[form][0],
                     lambda c: open_menu(c, "forms", c.scene.name, "form")))
    walls = form != "FLOOR" and not place
    if mod is not None and {"curve", "height"} <= set(idents) and walls:
        curve = studio._get_input(mod, idents["curve"])
        height = studio._get_input(mod, idents["height"])
        rows.append(("slider", "curve", "Curve", _frac(0.5 * r, 6 * r, curve),
                     "%.2f m" % curve,
                     lambda c, f: studio.set_backdrop_shape(bd, curve=_lerp(0.5 * r, 6 * r, f)),
                     (0.5 * r, 6 * r, "lin")))
        rows.append(("slider", "wall", "Wall height", _frac(4 * r, 24 * r, height),
                     "%.2f m" % height,
                     lambda c, f: studio.set_backdrop_shape(bd, height=_lerp(4 * r, 24 * r, f)),
                     (4 * r, 24 * r, "lin")))
    bsdf = library._backdrop_bsdf() if not place else None
    if bsdf is not None:
        sock = bsdf.inputs["Base Color"]

        def set_tone(c, f, sock=sock):
            sock.default_value = (*tone_colour(tuple(sock.default_value), f, "tone"), sock.default_value[3])

        rows.append(("slider", "tone", "Backdrop", max(sock.default_value[:3]),
                     "%d%%" % round(100 * max(sock.default_value[:3])), set_tone, (0, 100, "lin")))
    # Issue 7: an even grid, with a dot on the look applied last.
    every = library.looks()                   # 0.9: a few on the card, every look one click away
    chips = [("look_" + k, every[k]["label"], k == s.last_look,
              (lambda c, k=k: _op("plinth.apply_studio", preset=k))) for k in library.card_looks(s.last_look)]
    if len(every) > len(chips):
        chips.append(("looks_all", "All looks…", False,
                      lambda c: open_menu(c, "looks", c.scene.name, "looks_all")))
    rows.append(("grid", "Look", chips))
    rows.append(("more", "Edit studio"))
    return "Studio", (scenes.label(place) if place else studio.FORMS[form][0]).upper(), rows


def tone_colour(rgb, f, key):
    """The colour ``rgb`` at brightness ``f`` (0..1), keeping its tint. Black
    has no tint of its own: the one it had before it was dragged to 0 is
    remembered (uistate.hues), else white, so the slider always comes back up."""
    peak = max(rgb[:3])
    memo = uistate.hues.get(key)
    if peak > 1e-4:
        tint = [ch / peak for ch in rgb[:3]]
    elif memo is not None and uistate._same(memo[2], rgb):
        tint = list(memo[0])
    else:
        tint = [1.0, 1.0, 1.0]
    v = _lerp(0.0, 1.0, f)
    out = tuple(t * v for t in tint)
    uistate.hues[key] = (tuple(tint), 0.0, out)
    return out


def onboarding(scene, products):
    """First run (issue 'Onboarding'): what to do, in two plain steps."""
    if not products:
        return "Start here", "STEP 1 OF 2", [
            ("note", "Click the object you want to photograph. Plinth builds a studio "
                     "around it, sized to it."),
            ("primary", "build", "Select your product", None),
            ("more", "Edit studio")]
    return "Start here", "STEP 2 OF 2", [
        ("note", "Adds a curved backdrop, three lights and four shots, all sized to %s."
         % _short(products[0].name, 20)),
        ("primary", "build", "Build studio", lambda c: _op("plinth.build_studio")),
        ("more", "Edit studio")]


def card_light(scene, products):
    s = scene.plinth
    if s.active_light == hdri.CHIP:
        return card_hdri(scene)
    light = studio.active_light(scene)
    if light is None:
        if bpy.data.objects.get(studio.BACKDROP) is None:
            return "Light", "NO STUDIO", [("primary", "build", "Build studio first", None),
                                          ("more", "Edit light")]
        return card_no_lights(scene)
    data = studio.fresh(light.data)
    base_power, base_size = studio.light_base(light)
    sc = _rig_scale(scene)
    p_max = 4 * base_power * sc * sc
    s_lo, s_hi = 0.05 * sc, 3 * base_size * sc
    kelvin = studio.rgb_to_kelvin(tuple(data.color))
    shape = studio.light_shape(data)
    k = studio.power_factor(data)                # the light's own unit per area watt
    size = studio.light_size(data)
    rows = [
        ("slider", "intensity", "Intensity", _frac(0, p_max, studio.light_power(data)), studio.power_label(data),
         lambda c, f: studio.set_light_power(studio.fresh(data), _lerp(0, p_max, f)), (0, p_max * k, "lin")),
        ("slider", "softness", "Softness", _frac(s_lo, s_hi, size),
         "%d%%" % round(100 * _frac(s_lo, s_hi, size)),
         lambda c, f: studio.set_light_size(data, _lerp(s_lo, s_hi, f)), (0, 100, "lin")),
        ("slider", "warmth", "Warmth", _frac(10000, 2000, kelvin),
         studio.light_gel(data) or "%d K" % kelvin,
         lambda c, f: studio.set_light_colour(data, studio.kelvin_to_rgb(_lerp(10000, 2000, f))),
         (10000, 2000, "lin")),
        type_row(data),
    ]
    if data.type == "AREA":
        rows.append(("shapes", [(k, studio.LIGHT_SHAPES[k]["label"], k == shape,
                                 (lambda c, k=k: studio.set_light_shape(data, k)))
                                for k in studio.AREA_SHAPES]))
    elif data.type == "SPOT":
        rows.append(beam_row(data))
    rows.append(aim_row(scene, light))
    rigs = library.rigs()
    if rigs:                                      # light rigs from the content library
        rows.append(("grid", "Rig", [("rig_" + k, v["label"], k == s.last_look,
                                      (lambda c, k=k: _op("plinth.apply_studio", preset=k)))
                                     for k, v in rigs.items()]))
    rows += [("more", "Edit light"),
             ("header", {"menu": ("light", light.name)})]       # issue 10: Duplicate/Remove in ⋯
    return display(studio.light_label(light)), light_tag(data), rows


def light_tag(data):
    """'AREA · SOFTBOX', 'SPOT': the card and page tag."""
    data = studio.fresh(data)
    if data.type == "AREA":
        return "AREA · " + studio.LIGHT_SHAPES[studio.light_shape(data)]["label"].upper()
    return studio.LIGHT_TYPES[data.type]["label"].upper()


def type_row(data):
    """Blender's light types as tiles; Area comes back in the shape it had."""
    data = studio.fresh(data)
    return ("shapes", [(t, v["label"], t == data.type,
                        (lambda c, t=t: studio.set_light_shape(
                            data, studio.area_shape(data) if t == "AREA" else t)))
                       for t, v in studio.LIGHT_TYPES.items()], "Type", "type_")


def beam_row(data):
    """A spot's cone: Beam angle."""
    lo, hi = studio.SPOT_RANGE
    deg = math.degrees(data.spot_size)
    return ("slider", "beam", "Beam", _frac(lo, hi, data.spot_size), "%d°" % round(deg),
            lambda c, f: setattr(studio.fresh(data), "spot_size", _lerp(lo, hi, f)),
            (math.degrees(lo), math.degrees(hi), "lin"))


def card_no_lights(scene):
    """Every studio light was removed (deleted in the outliner, or one by one):
    the studio is still there, so offer a light, or the look's lights back."""
    look = library.all_presets().get(scene.plinth.last_look, {}).get("label")
    return "Light", "NO LIGHTS", [
        ("note", "Every light has been removed. The HDRI still lights the product if one is on."),
        ("shapes", [(k, v["label"], False, (lambda c, k=k: _add_light(c, "SOFTBOX" if k == "AREA" else k)))
                    for k, v in studio.LIGHT_TYPES.items()], "Add", "type_"),
        ("primary", "add_light", "Add a light", lambda c: _add_light(c)),
        ("buttons", "restore_lights", "", "",
         [("restore_rig", ("Bring back %s lights" % look) if look and len(look) <= 14 else "Bring back the lights",
           lambda c: _restore_lights(c))]),
        ("more", "Edit light")]


def _restore_lights(context):
    scene = context.scene
    lights = studio.restore_lights(scene, scene.plinth.last_look)
    if lights:
        scene.plinth.active_light = "Key" if "Key" in lights else studio.light_label(next(iter(lights.values())))


def card_hdri(scene):
    """The HDRI chip's card: brightness, rotation and the image."""
    st = hdri.settings(scene)
    rows = hdri_sliders(st)
    rows.append(("grid", "Image", hdri_items(st)))
    rows.append(("more", "Pick an image to edit", "Edit HDRI"))
    return "HDRI", st["label"].upper(), rows


def hdri_sliders(st):
    on = st["on"]
    sun = st.get("sky")
    if sun is not None:          # 0.9.1: the clear sky's brightness is in its own (physical) units
        lo, hi = hdri.SKY_STRENGTH / 10.0, hdri.SKY_STRENGTH * 10.0
        f = _log_frac(lo, hi, max(lo, st["strength"]))
        bright = ("slider", "hdri_strength", "Brightness", f, "%.2f×" % (st["strength"] / hdri.SKY_STRENGTH),
                  lambda c, f: hdri.set_hdri(c.scene, strength=_exp(lo, hi, f)), (lo, hi, "log"))
        return [bright,
                ("slider", "sun_height", "Sun height", sun["elevation"] / 90.0, "%d°" % round(sun["elevation"]),
                 lambda c, f: hdri.set_sun(c.scene, elevation=_lerp(0.0, 90.0, f)), (0, 90, "lin")),
                ("slider", "hdri_rotation", "Sun direction", st["rotation"] / 360.0, "%d°" % round(st["rotation"]),
                 lambda c, f: hdri.set_hdri(c.scene, rotation=_lerp(0.0, 360.0, f)), (0, 360, "lin"))]
    return [
        ("slider", "hdri_strength", "Brightness", _log_frac(0.1, 10.0, max(0.1, st["strength"])),
         "%.2f×" % st["strength"],
         (lambda c, f: hdri.set_hdri(c.scene, strength=_exp(0.1, 10.0, f))) if on else None,
         (0.1, 10.0, "log") if on else None),
        ("slider", "hdri_rotation", "Rotation", st["rotation"] / 360.0, "%d°" % round(st["rotation"]),
         (lambda c, f: hdri.set_hdri(c.scene, rotation=_lerp(0.0, 360.0, f))) if on else None,
         (0, 360, "lin") if on else None),
    ]


def hdri_items(st, own=False):
    """Image chips: Off, Blender's HDRIs, and (on the page) your own file."""
    items = [("hdri_off", "Off", not st["on"], lambda c: _op("plinth.set_hdri", image="")),
             ("hdri_" + hdri.SKY, hdri.SKY_LABEL, st["on"] and st["key"] == hdri.SKY,
              lambda c: _op("plinth.set_hdri", image=hdri.SKY))]
    items += [("hdri_" + k, hdri.label(k), st["on"] and st["key"] == k,
               (lambda c, k=k: _op("plinth.set_hdri", image=k))) for k in hdri.images()]
    if own:
        items.append(("hdri_own", "Your own…", st["on"] and st["key"] == "CUSTOM",
                      lambda c: bpy.ops.plinth.load_hdri("INVOKE_DEFAULT")))
    return items


def display(label):
    """'Light4' -> 'Light 4' for chips and titles."""
    import re
    return re.sub(r"(\D)(\d)", r"\1 \2", label)


def aim_row(scene, obj):
    """'Aim at' chips: every target, plus + to spawn one and aim at it."""
    current = studio.aim_target(scene, obj)
    tracked = obj.constraints.get("Plinth_Track") is not None
    chips = [("aim_" + t.name, studio.target_label(t), tracked and t == current,
              (lambda c, t=t: _aim(c, obj, t)))
             for t in studio.list_targets(scene)]
    chips.append(("aim_new", "+ Target", False, lambda c: _aim(c, obj, studio.add_target(c.scene))))
    return ("chips", "Aim at", chips)


def _aim(context, obj, target):
    studio.aim_at(obj, target)
    context.scene.plinth.active_target = target.name
    if obj.type == "CAMERA":
        studio.sync_focus(context.scene, obj)


def _select_light(context, light):
    context.scene.plinth.active_light = studio.light_label(light)


def _remove_light(context, light):
    studio.remove_light(context.scene, light)
    rest = studio.list_lights(context.scene)
    context.scene.plinth.active_light = studio.light_label(rest[0]) if rest else ""


def _add_light(context, shape="SOFTBOX"):
    tgt = bpy.data.objects.get(context.scene.plinth.active_target) or studio.get_target(context.scene)
    _select_light(context, studio.add_light(context.scene, shape=shape, target=tgt))


def card_material(scene, products):
    s = scene.plinth
    if not products:
        return "Material", "NOTHING SELECTED", [
            ("primary", "select", "Select the product", None), ("more", "Edit material")]
    obj = products[0]
    from . import bake, decals
    owner, slot, rec = decals.owner(s.active_decal) if s.active_decal else (None, None, None)
    if rec is not None and owner == obj:
        return card_decal(obj, slot, rec, s)
    if bake.is_baked(obj):
        return card_baked(obj)
    rows = []
    parts = part_row(obj)
    if parts:
        rows.append(parts)
    current = materials.current_preset(obj)
    mat = materials.part_material(obj)
    here = tuple(materials.get_input(obj, materials.TYPES[mat["plinth_type"]]["colour"])
                 if mat is not None and mat.get("plinth_type") in materials.TYPES
                 and materials.TYPES[mat["plinth_type"]]["colour"] else (0.5, 0.5, 0.5, 1))
    from . import previews
    original = ((lambda c: _op("plinth.set_finish", finish="ORIGINAL"))
                if current or obj.get(materials.ORIGINALS, {}).get(str(obj.active_material_index)) else None)
    if picker() == "COMPACT":
        rows += compact_rows(s, obj, current, here, original)
    else:                                         # 0.9: what it wears, recent picks, Browse
        rows.append(current_row(obj, mat, current, here))
        recent = recent_tiles(obj, here)
        if recent:
            rows.append(("presets", "Recent", recent, ("preset_ORIGINAL", "Original", original)))
    if mat is not None and mat.get("plinth_type") == "IMAGE":
        image = materials.image_of(mat)
        rows.append(("button", "choose_image", ("Image: " + _short(image.name, 22)) if image else "Choose image…",
                     lambda c: _op("plinth.load_image", "INVOKE_DEFAULT", purpose="MATERIAL")))
    if mat is not None and mat.get("plinth_type") == "TEXTURES":
        from . import textures
        maps = materials.texture_maps(mat)
        rows.append(("button", "texture_maps", ("Maps: " + _short(textures.summary(maps), 24)) if maps
                     else "Find its textures…", lambda c: _open_textures(c)))
    if mat is not None and mat.get("plinth_type") in materials.TYPES:
        from . import catalog
        inputs = materials.type_inputs(mat["plinth_type"])
        for name in catalog.pinned(mat["plinth_type"]):          # Material ⋯ › Card sliders
            _n, kind, _d, lo, hi, _sec = inputs[name]
            value = materials.get_input(obj, name)
            to_value = _exp if kind == "log" else _lerp
            to_frac = _log_frac if kind == "log" else _frac
            rows.append(("slider", "mat_" + _slug(name), name.replace(" (cm)", ""),
                         to_frac(lo, hi, value), ("%.1f cm" % value) if kind == "log" else "%.2f" % value,
                         (lambda c, f, name=name, lo=lo, hi=hi, obj=obj, to_value=to_value:
                          materials.set_input(obj, name, to_value(lo, hi, f))),
                         (lo, hi, "log" if kind == "log" else "lin")))
        tag = materials.PRESETS.get(current, {}).get("label", "CUSTOM").upper()
    else:
        from . import matlib
        tag = "SAVED" if matlib.current_entry(obj) else "ORIGINAL"
    rows.append(("buttons", "matactions", None, "",
                 [("browse", "Browse materials…", open_browser),
                  ("lib_save", "Save", (lambda c: _op("plinth.save_material"))
                   if obj.type == "MESH" and obj.material_slots and obj.active_material else None)]))
    if obj.type == "MESH":
        rows.append(decal_row(obj))
    from . import pages
    editable = (mat is not None and mat.get("plinth_type") in materials.TYPES) or \
        bool(pages.own_controls(pages._slot_material(obj))[1])
    rows.append(("more", "Edit material" if editable else "Pick a preset to edit"))
    header = {"menu": ("material", obj.name)} if obj.type == "MESH" else {}
    if len(obj.material_slots) > 1:
        header["title_menu"] = ("parts", obj.name)
    if header:
        rows.append(("header", header))
    return _short(materials.part_label(obj, obj.active_material_index)
                  if obj.material_slots else obj.name, 16), tag, rows


def _open_textures(context):
    """The Material page on its Textures tab (the maps, one chip each)."""
    uistate.tabs["MATERIAL:TEXTURES"] = "Textures"
    open_page(context, "MATERIAL")


def card_baked(obj):
    """A part whose material is baked to textures: fast, not editable."""
    from . import bake
    mat = obj.active_material
    src = bake.baked_source(mat)
    size = mat.get("plinth_baked_size", "")
    preset = materials.PRESETS.get(src.get("plinth_preset", "") if src else "", {}).get("label")
    rows = [("note", "%s, baked to %s textures: quick in Eevee. Go back to procedural to "
                     "change it." % (preset or "This material", size or "image")),
            ("button", "unbake", "Back to procedural", lambda c: _op("plinth.unbake_material")),
            ("header", {"menu": ("material", obj.name)})]
    if len(obj.material_slots) > 1:
        rows[-1][1]["title_menu"] = ("parts", obj.name)
    return _short(materials.part_label(obj, obj.active_material_index), 16), "BAKED", rows


def card_decal(obj, slot, rec, s):
    """A decal or livery layer: its image, size and turn (decals), opacity, gloss."""
    from . import decals
    rows = [("button", "decal_image", "Image: " + _short(rec.get("image") or "none", 22),
             lambda c: _op("plinth.load_image", "INVOKE_DEFAULT", purpose="REPLACE"))]
    if rec["mode"] == "PROJECT":
        lo, hi = decals.SIZE_RANGE
        size = rec.get("size", decals.DEFAULTS["size"])
        rows.append(("slider", "decal_size", "Size", _log_frac(lo, hi, size), "%.0f cm" % size,
                     lambda c, f: decals.update(obj, rec["id"], slot, size=_exp(lo, hi, f)), (lo, hi, "log")))
        turn = rec.get("rotation", 0.0)
        rows.append(("slider", "decal_rotation", "Turn", _frac(-180, 180, turn), "%d°" % round(turn),
                     lambda c, f: decals.update(obj, rec["id"], slot, rotation=_lerp(-180, 180, f)),
                     (-180, 180, "lin")))
    opacity = rec.get("opacity", 1.0)
    rows.append(("slider", "decal_opacity", "Opacity", opacity, "%d%%" % round(opacity * 100),
                 lambda c, f: decals.update(obj, rec["id"], slot, opacity=f), (0, 1, "lin")))
    gloss = rec.get("gloss", 0.7)
    rows.append(("slider", "decal_gloss", "Gloss", gloss, "%.2f" % gloss,
                 lambda c, f: decals.update(obj, rec["id"], slot, gloss=f), (0, 1, "lin")))
    if rec["mode"] == "PROJECT":                         # 0.9.1: over the touching panels
        span = rec.get("span", True)
        rows.append(("switch", "decal_span", "Across panels", "", span,
                     lambda c: decals.update(obj, rec["id"], slot, span=not span)))
    raised = rec.get("raised", 0.0)                      # 0.9: embossed (sidewall lettering)
    rows.append(("slider", "decal_raised", "Raised", raised, "%d%%" % round(raised * 100),
                 lambda c, f: decals.update(obj, rec["id"], slot, raised=f), (0, 1, "lin")))
    rows.append(("button", "decal_remove", "Remove", lambda c: _op("plinth.remove_decal", decal=rec["id"])))
    rows.append(("button", "decal_done", "Done", _set(s, "active_decal", "")))
    rows.append(("header", {"menu": ("material", obj.name)}))
    tag = "DECAL" if rec["mode"] == "PROJECT" else "LIVERY"
    return _short(decals.label(rec), 16), tag, rows


PICKER = None          # tests and tools: force "BROWSER" or "COMPACT"


def picker():
    """Preferences › Material picker: "BROWSER" (default) or "COMPACT"."""
    if PICKER:
        return PICKER
    try:
        return bpy.context.preferences.addons[__package__].preferences.material_picker
    except (KeyError, AttributeError):
        return "BROWSER"


def open_browser(context, section=None):
    """Open the material browser on the part's family (or ``section``)."""
    from . import catalog
    obj = context.active_object if context is not None else None
    if section is None and obj is not None and obj.type == "MESH" and obj.material_slots:
        cid = catalog.entry_for_part(obj)
        if cid and cid.startswith("preset:"):
            section = catalog.family_of(materials.PRESETS[cid[7:]])
        elif cid:
            section = "MINE"
    uistate.open_browser(section)
    try:
        bpy.ops.plinth.browser("INVOKE_DEFAULT")      # wheel, right-click, Esc
    except (RuntimeError, AttributeError):
        pass                                          # headless: no window to listen in


def current_row(obj, mat, current, here):
    """What the part wears now: its preview, name and family; click to browse."""
    from . import catalog, matlib, previews
    cid = catalog.entry_for_part(obj) if obj.material_slots else None
    if cid and cid.startswith("mine:") and cid[5:] in matlib.entries():
        entry = matlib.entries()[cid[5:]]
        label, meta = entry["label"], "My library" + ((" · " + entry["folder"]) if entry.get("folder") else "")
        rgb, preview = matlib.swatch(entry), previews.wanted_entry(entry)
    elif current in materials.PRESETS:
        p = materials.PRESETS[current]
        label = p["label"]
        meta = catalog.FAMILY_LABELS.get(catalog.family_of(p), "")
        rgb, preview = preset_swatch(p, here), previews.wanted(obj, current)
    else:
        own = obj.active_material if obj.material_slots else None
        label = own.name if own is not None else "No material"
        meta = "Its own material" if own is not None else "Pick one to start"
        rgb, preview = (here[:3] if own is None else tuple(own.diffuse_color[:3])), ""
    linked = materials.linked_parts(_scene_meshes(), obj) if mat is not None else []
    if len(linked) > 1:
        meta += " · linked ×%d" % len(linked)
    return ("matcurrent", label, meta, rgb, preview, open_browser)


def recent_tiles(obj, here):
    """Up to three recently used materials (not the one it wears)."""
    from . import catalog, matlib, previews
    state = catalog.load_state()
    now = catalog.entry_for_part(obj) if obj.material_slots else None
    lib = matlib.entries()
    tiles = []
    for cid in state.get("recent", []):
        kind, _s, key = cid.partition(":")
        if cid == now:
            continue
        if kind == "preset" and key in materials.PRESETS:
            p = materials.PRESETS[key]
            tiles.append(("recent_" + key, p["label"], preset_swatch(p, here), False,
                          (lambda c, cid=cid: _op("plinth.apply_catalog", entry=cid, scope="PART")),
                          previews.wanted(obj, key)))
        elif kind == "mine" and key in lib:
            tiles.append(("recent_mine_" + key, lib[key]["label"], matlib.swatch(lib[key]), False,
                          (lambda c, cid=cid: _op("plinth.apply_catalog", entry=cid, scope="PART")),
                          previews.wanted_entry(lib[key])))
        if len(tiles) == 3:
            break
    return tiles


COMPACT_TILES = 9      # Compact picker: tiles on the card (the rest: More… in the browser)


def compact_rows(s, obj, current, here, original):
    """Preferences › Material picker › Compact: families as chips and the
    family's materials as tiles, right on the card."""
    from . import catalog, previews
    entries = catalog.all_entries()
    counts = catalog.counts(entries)
    fam = s.material_family or "PAINT"
    label = "Saved" if fam == "SAVED" else catalog.FAMILY_LABELS.get(fam, fam)
    rows = [("dropdown", "family", "Type", label,
             lambda c: open_menu(c, "families", obj.name, "family"))]
    if fam == "SAVED":
        return rows + saved_rows(obj, original)
    found = [e for e in catalog.section(entries, fam) if e["kind"] == "preset"]
    # 0.9: the card holds COMPACT_TILES; the part's own kind (its type) first,
    # the material it wears always, and "More…" opens the family in the browser.
    from . import content
    kind = materials.PRESETS.get(current, {}).get("type")
    found.sort(key=lambda e: (e["key"] != current, "preset_" + e["key"] not in content.NEW_IDS,
                              e["type"] != kind))
    more = len(found) > COMPACT_TILES
    found = found[:COMPACT_TILES - 1] if more else found
    tiles = [("preset_" + e["key"], e["label"], preset_swatch(materials.PRESETS[e["key"]], here),
              e["key"] == current, (lambda c, k=e["key"]: _op("plinth.set_finish", finish=k)),
              previews.wanted(obj, e["key"]))
             for e in sorted(found, key=lambda e: list(materials.PRESETS).index(e["key"])
                             if e["key"] in materials.PRESETS else 0)]
    if more:
        tiles.append(("preset_MORE", "More… (%d)" % counts.get(fam, 0), (0.18, 0.18, 0.19), False,
                      (lambda c, fam=fam: open_browser(c, fam)), ""))
    rows.append(("presets", "Preset", tiles, ("preset_ORIGINAL", "Original", original)))
    return rows


def _families_menu_items(scene, els, m):
    """Compact picker: every family with its count, then Saved."""
    from . import catalog, matlib
    s = scene.plinth
    counts = catalog.counts(catalog.all_entries())
    items = [("cat_" + k, label, str(counts[k]), _set(s, "material_family", k), False, None)
             for k, label, _d in catalog.FAMILIES if counts.get(k)]
    items.append(("cat_SAVED", "Saved", str(len(matlib.entries()) or ""), _set(s, "material_family", "SAVED"),
                  False, None))
    return items


def _forms_menu_items(scene, els, m):
    """Studio › Shape ▾: every studio form, the current one marked."""
    bd = bpy.data.objects.get(studio.BACKDROP)
    now = studio.current_form(bd)
    return [("form_" + k, label, "●" if k == now else "",
             (lambda c, k=k: _op("plinth.set_form", form=k)), False, None)
            for k, (label, *_rest) in studio.FORMS.items()]


def _scenes_menu_items(scene, els, m):
    """Studio › Scene ▾: the studio, then every scene by kind (a photo scene
    whose pack files are missing shows ↓)."""
    from . import scenes
    now = scenes.current(scene)
    items = [("scene_STUDIO", "Studio", "●" if not now else "", (lambda c: _op("plinth.set_scene", place="")),
              False, None)]
    every = scenes.available()
    for kind, _title in scenes.KINDS:
        for k, v in every.items():
            if v["kind"] != kind:
                continue
            mark = "●" if k == now else ("↓" if v.get("ready") is False else "")
            items.append(("scene_" + k, _short(v["label"], 20), mark,
                          (lambda c, k=k: _op("plinth.set_scene", place=k)), False, None))
    return items


def _detail_menu_items(scene, els, m):
    """Studio › Detail ▾: the four levels, the one in use marked."""
    from . import scenes
    now = scenes.detail(scene)
    return [("detail_" + k, lab, "●" if k == now else "", (lambda c, k=k: _op("plinth.scene_detail", level=k)),
             False, None) for k, lab, _d in scenes.DETAILS]


def _looks_menu_items(scene, els, m):
    """Studio › All looks…: every look (built in, downloaded, saved), the one
    in use marked."""
    from . import library
    last = scene.plinth.last_look
    return [("look_" + k, _short(v.get("label", k), 20), "●" if k == last else "",
             (lambda c, k=k: _op("plinth.apply_studio", preset=k)), False, None)
            for k, v in {**library.looks(), **library.list_user()}.items()]


def _scene_meshes():
    scene = bpy.context.scene
    col = bpy.data.collections.get(studio.COLLECTION)
    own = set(col.all_objects) if col else set()
    return [o for o in scene.objects if o.type == "MESH" and o not in own] if scene else []


PART_CHIPS = 5         # parts shown as chips; more go behind "All parts"


def part_row(obj):
    """Several materials on the mesh: a Part row to pick which one to change
    (the parts the faces really show)."""
    used = sorted(materials.used_slots(obj))
    if len(used) < 2:
        return None
    chips = [("partchip_%d" % i, _short(materials.part_label(obj, i), 14), i == obj.active_material_index,
              (lambda c, i=i: setattr(obj, "active_material_index", i))) for i in used[:PART_CHIPS]]
    if len(used) > PART_CHIPS:
        chips.append(("partchip_all", "All %d ▾" % len(used), obj.active_material_index in used[PART_CHIPS:],
                      lambda c: open_menu(c, "parts", obj.name, "partchip_all")))
    return ("chips", "Part", chips)


def saved_rows(obj, original):
    """Material › Saved: your library as tiles (double-click renames), Save this
    part, Remove."""
    from . import matlib, previews
    lib = matlib.entries()
    mine = matlib.current_entry(obj)
    tiles = []
    for entry_id, entry in lib.items():
        tiles.append(("lib_" + entry_id, entry["label"], matlib.swatch(entry), entry_id == mine,
                      (lambda c, e=entry_id: _op("plinth.use_saved_material", entry=e)),
                      previews.wanted_entry(entry),
                      (entry["label"], (lambda c, text, e=entry_id: matlib.rename(e, text)))))
    rows = []
    if tiles:
        rows.append(("presets", "Saved", tiles, ("preset_ORIGINAL", "Original", original)))
    else:
        rows.append(("note", "Nothing saved yet. Save a part's material here to use it on any "
                             "part, in any file."))
    rows.append(("buttons", "library", None, "",
                 [("lib_save", "Save this part", lambda c: _op("plinth.save_material")),
                  ("lib_remove", "Remove", (lambda c: _op("plinth.remove_saved_material", entry=mine))
                   if mine in lib else None)]))
    return rows


def decal_row(obj):
    """Decals and liveries go on any part, whatever its finish: two buttons."""
    from . import decals
    n = len(decals.layers(obj))
    return ("buttons", "decals", "Decals", ("%d on this part" % n) if n else "",
            [("add_decal", "Add decal…", lambda c: _op("plinth.load_image", "INVOKE_DEFAULT", purpose="DECAL")),
             ("add_livery", "Add livery…", lambda c: _op("plinth.load_image", "INVOKE_DEFAULT",
                                                          purpose="LIVERY"))])


def _material_menu_items(scene, els, m):
    from . import bake, decals
    obj = bpy.data.objects.get(m.get("target", ""))
    if obj is None or obj.type != "MESH":
        return []
    from . import matlib
    items = [("save_material", "Save to my materials", "", lambda c: _op("plinth.save_material"),
              False, None)]
    saved = matlib.current_entry(obj)
    if saved and saved in matlib.entries():
        items.append(("update_saved", "Update \u201c%s\u201d" % _short(matlib.entries()[saved]["label"], 14),
                      "", lambda c: _op("plinth.update_saved_material", entry=saved), False, None))
    like = materials.parts_sharing(_scene_meshes(), obj) if obj.material_slots else []
    linked = materials.linked_parts(_scene_meshes(), obj) if obj.material_slots else []
    if len(linked) > 1:
        items.append(("unlink", "Unlink this part", "", lambda c: _op("plinth.link_parts", unlink=True),
                      False, None))
    elif len(like) > 1:
        items.append(("link", "Link parts like this (%d)" % len(like), "",
                      lambda c: _op("plinth.link_parts"), False, None))
    mat = materials.part_material(obj)
    if mat is not None and mat.get("plinth_type") in materials.TYPES:
        items.append(("pins", "Card sliders…", "", lambda c, t=mat["plinth_type"]: open_menu(
            c, "pins", t, "card_more"), False, None))
    items += [
             ("find_textures", "Find its textures", "", lambda c: _op("plinth.find_textures"), False, None),
             ("texture_folder", "Textures from a folder…", "",
              lambda c: _op("plinth.texture_folder", "INVOKE_DEFAULT"), False, None),
             ("hide_part", "Hide this part", "H", lambda c, o=obj: _hide(c, o), False, None),
             ("add_decal", "Add decal…", "", lambda c: _op("plinth.load_image", "INVOKE_DEFAULT",
                                                            purpose="DECAL"), False, None),
             ("add_livery", "Add livery (UV)…", "", lambda c: _op("plinth.load_image", "INVOKE_DEFAULT",
                                                                   purpose="LIVERY"), False, None)]
    for rec in decals.layers(obj):
        items.append(("decal_" + rec["id"], ("Decal: " if rec["mode"] == "PROJECT" else "Livery: ")
                      + _short(decals.label(rec), 18), "",
                      _set(scene.plinth, "active_decal", rec["id"]), False, None))
    ok = bake.can_bake(obj)[0]
    items += [("bake_" + k.lower(), "Bake textures · " + k, "",
               (lambda c, k=k: _op("plinth.bake_material", size=k)) if ok else None, False, None)
              for k in bake.SIZES]
    if bake.is_baked(obj):
        items.append(("unbake", "Back to procedural", "", lambda c: _op("plinth.unbake_material"),
                      False, None))
    return items


TILE_H = 62           # preset tile: a 3D preview over its name
SEG_MAX = 5           # cells in one segmented line


def preset_swatch(preset, current):
    """sRGB swatch for a preset tile: its own colour, or the part's colour for
    presets that keep it (paint finishes)."""
    from . import colours
    spec = materials.TYPES.get(preset.get("type"), {})
    value = preset.get("values", {}).get(spec.get("colour") or "", None)
    if value is None:
        value = preset.get("values", {}).get("Tint") or current
    return colours.srgb(value)


def _slug(name):
    import re
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _exp(lo, hi, f):
    """0..1 -> lo..hi exponentially (sizes: each step is a visible change)."""
    return lo * (hi / lo) ** max(0.0, min(1.0, f))


def _log_frac(lo, hi, v):
    import math
    return 0.0 if v <= lo else 1.0 if v >= hi else math.log(v / lo) / math.log(hi / lo)


def _short(text, n=12):
    return text if len(text) <= n else text[:n - 1] + "…"


def card_camera(scene, products):
    cam = scene.camera
    if cam is None or "plinth_shot" not in cam:
        return "Camera", "NO SHOT", [("primary", "build", "Pick a shot below", None),
                                     ("more", "Edit shot")]
    r = 0.5 * _rig_scale(scene)
    el, dist = studio.shot_orbit(scene, cam)
    lens = cam.data.lens
    ortho = studio.is_ortho(cam)
    if ortho:                    # 0.9.1: orthographic shots size the frame instead of a lens
        size = cam.data.ortho_scale
        first = ("slider", "lens", "Size", _frac(r, 40 * r, size), "%.2f m" % size,
                 lambda c, f: setattr(cam.data, "ortho_scale", _lerp(r, 40 * r, f)), (r, 40 * r, "lin"))
    else:
        first = ("slider", "lens", "Lens", _frac(18, 200, lens), "%d mm" % round(lens),
                 lambda c, f: setattr(cam.data, "lens", _lerp(18, 200, f)), (18, 200, "lin"))
    view = studio.shot_view(scene, cam)
    rows = [
        ("segrow", "projection", "View", [
            ("proj_PERSP", "Perspective", not ortho,
             lambda c: studio.set_shot_projection(c.scene, cam, False)),
            ("proj_ORTHO", "Orthographic", ortho,
             lambda c: studio.set_shot_projection(c.scene, cam, True))]),
        ("segrow", "views", "Look from", [
            ("view_" + k, label, view == k, (lambda c, k=k: studio.set_shot_view(c.scene, cam, k)))
            for k, (label, _a, _e) in studio.VIEWS.items()]),
        first,
        ("slider", "height", "Height", _frac(-10, 89, el), "%d°" % round(el),
         lambda c, f: studio.set_shot_orbit(c.scene, cam, elevation=_lerp(-10, 89, f)),
         (-10, 89, "lin")),
        ("slider", "distance", "Distance", _frac(r, 40 * r, dist), "%.2f m" % dist,
         lambda c, f: studio.set_shot_orbit(c.scene, cam, distance=_lerp(r, 40 * r, f)),
         (r, 40 * r, "lin")),
    ]
    aspect = tuple(cam.get("plinth_aspect", (4, 5)))
    # Issue 15: the five aspects as one segmented row.
    rows.append(("segrow", "aspect", "Aspect", [("aspect_%dx%d" % a, "%d:%d" % a, a == aspect,
                                                 (lambda c, a=a: studio.set_shot_aspect(c.scene, cam, a)))
                                                for a in studio.ASPECTS]))
    # Issue 16: toggles look like toggles.
    blur = cam.data.dof.use_dof
    rows.append(("switch", "blur", "Blur background", "", blur,
                 lambda c: studio.set_depth_of_field(c.scene, cam, not blur)))
    tt = studio.turntable_settings(cam)
    locked = cam.get("plinth_shot") == studio.TURNTABLE
    rows.append(("switch", "turntable", "Turntable", "", tt["on"],
                 None if locked else (lambda c: studio.set_turntable(c.scene, cam, on=not tt["on"]))))
    rows.append(aim_row(scene, cam))
    rows.append(("more", "Edit shot"))
    rows.append(("header", {"menu": ("shot", cam.name)}))
    w, h = cam.get("plinth_aspect", (4, 5))
    return studio.shot_label(cam), "%s · %d:%d" % ("ORTHO" if ortho else "%dMM" % round(lens), w, h), rows


QUALITY_HINT = {"EEVEE_STUDIO": "Seconds per shot", "EEVEE_FINAL": "Best Eevee, still quick",
                "CYCLES_PREVIEW": "Quick accurate check",
                "CYCLES_FINAL": "Web and shop", "CYCLES_HERO": "Print and campaign"}


def card_shoot(scene, products):
    s = scene.plinth
    q_items = s.bl_rna.properties["quality"].enum_items
    scale_row = ("scale", "Quality", QUALITY_HINT.get(s.quality, ""), [
        ("q_" + i.identifier, engine.QUALITY_LADDER[i.identifier][0], i.name,
         i.identifier == s.quality, _set(s, "quality", i.identifier)) for i in q_items])
    sizes = [("size_" + i.identifier, i.name, i.identifier == s.size, _set(s, "size", i.identifier))
             for i in s.bl_rna.properties["size"].enum_items if i.identifier != "CUSTOM"]
    n = len(enabled_shots(scene))
    rows = [scale_row, ("segrow", "size", "Size", sizes),
            ("switch", "transparent", "Transparent background", "", s.transparent,
             _set(s, "transparent", not s.transparent)),
            ("switch", "shadow", "Keep the floor shadow", "", s.shadow_catcher,
             _set(s, "shadow_catcher", not s.shadow_catcher)),
            ("more", "Edit output")]
    return "Shoot", "%02d SHOTS · %s" % (n, "READY" if n else "NONE TICKED"), rows


CARDS = {"STUDIO": card_studio, "LIGHT": card_light, "MATERIAL": card_material,
         "CAMERA": card_camera, "SHOOT": card_shoot}


def enabled_shots(scene):
    return [c for c in studio.list_shots(scene) if c.get("plinth_enabled", True)]


# --- Group builders -----------------------------------------------------------------

def group_pill(scene, g):
    s = g.scale
    m = lambda t: g.measure(t, FONT * s)
    lay = pill_layout(g.w, g.h, STEP_LABELS, m, s, g.left, g.right, g.top)
    if lay is not None and g.nav and overlaps(lay.container, _nav_box(g)):
        # Centred would sit under the navigation gizmo: centre in the space left of it.
        lay = pill_layout(g.w, g.h, STEP_LABELS, m, s, g.left, g.right + g.nav, g.top)
    if lay is None:
        return None
    current = scene.plinth.step
    els = [El("pill", "glass", lay.container, extra={"radius": lay.container[3] / 2}, block=True)]
    for key, label, r, hr in zip(STEP_KEYS, STEP_LABELS, lay.steps, lay.hits):
        els.append(El("step_" + key, "seg", r, label, on=key == current, hit=hr, group="pill",
                      act=(lambda c, k=key: bpy.ops.plinth.set_step(step=k))))
    return els


def group_engine(scene, g):
    s, ps = g.scale, scene.plinth
    h, pad = 36 * s, 3 * s
    top = g.h - g.top - MARGIN_TOP * s - 1 * s
    right = g.w - g.right - (g.nav or 0) - EDGE * s
    preset = engine_label(ps)
    chip_w = g.measure(preset, FONT * s) + 44 * s
    chip = (right - chip_w, top - h - pad, chip_w, h)
    segs = [("EEVEE", "Eevee"), ("CYCLES", "Cycles")]
    widths = [g.measure(t, FONT * s) + 32 * s for _k, t in segs]
    sw = sum(widths) + 2 * pad
    x0 = chip[0] - 8 * s - sw
    els = [El("engine", "glass", (x0, top - h - 2 * pad, sw, h + 2 * pad),
              extra={"radius": (h + 2 * pad) / 2}, block=True)]
    x = x0 + pad
    for (key, label), w in zip(segs, widths):
        els.append(El("engine_" + key, "seg", (x, top - h - pad, w, h), label, group="engine",
                      on=ps.engine == key, act=_set(ps, "engine", key)))
        x += w
    els.append(El("preset", "dropdown", chip, preset,
                  act=lambda c: bpy.ops.wm.call_menu(name="PLINTH_MT_engine")))
    return els


def group_view(scene, g):
    """Issue 6: 'look through the active shot' is always one click away."""
    cam = scene.camera
    if cam is None or "plinth_shot" not in cam:
        return None
    s = g.scale
    h, pad = 36 * s, 3 * s
    top = g.h - g.top - MARGIN_TOP * s - 1 * s
    x0 = g.left + EDGE * s
    segs = [("ORBIT", "Orbit"), ("SHOT", _short(studio.shot_label(cam), 14))]
    widths = [g.measure(t, FONT * s) + 32 * s for _k, t in segs]
    els = [El("view", "glass", (x0, top - h - 2 * pad, sum(widths) + 2 * pad, h + 2 * pad),
              extra={"radius": (h + 2 * pad) / 2}, block=True)]
    x = x0 + pad
    looking = g.frame is not None
    for (key, label), w in zip(segs, widths):
        on = looking if key == "SHOT" else not looking
        els.append(El("view_" + key, "seg", (x, top - h - pad, w, h), label, group="viewmode", on=on,
                      act=lambda c, key=key: _set_view(c, key)))
        x += w
    return els


def _view_width(scene, g):
    cam = scene.camera
    if cam is None or "plinth_shot" not in cam:
        return 0
    s = g.scale
    return sum(g.measure(t, FONT * s) + 32 * s for t in ("Orbit", _short(studio.shot_label(cam), 14))) + 6 * s


def group_gizmo(scene, g):
    """Move · Rotate · Scale: Blender's transform gizmos, on what the step is
    about (transform.py). Beside Orbit | Shot; a slimmer row under it when the
    step pill is in the way."""
    from . import transform
    s = g.scale
    on = g.gizmo or frozenset()
    widths = [g.measure(label, FONT * s) + 28 * s for _k, label, _a in transform.MODES]
    view_w = _view_width(scene, g)
    x0 = g.left + EDGE * s + (view_w + 8 * s if view_w else 0)
    top = g.h - g.top - MARGIN_TOP * s - 1 * s
    h, pad = 36 * s, 3 * s
    lay = pill_layout(g.w, g.h, STEP_LABELS, lambda t: g.measure(t, FONT * s), s, g.left, g.right, g.top)
    hint = ""
    if on:
        subj = transform.subject(scene)
        if subj is None:
            hint = "on the selection"
        elif subj.type == "CAMERA":
            hint = "on " + studio.shot_label(subj)
        else:
            hint = "on " + display(studio.light_label(subj))
    hint_w = g.measure(hint, SMALL * s) + 12 * s if hint else 0
    if lay is not None and x0 + sum(widths) + 2 * pad + hint_w + 10 * s > lay.container[0] - 8 * s:
        x0, h, pad = g.left + EDGE * s, 26 * s, 2 * s           # under Orbit | Shot
        top -= 42 * s + 6 * s
    els = [El("gizmo", "glass", (x0, top - h - 2 * pad, sum(widths) + 2 * pad, h + 2 * pad),
              extra={"radius": (h + 2 * pad) / 2}, block=True)]
    x = x0 + pad
    for (key, label, _attr), w in zip(transform.MODES, widths):
        els.append(El("gizmo_" + key, "seg", (x, top - h - pad, w, h), label, on=key in on,
                      act=(lambda c, key=key: transform.toggle(c, key))))
        x += w
    if hint:
        els.append(El("gizmo_hint", "hint", (x + pad + 10 * s, top - h - pad, hint_w, h), hint))
    return els


def _set_view(context, key):
    rv3d = getattr(context, "region_data", None)
    if rv3d is None or not hasattr(rv3d, "view_perspective"):
        return
    from . import framing
    if key == "SHOT":
        framing.remember(context)               # Orbit comes back to this view
        _pick_shot(context, context.scene.camera)
    else:
        framing.to_orbit(context)


def engine_label(ps):
    """``Eevee · Studio``: engine name + preset, so the dropdown can't be
    mistaken for the Studio step (REDESIGN.md §1, issue 2)."""
    if ps.engine == "EEVEE":
        return "Eevee · " + engine.EEVEE_PRESETS[ps.eevee_preset]["label"]
    return "Cycles · " + engine.CYCLES_PRESETS[ps.cycles_preset]["label"]


def group_light_chips(scene, g):
    if scene.plinth.step != "LIGHT":
        return None
    s = g.scale
    x = g.left + EDGE * s
    y = g.h - g.top - (MARGIN_TOP + 44 + 40) * s
    els = []
    lights = studio.list_lights(scene)
    if not lights and bpy.data.objects.get(studio.BACKDROP) is None:
        return None                               # no studio yet: nothing to add lights to
    from . import colours
    active = studio.active_light(scene)
    editing = studio.light_label(active) if active is not None and scene.plinth.active_light != hdri.CHIP else None
    for light in lights:
        key = studio.light_label(light)
        watts = studio.power_label(light.data)
        w = g.measure(display(key), FONT * s) + g.measure(watts, SMALL * s) * 1.2 + 58 * s
        y -= 44 * s
        # Issue 8: a dot in the light's own colour, and its wattage.
        els.append(El("light_" + key, "lightchip", (x, y, w, 44 * s), display(key), shown=watts,
                      on=editing == key, extra=colours.srgb(tuple(light.data.color)),
                      act=_set(scene.plinth, "active_light", key)))
        y -= 6 * s
    y -= 44 * s
    els.append(El("light_add", "add", (x, y, 44 * s, 44 * s), "+", act=_add_light))
    # The HDRI lights the scene too: its chip sits under the lights, apart.
    st = hdri.settings(scene)
    y -= (12 + 44) * s
    w = g.measure(hdri.CHIP, FONT * s) + g.measure(st["label"], SMALL * s) * 1.2 + 58 * s
    tint = colours.srgb(st["tint"][:3]) if st["on"] else (0.45, 0.45, 0.47)
    els.append(El("light_" + hdri.CHIP, "lightchip", (x, y, w, 44 * s), hdri.CHIP, shown=st["label"],
                  on=scene.plinth.active_light == hdri.CHIP, extra=tint,
                  act=_set(scene.plinth, "active_light", hdri.CHIP)))
    return els


# Rows a card can shed when the viewport is short, first to go first. Aim at
# is also in the viewport (click a target); the rest are on the step's page.
OPTIONAL_ROWS = ["scene_detail", "Rig", "Recent", "Aim at", "turntable", "views", "aspect", "Shape", "shadow", "mat_sliders",
                 "studio_sliders", "decals", "Part",
                 "Look", "Image"]


def _row_tag(row):
    if row[0] == "note":
        return row[2] if len(row) > 2 else None
    if row[0] in {"chips", "grid", "presets"}:
        return row[1]
    if row[0] in {"segrow", "switch", "buttons", "dropdown"}:
        return row[1]
    if row[0] == "shapes":
        return row[2] if len(row) > 2 else "Shape"
    if row[0] == "slider" and row[1].startswith("mat_"):
        return "mat_sliders"
    if row[0] == "slider" and row[1] in ("curve", "wall"):
        return "studio_sliders"                      # also on Edit studio › Shape
    return None


def group_card(scene, g, products):
    """The inspector card: the step's front (shedding optional rows until it
    fits above the shot bar), or its edit page when one is open."""
    from . import pages
    s = g.scale
    x = g.w - g.right - (g.nav or 0) - EDGE * s - CARD_W * s     # beside the nav gizmo
    page = pages.page_for(scene, products)
    if page is not None:
        return _page_elements(scene, g, page, x)
    if uistate.page_open(scene.plinth.step):
        uistate.close_page()                  # nothing to edit any more (e.g. light removed)
    title, tag, rows = CARDS[scene.plinth.step](scene, products)
    floor = EDGE * s
    dock = group_dock(scene, g)
    if dock:
        dx, dy, dw, dh = bbox(dock)
        if dx < x + CARD_W * s and x < dx + dw:                  # the dock is under the card
            floor = dy + dh + 12 * s
    for drop in [None] + OPTIONAL_ROWS:
        if drop is not None:
            rows = [r for r in rows if _row_tag(r) != drop]
        els = _card_elements(scene, g, title, tag, rows, x, products)
        if els[0].rect[1] >= floor:
            return els
    return els                            # still too tall: build() leaves it out


EDIT_LABELS = {"STUDIO": "Edit studio", "LIGHT": "Edit light", "MATERIAL": "Edit material",
               "CAMERA": "Edit shot", "SHOOT": "Edit output"}


def open_page(context, step):
    uistate.open_page(step)
    try:
        bpy.ops.plinth.page("INVOKE_DEFAULT")        # Esc goes back to the front
    except (RuntimeError, AttributeError):
        pass


def close_page(context=None):
    uistate.close_page()


def _card_top(g):
    return g.h - g.top - (MARGIN_TOP + 44 + 24) * g.scale


def _card_elements(scene, g, title, tag, rows, x, products=()):
    from . import pages
    s = g.scale
    inner = (CARD_W - 2 * CARD_PAD) * s
    top = _card_top(g)
    els = []
    y = top - CARD_PAD * s
    ix = x + CARD_PAD * s
    y -= TITLE * s
    header = next((r[1] for r in rows if r[0] == "header"), {})
    rows = [r for r in rows if r[0] != "header"]
    tag_w = inner
    if header.get("menu"):                       # issue 10: actions live in a ⋯ menu
        kind, target = header["menu"]
        d = 24 * s
        els.append(El("card_more", "dots", (ix + inner - d, y + (TITLE * s - d) / 2, d, d),
                      act=lambda c, kind=kind, target=target: open_menu(c, kind, target, "card_more")))
        tag_w -= d + 8 * s
    # Phase 16: the title gets what the tag leaves, so they never run into each other.
    tag_px = tag_width(tag, g, inner)
    title_w = max(40 * s, tag_w - tag_px - 12 * s)
    if header.get("title_menu"):                 # issue 11: the part is the title ("Body ▾")
        kind, target = header["title_menu"]
        tw = min(g.measure(title, TITLE * s) + 30 * s, title_w)
        els.append(El("card_title", "titledrop", (ix - 6 * s, y - 4 * s, tw + 6 * s, TITLE * s + 8 * s),
                      title, act=lambda c, kind=kind, target=target: open_menu(c, kind, target, "card_title")))
    else:
        els.append(El("card_title", "title", (ix, y, title_w, TITLE * s), title))
    els.append(El("card_tag", "tag", (ix + tag_w - tag_px, y, tag_px, TITLE * s), tag))
    step = scene.plinth.step
    for row in rows:
        y -= CARD_GAP * s
        if row[0] == "more":
            y -= 44 * s
            can = pages.can_open(scene, products)
            label = (row[2] if len(row) > 2 else EDIT_LABELS[step]) if can else row[1]
            els.append(El("edit", "editlink", (ix, y, inner, 44 * s), label, block=True, dim=not can,
                          act=(lambda c, step=step: open_page(c, step)) if can else None))
            continue
        y = layout_row(els, row, ix, y, inner, g)
    y -= CARD_PAD * s
    for e in els:
        e.layer = "card"
    card = El("card", "card", (x, y, CARD_W * s, top - y), block=True,
              extra={"radius": 22 * s, "page": step + "/front"})
    return [card] + els


def _page_elements(scene, g, page, x):
    """REDESIGN.md §3: back, title, tag; three tabs; up to six rows; Reset · Done."""
    s = g.scale
    inner = (CARD_W - 2 * CARD_PAD) * s
    top = _card_top(g)
    ix = x + CARD_PAD * s
    els = []
    y = top - CARD_PAD * s - 32 * s
    els.append(El("page_back", "back", (ix, y, 32 * s, 32 * s), "‹", act=close_page))
    tag_px = tag_width(page.tag, g, inner)
    els.append(El("card_title", "title", (ix + 42 * s, y + 6 * s, inner - 42 * s - tag_px - 12 * s,
                                          TITLE * s), page.title))
    els.append(El("card_tag", "tag", (ix + inner - tag_px, y + 6 * s, tag_px, TITLE * s), page.tag))
    y -= CARD_GAP * s + 40 * s
    tab = page.current_tab()
    els.append(El("tabs_bg", "well", (ix, y, inner, 40 * s), extra={"radius": 12 * s}, block=True))
    tw = (inner - 8 * s) / len(page.tabs)
    from .pages import _set_tab
    for i, (name, _rows) in enumerate(page.tabs):
        els.append(El("tab_" + _slug(name), "tab", (ix + 4 * s + i * tw, y + 4 * s, tw, 32 * s), name,
                      on=name == tab, group="tabs", act=_set_tab(page.key, name)))
    for row in page.rows()[:6]:
        y -= CARD_GAP * s
        y = layout_row(els, row, ix, y, inner, g)
    y -= (CARD_GAP + 2) * s
    els.append(El("page_rule", "hline", (ix, y, inner, 1 * s)))
    y -= 14 * s + 44 * s
    half = (inner - 10 * s) / 2
    els.append(El("page_reset", "outline", (ix, y, half, 44 * s), page.reset_label, block=True,
                  act=page.reset, dim=page.reset is None))
    els.append(El("page_done", "done", (ix + half + 10 * s, y, half, 44 * s), "Done", act=close_page))
    y -= CARD_PAD * s
    for e in els:
        e.layer = "card"
    card = El("card", "card", (x, y, CARD_W * s, top - y), block=True,
              extra={"radius": 22 * s, "page": "%s/page/%s" % (scene.plinth.step, tab)})
    return [card] + els


def tag_width(tag, g, inner):
    """Room for a mono, letter-spaced tag (UI-font measure, widened), at most
    half the card so the title always keeps some."""
    return min(g.measure(tag, MONO * g.scale) * 1.4 + 4 * g.scale, inner * 0.5)


def _wrap(text, width, size, g):
    words, lines, line = text.split(), [], ""
    for w in words:
        trial = (line + " " + w).strip()
        if line and g.measure(trial, size) > width:
            lines.append(line)
            line = w
        else:
            line = trial
    return lines + ([line] if line else [])


def layout_row(els, row, ix, y, inner, g):
    """Append one row's elements below ``y``; returns the new ``y`` (bottom)."""
    s = g.scale
    kind = row[0]
    if kind == "slider":
        y -= 46 * s
        sl = slider_elements(row, (ix, y, inner, 46 * s), g)
        if sl[0].drag is None:
            sl[0].dim = True
            sl[0].block = True
        els += sl
    elif kind == "strip":
        _k, sid, label, frac, shown, setter, cols, num = row
        y -= 46 * s
        st = slider_elements(("slider", sid, label, frac, shown, setter, num), (ix, y, inner, 46 * s), g)
        st[0].kind, st[0].id, st[0].extra = "strip", "strip_" + sid, cols
        els += st
    elif kind == "shapes":
        items = row[1]
        heading, prefix = (row[2], row[3]) if len(row) > 3 else ("Shape", "shape_")
        y -= FONT * s
        els.append(El(prefix + "head", "label", (ix, y, inner, FONT * s), heading,
                      extra={"hints": {prefix + k: label for k, label, _on, _a in items}}))
        y -= 8 * s + 48 * s
        gap = 6 * s
        w = (inner - gap * (len(items) - 1)) / len(items)
        for i, (key, label, on, act) in enumerate(items):
            els.append(El(prefix + key, "shape", (ix + i * (w + gap), y, w, 48 * s), label,
                          on=on, act=act, extra=key))
    elif kind == "chips":
        _k, heading, items = row
        y -= FONT * s
        els.append(El("head_" + heading, "label", (ix, y, inner, FONT * s), heading))
        y -= 8 * s
        cx = ix
        y -= 34 * s
        for cid, label, on, act in items:
            w = g.measure(label, SMALL * s) + 28 * s
            if cx + w > ix + inner + 0.5 and cx > ix:
                cx = ix
                y -= 34 * s + 6 * s
            els.append(El(cid, "chip", (cx, y, w, 34 * s), label, on=on, act=act,
                          block=True, dim=act is None))
            cx += w + 6 * s
    elif kind == "scale":
        _k, heading, hint_text, items = row
        y -= FONT * s
        els.append(El("head_" + heading, "label", (ix, y, inner, FONT * s), heading,
                      shown=hint_text))
        y -= 8 * s + 56 * s
        pad = 4 * s
        els.append(El("scale_bg", "well", (ix, y, inner, 56 * s), extra={"radius": 14 * s}, block=True))
        w = (inner - 2 * pad) / len(items)
        for i, (qid, small, label, on, act) in enumerate(items):
            els.append(El(qid, "seg2", (ix + pad + i * w, y + pad, w, 56 * s - 2 * pad), label,
                          on=on, act=act, shown=small, group="quality"))
        y -= 16 * s
        els.append(El("scale_ends", "ends", (ix, y, inner, 12 * s), "Faster", shown="Better"))
    elif kind == "grid":                          # issue 7: even 3-column chips
        _k, heading, items = row
        y -= FONT * s
        els.append(El("head_" + heading, "label", (ix, y, inner, FONT * s), heading))
        y -= 8 * s
        cols, gap, ch = 3, 6 * s, 34 * s
        w = (inner - gap * (cols - 1)) / cols
        for i, (cid, label, on, act) in enumerate(items):
            if i % cols == 0:
                y -= ch + (gap if i else 0)
            els.append(El(cid, "gridchip", (ix + (i % cols) * (w + gap), y, w, ch), label, on=on,
                          act=act, block=True, dim=act is None))
    elif kind == "presets":                       # issues 12, 13: tiles with their colour
        _k, heading, items, original = row
        y -= FONT * s
        els.append(El("head_" + heading, "label", (ix, y, inner, FONT * s), heading))
        oid, olabel, oact = original
        if oact is not None:                      # "Original" as a reset hint on the label row
            ow = g.measure(olabel, SMALL * s) + 12 * s
            els.append(El(oid, "linkhint", (ix + inner - ow, y - 3 * s, ow, FONT * s + 6 * s), olabel,
                          act=oact))
        y -= 8 * s
        cols, gap, th = 3, 6 * s, TILE_H * s
        w = (inner - gap * (cols - 1)) / cols
        for i, item in enumerate(items):
            pid, label, rgb, on, act = item[:5]
            if i % cols == 0:
                y -= th + (gap if i else 0)
            els.append(El(pid, "presettile", (ix + (i % cols) * (w + gap), y, w, th), label, on=on,
                          act=act, extra=rgb, shown=item[5] if len(item) > 5 else "",
                          edit=item[6] if len(item) > 6 else None))
    elif kind == "dropdown":                      # a heading and a ▾ that opens a menu
        _k, did, heading, label, act = row
        y -= FONT * s
        els.append(El("head_" + did, "label", (ix, y, inner, FONT * s), heading))
        y -= 8 * s + 36 * s
        els.append(El(did, "dropdown", (ix, y, inner, 36 * s), label, act=act))
    elif kind == "matcurrent":                    # 0.9: the part's material; click to browse
        _k, label, meta, rgb, preview, act = row
        y -= 58 * s
        els.append(El("matcurrent", "matcurrent", (ix, y, inner, 58 * s), label, shown=preview,
                      act=act, extra={"rgb": rgb, "meta": meta}))
    elif kind == "button":                        # a full-width outline button
        _k, bid, label, act = row
        y -= 40 * s
        els.append(El(bid, "outline", (ix, y, inner, 40 * s), label, act=act, block=True,
                      dim=act is None))
    elif kind == "buttons":                       # outline buttons side by side
        _k, bid, label, hint_text, items = row
        if label:
            y -= FONT * s
            els.append(El("head_" + bid, "label", (ix, y, inner, FONT * s), label, shown=hint_text))
            y -= 8 * s
        y -= 36 * s
        gap = 6 * s
        w = (inner - gap * (len(items) - 1)) / len(items)
        for i, (iid, text, act) in enumerate(items):
            els.append(El(iid, "outline", (ix + i * (w + gap), y, w, 36 * s), text, act=act, block=True,
                          dim=act is None))
    elif kind == "primary":
        _k, pid, label, act = row
        y -= 44 * s
        els.append(El("card_" + pid, "primary", (ix, y, inner, 44 * s), label, act=act,
                      block=True, dim=act is None))
    elif kind == "palette":
        _k, pid, label, items = row
        if label:
            y -= FONT * s
            els.append(El("head_" + pid, "label", (ix, y, inner, FONT * s), label))
            y -= 8 * s
        d, cols = 36 * s, 6
        gap = (inner - cols * d) / (cols - 1)
        for i, (sid, name, rgb, on, act) in enumerate(items):
            if i % cols == 0:
                y -= d + (gap if i else 0)
            els.append(El(sid, "palette", (ix + (i % cols) * (d + gap), y, d, d), name, on=on,
                          act=act, extra=rgb))
    elif kind == "targetseg":
        items = row[1]
        y -= 34 * s
        gap = 6 * s
        w = (inner - gap * (len(items) - 1)) / len(items)
        for i, (tid, label, rgb, on, act) in enumerate(items):
            els.append(El(tid, "targetpill", (ix + i * (w + gap), y, w, 34 * s), label, on=on,
                          act=act, extra=rgb))
    elif kind == "hexrow":
        _k, hid, hex_text, commit = row
        y -= 32 * s
        value = El(hid, "hexvalue", (ix, y, inner - 132 * s, 32 * s), hex_text, block=True,
                   edit=(hex_text, commit))
        els.append(value)

        def type_value(context, value=value):
            from . import overlay
            overlay.start_edit(context, value, width=110)
        els.append(El(hid + "_type", "outline", (ix + inner - 124 * s, y, 124 * s, 32 * s),
                      "Type a value…", act=type_value))
    elif kind == "tiles":
        _k, tid, label, items = row
        y -= FONT * s
        els.append(El("head_" + tid, "label", (ix, y, inner, FONT * s), label))
        y -= 8 * s
        cols, gap, th = (3 if len(items) <= 9 else 4), 6 * s, 66 * s     # many gobos: four across
        w = (inner - gap * (cols - 1)) / cols
        for i, (iid, name, pattern, on, act) in enumerate(items):
            if i % cols == 0:
                y -= th + (gap if i else 0)
            els.append(El(iid, "tile", (ix + (i % cols) * (w + gap), y, w, th), name, on=on, act=act,
                          extra=pattern))
    elif kind == "switch":
        _k, sid, label, help_text, on, act = row
        # Phase 16: label and help wrap beside the switch instead of running under it.
        room = inner - 56 * s
        lines = _wrap(label, room, FONT * s, g) or [label]
        helps = _wrap(help_text, room, HELP * s, g) if help_text else []
        h = max(30 * s, len(lines) * 18 * s + len(helps) * 15 * s + 8 * s)
        y -= h
        els.append(El("switch_" + sid, "switchrow", (ix, y, inner, h), label, shown=help_text, on=on,
                      act=act, block=True, dim=act is None, extra={"lines": lines, "help": helps}))
    elif kind == "segrow":
        _k, sid, label, items = row
        y -= FONT * s
        els.append(El("head_" + sid, "label", (ix, y, inner, FONT * s), label))
        y -= 8 * s
        # More than five cells don't fit one line (Material's seven types): two
        # lines, the first one longer, each evenly filled.
        per = len(items) if len(items) <= SEG_MAX else -(-len(items) // 2)
        lines = [items[k:k + per] for k in range(0, len(items), per)]
        for n, line in enumerate(lines):
            y -= 38 * s + (4 * s if n else 0)
            els.append(El("segbg_" + sid + ("_%d" % n if n else ""), "well", (ix, y, inner, 38 * s),
                          extra={"radius": 12 * s}, block=True))
            w = (inner - 8 * s) / len(line)
            for i, (cid, text, on, act) in enumerate(line):
                els.append(El(cid, "segcell", (ix + 4 * s + i * w, y + 4 * s, w, 30 * s), text, on=on,
                              act=act, group="seg:" + sid, block=True, dim=act is None))
    elif kind == "list":
        _k, lid, items = row
        rh = 46 * s
        shown = items[:6]
        y -= rh * len(shown)
        els.append(El("listbg_" + lid, "listgroup", (ix, y, inner, rh * len(shown)), block=True))
        for i, (rid, name, meta, aspect, on, act, active) in enumerate(shown):
            ry = y + rh * (len(shown) - 1 - i)
            els.append(El(rid, "listrow", (ix, ry, inner, rh), name, shown=meta, on=on, act=act,
                          extra={"aspect": aspect, "active": active, "first": i == 0}))
    elif kind == "path":
        _k, pid, label, path, preview, act = row
        y -= FONT * s
        els.append(El("head_" + pid, "label", (ix, y, inner, FONT * s), label))
        y -= 8 * s + 32 * s
        els.append(El(pid, "pathbox", (ix, y, inner, 32 * s), path))
        y -= 18 * s
        els.append(El(pid + "_preview", "note", (ix, y, inner, 14 * s), preview, extra=[preview]))
        y -= 8 * s + 32 * s
        els.append(El(pid + "_choose", "outline", (ix, y, 140 * s, 32 * s), "Choose folder…", act=act))
    elif kind == "note":
        lines = _wrap(row[1], inner, HELP * s, g)
        h = len(lines) * 16 * s
        y -= h
        els.append(El("note_%d" % len(els), "note", (ix, y, inner, h), row[1], extra=lines))
    return y


def slider_elements(row, rect, g):
    """A slider, plus (when its number can be typed) a hit area on the value:
    double-click it to type an exact number (REDESIGN.md §5)."""
    _k, sid, label, frac, shown, setter = row[:6]
    num = row[6] if len(row) > 6 else None
    s = g.scale
    x, y, w, h = rect
    # Phase 15: the hit area reaches past the track by the knob's radius, so a
    # knob at 0 or 1 (Clearcoat starts at 1.00) can be grabbed by either half.
    grab = KNOB_GRAB * s
    els = [El("slider_" + sid, "slider", rect, label, value=frac, shown=shown, drag=setter,
              track=(x, y, w, 28 * s), hit=(x - grab, y, w + 2 * grab, h))]
    if num is not None:
        vw = g.measure(shown, SMALL * s) + 16 * s
        els.append(El("value_" + sid, "valuehit", (x + w - vw, y + 28 * s, vw, 18 * s), shown,
                      block=True, edit=(_number_text(shown), typed_setter(setter, *num))))
    return els


def _number_text(shown):
    v = textedit.parse_number(shown)
    if v is None:
        return shown
    return ("%d" % v) if float(v).is_integer() else ("%g" % v)


def typed_setter(setter, lo, hi, kind="lin"):
    """commit(context, text) for a slider: the typed number -> its fraction.
    Out-of-range numbers clamp to the slider's ends; non-numbers are ignored."""
    def commit(context, text):
        v = textedit.parse_number(text)
        if v is None:
            return False
        setter(context, _log_frac(lo, hi, v) if kind == "log" else _frac(lo, hi, v))
        return True
    return commit


def focus_elements(els, g):
    """The overlay text field, on top of the element that has focus."""
    f = uistate.focus
    if not f:
        return []
    target = next((e for e in els if e.id == f.get("id")), None)
    if target is None:
        return []
    s = g.scale
    x, y, w, h = target.rect
    fh = min(max(h, 20 * s), 32 * s)
    width = max(w + 8 * s, f.get("width", 72) * s)
    rect = (x + w + 4 * s - width, y + (h - fh) / 2, width, fh) if f.get("align") == "right" \
        else (x - 4 * s, y + (h - fh) / 2, width, fh)
    return [El("textfield", "textfield", rect, f["model"].text, extra=f["model"], block=True,
               layer=target.layer)]


# 0.9.1 (tester feedback): the shot bar belongs to the shots. It shows in the
# Camera step (under the shot you're setting up) and Shoot (it holds Shoot);
# elsewhere it only appears while a batch is running, so progress stays visible.
DOCK_STEPS = ("CAMERA", "SHOOT")


def dock_visible(scene):
    if scene.plinth.step in DOCK_STEPS:
        return True
    job = _job()
    return job is not None and job.state == "running"


def group_dock(scene, g):
    """Shot bar v2 (REDESIGN.md §4): shot cards, + From view, then the scope
    switch and Shoot, in one black bar bottom-centre. Cards collapse to
    thumbnails, then page, rather than ever overflowing."""
    if not dock_visible(scene):
        return None
    s = g.scale
    shots = studio.list_shots(scene)
    pad, gap, ch = BAR_PAD * s, BAR_GAP * s, BAR_CHILD * s
    right_w = _shoot_column_width(scene, g)
    fixed = 2 * pad + (ADD_W * s + gap if shots else 0) + (1 * s + 2 * gap if shots else 0) + right_w
    free = g.w - g.left - g.right - 2 * EDGE * s
    cw = SHOT_W * s
    collapsed = False
    bar_h = 2 * pad + ch
    over_frame = g.frame is not None and g.frame[1] < EDGE * s + bar_h + 4 * s
    if shots and (fixed + len(shots) * (cw + gap) > free or over_frame):
        collapsed, cw = True, SHOT_COLLAPSED * s
    per_page, paged = len(shots), False
    if shots and fixed + len(shots) * (cw + gap) > free:
        paged = True
        per_page = int((free - fixed - 2 * (PAGER_W * s + gap)) // (cw + gap))
        if per_page < 1:
            return None
    pages = max(1, -(-len(shots) // max(1, per_page)))
    page = max(0, min(uistate.dock.get("page", 0), pages - 1)) if paged else 0
    visible = shots[page * per_page:(page + 1) * per_page] if paged else shots
    width = fixed + len(visible) * (cw + gap) + (2 * (PAGER_W * s + gap) if paged else 0)
    x = g.left + EDGE * s + (free - width) / 2
    y = _bar_y(scene, g, 2 * pad + ch)
    els = [El("dock", "glass", (x, y, width, ch + 2 * pad), extra={"radius": BAR_RADIUS * s},
              block=True)]
    cx, cy = x + pad, y + pad
    job = _job()
    running = job is not None and job.state == "running"
    if paged:
        els.append(El("dock_prev", "pager", (cx, cy, PAGER_W * s, ch), "‹",
                      act=(lambda c: _set_dock_page(page - 1)) if page > 0 else None,
                      block=True, dim=page == 0))
        cx += PAGER_W * s + gap
    for cam in visible:
        els += _shot_card(scene, cam, (cx, cy, cw, ch), s, collapsed, job, running)
        cx += cw + gap
    if paged:
        els.append(El("dock_next", "pager", (cx, cy, PAGER_W * s, ch), "›",
                      act=(lambda c: _set_dock_page(page + 1)) if page < pages - 1 else None,
                      block=True, dim=page >= pages - 1))
        cx += PAGER_W * s + gap
    if shots:
        els.append(El("shot_add", "addshot", (cx, cy, ADD_W * s, ch), "From view",
                      act=None if running else add_shot_from_view))
        cx += ADD_W * s + gap
        els.append(El("dock_divider", "vline", (cx, cy + 8 * s, 1 * s, ch - 16 * s)))
        cx += 1 * s + gap
    els += _shoot_column(scene, g, (cx, cy, right_w, ch), job)
    return els


BAR_PAD, BAR_GAP, BAR_CHILD, BAR_RADIUS = 8, 8, 72, 22
SHOT_W, SHOT_COLLAPSED, ADD_W, PAGER_W, TICK = 88, 72, 72, 28, 18
SCOPE_H, SHOOT_H = 28, 38


def _job():
    from . import shoot
    return shoot.current_job()


def _bar_y(scene, g, height):
    """Bottom edge of the shot bar: centred in the passepartout under the camera
    frame when there's room (issue 5), else the usual margin."""
    s = g.scale
    if g.frame is not None and g.frame[1] >= EDGE * s + height + 4 * s:
        return max(EDGE * s, (g.frame[1] - height) / 2)
    return EDGE * s


def _set_dock_page(n):
    uistate.dock["page"] = max(0, n)


def _shot_card(scene, cam, rect, s, collapsed, job, running):
    """One shot: card (click = look through it; again = its menu; double-click =
    rename), its tick (included in the batch) and, when active, its ⋯."""
    x, y, w, h = rect
    label = studio.shot_label(cam)
    active = scene.camera == cam
    ticked = bool(cam.get("plinth_enabled", True))
    run = None
    if job is not None and cam.name in job.shots:
        if cam.name in job.finished_shots():
            run = "done"
        elif running and job.current is not None and job.shots[job.current] == cam.name:
            run = "current"
    extra = {"aspect": tuple(cam.get("plinth_aspect", (4, 5))), "lens": cam.data.lens,
             "collapsed": collapsed, "run": run}
    if active:
        act = lambda c, cam=cam: open_shot_menu(c, cam)
    else:
        act = lambda c, cam=cam: _pick_shot(c, cam)
    els = [El("shot_" + cam.name, "shotcard", rect, label, on=active, act=act,
              dim=not ticked and not active, extra=extra,
              edit=(label, lambda c, t, cam=cam: studio.rename_shot(cam, t)))]
    t = TICK * s
    els.append(El("tick_" + cam.name, "tick", (x + w - t - 5 * s, y + h - t - 5 * s, t, t),
                  on=ticked, extra={"run": run},
                  act=None if running else (lambda c, cam=cam: _toggle_shot(cam)), block=True))
    if active and not running:
        els.append(El("shotmore_" + cam.name, "dots", (x + 5 * s, y + h - t - 5 * s, t, t),
                      act=lambda c, cam=cam: open_shot_menu(c, cam)))
    return els


def _shoot_column_width(scene, g):
    s = g.scale
    labels = [shoot_button_label(scene), "Ticked · 00", "✓ 00 saved · Open folder"]
    return max(176 * s, max(g.measure(t, 14 * s) for t in labels) + 56 * s)


def _shoot_column(scene, g, rect, job):
    """Scope switch over the primary button; the button morphs into progress
    while shooting and into 'N saved · Open folder' when done."""
    s = g.scale
    x, y, w, h = rect
    ps = scene.plinth
    els = []
    sy = y + h - SCOPE_H * s
    els.append(El("scope_bg", "well", (x, sy, w, SCOPE_H * s), extra={"radius": 10 * s}, block=True))
    n = len(enabled_shots(scene))
    cells = [("ACTIVE", "This shot"), ("TICKED", "Ticked · %d" % n)]
    cw = (w - 6 * s) / 2
    running = job is not None and job.state == "running"
    for i, (key, label) in enumerate(cells):
        els.append(El("scope_" + key, "segcell", (x + 3 * s + i * cw, sy + 3 * s, cw, SCOPE_H * s - 6 * s),
                      label, on=ps.shoot_scope == key, group="scope",
                      act=None if running else _set(ps, "shoot_scope", key), block=True))
    by, bh = y, SHOOT_H * s
    if running:
        stop = SHOOT_H * s
        els.append(El("progress", "progress", (x, by, w - stop - 6 * s, bh), job.label(),
                      value=job.fraction, block=True))
        els.append(El("shoot_stop", "stopbtn", (x + w - stop, by, stop, bh), "✕",
                      act=lambda c: bpy.ops.plinth.shoot_stop()))
    elif job is not None and job.state == "done":
        text = "✓ %d saved · Open folder" % job.saved
        els.append(El("shoot_done", "donebtn", (x, by, w, bh), text,
                      act=lambda c: bpy.ops.plinth.shoot_done()))
    else:
        label = shoot_button_label(scene)
        can = bool(_shots_to_shoot(scene))
        els.append(El("shoot", "shootbtn", (x, by, w, bh), label, dim=not can, block=True,
                      act=(lambda c: bpy.ops.plinth.shoot("INVOKE_DEFAULT")) if can else None))
    return els


def _shots_to_shoot(scene):
    if scene.plinth.shoot_scope == "ACTIVE":
        cam = scene.camera
        return [cam] if cam is not None and "plinth_shot" in cam else []
    return enabled_shots(scene)


def add_shot_from_view(context):
    """+ in the dock: a new shot exactly where the viewport is looking, active
    and ticked."""
    rv3d = getattr(context, "region_data", None)
    scene = context.scene
    if rv3d is not None and hasattr(rv3d, "view_matrix"):
        matrix = rv3d.view_matrix.inverted()
        lens = getattr(context.space_data, "lens", 50.0)
    else:                                       # no viewport (tests): copy the active shot
        cam = scene.camera
        matrix = cam.matrix_world.copy() if cam else studio.get_target(scene).matrix_world.copy()
        lens = cam.data.lens if cam else 50.0
    _pick_shot(context, studio.add_shot(scene, matrix, lens))


def shoot_button_label(scene):
    """Says exactly what Shoot will do: 'Shoot Hero', 'Shoot 3 shots', or why not."""
    shots = _shots_to_shoot(scene)
    if not studio.list_shots(scene):
        return "Build studio first"
    if not shots:
        return "Pick a shot" if scene.plinth.shoot_scope == "ACTIVE" else "Tick a shot"
    if len(shots) == 1:
        return "Shoot " + studio.shot_label(shots[0])
    return "Shoot %d shots" % len(shots)


def _toggle_shot(cam):
    cam["plinth_enabled"] = not cam.get("plinth_enabled", True)


# --- Menus (REDESIGN.md §2 ``menu``) ----------------------------------------------------
# uistate.menu = {"kind", "target", "for": element id it opens from}. Items are
# built fresh every draw, so they always act on current data.

def open_shot_menu(context, cam):
    open_menu(context, "shot", cam.name, "shot_" + cam.name)


def open_menu(context, kind, target, anchor_id):
    uistate.menu.clear()
    uistate.menu.update(kind=kind, target=target, anchor=anchor_id)
    try:
        bpy.ops.plinth.menu("INVOKE_DEFAULT")       # Esc, shortcuts, click outside
    except (RuntimeError, AttributeError):
        pass                                         # headless: no window to listen in


def _shot_menu_items(scene, els, cam):
    card = next((e for e in els if e.id == "shot_" + cam.name), None)
    shots = studio.list_shots(scene)
    i = shots.index(cam) if cam in shots else 0

    def rename(c):
        if card is not None:
            from . import overlay
            overlay.start_edit(c, card)

    def duplicate(c):
        _pick_shot(c, studio.duplicate_shot(c.scene, cam))

    return [("rename", "Rename", "F2", rename, False, ("F2", False)),
            ("duplicate", "Duplicate", "⇧D", duplicate, False, ("D", True)),
            ("left", "Move left", "", (lambda c: studio.move_shot(c.scene, cam, -1)) if i > 0 else None,
             False, None),
            ("right", "Move right", "",
             (lambda c: studio.move_shot(c.scene, cam, 1)) if i < len(shots) - 1 else None, False, None),
            ("delete", "Delete", "⌫", lambda c: studio.remove_shot(c.scene, cam), True,
             ("BACK_SPACE", False))]


def menu_items(scene, els):
    """[(id, label, shortcut text, act, danger, key)] of the open menu."""
    m = uistate.menu
    if m.get("kind") == "shot":
        cam = bpy.data.objects.get(m.get("target", ""))
        if cam is not None and "plinth_shot" in cam:
            return _shot_menu_items(scene, els, cam)
    extra = MENU_BUILDERS.get(m.get("kind"))
    return extra(scene, els, m) if extra else []


def _light_menu_items(scene, els, m):
    light = bpy.data.objects.get(m.get("target", ""))
    if light is None or light.type != "LIGHT":
        return []
    return [("duplicate", "Duplicate", "⇧D",
             lambda c: _select_light(c, studio.duplicate_light(c.scene, light)), False, ("D", True)),
            ("delete", "Remove", "⌫", lambda c: _remove_light(c, light), True, ("BACK_SPACE", False))]


def _parts_menu_items(scene, els, m):
    obj = bpy.data.objects.get(m.get("target", ""))
    if obj is None:
        return []
    shown = materials.used_slots(obj) or set(range(len(obj.material_slots)))   # parts on a face
    return [("part_%d" % i, _short(materials.part_label(obj, i), 18),
             "●" if i == obj.active_material_index else "",
             (lambda c, i=i: setattr(obj, "active_material_index", i)), False, None)
            for i in range(len(obj.material_slots)) if i in shown]


TEXMAP_MENU_IMAGES = 14


def _texmap_menu_items(scene, els, m):
    """A map chip's menu: From disk…, Clear, then the images in this file
    (the ones named for this map first)."""
    from . import textures
    ch = m.get("target", "")
    obj = bpy.context.active_object
    if obj is None or obj.type != "MESH" or ch not in textures.LABELS:
        return []
    mat = materials.part_material(obj)
    has = ch in materials.texture_maps(mat)
    items = [("tex_disk", "From disk…", "", lambda c: _op("plinth.texture_file", "INVOKE_DEFAULT", channel=ch),
              False, None),
             ("tex_clear", "Clear", "", (lambda c: textures.clear_map(c.active_object, ch)) if has else None,
              True, None)]
    pool = textures.images_in_file()
    pool.sort(key=lambda t: t[1] != ch)                  # this map's own first, the rest after
    for img, guess in pool[:TEXMAP_MENU_IMAGES]:
        items.append(("teximg_" + _slug(img.name), _short(img.name, 20), "●" if guess == ch else "",
                      lambda c, img=img: textures.set_map(c.active_object, ch, img), False, None))
    return items


MENU_BUILDERS = {"texmap": _texmap_menu_items, "light": _light_menu_items, "parts": _parts_menu_items, "material": _material_menu_items,
                 "families": _families_menu_items, "looks": _looks_menu_items,
                 "forms": _forms_menu_items, "scenes": _scenes_menu_items,
                 "detail": _detail_menu_items}
MENU_W, MENU_ITEM, MENU_PAD = 176, 34, 6


def _closing(act):
    def run(context):
        uistate.close_menu()
        act(context)
    return run


def group_menu(scene, g, els):
    """The open menu, above (or below) the element it opened from."""
    if not uistate.menu:
        return []
    items = menu_items(scene, els)
    anchor = next((e for e in els if e.id == uistate.menu.get("anchor")), None)
    if not items or anchor is None:
        return []
    s = g.scale
    room = g.h - g.top - 16 * s
    cols = 2 if len(items) * MENU_ITEM * s + 2 * MENU_PAD * s > room * 0.6 else 1   # long lists: two columns
    per = (len(items) + cols - 1) // cols
    cw = MENU_W * s
    w = cw * cols
    h = per * MENU_ITEM * s + 2 * MENU_PAD * s
    ax, ay, aw, ah = anchor.rect
    # Measure from the container the anchor sits in (the shot bar, a card):
    # with no borders, a menu just above its card would touch the bar's edge.
    box = None if anchor.layer == "sheet" else next(
        (e.rect for e in els if e.kind in {"glass", "card"} and e.rect[1] <= ay
         and ay + ah <= e.rect[1] + e.rect[3] and e.rect[0] <= ax <= e.rect[0] + e.rect[2]), None)
    if box is not None:
        ay, ah = box[1], box[3]
    x = max(g.left + 8 * s, min(ax, g.w - g.right - w - 8 * s))
    y = ay + ah + 8 * s                                     # opens above its anchor...
    if y + h > g.h - g.top - 8 * s:
        y = max(8 * s, ay - 8 * s - h)                      # ...or below if there's no room
    corner = (x, y) if y > ay else (x, y + h)
    out = [El("menu", "menu", (x, y, w, h), layer="menu", block=True, extra={"anchor": corner})]
    for n, (item_id, label, shortcut, act, danger, key) in enumerate(items):
        col, row = divmod(n, per)
        iy = y + h - MENU_PAD * s - (row + 1) * MENU_ITEM * s
        out.append(El("menu_" + item_id, "menuitem", (x + col * cw + MENU_PAD * s, iy, cw - 2 * MENU_PAD * s,
                                                      MENU_ITEM * s), label, shown=shortcut,
                      act=_closing(act) if act else None, block=True, dim=act is None,
                      layer="menu", extra={"danger": danger, "key": key}))
    return out


def _pick_shot(context, cam, look=True):
    """Look through a shot (or, with look=False, only make it the active shot:
    its dot in the Camera step). The render size takes the shot's aspect
    (keeping the long edge) so the viewport's camera frame shows the real crop."""
    from . import framing, shoot
    if look:
        framing.remember(context)
    scene = context.scene
    scene.camera = cam
    r = scene.render
    r.resolution_x, r.resolution_y = shoot.resolution_for(
        tuple(cam.get("plinth_aspect", (4, 5))), max(r.resolution_x, r.resolution_y))
    if not look:
        return
    rv3d = getattr(context, "region_data", None)
    if rv3d is not None and hasattr(rv3d, "view_perspective"):
        rv3d.view_perspective = "CAMERA"


def status_text(scene):
    ps = scene.plinth
    if ps.engine == "EEVEE":
        text = "EEVEE · %s · LIVE" % engine.EEVEE_PRESETS[ps.eevee_preset]["label"].upper()
    else:
        text = "CYCLES · %s · REFINING" % engine.CYCLES_PRESETS[ps.cycles_preset]["label"].upper()
    if ps.hybrid:
        eng, key = engine.QUALITY_LADDER[ps.quality]
        table = engine.EEVEE_PRESETS if eng == "EEVEE" else engine.CYCLES_PRESETS
        text += " → SHOOTS %s %s" % (eng, table[key]["label"].upper())
    return text


def nav_status(scene, g):
    """'DRAG MOVES HERO' while looking through a shot with Plinth navigation."""
    from . import nav
    if g.frame is None or not nav.enabled(bpy.context):
        return None
    return nav.status(scene)


def group_nav_hint(scene, g):
    """First time in the studio: how the mouse moves the view (until the first orbit)."""
    from . import nav
    if not nav.show_hint(bpy.context):
        return None
    s = g.scale
    lift = 92
    return [El("nav_hint", "hint", (g.left + EDGE * s, EDGE * s + lift * s,
                                     g.measure(nav.HINT, SMALL * s), 20 * s), nav.HINT)]


def _hide(context, obj):
    from . import isolate
    isolate.hide(context.scene, context.view_layer, [obj])
    props.tag_viewports(context)


def group_hidden(scene, g):
    """While parts are hidden (H): '2 hidden · Show', bottom left. One click (or
    H again) brings back exactly what was hidden."""
    from . import isolate
    gone = isolate.hidden(scene)
    if not gone:
        return None
    s = g.scale
    h, pad = 32 * s, 3 * s
    label = ("%s hidden" % _short(gone[0].name, 18)) if len(gone) == 1 else "%d parts hidden" % len(gone)
    button = "Show · H"
    lw = g.measure(label, SMALL * s) + 24 * s
    bw = g.measure(button, FONT * s) + 28 * s
    x0, y0 = g.left + EDGE * s, EDGE * s + 30 * s
    return [El("hidden", "glass", (x0, y0, lw + bw + 2 * pad, h + 2 * pad), extra={"radius": (h + 2 * pad) / 2},
               block=True),
            El("hidden_label", "hint", (x0 + pad + 12 * s, y0 + pad, lw, h), label),
            El("hidden_show", "seg", (x0 + pad + lw, y0 + pad, bw, h), button, on=True,
               act=lambda c: (isolate.show(c.scene, c.view_layer), props.tag_viewports(c)))]


def group_status(scene, g):
    s = g.scale
    text = nav_status(scene, g) or status_text(scene)
    act = None
    from . import content
    news = content.new_summary()
    if news:                                      # "3 NEW MATERIALS · EEVEE · …"; a click says "seen"
        text = news.upper() + " · " + text
        act = lambda c: _op("plinth.library_seen")
    w = g.measure(text, MONO * s) * 1.35         # UI-font measure, mono + spacing is wider
    return [El("status", "status", (g.w - g.right - EDGE * s - w, 8 * s, w, 14 * s), text, act=act)]


# --- In-viewport labels (behind the floating groups) ----------------------------------

def scene_labels(scene, g):
    s = g.scale
    els = []
    if g.frame is not None and scene.camera is not None and "plinth_shot" in scene.camera:
        cam = scene.camera
        w, h = cam.get("plinth_aspect", (4, 5))
        text = "%s · %dMM · %d:%d" % (studio.shot_label(cam).upper(), round(cam.data.lens), w, h)
        fx, fy, fw, fh = g.frame
        els.append(El("frame_label", "frame_label", (fx, fy + fh + 6 * s, fw, 14 * s), text))
    if scene.plinth.step in {"STUDIO", "LIGHT", "CAMERA"} and g.targets:
        for name, (tx, ty) in g.targets.items():
            tgt = bpy.data.objects.get(name)
            if tgt is None:
                continue
            r = 30 * s
            els.append(El("target_" + name, "target", (tx - r / 2, ty - r / 2, r, r),
                          studio.target_label(tgt).upper(), on=scene.plinth.active_target == name,
                          act=(lambda c, t=tgt: _click_target(c, t)),
                          drag_xy=(lambda c, x, y, t=tgt: _drag_target(c, t, x, y))))
    if scene.plinth.step == "MATERIAL" and g.decals:
        from . import decals
        for decal_id, (dx, dy) in g.decals.items():
            owner, slot, rec = decals.owner(decal_id)
            if rec is None:
                continue
            r = 30 * s
            els.append(El("decalpin_" + decal_id, "target", (dx - r / 2, dy - r / 2, r, r),
                          ("DECAL · " + decals.label(rec)).upper()[:22],
                          on=scene.plinth.active_decal == decal_id,
                          act=_set(scene.plinth, "active_decal", decal_id),
                          drag_xy=(lambda c, x, y, o=owner, d=decal_id, k=slot:
                                   (decals.drag_to(c, o, d, x, y, k),
                                    setattr(c.scene.plinth, "active_decal", d)))))
    if scene.plinth.step == "LIGHT" and g.lights:
        for name, (lx, ly) in g.lights.items():
            r = 26 * s
            els.append(El("point_" + name, "lightpoint", (lx - r / 2, ly - r / 2, r, r),
                          display(name).upper(),
                          on=scene.plinth.active_light == name,
                          act=_set(scene.plinth, "active_light", name)))
    if scene.plinth.step == "LIGHT" and g.sun is not None:       # 0.9.1: the clear sky's sun
        sx, sy = g.sun
        r = 34 * s
        els.append(El("sun_handle", "sun", (sx - r / 2, sy - r / 2, r, r), "SUN",
                      on=scene.plinth.active_light == hdri.CHIP,
                      act=_set(scene.plinth, "active_light", hdri.CHIP),
                      drag_xy=lambda c, x, y: hdri.drag_sun(c, x, y)))
    if scene.plinth.step == "CAMERA" and g.cameras:
        for name, (cx, cy) in g.cameras.items():
            cam = bpy.data.objects.get(name)
            if cam is None:
                continue
            r = 26 * s
            els.append(El("campoint_" + name, "lightpoint", (cx - r / 2, cy - r / 2, r, r),
                          studio.shot_label(cam).upper(), on=scene.camera == cam,
                          act=(lambda c, cam=cam: _pick_shot(c, cam, look=False))))
    return els


def _click_target(context, target):
    """Click a target icon: it becomes active, and the light (Light step) or the
    shot (Camera step) aims at it."""
    scene = context.scene
    scene.plinth.active_target = target.name
    if scene.plinth.step == "LIGHT":
        light = studio.active_light(scene)
        if light is not None:
            studio.aim_at(light, target)
    elif scene.plinth.step == "CAMERA" and scene.camera is not None and "plinth_shot" in scene.camera:
        studio.aim_at(scene.camera, target)


def _drag_target(context, target, x, y):
    from bpy_extras import view3d_utils
    region, rv3d = context.region, context.region_data
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, (x, y))
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, (x, y))
    studio.move_target_to_ray(context.scene, context.evaluated_depsgraph_get(), target,
                              origin, direction)
    context.scene.plinth.active_target = target.name


# --- Assembly -------------------------------------------------------------------------

def _nav_box(g):
    return (g.w - g.right - g.nav, g.h - g.top - NAV_H * g.scale, g.nav, NAV_H * g.scale)


def build(scene, g, products=()):
    """Every element for this viewport, in painter's order (last = on top)."""
    products = list(products)
    reserved = [_nav_box(g)] if g.nav else []
    visible = (g.left, 0, g.w - g.left - g.right, g.h - g.top)
    placed, out = [], []
    groups = [lambda: group_pill(scene, g), lambda: group_dock(scene, g),
              lambda: group_card(scene, g, products), lambda: group_engine(scene, g),
              lambda: group_light_chips(scene, g), lambda: group_hidden(scene, g),
              lambda: group_nav_hint(scene, g),
              lambda: group_status(scene, g), lambda: group_view(scene, g),
              lambda: group_gizmo(scene, g)]
    if uistate.page_open(scene.plinth.step):
        groups[1], groups[2] = groups[2], groups[1]      # an open page wins; the bar steps aside
    from . import browser
    if browser.is_open() and scene.plinth.step == "MATERIAL":
        card_x = g.w - g.right - (g.nav or 0) - EDGE * g.scale - CARD_W * g.scale
        at = next(i for i, f in enumerate(groups) if f.__code__.co_names.count("group_card"))
        groups.insert(at, lambda: browser.group_browser(scene, g, products, card_x))
    for make in groups:
        els = make()
        if not els:
            continue
        box = bbox(els)
        inside = (box[0] >= visible[0] - 0.5 and box[1] >= visible[1] - 0.5
                  and box[0] + box[2] <= visible[0] + visible[2] + 0.5
                  and box[1] + box[3] <= visible[1] + visible[3] + 0.5)
        if not inside or any(overlaps(box, b) for b in placed + reserved):
            continue
        placed.append(box)
        out.append(els)
    els = scene_labels(scene, g) + [e for els in out for e in els]
    els += group_menu(scene, g, els)
    return els + focus_elements(els, g)


KNOB_GRAB = 12        # px: the knob's halo; pressing within it grabs the knob


def drag_fraction(el, x, offset=0.0):
    tx, _ty, tw, _th = el.track
    return 0.0 if tw <= 0 else max(0.0, min(1.0, (x + offset - tx) / tw))


def grab_offset(el, x, scale=1.0):
    """Pressing on a slider's knob grabs it where it is (no jump to the cursor):
    the offset to add to the mouse x while dragging. Elsewhere on the track the
    value jumps to the press, as before."""
    tx, _ty, tw, _th = el.track
    knob = tx + tw * el.value
    return knob - x if abs(knob - x) <= KNOB_GRAB * scale else 0.0
