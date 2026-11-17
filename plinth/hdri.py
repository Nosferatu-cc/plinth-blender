# SPDX-License-Identifier: GPL-3.0-or-later
"""HDRI lighting: an environment image lights and reflects in the product.

Built into Plinth's own world (``Plinth_World``) beside its plain studio
colour: the image lights the scene, and the camera keeps seeing the studio
colour unless "Show behind the studio" is on (Light Path › Is Camera Ray,
which Eevee and Cycles both honour). A world the user had is put back when
the HDRI is turned off.

The built-in images are the ones Blender ships for its viewport (CC0, Poly
Haven; ``datafiles/studiolights/world``), referenced in place, not copied.
Any .hdr/.exr can be loaded instead. Strength, rotation, tint and the
background switch live in the world's nodes, so they save with the file.
"""

import functools
import math
import os

import bpy

CHIP = "HDRI"                      # Scene.plinth.active_light when the HDRI is being edited
WORLD = "Plinth_World"
PREFIX = "Plinth_HDRI"
USER_WORLD = "plinth_user_world"   # scene[...] = the world the user had before
STUDIO_COLOUR = (0.05, 0.05, 0.055, 1)

ORDER = ("studio", "interior", "city", "courtyard", "forest", "sunrise", "sunset", "night")
DESCRIPTIONS = {
    "studio": "A small photo studio: soft boxes and white walls",
    "interior": "A hotel room: warm window light",
    "city": "An open rooftop under a bright sky",
    "courtyard": "A sunny courtyard: crisp shadows",
    "forest": "Dappled light under trees",
    "sunrise": "Low, warm morning sun",
    "sunset": "Golden evening light over water",
    "night": "Dark sky, faint cool light",
}
EXTENSIONS = (".hdr", ".exr")


def builtin_dir():
    return os.path.join(bpy.utils.system_resource("DATAFILES") or "", "studiolights", "world")


@functools.lru_cache(maxsize=1)
def builtins():
    """{key: path} for the images Blender ships, in ORDER (others after).
    Cached: the UI asks on every redraw, and the folder doesn't change."""
    folder = builtin_dir()
    try:
        files = {os.path.splitext(f)[0]: os.path.join(folder, f) for f in os.listdir(folder)
                 if f.lower().endswith(EXTENSIONS)}
    except OSError:
        return {}
    keys = [k for k in ORDER if k in files] + sorted(k for k in files if k not in ORDER)
    return {k: files[k] for k in keys}


# HDRIs from the content library (content.py fills this):
# {key: {"label", "description", "path" (None if its pack's file is missing), "strength", "rotation"}}
CONTENT = {}


def images():
    """Every image for the HDRI chips: Blender's, then ones from installed packs. {key: path or None}"""
    return {**builtins(), **{k: v["path"] for k, v in CONTENT.items()}}


# --- Clear sky (0.9.1) ---------------------------------------------------------------
# Blender's physical sky instead of a photo: a sun you can move anywhere, with the
# sky's colour following it (blue noon, gold low sun). 5.x calls the model
# MULTIPLE_SCATTERING (4.2: NISHITA) and its haze aerosol_density (4.2:
# dust_density). The sky's own sun casts the shadows: Cycles directly, Eevee by
# extracting it from the world (world.use_sun_shadow), so there is no second
# sun lamp to drift out of line. Measured in 4.2 and 5.2: rotation 0 puts the
# sun towards +Y, 90 towards +X.

SKY = "sky"
SKY_LABEL = "Clear sky"
SKY_STRENGTH = 0.02          # the physical sky in photographic units (tuned: test_sky)
SKY_DEFAULTS = {"elevation": 35.0, "rotation": 210.0, "softness": 0.55, "haze": 1.0}
SUN = "Plinth_Sun"

