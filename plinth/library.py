# SPDX-License-Identifier: GPL-3.0-or-later
"""Studio preset library: whole looks you can apply, save and share.

A look is the whole studio (0.9): backdrop colour, finish (reflection,
roughness, edge fade) and shape, the world's colour or an HDRI, the Key/Rim/
Fill balance with their shape and place, any number of extra lights, the
colour look and a suggested Eevee preset. Every part is optional, so looks
from 0.7/0.8 (backdrop + three lights) still load and mean what they meant.

Values are stored *relative to the rig's scale*, so a look made around a ring
looks the same around a sofa: light power is a multiple of the light's default
power, softness a multiple of its default size, positions are in rig units.

What a look changes beyond the old schema it marks as its own (lights it
added, lights it moved, the floor, the cove, the world, the HDRI); the next
look that doesn't mention one of those puts it back to the studio default.
What you set yourself is never undone by a look that doesn't mention it.

User presets are small JSON files in the add-on's user folder.
"""

import json
import os
import re

import bpy
from mathutils import Vector

from . import engine, gobos, hdri, studio

ROOT = "plinth_look"                 # tag on things a look changed: {"floor", "shape", "world", "hdri"}
LOOK_LIGHT = "plinth_look_light"     # obj tag: a light a look added (the next look removes it)
LOOK_MOVED = "plinth_look_moved"     # obj tag: a rig light a look moved or reshaped
SHAPE_KEYS = ("width", "depth", "curve", "height", "corner")


def _light(power, softness, color, **more):
    return dict(power=power, softness=softness, color=color, **more)


def _extra(name, shape, power, softness, color, offset, gobo="NONE"):
    return {"name": name, "shape": shape, "power": power, "softness": softness, "color": color,
            "offset": offset, "gobo": gobo}


def _floor(reflection, roughness, fade=False):
    return {"reflection": reflection, "roughness": roughness, "fade": fade}


WHITE = (1.0, 1.0, 1.0)

