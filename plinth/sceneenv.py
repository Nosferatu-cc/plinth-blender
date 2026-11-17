# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene time of day, weather and settings (0.12).

A scene's settings live in scene[CFG] (a dict): its Variation (seed), Time of
day, Weather, Wind, and per category (trees, plants, rocks, props) Amount,
Size and Spread, plus a Clearing around the product. Variation rebuilds the
place; everything else is applied live:

- Time of day and Weather set the clear sky's sun (hdri.py), a world chain
  of Plinth's own after the sky (``Plinth_Scene_Cloud*`` nodes: drifting
  clouds, night with stars, horizon haze), a moonlight lamp at night, the
  ``Plinth_Scene_Env`` group every scene material reads (Wet, Night, Fog,
  FogColour) and the scene's lamps (objects tagged ``plinth_lamp``: their
  power at full night).
- Wind and the categories set the scatter instancer's inputs (kits.py).

Fog is aerial perspective in every scene material (scenemats.fog_wrap):
light from beyond is replaced by the haze's own colour with distance, so far
ridges fade into the sky in both engines without a volume.
"""
import math

import bpy

from . import hdri, kits, scenemats, studio

PREFIX = "Plinth_Scene_"
CFG = "plinth_scene_cfg"
CLOUDS = PREFIX + "Cloud"
MOONLIGHT = PREFIX + "Moonlight"

TIMES = (("DAWN", "Dawn", "First light: low, cool and pink, from the other side"),
         ("DAY", "Day", "High sun, short shadows"),
         ("GOLDEN", "Golden hour", "Low warm sun, long shadows"),
         ("DUSK", "Dusk", "The sun just down: blue sky, lamps coming on"),
         ("NIGHT", "Night", "Moonlight, stars and every lamp on"))
WEATHERS = (("CLEAR", "Clear", "A few clouds, crisp air"),
            ("OVERCAST", "Overcast", "Grey sky, soft light, no hard shadows"),
            ("WET", "After rain", "Wet ground and puddles, the clouds breaking"),
            ("MIST", "Mist", "Haze that swallows the distance"))
CATEGORIES = (("trees", "Trees"), ("plants", "Plants"), ("rocks", "Rocks"), ("props", "Props"))

# elevation (deg), turn from the scene's own sun direction, sky brightness,
# Night (lamps), haze colour (x the sky's light)
TIME = {"DAWN": (4.0, -150.0, 0.75, 0.55, (1.0, 0.82, 0.8)),
        "DAY": (52.0, 0.0, 1.0, 0.0, (0.82, 0.88, 1.0)),
        "GOLDEN": (9.0, 0.0, 0.95, 0.25, (1.0, 0.86, 0.68)),
        "DUSK": (0.6, 0.0, 0.8, 0.85, (0.62, 0.66, 0.9)),
        "NIGHT": (0.0, 0.0, 0.006, 1.0, (0.35, 0.42, 0.62))}
# cloud cover (None: the scene's), sun share, fog (1/visibility in m x u),
# wet, haze kept by the clouds
WEATHER = {"CLEAR": (None, 1.0, 1.0 / 15000.0, 0.0),           # visibility: clear air ~15 km,
           "OVERCAST": (0.97, 0.1, 1.0 / 5000.0, 0.0),           # grey days ~5 km, after rain ~8 km,
           "WET": (0.62, 0.85, 1.0 / 8000.0, 1.0),               # mist ~200 m
           "MIST": (0.55, 0.35, 1.0 / 200.0, 0.35)}


# --- Settings ---------------------------------------------------------------------------------

def defaults(spec):
    sky = spec.get("sky", {})
    return {"seed": 0, "time": sky.get("time", "GOLDEN"), "weather": sky.get("weather", "CLEAR"),
            "wind": sky.get("wind", 0.25), "clearing": 0.0,
            "cats": {k: {"amount": 1.0, "size": 1.0, "spread": 1.0} for k, _l in CATEGORIES}}


def config(scene, spec):
    """The scene's settings (defaults filled in; safe while drawing)."""
    out = defaults(spec)
    raw = scene.get(CFG) if scene is not None else None
    if raw is not None:
        try:
            d = raw.to_dict() if hasattr(raw, "to_dict") else dict(raw)
        except (TypeError, ValueError):
            d = {}
        for k in ("seed", "time", "weather", "wind", "clearing"):
            if k in d:
                out[k] = d[k]
        for cat, vals in dict(d.get("cats", {})).items():
            if cat in out["cats"]:
                out["cats"][cat].update({k: float(v) for k, v in dict(vals).items()})
    times, weathers = allowed(spec)
    if out["time"] not in times:
        out["time"] = times[0] if spec.get("sky", {}).get("time") not in times else spec["sky"]["time"]
    if out["weather"] not in weathers:
        out["weather"] = weathers[0]
    return out