# Eevee doesn't render the Sky Texture's sun disc (measured: Eevee's light didn't
# move whatever its sun threshold or the disc's size, while Cycles' did, 7x
# brighter). So the sky's own disc is off and a sun lamp, in line with the sky's
# sun, lights both engines alike. Its strength and colour per sun height were
# measured in Cycles against the real disc (white diffuse floor: sky with disc
# minus sky without, over a strength-1 lamp; tools: scratch sun_cal): strength
# for a white lamp, and the disc's colour (max channel 1). Per sky model.
SUN_TABLE = {
    "MULTIPLE_SCATTERING": (   # Blender 5.x
        (2, 15.029, (1.0, 0.3191, 0.0098)), (5, 39.283, (1.0, 0.5122, 0.1213)),
        (10, 71.08, (1.0, 0.6693, 0.3043)), (20, 104.348, (1.0, 0.7802, 0.4975)),
        (35, 124.628, (1.0, 0.8333, 0.6135)), (60, 136.682, (1.0, 0.8611, 0.6815)),
        (90, 140.311, (1.0, 0.8686, 0.7006))),
    "NISHITA": (               # Blender 4.2
        (2, 13.635, (1.0, 0.3556, 0.0092)), (5, 38.789, (1.0, 0.5266, 0.1166)),
        (10, 68.455, (1.0, 0.6633, 0.2905)), (20, 97.197, (1.0, 0.7599, 0.479)),
        (35, 114.181, (1.0, 0.8062, 0.5943)), (60, 124.161, (1.0, 0.8304, 0.6621)),
        (90, 128.099, (1.0, 0.837, 0.6813))),
}


def sun_light(model, elevation):
    """(energy per unit of sky strength, colour) of the lamp standing in for the
    sky's sun at this height (linear between measured heights; 0 below 0)."""
    rows = SUN_TABLE.get(model, SUN_TABLE["NISHITA"])
    if elevation <= 0:
        return 0.0, rows[0][2]
    if elevation <= rows[0][0]:
        strength, colour = rows[0][1] * elevation / rows[0][0], rows[0][2]
    else:
        for lo, hi in zip(rows, rows[1:]):
            if elevation <= hi[0]:
                break
        f = (min(elevation, hi[0]) - lo[0]) / (hi[0] - lo[0])
        strength = lo[1] + (hi[1] - lo[1]) * f
        colour = tuple(a + (b - a) * f for a, b in zip(lo[2], hi[2]))
    mean = sum(colour) / 3.0
    return strength / mean, colour          # a coloured lamp: its mean matches the white one


# --- The sun in the viewport (Light step, sky chip): drag it across the sky -------
HANDLE_DISTANCE = 1.5        # x the studio's scale: a sphere just around the product, so
                             # its outline on screen always surrounds the product (a sun
                             # in front of the camera stays reachable)


def sun_handle(scene):
    """World point of the draggable sun icon (or None when the sky is off). Read-only."""
    from . import studio
    st = settings(scene)
    tgt = studio.find_target(scene)
    if not st["on"] or st["sky"] is None or tgt is None:
        return None
    d = HANDLE_DISTANCE * float(tgt.get("plinth_scale", 1.0))
    return tgt.location + sun_direction(st["sky"]["elevation"], st["rotation"]) * d


def drag_sun(context, x, y):
    """Drag the sun icon to region point (x, y): the sun moves to where the
    mouse points on the sphere it travels on (just above the horizon at least)."""
    from bpy_extras import view3d_utils
    from mathutils import Vector
    from . import studio
    tgt = studio.find_target(context.scene)
    if tgt is None:
        return False
    region, rv3d = context.region, context.space_data.region_3d
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, (x, y))
    ray = view3d_utils.region_2d_to_vector_3d(region, rv3d, (x, y)).normalized()
    centre = tgt.location
    radius = HANDLE_DISTANCE * float(tgt.get("plinth_scale", 1.0))
    oc = origin - centre
    b = oc.dot(ray)
    disc = b * b - (oc.dot(oc) - radius * radius)
    st = settings(context.scene)
    now = sun_direction(st["sky"]["elevation"], st["rotation"]) if st["sky"] else Vector((0, 0, 1))
    outside = oc.length > radius
    if disc >= 0.0:
        # The sun stays on its side of the product while dragged: in front (the
        # face you see is lit), or behind it (rim light). From inside the sphere
        # only the far side is ahead of the view.
        front = outside and now.dot(oc) > 0.0
        point = origin + ray * (-b - math.sqrt(disc) if front else -b + math.sqrt(disc))
    else:                                        # off the sphere: its outline, nearest the mouse
        point = origin + ray * -b
    el, rot = sun_from_direction(point - centre)
    return set_sun(context.scene, elevation=max(1.0, el), rotation=rot)


def _sun_lamp(scene, create=True):
    obj = bpy.data.objects.get(SUN)
    if obj is None and create:
        from . import studio
        data = bpy.data.lights.get(SUN) or bpy.data.lights.new(SUN, "SUN")
        obj = bpy.data.objects.new(SUN, data)
        studio._link(obj, studio.studio_collection(scene))
        studio.tune_light(data)
        obj["plinth_sun"] = True
    return obj


