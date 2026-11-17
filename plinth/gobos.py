# SPDX-License-Identifier: GPL-3.0-or-later
"""Procedural gobos: patterns cut into a light (window panes, blinds, leaves).

A gobo is a real card in front of the light, as on a set: a plane with the
pattern cut out (transparent where light passes), parented to the light and
seen only by shadow rays. That renders the same in Eevee and Cycles (light
node textures are Cycles-only, so Eevee used to show a plain light).

* A pattern needs hard light, as on a set: a softbox blurs it away. So a
  light with a pattern becomes a projector: its size is kept aside (back on
  "None") and Edge softness sets the source size instead, crisp to soft.
* The card sits on the light's axis, most of the way to what it aims at.
  Drivers keep it there as the light moves (simple expressions: no Python).
* Its size grows with its distance from the light, so the pattern lands the
  same size on the product wherever the card is: one pattern unit is the
  light's distance to its target, like a slide projector. (The pattern is in
  the card's own space on purpose: a gobo moves with its light.)
* Shadow linking keeps each card out of every other light's shadows.

Everything Plinth adds is named ``Plinth_Gobo*``; switching patterns rebuilds
the card's material nodes, never stacks them.
"""

import bpy

GOBO_PRESETS = {
    "NONE": {
        "label": "None",
        "description": "Clean, even light",
    },
    "WINDOW": {
        "label": "Window",
        "description": "Sunlight through window panes",
        "texture": "ShaderNodeTexBrick",
        "props": {"offset": 0.0, "squash": 1.0},
        "inputs": {"Color1": (1, 1, 1, 1), "Color2": (1, 1, 1, 1), "Mortar": (0, 0, 0, 1),
                   "Scale": 1.2, "Mortar Size": 0.05, "Brick Width": 0.5, "Row Height": 0.5},
        "ramp": (0.4, 0.6, "CONSTANT"),
    },
    "BLINDS": {
        "label": "Blinds",
        "description": "Slatted stripes of light",
        "texture": "ShaderNodeTexWave",
        "props": {"wave_type": "BANDS", "bands_direction": "Y"},
        "inputs": {"Scale": 2.0, "Distortion": 0.0},
        "ramp": (0.5, 0.52, "CONSTANT"),
    },
    "LEAVES": {
        "label": "Leaves",
        "description": "Dappled light through foliage",
        "texture": "ShaderNodeTexNoise",
        "props": {},
        "inputs": {"Scale": 3.0, "Detail": 6.0, "Roughness": 0.6},
        "ramp": (0.45, 0.6, "LINEAR"),
    },
    "DOTS": {
        "label": "Dots",
        "description": "A grid of soft dots, like a perforated screen",
        "texture": "ShaderNodeTexVoronoi",
        "props": {"feature": "F1", "distance": "EUCLIDEAN"},
        "inputs": {"Scale": 4.0, "Randomness": 0.0},
        "ramp": (0.25, 0.3, "CONSTANT"),
        "invert": True,
    },
    "STRIPES": {
        "label": "Stripes",
        "description": "Bold diagonal bands of light and shadow",
        "texture": "ShaderNodeTexWave",
        "props": {"wave_type": "BANDS", "bands_direction": "DIAGONAL"},
        "inputs": {"Scale": 1.5, "Distortion": 0.0},
        "ramp": (0.5, 0.52, "CONSTANT"),
    },
    "SPOT": {
        "label": "Spot",
        "description": "One round pool of light, like a theatre spot",
        "texture": "ShaderNodeTexGradient",
        "props": {"gradient_type": "SPHERICAL"},
        "inputs": {},
        "ramp": (0.84, 0.88, "LINEAR"),
    },
    "WATER": {
        "label": "Water",
        "description": "Rippling caustics, as light through a pool",
        "texture": "ShaderNodeTexVoronoi",
        "props": {"feature": "DISTANCE_TO_EDGE"},
        "inputs": {"Scale": 7.0, "Randomness": 1.0},
        "ramp": (0.02, 0.07, "LINEAR"),
        "invert": True,
    },
    "CRACKLE": {
        "label": "Crackle",
        "description": "Light broken by a web of fine shadow lines",
        "texture": "ShaderNodeTexVoronoi",
        "props": {"feature": "DISTANCE_TO_EDGE"},
        "inputs": {"Scale": 6.0, "Randomness": 1.0},
        "ramp": (0.03, 0.05, "CONSTANT"),
    },
    "CHEQUER": {
        "label": "Chequer",
        "description": "A bold chequerboard of light and shadow",
        "texture": "ShaderNodeTexChecker",
        "props": {},
        "inputs": {"Color1": (1, 1, 1, 1), "Color2": (0, 0, 0, 1), "Scale": 5.0},
        "ramp": (0.45, 0.55, "CONSTANT"),
    },
    "RIPPLES": {
        "label": "Ripples",
        "description": "Concentric rings spreading from the centre",
        "texture": "ShaderNodeTexWave",
        "props": {"wave_type": "RINGS", "rings_direction": "SPHERICAL"},
        "inputs": {"Scale": 2.5, "Distortion": 0.0},
        "ramp": (0.5, 0.55, "CONSTANT"),
    },
    "LATTICE": {
        "label": "Lattice",
        "description": "A diamond trellis, like a garden screen",
        "texture": "ShaderNodeTexBrick",
        "props": {"offset": 0.0, "squash": 1.0},
        "inputs": {"Color1": (1, 1, 1, 1), "Color2": (1, 1, 1, 1), "Mortar": (0, 0, 0, 1),
                   "Scale": 2.0, "Mortar Size": 0.06, "Brick Width": 0.5, "Row Height": 0.5},
        "ramp": (0.4, 0.6, "CONSTANT"),
        "turn": 45.0,
    },
    "BREAKUP": {
        "label": "Breakup",
        "description": "Soft, uneven patches, like light through clouds",
        "texture": "ShaderNodeTexNoise",
        "props": {},
        "inputs": {"Scale": 4.0, "Detail": 2.0, "Roughness": 0.5},
        "ramp": (0.44, 0.56, "LINEAR"),
    },
    "GRILLE": {
        "label": "Grille",
        "description": "A fine grid of square holes, like a mesh screen",
        "texture": "ShaderNodeTexBrick",
        "props": {"offset": 0.0, "squash": 1.0},
        "inputs": {"Color1": (1, 1, 1, 1), "Color2": (1, 1, 1, 1), "Mortar": (0, 0, 0, 1),
                   "Scale": 5.0, "Mortar Size": 0.035, "Brick Width": 0.5, "Row Height": 0.5},
        "ramp": (0.4, 0.6, "CONSTANT"),
    },
}