def save(scene, cfg):
    scene[CFG] = cfg


def allowed(spec):
    sky = spec.get("sky", {})
    return (tuple(sky.get("times", [k for k, _l, _d in TIMES])),
            tuple(sky.get("weathers", [k for k, _l, _d in WEATHERS])))


def forget(scene):
    scene.pop(CFG, None)


# --- The world after the sky --------------------------------------------------------------------

def remove_atmosphere(scene=None):
    world = bpy.data.worlds.get(hdri.WORLD)
    tree = world.node_tree if world is not None else None
    if tree is None:
        return
    gone = [n for n in tree.nodes if n.name.startswith(CLOUDS)]
    if not gone:
        return
    if tree.animation_data is not None:
        for fc in list(tree.animation_data.drivers):
            if CLOUDS in fc.data_path:
                tree.animation_data.drivers.remove(fc)
    for n in gone:
        tree.nodes.remove(n)
    sky, tint = tree.nodes.get(hdri.PREFIX + "Sky"), tree.nodes.get(hdri.PREFIX + "Tint")
    if sky is not None and tint is not None and world.get("plinth_hdri") == hdri.SKY:
        tree.links.new(sky.outputs["Color"], next(s_ for s_ in tint.inputs if s_.identifier == "A_Color"))


def atmosphere(scene, cover=0.4, speed=0.5):
    """Plinth's own steps between the clear sky and the light (named
    ``Plinth_Scene_Cloud*``, removed by clear()): a cloud layer projected on a
    plane above (shrinking toward the horizon, brighter than the sky on top,
    greyer in their thick middles, drifting with the frame), the night (the
    sky dimmed by ``Dim``, stars where it's dark), haze at the horizon in the
    fog's colour, and below the horizon dark ground (reflections show earth,
    not more glowing haze). Values live in nodes named ``...Cover``, ``...Dim``,
    ``...Haze`` (set by apply())."""
    world = scene.world
    tree = world.node_tree if world is not None else None
    sky = tree.nodes.get(hdri.PREFIX + "Sky") if tree is not None else None
    tint = tree.nodes.get(hdri.PREFIX + "Tint") if tree is not None else None
    if sky is None or tint is None:
        return None
    remove_atmosphere(scene)
    before = {n.name for n in tree.nodes}
    nt = scenemats._Nodes(tree)

    def named(n, name):
        n.name = n.label = CLOUDS + name
        n.location = (sky.location.x + 200, sky.location.y - 300)
        return n

    def value(name, v):
        n = named(nt.new("ShaderNodeValue"), name)
        n.outputs[0].default_value = v
        return n.outputs[0]

    cover_v, dim_v, haze_v = value("Cover", cover), value("Dim", 1.0), value("Haze", 0.0)
    hz = named(nt.new("ShaderNodeRGB"), "HazeColour")
    hz.outputs[0].default_value = (0.8, 0.85, 0.9, 1.0)
    d = named(nt.new("ShaderNodeTexCoord"), "Coord").outputs["Generated"]
    sep = named(nt.new("ShaderNodeSeparateXYZ"), "Sep")
    nt.link(d, sep.inputs[0])
    z = nt.math("MAXIMUM", sep.outputs["Z"], 0.03)
    plane = named(nt.new("ShaderNodeCombineXYZ"), "Plane")
    nt.link(nt.math("DIVIDE", sep.outputs["X"], z), plane.inputs[0])
    nt.link(nt.math("DIVIDE", sep.outputs["Y"], z), plane.inputs[1])
    noise = named(nt.new("ShaderNodeTexNoise"), "Noise")
    noise.noise_dimensions = "4D"
    noise.inputs["Scale"].default_value = 0.9
    noise.inputs["Detail"].default_value = 12.0
    noise.inputs["Roughness"].default_value = 0.58
    if "Distortion" in noise.inputs:
        noise.inputs["Distortion"].default_value = 0.25
    nt.link(plane.outputs[0], noise.inputs["Vector"])
    fc = noise.inputs["W"].driver_add("default_value")
    fc.driver.type = "SCRIPTED"
    fc.driver.expression = "frame * %.6f" % (0.0012 * speed)
    lo = nt.math("SUBTRACT", 0.68, nt.math("MULTIPLY", cover_v, 0.3))
    span = nt.math("ADD", 0.14, nt.math("MULTIPLY", nt.smooth(cover_v, 0.8, 1.0), 0.5))
    dens = nt.math("DIVIDE", nt.math("SUBTRACT", noise.outputs["Fac"], lo), span)
    dens = scenemats._clamp(nt, dens, 0.0, 1.0)
    dens = nt.math("MAXIMUM", dens, nt.smooth(cover_v, 0.9, 1.0))              # overcast: all cloud
    dens = nt.math("MULTIPLY", dens, nt.smooth(sep.outputs["Z"], 0.0, 0.16))
    dens = nt.math("MULTIPLY", dens, 0.97)
    under = nt.smooth(noise.outputs["Fac"], 0.55, 0.85)                           # thick middles greyer below
    lum = named(nt.new("ShaderNodeRGBToBW"), "Lum")
    nt.link(sky.outputs["Color"], lum.inputs[0])
    grey = nt.math("SUBTRACT", 2.2, nt.math("MULTIPLY", nt.math("MAXIMUM", under, nt.smooth(cover_v, 0.8, 1.0)),
                                            1.25))
    cloud = scenemats._grey(nt, nt.math("MULTIPLY", lum.outputs[0], grey))
    warm = nt.mix(0.25, cloud, sky.outputs["Color"])
    out = nt.mix(dens, sky.outputs["Color"], warm)
    # Haze toward the horizon, in the fog's colour (brightness of the sky there).
    near_h = nt.math("SUBTRACT", 1.0, nt.smooth(sep.outputs["Z"], 0.0, 0.35))
    haze_col = nt.mix(1.0, hz.outputs[0], scenemats._grey(nt, nt.math("MULTIPLY", lum.outputs[0], 1.15)),
                      "MULTIPLY")
    out = nt.mix(nt.math("MULTIPLY", haze_v, near_h), out, haze_col)
    # Night: dimmed, stars where it's dark and clear.
    out = nt.mix(1.0, out, scenemats._grey(nt, dim_v), "MULTIPLY")
    star = nt.voronoi(d, 700.0)
    dots = nt.math("LESS_THAN", star.outputs["Distance"], 0.04)
    tw = nt.math("POWER", nt.noise(d, 260.0, 1.0).outputs["Fac"], 5.0)
    dark = nt.smooth(dim_v, 0.05, 0.01)
    stars = nt.math("MULTIPLY", nt.math("MULTIPLY", dots, tw), nt.math("MULTIPLY", dark, 0.06))
    stars = nt.math("MULTIPLY", stars, nt.math("SUBTRACT", 1.0, dens))
    stars = nt.math("MULTIPLY", stars, nt.smooth(sep.outputs["Z"], 0.02, 0.2))
    out = nt.mix(1.0, out, scenemats._grey(nt, stars), "ADD")
    below = nt.math("SUBTRACT", 1.0, nt.smooth(sep.outputs["Z"], -0.06, 0.0))
    earth = scenemats._grey(nt, nt.math("MULTIPLY", lum.outputs[0], nt.math("MULTIPLY", dim_v, 0.18)))
    out = nt.mix(below, out, earth)
    nt.link(out, next(s_ for s_ in tint.inputs if s_.identifier == "A_Color"))
    for n in list(tree.nodes):
        if n.name not in before and not n.name.startswith(CLOUDS):
            n.name = CLOUDS + n.name
    return out