def sync_sun(scene):
    """Put the sun lamp where the sky's sun is (or hide it when the sky is off)."""
    world = _world(scene)
    sky = _node(world, "Sky")
    on = world is not None and world.get("plinth_hdri") == SKY and sky is not None
    lamp = _sun_lamp(scene, create=on)
    if lamp is None:
        return None
    lamp.hide_render = lamp.hide_viewport = not on
    if not on:
        return lamp
    el, rot = math.degrees(sky.sun_elevation), math.degrees(sky.sun_rotation)
    per, colour = sun_light(sky.sky_type, el)
    tint = _sock(_node(world, "Tint").inputs, "B_Color").default_value
    lamp.data.energy = per * _node(world, "Light").inputs["Strength"].default_value
    lamp.data.color = tuple(c * t for c, t in zip(colour, tint[:3]))
    lamp.data.angle = sky.sun_size
    lamp.rotation_mode = "QUATERNION"
    lamp.rotation_quaternion = (-sun_direction(el, rot)).to_track_quat("-Z", "Y")
    return lamp


def sky_type(node):
    items = {i.identifier for i in node.bl_rna.properties["sky_type"].enum_items}
    return "MULTIPLE_SCATTERING" if "MULTIPLE_SCATTERING" in items else "NISHITA"


def _haze_prop(node):
    return "aerosol_density" if hasattr(node, "aerosol_density") else "dust_density"


def sun_direction(elevation, rotation):
    """World direction towards the sun (degrees in, unit vector out)."""
    from mathutils import Vector
    e, r = math.radians(elevation), math.radians(rotation)
    return Vector((math.sin(r) * math.cos(e), math.cos(r) * math.cos(e), math.sin(e)))


def sun_from_direction(direction):
    """(elevation, rotation) in degrees for a world direction (inverse of sun_direction)."""
    d = direction.normalized()
    return (math.degrees(math.asin(max(-1.0, min(1.0, d.z)))),
            math.degrees(math.atan2(d.x, d.y)) % 360.0)


def label(key):
    if key == SKY:
        return SKY_LABEL
    if key in CONTENT:
        return CONTENT[key]["label"]
    return key.replace("_", " ").title()


# --- Reading (safe while drawing: never writes) ------------------------------------

def _sock(sockets, identifier):
    """A Mix node socket by identifier (its names repeat per data type)."""
    return next(s for s in sockets if s.identifier == identifier)


def _world(scene):
    world = scene.world
    return world if world is not None and world.name == WORLD else None


def _node(world, name):
    tree = world.node_tree if world is not None else None
    return tree.nodes.get(PREFIX + name) if tree is not None else None


def settings(scene):
    """{"on", "key", "label", "path", "strength", "rotation", "background", "tint"}."""
    world = _world(scene)
    on = bool(world is not None and world.get("plinth_hdri"))
    key = world.get("plinth_hdri", "") if world is not None else ""
    image = _node(world, "Image")
    light = _node(world, "Light")
    mapping = _node(world, "Map")
    hide = _node(world, "Hide")
    tint = _node(world, "Tint")
    path = image.image.filepath if image is not None and image.image is not None else ""
    sky = _node(world, "Sky") if key == SKY else None
    if sky is not None:
        sun = {"elevation": math.degrees(sky.sun_elevation), "softness": math.degrees(sky.sun_size),
               "haze": getattr(sky, _haze_prop(sky))}
        rotation = math.degrees(sky.sun_rotation) % 360
    else:
        sun, rotation = None, (math.degrees(mapping.inputs["Rotation"].default_value[2]) % 360 if mapping else 0.0)
    return {
        "sky": sun,
        "on": on,
        "key": key,
        "label": (label(key) if key != "CUSTOM" else os.path.splitext(os.path.basename(path))[0]) if on else "Off",
        "path": path,
        "strength": light.inputs["Strength"].default_value if light is not None else 1.0,
        "rotation": rotation,
        "background": bool(hide is not None and hide.inputs[1].default_value < 0.5),
        "tint": tuple(_sock(tint.inputs, "B_Color").default_value) if tint is not None else (1, 1, 1, 1),
    }


# --- Writing ------------------------------------------------------------------------