BUILTIN = {
    "CAR": {
        "label": "Car studio",
        "description": "Dark studio with big softbox reflections that shape paint and glass",
        "colour": "NATURAL",
        "backdrop": (0.012, 0.012, 0.013, 1.0),
        "lights": {
            "Key": {"power": 1.3, "softness": 2.4, "color": (1.0, 1.0, 1.0)},
            "Rim": {"power": 1.1, "softness": 1.3, "color": (0.94, 0.97, 1.0)},
            "Fill": {"power": 0.5, "softness": 2.0, "color": (1.0, 1.0, 1.0)},
        },
    },
    "CLEAN": {
        "label": "Clean white",
        "description": "Bright, airy e-commerce look on a white sweep",
        "colour": "TRUE",
        "backdrop": (0.85, 0.85, 0.86, 1.0),
        "lights": {
            "Key": {"power": 1.0, "softness": 1.4, "color": (1.0, 0.98, 0.96)},
            "Rim": {"power": 0.6, "softness": 1.0, "color": (1.0, 1.0, 1.0)},
            "Fill": {"power": 1.6, "softness": 1.5, "color": (1.0, 1.0, 1.0)},
        },
    },
    "DARK": {
        "label": "Dark luxe",
        "description": "Moody and low-key, with a strong edge light",
        "colour": "PUNCHY",
        "backdrop": (0.02, 0.02, 0.022, 1.0),
        "lights": {
            "Key": {"power": 0.7, "softness": 0.6, "color": (1.0, 0.95, 0.88)},
            "Rim": {"power": 1.8, "softness": 0.7, "color": (0.9, 0.94, 1.0)},
            "Fill": {"power": 0.15, "softness": 1.0, "color": (1.0, 1.0, 1.0)},
        },
    },
    "SOFT": {
        "label": "Soft daylight",
        "description": "Gentle, warm window light with low contrast",
        "colour": "TRUE",
        "backdrop": (0.55, 0.52, 0.48, 1.0),
        "lights": {
            "Key": {"power": 1.1, "softness": 2.0, "color": (1.0, 0.93, 0.82), "gobo": "WINDOW"},
            "Rim": {"power": 0.4, "softness": 1.5, "color": (1.0, 0.97, 0.92)},
            "Fill": {"power": 1.2, "softness": 2.0, "color": (1.0, 0.98, 0.95)},
        },
    },
    # 0.9: whole-studio looks.
    "INFINITY": {
        "label": "Infinity white",
        "description": "Seamless white with no visible floor line: shadows float on pure white",
        "colour": "TRUE", "eevee": "STUDIO",
        "backdrop": (0.9, 0.9, 0.9, 1.0), "floor": _floor(0.25, 0.6), "world": (0.35, 0.35, 0.36),
        "shape": {"curve": 1.6},
        "lights": {"Key": _light(0.9, 2.0, (1.0, 0.99, 0.97)), "Rim": _light(0.4, 1.5, WHITE),
                   "Fill": _light(0.9, 2.0, WHITE)},
        "extra": [_extra("Top", "SOFTBOX", 0.5, 2.5, WHITE, (0.0, 0.3, 3.2))],
    },
    "GLOSSY_BLACK": {
        "label": "Black gloss",
        "description": "Car reveal: a mirror-black floor and long strip lights tracing the lines",
        "colour": "PUNCHY", "eevee": "GLOSS",
        "backdrop": (0.004, 0.004, 0.0045, 1.0), "floor": _floor(1.0, 0.04), "world": (0.002, 0.002, 0.002),
        "lights": {"Key": _light(2.2, 0.5, WHITE, shape="STRIP", offset=(0.0, -1.0, 3.0)),
                   "Rim": _light(1.6, 1.2, (0.95, 0.97, 1.0)), "Fill": _light(0.25, 1.5, WHITE)},
        "extra": [_extra("Rim2", "STRIP", 1.8, 0.4, (0.95, 0.97, 1.0), (-2.2, 1.6, 1.8))],
    },
    "SHOWROOM": {
        "label": "Showroom",
        "description": "Overhead strip panels over a satin grey floor, like a dealership",
        "colour": "NATURAL", "eevee": "GLOSS",
        "backdrop": (0.2, 0.2, 0.21, 1.0), "floor": _floor(0.7, 0.18), "world": (0.08, 0.08, 0.085),
        "lights": {"Key": _light(1.0, 0.5, WHITE, shape="STRIP", offset=(0.0, -1.2, 3.2)),
                   "Rim": _light(0.7, 1.6, WHITE, shape="STRIP", offset=(0.0, 1.4, 3.0)),
                   "Fill": _light(0.4, 2.0, WHITE)},
        "extra": [_extra("Panel2", "STRIP", 0.8, 0.6, WHITE, (-1.4, 0.0, 3.2)),
                  _extra("Panel3", "STRIP", 0.8, 0.6, WHITE, (1.4, 0.0, 3.2))],
    },
    "GRADIENT": {
        "label": "Gradient grey",
        "description": "Mid grey that falls away into darkness behind the product",
        "colour": "NATURAL",
        "backdrop": (0.14, 0.14, 0.14, 1.0), "floor": _floor(0.4, 0.5), "world": (0.015, 0.015, 0.016),
        "lights": {"Key": _light(1.2, 1.4, WHITE, offset=(-1.4, -1.8, 0.9)), "Rim": _light(0.5, 1.2, WHITE),
                   "Fill": _light(0.2, 2.0, WHITE)},
    },
    "GOLDEN": {
        "label": "Golden hour",
        "description": "Low warm sun raking across, cool sky fill and a sunset in the reflections",
        "colour": "NATURAL",
        "backdrop": (0.32, 0.22, 0.15, 1.0), "floor": _floor(0.3, 0.55),
        "hdri": {"image": "sunset", "strength": 0.5, "rotation": 200.0, "background": False},
        "lights": {"Key": _light(1.4, 0.45, (1.0, 0.6, 0.3), offset=(-3.0, -1.2, 0.7)),
                   "Rim": _light(0.8, 1.0, (1.0, 0.7, 0.4)), "Fill": _light(0.35, 2.0, (0.55, 0.65, 1.0))},
    },
    "NEON": {
        "label": "Neon edge",
        "description": "Black stage with magenta and cyan edge lights: tech, gaming, nightlife",
        "colour": "PUNCHY", "eevee": "GLOSS",
        "backdrop": (0.008, 0.008, 0.01, 1.0), "floor": _floor(0.9, 0.12), "world": (0.002, 0.002, 0.003),
        "lights": {"Key": _light(0.25, 1.0, (0.8, 0.85, 1.0)),
                   "Rim": _light(2.2, 1.0, (1.0, 0.08, 0.6), offset=(2.2, 1.2, 1.2)),
                   "Fill": _light(0.0, 1.0, WHITE)},
        "extra": [_extra("Edge", "STRIP", 3.0, 0.5, (0.05, 0.75, 1.0), (-2.4, 0.4, 1.0))],
    },
    "TABLETOP": {
        "label": "Tabletop warm",
        "description": "Cosy warm light through blinds on a warm sweep: food, drinks, home goods",
        "colour": "TRUE",
        "backdrop": (0.42, 0.3, 0.2, 1.0), "floor": _floor(0.3, 0.5), "world": (0.06, 0.05, 0.04),
        "lights": {"Key": _light(1.2, 1.8, (1.0, 0.84, 0.66), gobo="BLINDS"),
                   "Rim": _light(0.6, 1.2, (1.0, 0.9, 0.75)), "Fill": _light(0.7, 2.0, (1.0, 0.92, 0.8))},
    },
    "PASTEL": {
        "label": "Soft pastel",
        "description": "Soft, shadowless light on a blush pink sweep: beauty and skincare",
        "colour": "TRUE", "eevee": "STUDIO",
        "backdrop": (0.85, 0.6, 0.62, 1.0), "floor": _floor(0.35, 0.35), "world": (0.5, 0.4, 0.42),
        "lights": {"Key": _light(0.9, 2.6, WHITE), "Rim": _light(0.5, 2.0, WHITE),
                   "Fill": _light(1.6, 2.4, (1.0, 0.97, 0.98))},
        "extra": [_extra("Top", "SOFTBOX", 0.6, 2.5, WHITE, (0.0, 0.0, 3.2))],
    },
    "TECH": {
        "label": "Tech dark",
        "description": "Blue-black with cool strip highlights: phones, laptops, audio",
        "colour": "PUNCHY", "eevee": "GLOSS",
        "backdrop": (0.025, 0.03, 0.04, 1.0), "floor": _floor(0.75, 0.2), "world": (0.004, 0.005, 0.007),
        "lights": {"Key": _light(0.9, 1.2, (0.92, 0.95, 1.0)),
                   "Rim": _light(1.8, 1.0, (0.7, 0.82, 1.0), shape="STRIP"),
                   "Fill": _light(0.15, 1.0, WHITE)},
        "extra": [_extra("Top", "STRIP", 0.7, 0.4, (0.85, 0.9, 1.0), (0.0, -0.3, 3.0))],
    },
    # 0.9: looks built on a studio form (studio.FORMS), not only light and colour.
    "DOME": {
        "label": "Dome studio",
        "description": "A white dome all round: soft, seamless reflections for chrome, jewellery, watches",
        "colour": "TRUE", "eevee": "STUDIO", "form": "DOME",
        "backdrop": (0.8, 0.8, 0.8, 1.0), "floor": _floor(0.3, 0.5),
        "lights": {"Key": _light(0.9, 2.4, WHITE), "Rim": _light(0.6, 2.0, WHITE), "Fill": _light(0.8, 2.4, WHITE)},
        "extra": [_extra("Top", "SOFTBOX", 0.8, 3.0, WHITE, (0.0, 0.0, 3.0))],
    },
    "CORNER": {
        "label": "Corner studio",
        "description": "Shoot into a soft curved corner: depth behind the product, an even falloff",
        "colour": "NATURAL", "form": "CORNER",
        "backdrop": (0.32, 0.31, 0.3, 1.0), "floor": _floor(0.35, 0.55),
        "lights": {"Key": _light(1.2, 1.8, (1.0, 0.97, 0.93)), "Rim": _light(0.7, 1.2, WHITE),
                   "Fill": _light(0.5, 2.0, WHITE)},
    },
    "ROUND": {
        "label": "Round cyc",
        "description": "A circular cyclorama: the same seamless background from every side, for turntables",
        "colour": "NATURAL", "form": "ROUND",
        "backdrop": (0.4, 0.4, 0.41, 1.0), "floor": _floor(0.4, 0.5),
        "lights": {"Key": _light(1.0, 2.0, WHITE), "Rim": _light(0.9, 1.4, WHITE), "Fill": _light(0.6, 2.0, WHITE)},
    },
    "SLANTED": {
        "label": "Slanted room",
        "description": "A light grey room with 45 degree corners: soft panels of light in every reflection",
        "colour": "NATURAL", "eevee": "GLOSS", "form": "ROOM",
        "backdrop": (0.6, 0.6, 0.61, 1.0), "floor": _floor(0.55, 0.3),
        "lights": {"Key": _light(1.1, 1.6, WHITE), "Rim": _light(1.0, 1.2, WHITE), "Fill": _light(0.4, 2.0, WHITE)},
        "extra": [_extra("Top", "STRIP", 0.8, 0.6, WHITE, (0.0, 0.0, 3.0))],
    },
    "HALFROUND": {
        "label": "Half-round",
        "description": "Dark half-round cyc for cars and big products: walk the camera round, no edges",
        "colour": "PUNCHY", "eevee": "GLOSS", "form": "HALFROUND",
        "backdrop": (0.03, 0.03, 0.033, 1.0), "floor": _floor(0.7, 0.2), "world": (0.004, 0.004, 0.005),
        "lights": {"Key": _light(1.5, 0.5, WHITE, shape="STRIP", offset=(0.0, -1.0, 3.0)),
                   "Rim": _light(1.4, 1.2, (0.95, 0.97, 1.0)), "Fill": _light(0.25, 1.5, WHITE)},
    },
    "HORIZON": {
        "label": "Horizon",
        "description": "An endless floor under an open sky: outdoor product shots without leaving the studio",
        "colour": "NATURAL", "form": "FLOOR",
        "backdrop": (0.3, 0.3, 0.31, 1.0), "floor": _floor(0.3, 0.7),
        "hdri": {"image": "city", "strength": 1.0, "rotation": 0.0, "background": True},
        "lights": {"Key": _light(0.5, 2.5, (1.0, 0.97, 0.92)), "Rim": _light(0.3, 2.0, WHITE),
                   "Fill": _light(0.0, 1.0, WHITE)},
    },
    "OVERCAST": {
        "label": "Overcast sky",
        "description": "Soft open-sky daylight from an outdoor HDRI: sport, outdoor gear, cars",
        "colour": "NATURAL",
        "backdrop": (0.35, 0.35, 0.36, 1.0), "floor": _floor(0.3, 0.6),
        "hdri": {"image": "city", "strength": 1.0, "rotation": 0.0, "background": False},
        "lights": {"Key": _light(0.35, 3.0, (0.95, 0.97, 1.0), offset=(0.0, -1.0, 3.2)),
                   "Rim": _light(0.25, 2.0, WHITE), "Fill": _light(0.0, 1.0, WHITE)},
    },
}


