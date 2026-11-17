# SPDX-License-Identifier: GPL-3.0-or-later
"""Edit pages (REDESIGN.md §3): every step's full settings on the card itself.

Each step's "Edit …" link flips the card to a page: back, title, three tabs,
at most six rows per tab, then Reset and Done. This module says what is on
each page as rows (plain data with actions); hud.py lays rows out and
overlay.py paints them. Like the cards, building a page never writes data.

Row kinds, beyond the card's slider/chips/shapes:
  ("strip", id, label, frac, shown, setter, colours, num)  24 coloured segments
  ("palette", id, label, [(id, name, srgb, on, act)])        6-column swatches
  ("targetseg", [(id, label, srgb, on, act)])                which colour to edit
  ("hexrow", id, hex, commit)                                 + "Type a value…"
  ("tiles", id, label, [(id, name, pattern, on, act)])       3-column pattern tiles
  ("switch", id, label, help, on, act)                        whole row toggles
  ("segrow", id, label, [(id, label, on, act)])               equal cells
  ("list", id, [(id, name, meta, aspect, on, act, active)])  shots with switches
  ("path", id, label, path, preview, act)                     folder + file preview
  ("note", text)
"""

import math

import bpy

from . import bake, colours, engine, gobos, hdri, hud, library, materials, shoot, studio, uistate
from .hud import _frac, _lerp, _log_frac, _exp, _set

MAX_ROWS = 6


class Page:
    __slots__ = ("key", "title", "tag", "tabs", "reset_label", "reset", "note")

    def __init__(self, key, title, tag, tabs, reset_label, reset):
        self.key, self.title, self.tag = key, title, tag
        self.tabs = tabs                   # [(name, rows)] — exactly three
        self.reset_label, self.reset = reset_label, reset

    def current_tab(self):
        names = [n for n, _r in self.tabs]
        tab = uistate.tabs.get(self.key)
        return tab if tab in names else names[0]

    def rows(self):
        tab = self.current_tab()
        return next(r for n, r in self.tabs if n == tab)


def _set_tab(key, tab):
    def act(context):
        uistate.tabs[key] = tab
    return act


def _sel(fn, *args, **kw):
    """An action that calls fn(*args, **kw) and ignores the context."""
    return lambda context: fn(*args, **kw)


# --- Colour rows (shared) -------------------------------------------------------------

def colour_rows(key, rgb_linear, set_linear, palette, palette_label, gel=None):
    """Palette, Hue / Saturation / Brightness strips and the hex row for one
    colour. ``set_linear(context, rgb, name)``: name is the swatch picked."""
    srgb = colours.srgb(rgb_linear)
    h, s, v = colours.hsv(srgb)
    # Black has no hue and grey no saturation: dragging Brightness to 0 must
    # not lose the colour (it came back grey, or the card's slider stuck).
    h, s = uistate.remembered_hue(key, rgb_linear, h, s, v)

    def set_srgb(c, rgb, name=None, hs=None):
        linear = colours.linear_rgb(rgb)
        hh, ss, _vv = colours.hsv(rgb)
        uistate.remember_hue(key, *(hs or (hh, ss)), linear)
        set_linear(c, linear, name)

    swatches = []
    for name, hx in palette:
        rgb = colours.hex_to_rgb(hx)
        on = (gel == name) if gel is not None else colours.same(rgb, srgb)
        swatches.append(("sw_%s_%s" % (key, hud._slug(name)), name, rgb, on,
                         (lambda c, rgb=rgb, name=name: set_srgb(c, rgb, name))))

    def strip(ch, label, value, shown):
        def setter(c, f, ch=ch):
            hh, ss, vv = h, s, v
            f = max(0.0, min(1.0, f))
            if ch == "h":
                hh = f
            elif ch == "s":
                ss = f
            else:
                vv = f
            set_srgb(c, colours.from_hsv(hh, ss, vv), hs=(hh, ss))
        return ("strip", "%s_%s" % (key, ch), label, value, shown, setter,
                colours.strip_colours(ch, h, s, v), (0, 360 if ch == "h" else 100, "lin"))

    def commit_hex(c, text):
        from .textedit import parse_hex
        rgb = parse_hex(text)
        if rgb is None:
            return False
        set_srgb(c, rgb)
        return True

    return [("palette", "pal_" + key, palette_label, swatches),
            strip("h", "Hue", h, "%d°" % round(h * 360)),
            strip("s", "Saturation", s, "%d%%" % round(s * 100)),
            strip("v", "Brightness", v, "%d%%" % round(v * 100)),
            ("hexrow", "hex_" + key, colours.rgb_to_hex(srgb), commit_hex)]


# --- Studio ---------------------------------------------------------------------------

SCENE_CAT = "scene_category"           # uistate.tabs[...]: the Items tab's category