# Pattern controls on Light › Gobo (scale is relative, rotation in degrees,
# softness sets the projector's size and softens the pattern's edge a little).
# Stored on the light data so a new pattern, or a duplicated light, keeps them.
PARAM_DEFAULTS = {"scale": 1.0, "rotation": 0.0, "softness": 0.4}

PREFIX = "Plinth_Gobo"
CARD_MESH = "Plinth_GoboCard"
CARD_SIZE = 3.0             # the card spans ±1.5 pattern units: the pattern covers the product
BLOCKERS = "Plinth_Blockers_"
# The card's distance from the light, as a fraction of the light's distance to
# its target. CLEAR keeps it this many product radii in front of the target,
# so it never cuts through the product.
CARD_AT, CLEAR = 0.85, 1.4
# Projector size (m at rig scale 1) at Edge softness 0 and 1, and where a
# light's own size waits while it has a pattern.
PROJECTOR = (0.02, 0.6)
SAVED_SIZE = "plinth_gobo_size"
CARD_VISIBILITY = ("visible_camera", "visible_diffuse", "visible_glossy",
                   "visible_transmission", "visible_volume_scatter")


# --- Lookups ------------------------------------------------------------------------

def _data(light):
    return light.data if isinstance(light, bpy.types.Object) else light


def _objects(light):
    """The light objects behind a light or Light datablock."""
    if isinstance(light, bpy.types.Object):
        return [light]
    return [o for o in bpy.data.objects if o.type == "LIGHT" and o.data == light]


def _label(light):
    return light.name[len("Plinth_"):] if light.name.startswith("Plinth_") else light.name