def _rig_scale(scene):
    tgt = studio.find_target(scene)
    return float(tgt.get("plinth_scale", 1.0)) if tgt is not None else 1.0


def _backdrop_bsdf():
    bd = bpy.data.objects.get(studio.BACKDROP)
    mat = bd.active_material if bd else None
    if mat and mat.node_tree:
        return next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    return None


def _shape_defaults(scene):
    bd = bpy.data.objects.get(studio.BACKDROP)
    return studio.cove_defaults(0.5 * _rig_scale(scene), studio.current_form(bd))


def _shape_now(obj):
    mod = obj.modifiers.get(studio.COVE_GN) if obj else None
    if mod is None:
        return {}
    return {k: studio._get_input(mod, ident) for k, _l, ident in studio.backdrop_inputs(obj)}


def _tags(idblock):
    return set(idblock.get(ROOT, "").split()) if idblock is not None else set()


def _tag(idblock, part, on):
    if idblock is None:
        return
    tags = _tags(idblock)
    tags = tags | {part} if on else tags - {part}
    idblock[ROOT] = " ".join(sorted(tags))


def capture(scene, label="My studio", description=""):
    """The current studio as a look dict."""
    scale = _rig_scale(scene)
    tgt = studio.find_target(scene)
    preset = {"label": label, "description": description, "lights": {},
              "colour": scene.plinth.view_transform}
    bsdf = _backdrop_bsdf()
    if bsdf:
        preset["backdrop"] = tuple(bsdf.inputs["Base Color"].default_value)
    bd = bpy.data.objects.get(studio.BACKDROP)
    if bd is not None:
        preset["floor"] = studio.floor_settings(bd)
        preset["form"] = studio.current_form(bd)
        base, now = _shape_defaults(scene), _shape_now(bd)
        preset["shape"] = {k: now[k] / base[k] for k in SHAPE_KEYS if k in now and base.get(k)}
    h = hdri.settings(scene)
    if h["on"]:
        preset["hdri"] = {"strength": h["strength"], "rotation": h["rotation"], "background": h["background"]}
        preset["hdri"]["image" if h["key"] != "CUSTOM" else "path"] = h["key"] if h["key"] != "CUSTOM" else h["path"]
        if h["sky"]:                                 # 0.9.1: the clear sky's sun
            preset["hdri"]["sun"] = dict(h["sky"])
    else:
        preset["hdri"] = None
    colour = hdri.studio_colour(scene)
    if colour is not None:
        preset["world"] = colour
    for light in studio.list_lights(scene):
        data, label_ = light.data, studio.light_label(light)
        rig = studio.LIGHT_RIG.get(label_)
        offset = tuple((light.location - tgt.location) / scale) if tgt is not None else (0.0, 0.0, 0.0)
        if rig is not None:
            preset["lights"][label_] = {
                "power": data.energy / (rig[3] * scale * scale),
                "softness": gobos.own_size(data)[0] / (rig[1] * scale),
                "color": tuple(data.color), "gobo": gobos.current_gobo(data),
                "shape": studio.light_shape(data), "offset": offset}
        else:
            power, size = studio.light_base(light)
            preset.setdefault("extra", []).append(_extra(
                label_, studio.light_shape(data), data.energy / (power * scale * scale),
                gobos.own_size(data)[0] / (size * scale), tuple(data.color), offset, gobos.current_gobo(data)))
    preset["eevee"] = scene.plinth.eevee_preset
    return preset