def page_scene(scene, key):
    """Edit studio while the product is in a scene: Place (which scene, Turn,
    Detail, Variation), Look (Time of day, Weather, Wind) and Items (each
    category's Amount, Size and Spread, and a Clearing round the product)."""
    from . import kits, sceneenv, scenes
    every = scenes.available()
    t = scenes.turn(scene)
    place = [("grid", "Scene", [("scenepick_" + k, v["label"], k == key,
                                 (lambda c, k=k: hud._op("plinth.set_scene", place=k)))
                                for k, v in every.items() if v.get("ready", True)] +
              [("scenepick_STUDIO", "Studio", False, lambda c: hud._op("plinth.set_scene", place=""))]),
             ("slider", "scene_turn", "Turn", t / 360.0, "%d°" % round(t),
              lambda c, f: scenes.set_turn(c.scene, _lerp(0.0, 360.0, f)), (0, 360, "lin"))]
    kinds = dict(scenes.KINDS)
    if not scenes.has_detail(key):                # photo scenes: just the place
        place.append(("note", every[key]["description"]))
        return Page("STUDIO", "Scene", kinds[every[key]["kind"]].upper(), [("Scene", place)],
                    "Back to the studio", lambda c: hud._op("plinth.set_scene", place=""))
    now = scenes.detail(scene)
    cfg = scenes.settings(scene, key) or sceneenv.defaults(scenes.SCENES[key])
    place.append(("grid", "Detail", [("detailpick_" + k, lab, k == now,
                                      (lambda c, k=k: hud._op("plinth.scene_detail", level=k)))
                                     for k, lab, _d in scenes.DETAILS]))
    seed = int(cfg.get("seed", 0))
    place.append(("buttons", "variation", "Variation", "Layout %d" % (seed + 1) if seed else "The original layout",
                  [("scene_shuffle", "Shuffle", lambda c: hud._op("plinth.scene_shuffle")),
                   ("scene_original", "Original", lambda c: hud._op("plinth.scene_shuffle", original=True))]))
    place.append(("note", every[key]["description"]))
    state = scenes.kit_state(key)
    if state == "ready":
        place.append(("note", "Scanned assets: the %s" % kits.kit_entry(key)["label"]))
    elif state == "missing":
        place.append(("note", "The %s's files are missing: install its pack again"
                      % kits.kit_entry(key)["label"]))
    else:
        place.append(("note", "Scanned ground maps built in. A scene kit adds scanned rocks, plants and props"))
    spec = scenes.SCENES[key]
    times, weathers = sceneenv.allowed(spec)
    short = {"GOLDEN": "Golden", "OVERCAST": "Overcast", "WET": "Wet"}
    look = [("segrow", "scene_time", "Time of day",
             [("time_" + k, short.get(k, lab), k == cfg["time"],
               (lambda c, k=k: scenes.set_setting(c.scene, time=k)))
              for k, lab, _d in sceneenv.TIMES if k in times]),
            ("note", next(d for k, _l, d in sceneenv.TIMES if k == cfg["time"]))]
    if len(weathers) > 1:
        look += [("segrow", "scene_weather", "Weather",
                  [("weather_" + k, short.get(k, lab), k == cfg["weather"],
                    (lambda c, k=k: scenes.set_setting(c.scene, weather=k)))
                   for k, lab, _d in sceneenv.WEATHERS if k in weathers]),
                 ("note", next(d for k, _l, d in sceneenv.WEATHERS if k == cfg["weather"]))]
    if spec.get("sky", {}).get("wind", 0.0) or cfg["wind"]:
        look.append(("slider", "scene_wind", "Wind", cfg["wind"], "%d%%" % round(cfg["wind"] * 100),
                     lambda c, f: scenes.set_setting(c.scene, wind=f), (0, 100, "lin")))
    cats = scenes.categories_in_use(scene) or list(sceneenv.CATEGORIES)
    cat = uistate.tabs.get(SCENE_CAT)
    cat = cat if cat in dict(cats) else cats[0][0]
    vals = cfg["cats"][cat]
    r = scenes._r(scene)

    def setter(name, lo, hi, log):
        conv = _exp if log else _lerp
        return lambda c, f: scenes.set_category(c.scene, cat, **{name: conv(lo, hi, f)})
    items = [("grid", "Items", [("itemcat_" + k, lab, k == cat, (lambda c, k=k: uistate.tabs.__setitem__(SCENE_CAT, k)))
                                for k, lab in cats]),
             ("slider", "scene_amount", "Amount", _frac(0.0, 2.0, vals["amount"]),
              "%d%%" % round(vals["amount"] * 100), setter("amount", 0.0, 2.0, False), (0, 200, "lin")),
             ("slider", "scene_size", "Size", _log_frac(0.3, 3.0, vals["size"]), "%.2f×" % vals["size"],
              setter("size", 0.3, 3.0, True), (0.3, 3.0, "log")),
             ("slider", "scene_spread", "Spread", _log_frac(0.5, 3.0, vals["spread"]), "%.2f×" % vals["spread"],
              setter("spread", 0.5, 3.0, True), (0.5, 3.0, "log")),
             ("slider", "scene_clearing", "Clearing", _frac(0.0, 12.0, cfg["clearing"]),
              "%.1f m" % (cfg["clearing"] * r), lambda c, f: scenes.set_setting(c.scene, clearing=_lerp(0.0, 12.0, f)),
              (0, 12, "lin")),
             ("note", "Amount, Size and Spread move every %s at once; Clearing keeps all of them back from the "
                      "product" % dict(cats)[cat].lower())]
    return Page("STUDIO", "Scene", kinds[every[key]["kind"]].upper(), [("Place", place[:6]), ("Look", look[:6]),
                                                                        ("Items", items)],
                "Back to the studio", lambda c: hud._op("plinth.set_scene", place=""))


STUDIO_COLOUR_TARGET = "STUDIO:colour"      # uistate.tabs[...]: "BACKDROP" or "BACKGROUND"
BACKGROUND_PALETTE = [("White", "#ffffff")] + colours.STUDIO[1:]


def studio_colour_rows(scene):
    """Studio › Colour: the backdrop's colour, or the background (the world
    colour seen past the backdrop: an endless floor's horizon, Infinity
    white's surround, and what reflections see where there are no lights)."""
    bsdf = library._backdrop_bsdf()
    world = hdri.studio_colour(scene)
    world = tuple(world[:3]) if world is not None else tuple(hdri.STUDIO_COLOUR[:3])
    target = uistate.tabs.get(STUDIO_COLOUR_TARGET, "BACKDROP")
    if bsdf is None:
        target = "BACKGROUND"
    rows = []
    if bsdf is not None:
        sock = bsdf.inputs["Base Color"]
        rows.append(("targetseg", [
            ("target_backdrop", "Backdrop", colours.srgb(tuple(sock.default_value)), target == "BACKDROP",
             lambda c: uistate.tabs.__setitem__(STUDIO_COLOUR_TARGET, "BACKDROP")),
            ("target_background", "Background", colours.srgb(world), target == "BACKGROUND",
             lambda c: uistate.tabs.__setitem__(STUDIO_COLOUR_TARGET, "BACKGROUND"))]))
    if target == "BACKDROP":
        def set_backdrop(c, rgb, name=None, sock=sock):
            sock.default_value = (*rgb, 1.0)
        return rows + colour_rows("studio", tuple(sock.default_value), set_backdrop, colours.STUDIO, "Backdrop")

    def set_background(c, rgb, name=None):
        hdri.set_studio_colour(c.scene, rgb)
        library._tag(c.scene.world, "world", False)      # yours now: the next look leaves it alone
    return rows + colour_rows("world", world, set_background, BACKGROUND_PALETTE, "Background")