def _plinth_world(scene):
    """Plinth's world on the scene, remembering the user's own world first."""
    world = scene.world
    if world is not None and world.name != WORLD and USER_WORLD not in scene:
        scene[USER_WORLD] = world.name
    ours = bpy.data.worlds.get(WORLD)
    if ours is None:
        ours = bpy.data.worlds.new(WORLD)
        ours.color = STUDIO_COLOUR[:3]
    if hasattr(ours, "use_nodes") and not ours.use_nodes:
        ours.use_nodes = True
    scene.world = ours
    return ours


def _build(world):
    """The HDRI nodes beside the plain studio background; idempotent."""
    tree = world.node_tree
    nodes, links = tree.nodes, tree.links
    out = next((n for n in nodes if n.type == "OUTPUT_WORLD"), None) or nodes.new("ShaderNodeOutputWorld")
    plain = nodes.get("Background")
    if plain is None or plain.type != "BACKGROUND":
        plain = nodes.new("ShaderNodeBackground")
        plain.name = "Background"
        plain.inputs["Color"].default_value = STUDIO_COLOUR

    def node(idname, name, x, y=300):
        n = nodes.get(PREFIX + name)
        if n is None:
            n = nodes.new(idname)
            n.name = n.label = PREFIX + name
            n.location = (out.location.x + x, out.location.y + y)
        return n

    coord = node("ShaderNodeTexCoord", "Coord", -1300)
    mapping = node("ShaderNodeMapping", "Map", -1100)
    image = node("ShaderNodeTexEnvironment", "Image", -850)
    tint = node("ShaderNodeMix", "Tint", -600)
    light = node("ShaderNodeBackground", "Light", -350)
    path = node("ShaderNodeLightPath", "Camera", -600, 650)
    hide = node("ShaderNodeMath", "Hide", -350, 550)
    mix = node("ShaderNodeMixShader", "Mix", -150)
    tint.data_type, tint.blend_type = "RGBA", "MULTIPLY"
    tint.inputs["Factor"].default_value = 1.0
    hide.operation = "MULTIPLY"

    links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
    links.new(mapping.outputs[0], image.inputs["Vector"])
    links.new(image.outputs["Color"], _sock(tint.inputs, "A_Color"))
    links.new(_sock(tint.outputs, "Result_Color"), light.inputs["Color"])
    links.new(path.outputs["Is Camera Ray"], hide.inputs[0])
    links.new(hide.outputs[0], mix.inputs["Fac"])
    links.new(light.outputs[0], mix.inputs[1])                      # 0: the HDRI
    links.new(plain.outputs[0], mix.inputs[2])                      # 1: the studio colour
    links.new(mix.outputs[0], out.inputs["Surface"])
    return image


def set_sky(scene):
    """Light the scene with the clear sky (first time: a high afternoon sun)."""
    world = _plinth_world(scene)
    first = _node(world, "Image") is None
    image = _build(world)
    tree = world.node_tree
    sky = tree.nodes.get(PREFIX + "Sky")
    new = sky is None
    if new:
        sky = tree.nodes.new("ShaderNodeTexSky")
        sky.name = sky.label = PREFIX + "Sky"
        sky.location = (image.location.x, image.location.y - 320)
        sky.sky_type = sky_type(sky)
    sky.sun_disc = False                             # the sun lamp is the sun (Eevee has no disc)
    tree.links.new(sky.outputs["Color"], _sock(_node(world, "Tint").inputs, "A_Color"))
    was = world.get("plinth_hdri")
    world["plinth_hdri"] = SKY
    if hasattr(world, "use_sun_shadow"):            # Eevee: the sky's sun casts real shadows
        world.use_sun_shadow = True
    if new or was != SKY:
        set_hdri(scene, strength=SKY_STRENGTH, background=False, tint=(1, 1, 1, 1))
        set_sun(scene, **SKY_DEFAULTS)
    elif first:
        set_hdri(scene, strength=SKY_STRENGTH)
    sync_sun(scene)
    return None


def set_sun(scene, elevation=None, rotation=None, softness=None, haze=None):
    """Clear sky: sun height (degrees above the horizon), direction (degrees),
    softness (the sun's size in degrees: shadow edges) and haze. False when the
    sky isn't on."""
    world = _world(scene)
    sky = _node(world, "Sky")
    if world is None or world.get("plinth_hdri") != SKY or sky is None:
        return False
    if elevation is not None:
        sky.sun_elevation = math.radians(max(0.0, min(90.0, elevation)))
    if rotation is not None:
        sky.sun_rotation = math.radians(rotation % 360.0)
    if softness is not None:
        sky.sun_size = math.radians(max(0.1, min(10.0, softness)))
        if hasattr(world, "sun_angle"):
            world.sun_angle = sky.sun_size
    if haze is not None:
        setattr(sky, _haze_prop(sky), max(0.0, min(10.0, haze)))
    sync_sun(scene)
    return True