def _restore_rig_light(scene, obj, name, scale):
    """A rig light a look moved goes back to its rig place and shape."""
    tgt = studio.get_target(scene)
    shape, _size, _sy, _power, offset, _k = studio.LIGHT_RIG[name]
    obj.location = tgt.location + offset * scale
    studio.set_light_shape(obj.data, "STRIP" if shape == "RECTANGLE" else "SOFTBOX")
    obj.pop(LOOK_MOVED, None)


def _remove_look_lights(scene):
    for obj in [o for o in studio.list_lights(scene) if o.get(LOOK_LIGHT)]:
        if scene.plinth.active_light == studio.light_label(obj):
            scene.plinth.active_light = "Key"
        studio.remove_light(scene, obj)


def _add_look_light(scene, spec, scale):
    """Make (or, for a light of yours with that name, set) one of a look's
    extra lights. Lights a look makes are its own: the next look removes them."""
    tgt = studio.get_target(scene)
    label = name = re.sub(r"[^A-Za-z0-9]+", "", spec.get("name", "")) or "Light"
    obj = bpy.data.objects.get("Plinth_" + label)
    if obj is not None and obj.type == "LIGHT" and obj.get("plinth_light") and label not in studio.LIGHT_RIG:
        data = obj.data                              # a saved look of this studio: your light, in place
        gobos.forget_size(data)
    else:
        n = 2
        while bpy.data.objects.get("Plinth_" + label) is not None:
            label, n = "%s%d" % (name, n), n + 1
        data = bpy.data.lights.new("Plinth_" + label, "AREA")
        studio.tune_light(data)
        data["plinth_base"] = (studio.NEW_LIGHT["power"], studio.NEW_LIGHT["size"])
        obj = bpy.data.objects.new("Plinth_" + label, data)
        obj["plinth_light"] = True
        obj[LOOK_LIGHT] = True
        studio._link(obj, studio.studio_collection(scene))
    obj.location = tgt.location + Vector(spec.get("offset", (0.0, -2.5, 2.0))) * scale
    skipped = studio.set_light_shape(data, spec.get("shape", "SOFTBOX") if spec.get("shape") in studio.LIGHT_SHAPES
                                     else "SOFTBOX")
    data.energy = studio.NEW_LIGHT["power"] * scale * scale * float(spec.get("power", 1.0))
    data.size = studio.NEW_LIGHT["size"] * scale * float(spec.get("softness", 1.0))
    if hasattr(data, "size_y"):
        data.size_y = data.size * studio.LIGHT_SHAPES[studio.light_shape(data)]["aspect"]
    data.color = tuple(spec.get("color", WHITE))[:3]
    studio.aim_at(obj, tgt)
    skipped += gobos.set_gobo(data, spec.get("gobo", "NONE"))
    return skipped