def page_studio(scene, products):
    bd = bpy.data.objects.get(studio.BACKDROP)
    if bd is None:
        return None
    from . import scenes
    if scenes.current(scene):
        return page_scene(scene, scenes.current(scene))
    radius = 0.5 * hud._rig_scale(scene)
    mod = bd.modifiers.get(studio.COVE_GN)
    idents = {k: i for k, _l, i in studio.backdrop_inputs(bd)}
    form = studio.current_form(bd)
    base = studio.cove_defaults(radius, form)
    labels = {"width": "Width", "depth": "Depth", "curve": "Curve", "height": "Wall height", "corner": "Corner"}
    shape = [("grid", "Form", [("form_" + k, v[0], k == form, (lambda c, k=k: hud._op("plinth.set_form", form=k)))
                               for k, v in studio.FORMS.items()])]
    for key, label in labels.items():
        if mod is None or key not in idents or key not in base or (form == "FLOOR" and key in ("curve", "height")):
            continue
        value = studio._get_input(mod, idents[key])
        lo_m, hi_m = 0.25 * base[key], 2.5 * base[key]
        shape.append(("slider", "cove_" + key, label, _frac(lo_m, hi_m, value), "%.2f m" % value,
                      (lambda c, f, key=key, lo_m=lo_m, hi_m=hi_m:
                       studio.set_backdrop_shape(bd, **{key: _lerp(lo_m, hi_m, f)})),
                      (lo_m, hi_m, "lin")))
    walls = []
    if studio.FORMS[form][2] == studio.ROOM_GN and mod is not None:
        def flag(key):
            return studio._get_input(mod, idents[key]) > 0.5 if key in idents else False

        for key, label in (("back", "Back wall"), ("left", "Left wall"), ("right", "Right wall"),
                           ("front", "Front wall")):
            if key in idents:
                walls.append(("switch", "wall_" + key, label, "", flag(key),
                              _sel(studio.set_backdrop_shape, bd, **{key: 0.0 if flag(key) else 1.0})))
        if "slant" in idents:
            walls.append(("switch", "slant", "Slanted corners", "Cut at 45 degrees instead of rounded",
                          flag("slant"), _sel(studio.set_backdrop_shape, bd, slant=0.0 if flag("slant") else 1.0)))
        if "roof" in idents:
            roof = studio._get_input(mod, idents["roof"])
            walls.append(("slider", "roof", "Dome roof", roof, "%d%%" % round(roof * 100),
                          lambda c, f: studio.set_backdrop_shape(bd, roof=f), (0, 1, "lin")))

    colour = studio_colour_rows(scene)

    fl = studio.floor_settings(bd)
    ps = scene.plinth
    fade = ([("switch", "fade", "Fade the floor edge", "Soft horizon instead of a hard edge", fl["fade"],
              _sel(studio.set_floor, bd, fade=not fl["fade"]))] if form == "SWEEP" else [])
    floor = [
        ("slider", "reflection", "Reflection", fl["reflection"], "%.2f" % fl["reflection"],
         lambda c, f: studio.set_floor(bd, reflection=f), (0, 1, "lin")),
        ("slider", "roughness", "Roughness", fl["roughness"], "%.2f" % fl["roughness"],
         lambda c, f: studio.set_floor(bd, roughness=f), (0, 1, "lin")),
    ] + fade + [
        ("switch", "catcher", "Floor shadow on transparent shots", "", ps.shadow_catcher,
         _set(ps, "shadow_catcher", not ps.shadow_catcher)),
    ]
    look = library.all_presets().get(ps.last_look, {}).get("label")
    tabs = [("Shape", shape)] + ([("Walls", walls)] if walls else []) + [("Colour", colour), ("Floor", floor)]
    return Page("STUDIO", "Studio", studio.FORMS[form][0].upper(), tabs,
                ("Reset to " + look if len(look) <= 10 else "Reset look") if look else "Reset studio",
                lambda c: studio.reset_backdrop(c.scene, c.scene.plinth.last_look))


# --- Light ----------------------------------------------------------------------------

