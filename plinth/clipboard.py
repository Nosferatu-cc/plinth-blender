# SPDX-License-Identifier: GPL-3.0-or-later
"""Material copy and paste (0.9.1): in the Plinth workspace, M + left-click a
part copies what it wears; M + right-click another part pastes it there with
all its settings.

A Plinth material is copied as a recipe (preset, every control's value, the
image, the library entry it came from) and pasted as the target part's own
material, so editing one never changes the other. Your own materials (imported
or hand-made) are shared, like Blender's Link Materials. Decals stay with the
part they were placed on.
"""
import bpy

from . import materials

CLIP = {}


def _value(v):
    try:
        return tuple(v)                       # colours and vectors
    except TypeError:
        return v


def copy(obj, index=None):
    """Remember the part's material. Returns its label, or None (no material)."""
    if obj is None or obj.type != "MESH" or not obj.material_slots:
        return None
    i = materials._slot_index(obj, index)
    mat = obj.material_slots[i].material if i < len(obj.material_slots) else None
    if mat is None:
        return None
    from . import bake, matlib
    if bake.is_baked(obj):
        mat = bake.baked_source(mat) or mat
    CLIP.clear()
    if mat.get("plinth_preset") in materials.PRESETS:
        type_key = mat["plinth_type"]
        node = materials.group_node(mat)
        values = {name: _value(materials.socket(node, type_key, name).default_value)
                  for name, *_ in materials.controls(type_key)}
        image = materials.image_of(mat) if type_key == "IMAGE" else None
        CLIP.update(kind="recipe", preset=mat["plinth_preset"], type=type_key, values=values,
                    image=image.name if image is not None else None, library=mat.get(matlib.TAG),
                    label=materials.PRESETS[mat["plinth_preset"]]["label"],
                    maps={ch: img.name for ch, img in materials.texture_maps(mat).items()})
    else:
        CLIP.update(kind="material", name=mat.name, label=mat.name)
    return CLIP["label"]


def label():
    return CLIP.get("label")


def paste(obj, index=None):
    """Put the copied material on the part. Returns False when there's nothing
    to paste (or it no longer exists)."""
    if not CLIP or obj is None or obj.type != "MESH":
        return False
    if not obj.material_slots:
        obj.data.materials.append(None)
    i = materials._slot_index(obj, index)
    if CLIP["kind"] == "recipe":
        materials.apply_preset(obj, CLIP["preset"], i, keep_colour=False, fresh=True)
        kinds = {n: k for n, k, *_ in materials.controls(CLIP["type"])}
        for name, value in CLIP["values"].items():
            if name in kinds:
                materials.set_input(obj, name, bool(value) if kinds[name] == "bool" else value, i)
        image = bpy.data.images.get(CLIP["image"] or "")
        if image is not None:
            materials.set_image(obj, image, i)
        maps = {ch: bpy.data.images.get(name) for ch, name in CLIP.get("maps", {}).items()}
        if any(maps.values()):                    # a texture set brings its maps
            from . import textures
            textures.assign(obj, {ch: img for ch, img in maps.items() if img is not None}, i, replace=True)
        if CLIP.get("library"):
            from . import matlib
            obj.material_slots[i].material[matlib.TAG] = CLIP["library"]
        return True
    mat = bpy.data.materials.get(CLIP["name"])
    if mat is None:
        return False
    from . import bake, decals
    bake.unbake(obj, i)
    materials._remember_original(obj, i)
    current = obj.material_slots[i].material
    if current is not None and current.get("plinth_preset"):
        materials.remember_edits(current)
    slot = obj.material_slots[i]
    slot.link = "OBJECT"
    slot.material = mat
    if decals.layers(obj, i):
        decals.rewire(obj, i)
    return True