def apply(scene, preset):
    """Apply a look dict. Returns what was skipped (missing lights etc.)."""
    from . import scenes
    scenes.clear(scene)                                    # 0.10: a look is a studio: out of any scene
    skipped = []
    if preset.get("colour") in engine.COLOUR_LOOKS:       # 0.9: a look carries its colour
        scene.plinth.view_transform = preset["colour"]
    bd = bpy.data.objects.get(studio.BACKDROP)
    if "backdrop" in preset:
        bsdf = _backdrop_bsdf()
        if bsdf:
            bsdf.inputs["Base Color"].default_value = preset["backdrop"]
        else:
            skipped.append("backdrop")
    # Floor finish and cove shape: the look's, or back to the default if the
    # last look set them and this one doesn't say.
    if bd is not None:
        # The form first (0.9): shape and floor below are the form's own.
        if preset.get("form") in studio.FORMS:
            if studio.current_form(bd) != preset["form"]:
                studio.set_form(scene, preset["form"])
            _tag(bd, "form", preset["form"] != "SWEEP")
        elif "form" in _tags(bd):
            studio.set_form(scene, "SWEEP")
            _tag(bd, "form", False)
            _tag(bd, "shape", False)
        if "floor" in preset:
            studio.set_floor(bd, **{k: v for k, v in preset["floor"].items() if k in studio.FLOOR_DEFAULTS})
            _tag(bd, "floor", True)
        elif "floor" in _tags(bd):
            studio.set_floor(bd, **studio.FLOOR_DEFAULTS)
            _tag(bd, "floor", False)
        base = _shape_defaults(scene)
        if "shape" in preset:
            studio.set_backdrop_shape(bd, **{k: base[k] * float(preset["shape"].get(k, 1.0)) for k in SHAPE_KEYS if k in base})
            _tag(bd, "shape", True)
        elif "shape" in _tags(bd):
            studio.set_backdrop_shape(bd, **base)
            _tag(bd, "shape", False)
    elif "floor" in preset or "shape" in preset:
        skipped.append("backdrop")
    # The world: an HDRI, or the plain studio colour.
    if "hdri" in preset:
        h = preset["hdri"]
        if h:
            err = hdri.set_image(scene, key=h.get("image"), path=h.get("path"))
            if err:
                skipped.append("hdri")
            else:
                hdri.set_hdri(scene, strength=h.get("strength", 1.0), rotation=h.get("rotation", 0.0),
                              background=h.get("background", False))
                if h.get("image") == hdri.SKY and h.get("sun"):
                    hdri.set_sun(scene, **{k: v for k, v in h["sun"].items() if k in hdri.SKY_DEFAULTS})
                _tag(scene.world, "hdri", True)
        elif hdri.settings(scene)["on"]:
            hdri.turn_off(scene)
    elif "hdri" in _tags(scene.world) and hdri.settings(scene)["on"]:
        _tag(scene.world, "hdri", False)
        hdri.turn_off(scene)
    if "world" in preset:
        hdri.set_studio_colour(scene, preset["world"])
        _tag(scene.world, "world", True)
    elif "world" in _tags(scene.world):
        hdri.set_studio_colour(scene, hdri.STUDIO_COLOUR)
        _tag(scene.world, "world", False)
    skipped += apply_lights(scene, preset)
    if preset.get("eevee") in engine.EEVEE_PRESETS and not scene.plinth.auto_preset:
        if scene.plinth.eevee_preset != preset["eevee"]:
            scene.plinth.eevee_preset = preset["eevee"]
    return skipped