def page_light(scene, products):
    if scene.plinth.active_light == hdri.CHIP:
        return page_hdri(scene)
    light = studio.active_light(scene)
    if light is None:
        return None
    data = light.data
    sc = hud._rig_scale(scene)
    kelvin = studio.rgb_to_kelvin(tuple(data.color))
    gel = studio.light_gel(data)
    warm = [colours.srgb(studio.kelvin_to_rgb(2000 + (i + 0.5) / 24 * 8000)) for i in range(24)]

    def set_kelvin(c, f):
        studio.set_light_colour(data, studio.kelvin_to_rgb(_lerp(2000, 10000, f)))

    def set_colour(c, rgb, name=None):
        studio.set_light_colour(data, rgb, gel=name)

    rows = colour_rows("light", tuple(data.color), set_colour, colours.GELS, "Gels", gel=gel or "")
    colour = [("strip", "warmth", "Warmth", _frac(2000, 10000, kelvin),
               "%s" % (gel if gel else "%d K" % kelvin), set_kelvin, warm, (2000, 10000, "lin")),
              rows[0], rows[4],
              ("note", "Picking a gel overrides warmth; dragging warmth clears the gel.")]

    s_lo, s_hi = 0.05 * sc, 6 * sc
    has_height = getattr(data, "shape", "") in {"RECTANGLE", "ELLIPSE"}
    spread = math.degrees(getattr(data, "spread", math.pi))
    shape = [
        ("slider", "width", "Width", _frac(s_lo, s_hi, data.size), "%.2f m" % data.size,
         lambda c, f: setattr(data, "size", _lerp(s_lo, s_hi, f)), (s_lo, s_hi, "lin")),
        ("slider", "height", "Height" if has_height else "Height (strip and ellipse only)",
         _frac(s_lo, s_hi, getattr(data, "size_y", data.size)),
         "%.2f m" % getattr(data, "size_y", data.size),
         (lambda c, f: setattr(data, "size_y", _lerp(s_lo, s_hi, f))) if has_height else None,
         (s_lo, s_hi, "lin") if has_height else None),
        ("slider", "spread", "Spread", _frac(1, 180, spread), "%d°" % round(spread),
         lambda c, f: setattr(data, "spread", math.radians(_lerp(1, 180, f))), (1, 180, "lin")),
        ("switch", "visible", "Visible in shots", "Show the light itself in frame (Cycles)",
         studio.light_visible(light), _sel(studio.set_light_visible, light, not studio.light_visible(light))),
    ]

    current = gobos.current_gobo(data)
    p = gobos.gobo_params(data)
    has = current != "NONE"

    def param(key, lo, hi, kind="lin"):
        to = _exp if kind == "log" else _lerp
        return lambda c, f: gobos.set_gobo_params(data, **{key: to(lo, hi, f)})
    gobo = [
        ("tiles", "gobo", "Pattern", [("gobo_" + k, v["label"], k, k == current,
                                       _sel(gobos.set_gobo, data, k))
                                      for k, v in gobos.GOBO_PRESETS.items()]),
        ("slider", "gobo_scale", "Scale", _log_frac(0.25, 4.0, p["scale"]), "%.1f×" % p["scale"],
         param("scale", 0.25, 4.0, "log") if has else None, (0.25, 4.0, "log") if has else None),
        ("slider", "gobo_rotation", "Rotation", _frac(0, 180, p["rotation"]), "%d°" % round(p["rotation"]),
         param("rotation", 0, 180) if has else None, (0, 180, "lin") if has else None),
        ("slider", "gobo_softness", "Edge softness", p["softness"], "%.2f" % p["softness"],
         param("softness", 0, 1) if has else None, (0, 1, "lin") if has else None),
        ("note", "A pattern turns the light into a small projector; None gives it back its size."),
    ]
    return Page("LIGHT", hud.display(studio.light_label(light)),
                studio.LIGHT_SHAPES[studio.light_shape(data)]["label"].upper(),
                [("Colour", colour), ("Shape", shape), ("Gobo", gobo),
                 ("Target", target_rows(scene, light, products))],
                "Reset light", lambda c: studio.reset_light(c.scene, light, c.scene.plinth.last_look))


def target_rows(scene, obj, products):
    """The Target tab (Edit light, Edit shot): which target this light or shot
    aims at, and exact X / Y / Z for that target, plus Centre on product.
    Dragging the target's icon in the view slides it over surfaces; these place
    it anywhere, even in mid-air."""
    rows = [hud.aim_row(scene, obj)]
    tgt = studio.aim_target(scene, obj)
    if tgt is None:
        return rows + [("note", "Pick or add a target to place it.")]
    ranges = studio.target_range(scene)
    for axis in ("x", "y", "z"):
        lo, hi = ranges[axis]
        value = getattr(tgt.location, axis)
        rows.append(("slider", "target_" + axis, axis.upper(), _frac(lo, hi, value), "%+.2f m" % value,
                     (lambda c, f, axis=axis, lo=lo, hi=hi, tgt=tgt:
                      studio.set_target_position(tgt, **{axis: _lerp(lo, hi, f)})), (lo, hi, "lin")))
    centre = studio.product_centre(products)
    rows.append(("buttons", "target_actions", "", "", [
        ("target_centre", "Centre on product",
         (lambda c, tgt=tgt, p=tuple(centre): studio.set_target_position(tgt, x=p[0], y=p[1], z=p[2]))
         if centre is not None else None)]))
    rows.append(("note", "Editing %s. Drag its icon in the view to slide it over surfaces."
                 % studio.target_label(tgt)))
    return rows[:MAX_ROWS]


def page_hdri(scene):
    st = hdri.settings(scene)
    on = st["on"]
    if st["key"] == "CUSTOM":
        about = "Your own: " + st["path"]
    elif st["key"] == hdri.SKY:
        about = "A physical sky: move the sun and its colour follows (blue at noon, gold when low)."
    else:
        about = hdri.DESCRIPTIONS.get(st["key"], "Lights and reflects in the product; "
                                      "the camera still sees the studio.")
    image = [("grid", "Image", hud.hdri_items(st, own=True)), ("note", about)]
    background = ("switch", "hdri_background", "Show behind the studio",
                  "The camera sees the image instead of the studio colour", st["background"],
                  _sel(hdri.set_hdri, scene, background=not st["background"]) if on else None)
    sun = st.get("sky")
    if sun is not None:
        adjust = hud.hdri_sliders(st) + [
            ("slider", "sun_softness", "Sun softness", hud._log_frac(0.1, 10.0, max(0.1, sun["softness"])),
             "%.1f°" % sun["softness"], lambda c, f: hdri.set_sun(c.scene, softness=hud._exp(0.1, 10.0, f)),
             (0.1, 10.0, "log")),
            ("slider", "sun_haze", "Haze", sun["haze"] / 10.0, "%.1f" % sun["haze"],
             lambda c, f: hdri.set_sun(c.scene, haze=hud._lerp(0.0, 10.0, f)), (0, 10, "lin")),
            background]
    else:
        adjust = hud.hdri_sliders(st) + [
            background,
            ("note", "Turn the image to move its brightest light. The backdrop still blocks "
                     "light from behind it."),
        ]
    if on:
        def set_tint(c, rgb, name=None):
            hdri.set_hdri(c.scene, tint=rgb)
        colour = colour_rows("hdri", tuple(st["tint"][:3]), set_tint, colours.GELS, "Tint")
    else:
        colour = [("note", "Pick an image first.")]
    if sun is not None:
        reset = lambda c: (hdri.set_hdri(c.scene, strength=hdri.SKY_STRENGTH, background=False, tint=(1, 1, 1)),
                           hdri.set_sun(c.scene, **hdri.SKY_DEFAULTS))
    else:
        reset = (lambda c: hdri.set_hdri(c.scene, strength=1.0, rotation=0.0, background=False,
                                         tint=(1, 1, 1))) if on else None
    return Page("HDRI", "HDRI", st["label"].upper(),
                [("Image", image), ("Adjust", adjust), ("Colour", colour)], "Reset HDRI", reset)