def card_of(light):
    """The light's gobo card, or None. Read-only (safe while drawing)."""
    return bpy.data.objects.get(PREFIX + _label(light))


def current_gobo(light):
    return _data(light).get("plinth_gobo", "NONE")


def gobo_params(light):
    data = _data(light)
    return {k: float(data.get("plinth_gobo_" + k, v)) for k, v in PARAM_DEFAULTS.items()}


# --- Applying -------------------------------------------------------------------------

def set_gobo(light, key):
    """Apply a gobo preset to a light (object or Light datablock). Returns
    skipped settings."""
    preset = GOBO_PRESETS[key]
    data = _data(light)
    data["plinth_gobo"] = key
    _clear_light_nodes(data)
    if "texture" in preset and SAVED_SIZE not in data:
        data[SAVED_SIZE] = (data.size, getattr(data, "size_y", data.size))
    elif "texture" not in preset and SAVED_SIZE in data:
        size, size_y = data[SAVED_SIZE]
        data.size = size
        if hasattr(data, "size_y"):
            data.size_y = size_y
        del data[SAVED_SIZE]
    skipped = []
    if "texture" in preset:
        _set_projector(data, gobo_params(data)["softness"])
    for obj in _objects(light):
        if "texture" in preset:
            skipped += _build_card(obj, preset)
        else:
            remove_card(obj)
    relink()
    return sorted(set(skipped))


def own_size(light):
    """(size, size_y) the light has without a pattern: its projector size is
    temporary (looks capture this one)."""
    data = _data(light)
    if SAVED_SIZE in data:
        return tuple(data[SAVED_SIZE])
    return data.size, getattr(data, "size_y", data.size)


def forget_size(light):
    """The light's size is being set from outside (a look, Reset): don't put the
    old one back when the pattern goes."""
    _data(light).pop(SAVED_SIZE, None)


def _set_projector(light, softness):
    data = _data(light)
    main = bpy.data.objects.get("Plinth_Target")
    scale = float(main.get("plinth_scale", 1.0)) if main is not None else 1.0
    lo, hi = PROJECTOR
    size = (lo + (hi - lo) * max(0.0, min(1.0, softness)) ** 1.5) * scale
    aspect = data.size_y / data.size if getattr(data, "size_y", 0) and data.size else 1.0
    data.size = size
    if hasattr(data, "size_y"):
        data.size_y = size * aspect


def sync(light):
    """Rebuild a light's card from its stored pattern (duplicated lights)."""
    key = current_gobo(light)
    if key != "NONE" and card_of(light) is None:
        set_gobo(light, key)


def set_gobo_params(light, scale=None, rotation=None, softness=None):
    """Light › Gobo sliders. Returns False when the light has no pattern."""
    data = _data(light)
    for k, v in (("scale", scale), ("rotation", rotation), ("softness", softness)):
        if v is not None:
            data["plinth_gobo_" + k] = float(v)
    if softness is not None and current_gobo(data) != "NONE":
        _set_projector(data, softness)
    applied = False
    for obj in _objects(light):
        card = card_of(obj)
        if card is not None:
            applied = _apply_params(obj, card) or applied
    return applied


def remove_card(light):
    """Take a light's card away (and its material). True if it had one."""
    card = card_of(light)
    if card is None:
        return False
    mat = card.active_material
    bpy.data.objects.remove(card)
    if mat is not None and mat.users == 0:
        bpy.data.materials.remove(mat)
    return True


def follow(light):
    """Point the card's drivers at the light's current target (after aiming)."""
    card = card_of(light)
    if card is not None:
        _apply_params(light, card)


# --- The card -----------------------------------------------------------------------

def _clear_light_nodes(data):
    """Older Plinth built gobos as light nodes (Cycles-only): take them out."""
    tree = getattr(data, "node_tree", None)
    if tree is None:
        return
    old = [n for n in tree.nodes if n.name.startswith(PREFIX)]
    for node in old:
        tree.nodes.remove(node)
    if old:
        emission = next((n for n in tree.nodes if n.type == "EMISSION"), None)
        if emission is not None:
            emission.inputs["Color"].default_value = (1, 1, 1, 1)