def apply_lights(scene, preset):
    """A look's lights only: Key, Rim, Fill and its extra lights (the last
    look's extras go). Returns what was skipped."""
    scale = _rig_scale(scene)
    skipped = []
    # Key, Rim, Fill.
    for name, values in preset.get("lights", {}).items():
        obj = bpy.data.objects.get("Plinth_" + name)
        if obj is None or name not in studio.LIGHT_RIG:
            skipped.append(name)
            continue
        _shape, size, size_y, power, _off, _kelvin = studio.LIGHT_RIG[name]
        gobos.forget_size(obj.data)                  # the look's size is the light's own
        moved = "offset" in values or "shape" in values
        if not moved and obj.get(LOOK_MOVED):
            _restore_rig_light(scene, obj, name, scale)
        if values.get("shape") in studio.LIGHT_SHAPES:
            skipped += studio.set_light_shape(obj.data, values["shape"])
        if "offset" in values:
            obj.location = studio.get_target(scene).location + Vector(values["offset"]) * scale
        if moved:
            obj[LOOK_MOVED] = True
        obj.data.energy = power * scale * scale * values["power"]
        obj.data.size = size * scale * values["softness"]
        if "shape" in values:
            obj.data.size_y = obj.data.size * studio.LIGHT_SHAPES[values["shape"]]["aspect"]
        else:
            obj.data.size_y = size_y * scale * values["softness"]
        obj.data.color = values["color"][:3]
        skipped += gobos.set_gobo(obj.data, values.get("gobo", "NONE"))
    # Extra lights: the last look's go, this look's come.
    if not preset.get("rig") or "extra" in preset:
        _remove_look_lights(scene)
    if preset.get("extra") and studio.find_target(scene) is not None:
        for spec in preset["extra"]:
            skipped += _add_look_light(scene, spec, scale)
        gobos.relink()
    elif preset.get("extra"):
        skipped.append("extra lights")
    return skipped