# --- Material -------------------------------------------------------------------------
# Paint follows the canvas exactly; other types put their colours on Colour
# and their remaining sections on two more tabs, in order, six rows at most.

PAINT_TABS = [("Flake", ["Metallic flake", "Flake size", "Flake sparkle", "Flip amount", "Pearl"]),
              ("Finish", ["Base roughness", "Clearcoat", "Clearcoat roughness", "Orange peel",
                          "Orange peel scale"])]
SHORT = {"Clearcoat roughness": "Coat roughness", "Orange peel scale": "Peel scale"}
TARGET_LABELS = {"Base colour": "Base", "Flip colour": "Flip", "Flake colour": "Flake",
                 "Colour": "Base", "Edge tint": "Edge", "Tint": "Tint",
                 "Absorption colour": "Absorption", "Dust colour": "Dust", "Backing colour": "Backing"}
# Tyre's sections are Tread and Surface (its colours are on the Colour tab).


def material_tabs(type_key):
    """[(tab, [input names])] for a material type's two non-colour tabs."""
    if type_key == "PAINT":
        return PAINT_TABS
    spec = materials.TYPES[type_key]
    by_section = {}                                # in order of first appearance
    for name, kind, _d, _lo, _hi, section in materials.controls(type_key):
        if kind != "colour":
            by_section.setdefault(section, []).append(name)
    sections = list(by_section.items())
    while len(sections) > 2:                       # merge the smallest neighbours
        sizes = [len(a[1]) + len(b[1]) for a, b in zip(sections, sections[1:])]
        i = sizes.index(min(sizes))
        sections[i:i + 2] = [(sections[i][0], sections[i][1] + sections[i + 1][1])]
    if len(sections) == 1:                         # one section: split it in two
        name, inputs = sections[0]
        half = (len(inputs) + 1) // 2
        sections = [(name, inputs[:half]), ("Details", inputs[half:])]
    return sections


def page_material(scene, products):
    if not products:
        return None
    obj = products[0]
    mat = materials.part_material(obj)
    if mat is None or mat.get("plinth_type") not in materials.TYPES:
        return page_own_material(obj, _slot_material(obj))
    type_key = mat["plinth_type"]
    spec = materials.TYPES[type_key]
    inputs = materials.type_inputs(type_key)
    key = "MATERIAL:" + type_key
    colour_inputs = [n for n, kind, *_ in materials.controls(type_key) if kind == "colour"]
    target_key = key + ":target"
    target = uistate.tabs.get(target_key)
    target = target if target in colour_inputs else (colour_inputs[0] if colour_inputs else None)
    colour = []
    if target is not None:
        if len(colour_inputs) > 1:
            colour.append(("targetseg", [
                ("target_" + hud._slug(n), TARGET_LABELS.get(n, n),
                 colours.srgb(materials.get_input(obj, n)), n == target,
                 (lambda c, n=n: uistate.tabs.__setitem__(target_key, n)))
                for n in colour_inputs]))

        def set_colour(c, rgb, name=None, target=target):
            materials.set_input(obj, target, (*rgb, 1.0))
        rows = colour_rows("mat", tuple(materials.get_input(obj, target)), set_colour, colours.PAINT, "")
        colour += rows
    else:
        colour.append(("note", "This material has no colour to pick."))

    def slider(name):
        _n, kind, _d, lo, hi, _sec = inputs[name]
        value = materials.get_input(obj, name)
        if kind == "bool":
            return ("switch", "mat_" + hud._slug(name), name, "Windows: thin. Lenses, crystal: solid",
                    bool(value), lambda c, v=bool(value): bpy.ops.plinth.glass_thin(thin=not v))
        to_value = _exp if kind == "log" else _lerp
        to_frac = _log_frac if kind == "log" else _frac
        shown = ("%.1f cm" % value) if kind == "log" else "%.2f" % value
        return ("slider", "mat_" + hud._slug(name), SHORT.get(name, name.replace(" (cm)", "")),
                to_frac(lo, hi, value), shown,
                (lambda c, f, name=name, lo=lo, hi=hi: materials.set_input(obj, name, to_value(lo, hi, f))),
                (lo, hi, "log" if kind == "log" else "lin"))

    tabs = [("Colour", colour)] + [(name, [slider(n) for n in names][:MAX_ROWS])
                                   for name, names in material_tabs(type_key)]
    if type_key == "TEXTURES":                     # no colour of its own: its maps instead
        tabs[0] = ("Textures", texture_rows(obj, mat))
    if type_key not in materials.NO_MAPPING:
        tabs.append(("Mapping", mapping_rows(obj)))
    current = materials.current_preset(obj)
    label = materials.PRESETS.get(current, {}).get("label")
    reset = (lambda c: materials.apply_preset(obj, current, keep_colour=False, fresh=True)) \
        if current else None
    return Page(key, hud._short(materials.part_label(obj, obj.active_material_index), 16),
                (label or "CUSTOM").upper(), tabs,
                ("Reset to " + label if len(label) <= 10 else "Reset preset") if label else "Reset", reset)