def _atmos_node(scene, name):
    world = scene.world
    return world.node_tree.nodes.get(CLOUDS + name) if world is not None and world.node_tree else None


def _moonlight(scene, on, collection):
    obj = bpy.data.objects.get(MOONLIGHT)
    if not on:
        if obj is not None:
            data = obj.data
            bpy.data.objects.remove(obj)
            if data is not None and data.users == 0:
                bpy.data.lights.remove(data)
        return None
    data = bpy.data.lights.get(MOONLIGHT) or bpy.data.lights.new(MOONLIGHT, "SUN")
    data.energy, data.color, data.angle = 0.035, (0.62, 0.72, 1.0), math.radians(0.52)
    studio.tune_light(data)
    if obj is None:
        obj = bpy.data.objects.new(MOONLIGHT, data)
        studio._link(obj, collection)
    obj["plinth_scene_part"] = True
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = (-hdri.sun_direction(38.0, 140.0)).to_track_quat("-Z", "Y")
    return obj


# --- Applying -------------------------------------------------------------------------------------

def light_level(time, weather):
    """(lamps 0..1, sun share) for a time and weather."""
    night = TIME[time][3]
    if weather == "OVERCAST":
        night = min(1.0, night + 0.15)
    return night, WEATHER[weather][1]


def apply(scene, spec, cfg, u, collection):
    """Time of day and weather, without rebuilding: the sky (outdoor scenes
    with a clear sky), the Env group, lamps and the scene's own hook
    (spec["look"](scene, time, weather) for worlds of their own)."""
    sky = spec.get("sky", {})
    time, weather = cfg["time"], cfg["weather"]
    elev, turn, bright, night, haze_rgb = TIME[time]
    cover, sun_share, fog, wet = WEATHER[weather]
    if cover is None:
        cover = sky.get("cover", 0.35)
    night, sun_share = light_level(time, weather)
    if sky.get("own"):                                       # Moon, Neon: their own worlds
        fog = sky.get("fog", 0.0) if weather != "MIST" else WEATHER["MIST"][2]
    elif hdri.settings(scene)["on"]:
        base_rot = sky.get("rotation", 200.0)
        hdri.set_sun(scene, elevation=max(0.0, sky.get(time.lower(), elev)),
                     rotation=(base_rot + turn + _turn(scene)) % 360.0,
                     haze=sky.get("haze", 1.0) * (1.6 if weather in ("MIST", "OVERCAST") else 1.0),
                     softness=0.55 if weather == "CLEAR" else 4.0)
        dim = bright * (0.55 if weather == "OVERCAST" else 1.0)
        for name, v in (("Cover", cover), ("Dim", dim), ("Haze", min(1.0, 0.2 + fog * 2500.0))):
            n = _atmos_node(scene, name)
            if n is not None:
                n.outputs[0].default_value = v
        hc = _atmos_node(scene, "HazeColour")
        if hc is not None:
            hc.outputs[0].default_value = tuple(haze_rgb) + (1.0,)
        lamp = hdri._sun_lamp(scene, create=False)
        if lamp is not None and lamp.data is not None:
            lamp.data.energy *= sun_share * (1.0 if time != "DUSK" else 0.0)
            if weather == "OVERCAST":                         # no hard shadows under cloud
                lamp.data.angle = math.radians(25.0)
        _moonlight(scene, time == "NIGHT", collection)
    hook = spec.get("look")
    if hook is not None:
        hook(scene, time, weather)
    fog_colour = _fog_colour(time, weather, sky)
    scenemats.set_env(Wet=wet if not sky.get("dry") else 0.0, Night=night, Fog=fog / max(u, 1e-6),
                      FogR=fog_colour[0], FogG=fog_colour[1], FogB=fog_colour[2])
    for obj in collection.all_objects:
        full = obj.get("plinth_lamp")
        if full is not None and obj.type == "LIGHT":
            day = float(obj.get("plinth_lamp_day", 0.0))
            obj.data.energy = full * (day + (1.0 - day) * night)