# --- Looks from files (user, content packs) ---------------------------------------

def normalise(data):
    """A look read from JSON/TOML (lists for tuples, unknown keys) as the dict
    apply() takes. Unknown keys are dropped, bad values fall back."""
    out = {"label": str(data.get("label", "Studio")), "description": str(data.get("description", "")),
           "lights": {}}
    for key in ("colour", "eevee", "content"):
        if isinstance(data.get(key), str):
            out[key] = data[key]
    if data.get("form") in studio.FORMS:
        out["form"] = data["form"]
    if data.get("rig"):
        out["rig"] = True
    for key in ("backdrop", "world"):
        if isinstance(data.get(key), (list, tuple)) and len(data[key]) >= 3:
            out[key] = tuple(float(c) for c in data[key])
    if isinstance(data.get("floor"), dict):
        out["floor"] = {k: (bool(v) if k == "fade" else float(v)) for k, v in data["floor"].items()
                        if k in studio.FLOOR_DEFAULTS}
    if isinstance(data.get("shape"), dict):
        out["shape"] = {k: float(v) for k, v in data["shape"].items() if k in SHAPE_KEYS}
    if "hdri" in data:
        h = data["hdri"]
        out["hdri"] = None if not isinstance(h, dict) or not (h.get("image") or h.get("path")) else {
            k: (bool(v) if k == "background" else (str(v) if k in ("image", "path") else float(v)))
            for k, v in h.items() if k in ("image", "path", "strength", "rotation", "background")}
        if out["hdri"] and isinstance(h.get("sun"), dict):
            out["hdri"]["sun"] = {k: float(v) for k, v in h["sun"].items() if k in hdri.SKY_DEFAULTS}

    def light(v, extra=False):
        got = {"power": float(v.get("power", 1.0)), "softness": float(v.get("softness", 1.0)),
               "color": tuple(float(c) for c in v.get("color", WHITE))[:3], "gobo": str(v.get("gobo", "NONE"))}
        if got["gobo"] not in gobos.GOBO_PRESETS:
            got["gobo"] = "NONE"
        if v.get("shape") in studio.LIGHT_SHAPES:
            got["shape"] = v["shape"]
        if isinstance(v.get("offset"), (list, tuple)) and len(v["offset"]) == 3:
            got["offset"] = tuple(float(c) for c in v["offset"])
        if extra:
            got["name"] = str(v.get("name", "Light"))
            got.setdefault("shape", "SOFTBOX")
        return got

    for name, v in (data.get("lights") or {}).items():
        if isinstance(v, dict):
            out["lights"][name] = light(v)
    if isinstance(data.get("extra"), list):
        out["extra"] = [light(v, True) for v in data["extra"] if isinstance(v, dict)]
    return out