def mapping_rows(obj):
    """Material › Mapping (0.13): how patterns sit on this object. Triplanar
    projects from the world's three axes (no UVs needed); UV follows the
    part's own unwrap. Flip U / V and Turn mirror and rotate the pattern, per
    object, so two halves sharing a carbon material can weave opposite ways."""
    m = materials.mapping(obj)
    uv_ok = materials.has_uvs(obj)
    uv = m["mode"] == "UV"

    def set_(**kw):
        return lambda c: materials.set_mapping(obj, **kw)
    rows = [("segrow", "map_mode", "Projection", [
        ("map_WORLD", "Triplanar", not uv, set_(mode="WORLD")),
        ("map_UV", "UV" if uv_ok else "UV (none)", uv, set_(mode="UV") if uv_ok else None)]),
        ("switch", "map_flip_u", "Flip U", "Mirror left to right (across the car)" if not uv
         else "Mirror along the UVs' width", m["flip_u"], set_(flip_u=not m["flip_u"])),
        ("switch", "map_flip_v", "Flip V", "Mirror front to back" if not uv else "Mirror along the UVs' height",
         m["flip_v"], set_(flip_v=not m["flip_v"])),
        ("slider", "map_turn", "Turn", m["turn"] / 360.0, "%d°" % round(m["turn"]),
         lambda c, f: materials.set_mapping(obj, turn=_lerp(0.0, 360.0, f)), (0, 360, "lin"))]
    lo, hi = materials.UV_SIZE_RANGE
    size = m["size"] if m["size"] > 0 else 1.0
    rows.append(("slider", "map_size", "UV size" if uv else "UV size (UV only)", _log_frac(lo, hi, size),
                 "%.2f m" % size, (lambda c, f: materials.set_mapping(obj, size=_exp(lo, hi, f))) if uv else None,
                 (lo, hi, "log") if uv else None))
    rows.append(("note", "This object only: the other half can flip while sharing the material."
                 if uv_ok else "No UVs on this part: patterns project from three sides."))
    return rows


def texture_rows(obj, mat):
    """Material › Textures: one chip per map (lit when it has an image; click
    to pick one from the file or disk, or clear it), then find them
    automatically or from a folder."""
    from . import textures
    maps = materials.texture_maps(mat)
    chips = [("texmap_" + ch, textures.LABELS[ch], ch in maps,
              (lambda c, ch=ch: hud.open_menu(c, "texmap", ch, "texmap_" + ch)))
             for ch in textures.SHOWN]
    rows = [("grid", "Maps", chips),
            ("buttons", "texture_find", "", "", [
                ("tex_auto", "Find automatically", lambda c: hud._op("plinth.find_textures")),
                ("tex_folder", "From a folder…", lambda c: hud._op("plinth.texture_folder", "INVOKE_DEFAULT"))])]
    if maps:
        names = sorted({img.name for img in maps.values()})
        rows.append(("note", "%d map%s: %s" % (len(maps), "" if len(maps) == 1 else "s",
                                               hud._short(", ".join(names), 80))))
    else:
        rows.append(("note", "No maps yet. Click a map to pick an image from this file or disk, "
                             "or let Plinth look for the model's own."))
    return rows


# --- Your own materials (0.9) ------------------------------------------------------
# A part that wears its own material (imported, made by hand) is editable too:
# the inputs of the shader that feeds its output, read live from the node.
# Principled BSDF gets a curated list in plain words; a node group shows its
# own inputs with their own ranges. Nothing is rebuilt or replaced.

PRINCIPLED = [("Base Color", "Colour"), ("Emission Color", "Glow colour"), ("Coat Tint", "Coat tint"),
              ("Metallic", "Metallic"), ("Roughness", "Roughness"), ("Specular IOR Level", "Reflection"),
              ("Coat Weight", "Clearcoat"), ("Coat Roughness", "Coat roughness"),
              ("Transmission Weight", "Transmission"), ("IOR", "IOR"), ("Alpha", "Opacity"),
              ("Sheen Weight", "Sheen"), ("Subsurface Weight", "Subsurface"), ("Anisotropic", "Anisotropy"),
              ("Emission Strength", "Glow")]
OWN_RANGES = {"Emission Strength": (0.0, 50.0), "IOR": (1.0, 3.0)}


def _slot_material(obj):
    if obj is None or obj.type != "MESH" or not obj.material_slots:
        return None
    return obj.material_slots[obj.active_material_index].material


def own_controls(mat):
    """(shader node, [(socket, label, kind, lo, hi)]) for a material that isn't
    Plinth's; (None, []) when there is nothing to show. Read-only."""
    if mat is None or mat.node_tree is None or mat.get("plinth_type") in materials.TYPES:
        return None, []
    outs = materials.outputs(mat)
    link = outs[0].inputs["Surface"].links[0] if outs and outs[0].inputs["Surface"].links else None
    node = link.from_node if link is not None else None
    from . import decals
    while node is not None and node.name.startswith(decals.PREFIX) and "Below" in node.inputs:
        below = node.inputs["Below"].links                   # under the decal layers: the material
        node = below[0].from_node if below else None
    if node is None:
        return None, []
    found = []
    if node.type == "BSDF_PRINCIPLED":
        for name, label in PRINCIPLED:
            sock = node.inputs.get(name)
            if sock is None or sock.is_linked or not sock.enabled:
                continue
            kind = "colour" if sock.type == "RGBA" else "float"
            lo, hi = OWN_RANGES.get(name, (0.0, 1.0))
            found.append((sock, label, kind, lo, hi))
    else:
        for sock in node.inputs:
            if sock.is_linked or not sock.enabled or sock.type not in {"VALUE", "RGBA"} \
                    or not hasattr(sock, "default_value"):
                continue
            kind = "colour" if sock.type == "RGBA" else "float"
            lo = hi = None
            if kind == "float":
                prop = sock.bl_rna.properties["default_value"]
                lo, hi = max(prop.soft_min, -1000.0), min(prop.soft_max, 1000.0)
                item = None
                if node.type == "GROUP" and node.node_tree is not None:
                    item = next((i for i in node.node_tree.interface.items_tree
                                 if getattr(i, "identifier", None) == sock.identifier), None)
                if item is not None and hasattr(item, "min_value"):
                    lo, hi = max(item.min_value, -1000.0), min(item.max_value, 1000.0)
                if hi - lo > 100:
                    lo, hi = 0.0, max(1.0, float(sock.default_value) * 4 or 1.0)
            found.append((sock, sock.name, kind, lo, hi))
    return node, found