def _card_mesh():
    mesh = bpy.data.meshes.get(CARD_MESH)
    if mesh is None:
        h = CARD_SIZE / 2
        mesh = bpy.data.meshes.new(CARD_MESH)
        mesh.from_pydata([(-h, -h, 0), (h, -h, 0), (h, h, 0), (-h, h, 0)], [], [(0, 1, 2, 3)])
        mesh.update()
    return mesh


def _build_card(light, preset):
    name = PREFIX + _label(light)
    card = bpy.data.objects.get(name)
    if card is None:
        card = bpy.data.objects.new(name, _card_mesh())
    for col in light.users_collection:                 # the Plinth collection, beside its light
        if card.name not in col.objects:
            col.objects.link(card)
    card.parent = light
    card.matrix_parent_inverse.identity()
    card.rotation_euler = (0.0, 0.0, 0.0)
    card["plinth_gobo_card"] = light.name
    card.hide_select = True
    for attr in CARD_VISIBILITY:                       # shadows only
        if hasattr(card, attr):
            setattr(card, attr, False)
    for attr in ("hide_probe_volume", "hide_probe_sphere", "hide_probe_plane"):
        if hasattr(card, attr):
            setattr(card, attr, True)
    card.display_type = "TEXTURED"                    # wire objects aren't rendered in the viewport

    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    if not card.data.materials:                        # one shared mesh: the material is the object's
        card.data.materials.append(None)
    card.material_slots[0].link = "OBJECT"
    card.material_slots[0].material = mat
    skipped = _build_pattern(mat, preset)
    _apply_params(light, card)
    return skipped


def _build_pattern(mat, preset):
    """Card material: transparent where the pattern lets light through."""
    if hasattr(mat, "use_nodes") and not mat.use_nodes:
        mat.use_nodes = True
    for attr, value in (("surface_render_method", "DITHERED"), ("use_transparent_shadow", True),
                        ("blend_method", "HASHED"), ("shadow_method", "HASHED")):
        if hasattr(mat, attr):
            setattr(mat, attr, value)
    tree = mat.node_tree
    tree.nodes.clear()

    def add(idname, name, x, y=0):
        n = tree.nodes.new(idname)
        n.name = n.label = PREFIX + name
        n.location = (x, y)
        return n

    coord = add("ShaderNodeTexCoord", "Coord", -1000)
    mapping = add("ShaderNodeMapping", "Map", -800)
    tex = add(preset["texture"], "Pattern", -600)
    ramp = add("ShaderNodeValToRGB", "Ramp", -350)
    block = add("ShaderNodeBsdfDiffuse", "Block", -100, -150)
    clear = add("ShaderNodeBsdfTransparent", "Clear", -100, -300)
    mix = add("ShaderNodeMixShader", "Mix", 100)
    path = add("ShaderNodeLightPath", "Camera", 100, 300)
    unseen = add("ShaderNodeMixShader", "Unseen", 300)
    out = add("ShaderNodeOutputMaterial", "Output", 500)
    block.inputs["Color"].default_value = (0, 0, 0, 1)

    links = tree.links
    links.new(coord.outputs["Object"], mapping.inputs["Vector"])
    links.new(mapping.outputs[0], tex.inputs["Vector"])
    links.new(tex.outputs["Distance"] if "Distance" in tex.outputs else tex.outputs[0], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], mix.inputs["Fac"])            # white: light passes
    links.new(block.outputs[0], mix.inputs[1])
    links.new(clear.outputs[0], mix.inputs[2])
    # The viewport's Eevee draws camera-hidden objects anyway: camera rays see
    # straight through the card, only shadows see the pattern.
    links.new(path.outputs["Is Camera Ray"], unseen.inputs["Fac"])
    links.new(mix.outputs[0], unseen.inputs[1])
    links.new(clear.outputs[0], unseen.inputs[2])
    links.new(unseen.outputs[0], out.inputs["Surface"])

    skipped = []
    for attr, value in preset["props"].items():
        if hasattr(tex, attr):
            setattr(tex, attr, value)
        else:
            skipped.append(attr)
    for name, value in preset["inputs"].items():
        if name in tex.inputs:
            tex.inputs[name].default_value = value
        else:
            skipped.append(name)
    if preset.get("invert"):                       # dots: light inside the cells' centres
        ramp.color_ramp.elements[0].color = (1, 1, 1, 1)
        ramp.color_ramp.elements[1].color = (0, 0, 0, 1)
    return skipped