def set_image(scene, key=None, path=None):
    """Light the scene with a built-in image (key) or any file (path).
    Returns an error message, or None."""
    if key == SKY:
        return set_sky(scene)
    if path is None:
        path = images().get(key)
        if path is None:
            return "That HDRI's pack isn't installed" if key in CONTENT else "Blender's HDRIs weren't found"
    elif not os.path.isfile(bpy.path.abspath(path)):
        return "File not found: " + path
    try:
        img = bpy.data.images.load(path, check_existing=True)
    except RuntimeError as exc:
        return str(exc).strip().splitlines()[-1]
    world = _plinth_world(scene)
    first = _node(world, "Image") is None
    from_sky = world.get("plinth_hdri") == SKY
    image = _build(world)                       # links the image (not the sky) to the light
    image.image = img
    world["plinth_hdri"] = key if key else "CUSTOM"
    if first:
        set_hdri(scene, strength=1.0, rotation=0.0, background=False, tint=(1, 1, 1, 1))
    elif from_sky:                              # a photo isn't in the sky's physical units
        set_hdri(scene, strength=1.0)
    sync_sun(scene)
    return None


def set_hdri(scene, strength=None, rotation=None, background=None, tint=None):
    """HDRI › Adjust and Colour. False when no HDRI is on."""
    world = _world(scene)
    if world is None or not world.get("plinth_hdri"):
        return False
    if strength is not None:
        _node(world, "Light").inputs["Strength"].default_value = max(0.0, strength)
    if rotation is not None:
        if world.get("plinth_hdri") == SKY:          # the sky turns by its sun
            set_sun(scene, rotation=rotation)
        else:
            _node(world, "Map").inputs["Rotation"].default_value = (0.0, 0.0, math.radians(rotation))
    if background is not None:
        _node(world, "Hide").inputs[1].default_value = 0.0 if background else 1.0
    if tint is not None:
        _sock(_node(world, "Tint").inputs, "B_Color").default_value = tuple(tint[:3]) + (1.0,)
    if world.get("plinth_hdri") == SKY:
        sync_sun(scene)
    return True


def studio_colour(scene):
    """The plain studio colour behind Plinth's world (RGB), or None when the
    scene uses a world of its own. Read-only."""
    world = _world(scene)
    if world is None:
        return None
    plain = world.node_tree.nodes.get("Background") if world.node_tree is not None else None
    if plain is not None and plain.type == "BACKGROUND":
        return tuple(plain.inputs["Color"].default_value)[:3]
    return tuple(world.color)


def set_studio_colour(scene, rgb):
    """The colour of the studio around the product (what reflections see where
    there are no lights). Uses Plinth's world, remembering the scene's own."""
    world = _plinth_world(scene)
    world.color = tuple(rgb[:3])
    tree = world.node_tree
    if tree is None:
        return world
    nodes = tree.nodes
    out = next((n for n in nodes if n.type == "OUTPUT_WORLD"), None) or nodes.new("ShaderNodeOutputWorld")
    plain = nodes.get("Background")
    if plain is None or plain.type != "BACKGROUND":
        plain = nodes.new("ShaderNodeBackground")
        plain.name = "Background"
    plain.inputs["Color"].default_value = tuple(rgb[:3]) + (1.0,)
    if not world.get("plinth_hdri") and not plain.outputs[0].is_linked:
        tree.links.new(plain.outputs[0], out.inputs["Surface"])
    return world


def turn_off(scene):
    """Back to the plain studio colour, or the user's own world if they had one."""
    world = _world(scene)
    if world is not None:
        world["plinth_hdri"] = ""
        tree = world.node_tree
        plain = tree.nodes.get("Background") if tree is not None else None
        out = next((n for n in tree.nodes if n.type == "OUTPUT_WORLD"), None) if tree is not None else None
        if plain is not None and out is not None:
            tree.links.new(plain.outputs[0], out.inputs["Surface"])
    theirs = bpy.data.worlds.get(scene.get(USER_WORLD, ""))
    if theirs is not None:
        scene.world = theirs
    scene.pop(USER_WORLD, None)
    sync_sun(scene)
    return True