def page_own_material(obj, mat):
    """Edit page for the part's own material: Colour, Surface, More."""
    node, found = own_controls(mat)
    if not found:
        return None
    colours_ = [c for c in found if c[2] == "colour"]
    floats = [c for c in found if c[2] == "float"]
    key = "MATERIAL:OWN"
    target_key = key + ":target"
    names = [c[1] for c in colours_]
    target = uistate.tabs.get(target_key)
    target = target if target in names else (names[0] if names else None)
    colour = []
    if target is not None:
        sock = next(c[0] for c in colours_ if c[1] == target)
        if len(colours_) > 1:
            colour.append(("targetseg", [
                ("target_" + hud._slug(c[1]), hud._short(c[1], 10), colours.srgb(tuple(c[0].default_value)),
                 c[1] == target, (lambda cc, n=c[1]: uistate.tabs.__setitem__(target_key, n)))
                for c in colours_[:4]]))

        def set_colour(c, rgb, name=None, sock=sock):
            sock.default_value = (*rgb, 1.0)
        colour += colour_rows("own", tuple(sock.default_value), set_colour, colours.PAINT, "")
    else:
        colour.append(("note", "This material has no colour to pick."))

    def slider(c):
        sock, label, _kind, lo, hi = c
        value = float(sock.default_value)
        return ("slider", "own_" + hud._slug(label), hud._short(label, 22), _frac(lo, hi, value),
                "%.2f" % value, (lambda cc, f, sock=sock, lo=lo, hi=hi: setattr(sock, "default_value",
                                                                                _lerp(lo, hi, f))),
                (lo, hi, "lin"))
    surface = [slider(c) for c in floats[:MAX_ROWS]]
    more = [slider(c) for c in floats[MAX_ROWS:2 * MAX_ROWS - 1]]
    note = ("note", "Your own material: changes apply wherever it's used. Plinth presets "
                    "(Browse) give you more controls.")
    if not surface:
        surface = [note]
    else:
        more.append(note)
    return Page(key, hud._short(materials.part_label(obj, obj.active_material_index), 16), "OWN",
                [("Colour", colour), ("Surface", surface), ("More", more)], "Reset", None)


# --- Camera ---------------------------------------------------------------------------

def page_camera(scene, products):
    cam = scene.camera
    if cam is None or "plinth_shot" not in cam:
        return None
    dof = cam.data.dof
    fo = studio.focus_settings(cam)
    r = 0.5 * hud._rig_scale(scene)
    ortho = studio.is_ortho(cam)
    view = studio.shot_view(scene, cam)
    focus = [
        ("segrow", "projection", "View", [
            ("proj_PERSP", "Perspective", not ortho, _sel(studio.set_shot_projection, scene, cam, False)),
            ("proj_ORTHO", "Orthographic", ortho, _sel(studio.set_shot_projection, scene, cam, True))]),
        ("segrow", "views", "Look from", [
            ("view_" + k, label, view == k, _sel(studio.set_shot_view, scene, cam, k))
            for k, (label, _a, _e) in studio.VIEWS.items()]),
        ("switch", "blur", "Blur background", "", dof.use_dof,
         _sel(studio.set_depth_of_field, scene, cam, not dof.use_dof)),
        ("slider", "aperture", "Aperture", _log_frac(1.2, 22, dof.aperture_fstop),
         "f/%g" % round(dof.aperture_fstop, 1),
         lambda c, f: setattr(dof, "aperture_fstop", _exp(1.2, 22, f)), (1.2, 22, "log")),
        ("segrow", "focus_on", "Focus on", [
            ("focus_TARGET", "Main target", fo["mode"] == "TARGET",
             lambda c: studio.set_focus(c.scene, cam, mode="TARGET")),
            ("focus_NEAREST", "Nearest surface", fo["mode"] == "NEAREST",
             lambda c: studio.set_focus(c.scene, cam, mode="NEAREST"))]),
        ("slider", "focus_offset", "Focus offset", _frac(-4 * r, 4 * r, fo["offset"]),
         "%+.2f m" % fo["offset"],
         lambda c, f: studio.set_focus(c.scene, cam, offset=_lerp(-4 * r, 4 * r, f)), (-4 * r, 4 * r, "lin")),
    ]
    tt = studio.turntable_settings(cam)
    locked = cam.get("plinth_shot") == studio.TURNTABLE
    motion = [
        ("switch", "turntable", "Turntable",
         "This shot is a 360° loop" if locked else "Renders a 360° loop from this shot", tt["on"],
         None if locked else _sel(studio.set_turntable, scene, cam, on=not tt["on"])),
        ("slider", "frames", "Frames", _frac(8, 360, tt["frames"]), "%d" % tt["frames"],
         lambda c, f: studio.set_turntable(c.scene, cam, frames=_lerp(8, 360, f)), (8, 360, "lin")),
        ("segrow", "direction", "Direction", [
            ("dir_CW", "Clockwise", tt["clockwise"], _sel(studio.set_turntable, scene, cam, clockwise=True)),
            ("dir_CCW", "Anticlockwise", not tt["clockwise"],
             _sel(studio.set_turntable, scene, cam, clockwise=False))]),
        ("slider", "start_angle", "Start angle", _frac(0, 360, tt["start"]), "%d°" % round(tt["start"]),
         lambda c, f: studio.set_turntable(c.scene, cam, start=_lerp(0, 360, f) % 360.0), (0, 360, "lin")),
    ]
    items = []
    for shot in studio.list_shots(scene):
        aspect = tuple(shot.get("plinth_aspect", (4, 5)))
        enabled = bool(shot.get("plinth_enabled", True))
        items.append(("list_" + shot.name, studio.shot_label(shot),
                      "%d MM · %d:%d" % (round(shot.data.lens), *aspect), aspect, enabled,
                      (lambda c, shot=shot: hud._toggle_shot(shot)), shot == cam))
    shots = [("list", "shots", items),
             ("note", "Switches choose what Shoot renders. Rename, reorder and delete from a "
                      "shot's ⋯ in the bar.")]
    w, h = cam.get("plinth_aspect", (4, 5))
    return Page("CAMERA", studio.shot_label(cam), "%dMM · %d:%d" % (round(cam.data.lens), w, h),
                [("View", focus), ("Motion", motion), ("Shots", shots),
                 ("Target", target_rows(scene, cam, products))],
                "Reset shot", _sel(studio.reset_shot, scene, cam))