def _turn(scene):
    from . import scenes
    return scenes.turn(scene)


def _fog_colour(time, weather, sky):
    """The haze's radiance (scene-referred, roughly the horizon sky's)."""
    base = sky.get("fog_colour")
    if base is None:
        level = {"DAWN": 0.9, "DAY": 2.2, "GOLDEN": 1.4, "DUSK": 0.35, "NIGHT": 0.004}[time]
        if weather == "OVERCAST":
            level *= 0.7
        base = tuple(c * level for c in TIME[time][4])
    return base


def set_wind(collection, strength):
    """Every scatter object's Wind (its category's sway x strength)."""
    for obj in collection.all_objects:
        if obj.get("plinth_sway") is not None:
            kits.set_controls(obj, Wind=float(obj["plinth_sway"]) * strength)


def set_category(collection, cat, amount=None, size=None, spread=None, clearing=None, share=1.0):
    """A category's live controls on all its scatter objects."""
    for obj in collection.all_objects:
        if obj.get("plinth_category") != cat and not (cat is None and obj.get("plinth_category")):
            continue
        vals = {}
        if amount is not None:
            vals["Amount"] = min(1.0, float(obj.get("plinth_share", share)) * amount)
        if size is not None:
            vals["Size"] = size
        if spread is not None:
            vals["Spread"] = spread
        if clearing is not None:
            vals["Clearing"] = clearing
        kits.set_controls(obj, **vals)