# --- User presets on disk -------------------------------------------------------

def user_folder():
    """Where saved studios live (created on demand)."""
    try:
        return bpy.utils.extension_path_user(__package__, path="studios", create=True)
    except (ValueError, AttributeError):     # running as a legacy add-on / from source
        return bpy.utils.user_resource("CONFIG", path=os.path.join("plinth", "studios"),
                                       create=True)


def _slug(label):
    return re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_").lower() or "studio"


def save_user(scene, label, description="", folder=None):
    folder = folder or user_folder()
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, _slug(label) + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(capture(scene, label, description), f, indent=2)
    return path


_USER_CACHE = {}         # folder -> (stamp, presets): the Studio menu reads this while drawing


def list_user(folder=None):
    """{key: preset} for every readable .json in the folder. Bad files are
    skipped. Cached on the files' names and times (read while drawing)."""
    folder = folder or user_folder()
    try:
        stamp = tuple(sorted((e.name, e.stat().st_mtime_ns) for e in os.scandir(folder)
                             if e.name.endswith(".json")))
    except OSError:
        return {}
    hit = _USER_CACHE.get(folder)
    if hit is not None and hit[0] == stamp:
        return dict(hit[1])
    found = {}
    for fn in sorted(os.listdir(folder)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(folder, fn), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and "lights" in data:
            try:
                found["USER_" + fn[:-5]] = normalise(data)
            except (TypeError, ValueError, AttributeError):
                continue
    _USER_CACHE[folder] = (stamp, found)
    return dict(found)


# Studio looks and light rigs from the content library (content.py fills this;
# a rig is a look without a backdrop, marked "rig": True).
CONTENT = {}

# The Studio card shows this many looks; the rest are behind "All looks…".
CARD_LOOKS = 5
FEATURED = ("CLEAN", "CAR", "DOME", "INFINITY", "GLOSSY_BLACK")


def looks():
    """Every look for the Studio card: the built-in looks, then ones from installed packs."""
    return {**BUILTIN, **{k: v for k, v in CONTENT.items() if not v.get("rig")}}


def card_looks(last=""):
    """The few looks on the Studio card: the one in use first if it isn't
    featured, then the featured ones."""
    from . import content
    every = looks()
    fresh = [k for k in every if "look_" + k in content.NEW_IDS]          # just installed
    keys = fresh + [k for k in FEATURED if k in every and k not in fresh]
    if last in every and last not in keys[:CARD_LOOKS]:
        keys = [last] + keys
    return keys[:CARD_LOOKS]


def rigs():
    """Light rigs from the content library (the Light card's Rig row)."""
    return {k: v for k, v in CONTENT.items() if v.get("rig")}


def all_presets(folder=None):
    return {**BUILTIN, **CONTENT, **list_user(folder)}