# --- Shoot ----------------------------------------------------------------------------

BAKE_BUTTON = {"NONE": "Bake lighting for Eevee", "BAKED": "Lighting baked · Bake again",
               "STALE": "Lighting changed · Bake again", "RUNNING": "Baking lighting…"}


def bake_row(scene):
    state = bake.lighting_state(scene, cached=True)
    act = None if state == "RUNNING" or studio.find_target(scene) is None else \
        (lambda c: bpy.ops.plinth.bake_lighting())
    return ("button", "bake_lighting", BAKE_BUTTON[state], act)


def gpu_row():
    """Cycles is on the CPU although there's a graphics card: one click fixes it
    (engine.check_gpu ran at start; nothing is refreshed while drawing)."""
    setup = engine.GPU_INFO.get("setup")
    if not setup:
        return None
    return ("button", "enable_gpu", "Use the GPU for Cycles",     # the card's name: Preferences
            lambda c: bpy.ops.plinth.enable_gpu())


def page_shoot(scene, products):
    s = scene.plinth

    def seg(prop, items, dim=()):
        return [("%s_%s" % (prop, k), label, getattr(s, prop) == k,
                 None if k in dim else _set(s, prop, k)) for k, label in items]

    jpeg = s.file_format == "JPEG"
    fmt = [
        ("segrow", "file_format", "File type", seg("file_format", [("PNG", "PNG"), ("JPEG", "JPEG"),
                                                                   ("OPEN_EXR", "EXR")])),
        ("segrow", "color_depth", "Colour depth" + (" (JPEG is 8-bit)" if jpeg else ""),
         seg("color_depth", [("8", "8-bit"), ("16", "16-bit")], dim=("16",) if jpeg else ())),
        ("segrow", "view_transform", "Colour look",
         seg("view_transform", [(k, engine.COLOUR_LOOKS[k]["label"])
                                for k in ("TRUE", "NATURAL", "PUNCHY", "STANDARD")])),
        ("switch", "save_mask", "Also save a mask", "A cut-out next to each transparent shot",
         s.save_mask, _set(s, "save_mask", not s.save_mask)),
    ]
    cam = scene.camera if scene.camera is not None and "plinth_shot" in scene.camera else None
    example = (shoot.file_name(cam, s.file_naming, shoot.product_name(scene)) if cam else "Hero") \
        + shoot.EXT.get(s.file_format, "")
    folder = [
        ("path", "output_dir", "Save to", s.output_dir, "e.g. " + example,
         lambda c: bpy.ops.plinth.choose_folder("INVOKE_DEFAULT")),
        ("chips", "File names", [("naming_" + k, label, s.file_naming == k, _set(s, "file_naming", k))
                                 for k, label in (("SHOT", "Shot"), ("PRODUCT", "Product + shot"),
                                                  ("DATE", "+ date"))]),
        ("switch", "open_when_done", "Open the folder when done", "", s.open_when_done,
         _set(s, "open_when_done", not s.open_when_done)),
    ]
    eng = [
        ("switch", "hybrid", "Work in Eevee, shoot in Cycles", "The quality scale picks the engine",
         s.hybrid, _set(s, "hybrid", not s.hybrid)),
        ("segrow", "device", "Render on", seg("device", [("GPU", "GPU"), ("CPU", "CPU")])),
        ("switch", "denoise", "Denoise", "Cycles: smooth out render noise", s.denoise,
         _set(s, "denoise", not s.denoise)),
        ("slider", "sample_boost", "Sample boost", _log_frac(0.25, 4.0, s.sample_boost),
         "%.2g×" % s.sample_boost, lambda c, f: setattr(s, "sample_boost", _exp(0.25, 4.0, f)),
         (0.25, 4.0, "log")),
        bake_row(scene),
        gpu_row() or ("note", "Quality presets set the samples; boost multiplies them. Baked "
                              "lighting adds bounce light and Glow light to Eevee."),
    ]
    n = len(hud.enabled_shots(scene))
    return Page("SHOOT", "Shoot", "%02d SHOTS" % n,
                [("Format", fmt), ("Folder", folder), ("Engine", eng)],
                "Reset output", reset_output)


OUTPUT_DEFAULTS = ("file_format", "color_depth", "view_transform", "save_mask", "file_naming",
                   "open_when_done", "hybrid", "device", "denoise", "sample_boost")


def reset_output(context):
    s = context.scene.plinth
    for prop in OUTPUT_DEFAULTS:
        s.property_unset(prop)


PAGES = {"STUDIO": page_studio, "LIGHT": page_light, "MATERIAL": page_material,
         "CAMERA": page_camera, "SHOOT": page_shoot}


def can_open(scene, products):
    """Whether this step has anything to edit yet (cheap: for the Edit link)."""
    step = scene.plinth.step
    if step == "STUDIO":
        return bpy.data.objects.get(studio.BACKDROP) is not None
    if step == "LIGHT":
        return studio.active_light(scene) is not None or scene.plinth.active_light == hdri.CHIP
    if step == "MATERIAL":
        products = list(products)
        mat = materials.part_material(products[0]) if products else None
        if mat is not None and mat.get("plinth_type") in materials.TYPES:
            return True
        return bool(products) and bool(own_controls(_slot_material(products[0]))[1])
    if step == "CAMERA":
        return scene.camera is not None and "plinth_shot" in scene.camera
    return True


def page_for(scene, products):
    """The open page for this step, or None (closed, or nothing to edit yet)."""
    step = scene.plinth.step
    if not uistate.page_open(step):
        return None
    return PAGES[step](scene, list(products))