def _target(light):
    con = light.constraints.get("Plinth_Track")
    if con is not None and con.target is not None:
        return con.target
    name = light.get("plinth_target")
    return (bpy.data.objects.get(name) if name else None) or bpy.data.objects.get("Plinth_Target")


def _apply_params(light, card):
    import math
    preset = GOBO_PRESETS.get(current_gobo(light), {})
    mat = card.active_material
    tree = mat.node_tree if mat is not None else None
    mapping = tree.nodes.get(PREFIX + "Map") if tree else None
    ramp = tree.nodes.get(PREFIX + "Ramp") if tree else None
    if mapping is None or ramp is None or "ramp" not in preset:
        return False
    p = gobo_params(light)
    sc = max(0.05, p["scale"])
    mapping.inputs["Scale"].default_value = (sc, sc, sc)
    turn = p["rotation"] + preset.get("turn", 0.0)           # a lattice is a grid turned 45°
    mapping.inputs["Rotation"].default_value = (0.0, 0.0, math.radians(turn))
    lo, hi, interp = preset["ramp"]
    centre = (lo + hi) / 2
    width = max(hi - lo, 0.15 * p["softness"] * (hi - lo + 0.25))
    ramp.color_ramp.interpolation = interp if p["softness"] < 0.02 else "EASE"
    ramp.color_ramp.elements[0].position = max(0.0, centre - width / 2)
    ramp.color_ramp.elements[1].position = min(1.0, centre + width / 2)
    _drive(light, card, CARD_AT)
    return True


def _drive(light, card, k):
    """Card distance from the light: k of the way to the target, but at least
    CLEAR product radii short of it. Its scale equals that distance."""
    target = _target(light)
    if target is None:
        card.location = (0.0, 0.0, -1.0)
        return
    main = bpy.data.objects.get("Plinth_Target")
    radius = 0.5 * float(main.get("plinth_scale", 1.0)) if main is not None else 0.5
    expr = "max(min(%.4f*d, d - %.4f), 0.2*d)" % (k, CLEAR * radius)
    for path, index, sign in (("location", 2, "-"), ("scale", 0, ""), ("scale", 1, "")):
        card.driver_remove(path, index)
        drv = card.driver_add(path, index).driver
        drv.type = "SCRIPTED"
        var = drv.variables.new()
        var.name, var.type = "d", "LOC_DIFF"
        var.targets[0].id = light
        var.targets[1].id = target
        drv.expression = sign + expr
    card.scale[2] = 1.0


# --- Shadow linking -----------------------------------------------------------------

def relink():
    """Every light is shadowed by everything except other lights' cards. A light
    whose shadow linking the user set up themselves is left alone."""
    cards = [o for o in bpy.data.objects if o.get("plinth_gobo_card")]
    for light in [o for o in bpy.data.objects if o.type == "LIGHT"]:
        ll = getattr(light, "light_linking", None)
        if ll is None:
            return                                         # no shadow linking in this Blender
        ours = ll.blocker_collection is not None and ll.blocker_collection.name.startswith(BLOCKERS)
        if ll.blocker_collection is not None and not ours:
            continue
        others = [c for c in cards if c.get("plinth_gobo_card") != light.name]
        name = BLOCKERS + light.name
        if not others:
            ll.blocker_collection = None
            old = bpy.data.collections.get(name)
            if old is not None:
                bpy.data.collections.remove(old)
            continue
        col = bpy.data.collections.get(name) or bpy.data.collections.new(name)
        for obj in list(col.objects):
            if obj not in others:
                col.objects.unlink(obj)
        for obj in others:
            if obj.name not in col.objects:
                col.objects.link(obj)
        for entry in col.collection_objects:
            entry.light_linking.link_state = "EXCLUDE"
        ll.blocker_collection = col
