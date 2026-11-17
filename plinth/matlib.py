# SPDX-License-Identifier: GPL-3.0-or-later
"""Your material library: save a part's material once, reuse it on any part, in
any file (Material › Saved).

* A Plinth material is saved as a *recipe*: its preset, type and every control
  value (and an Image material's picture, by path). Using it applies the
  preset and sets the values, so nothing is duplicated and it stays editable.
* Any other material (your own, an imported one) is saved as a small .blend
  holding just that material and what it needs. Using it appends it once per
  file (tagged ``plinth_library``) and puts it on the part; Original still
  brings back what the part had before.

Entries live as files in the add-on's user folder (``materials/``): one JSON
per entry, plus a .blend for the "blend" kind. Bad files are skipped.
"""

import json
import os
import re
import time

import bpy

from . import materials

TAG = "plinth_library"          # on a material: the library entry it came from
KINDS = ("recipe", "blend")
SCHEMA = 2                      # 2 (0.9): optional "folder"; written atomically


def folder():
    """Where saved materials live (created on demand)."""
    try:
        return bpy.utils.extension_path_user(__package__, path="materials", create=True)
    except (ValueError, AttributeError):     # running as a legacy add-on / from source
        return bpy.utils.user_resource("CONFIG", path=os.path.join("plinth", "materials"), create=True)


def _slug(label):
    return re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_").lower() or "material"


def _json(value):
    if hasattr(value, "__len__") and not isinstance(value, str):
        return [round(float(v), 5) for v in value]
    if isinstance(value, bool):
        return value
    return round(float(value), 5)


# --- Reading ------------------------------------------------------------------------

_seen = {}             # folder -> (stamp, {id: entry}): read again only when files change


def _stamp(where):
    try:
        return tuple(sorted((e.name, e.stat().st_mtime_ns) for e in os.scandir(where)))
    except OSError:
        return None


def entries(where=None):
    """{id: entry} for every readable saved material, oldest first. Read-only
    (the card calls it while drawing: cached until a file changes)."""
    where = where or folder()
    stamp = _stamp(where)
    if stamp is None:
        return {}
    cached = _seen.get(where)
    if cached is not None and cached[0] == stamp:
        return dict(cached[1])
    found = []
    for fn in os.listdir(where):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(where, fn), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("kind") not in KINDS or not data.get("label"):
            continue
        if data["kind"] == "recipe" and data.get("preset") not in materials.PRESETS:
            continue                                   # from a newer Plinth, or a removed preset
        if data["kind"] == "blend" and not os.path.isfile(os.path.join(where, fn[:-5] + ".blend")):
            continue
        data["id"] = fn[:-5]
        found.append(data)
    found.sort(key=lambda d: (d.get("saved", 0), d["id"]))
    result = {d["id"]: d for d in found}
    _seen[where] = (stamp, result)
    return dict(result)


def current_entry(obj, index=None):
    """The library entry this part's material came from (and still is), or None."""
    i = materials._slot_index(obj, index)
    if i >= len(obj.material_slots) or obj.material_slots[i].material is None:
        return None
    mat = obj.material_slots[i].material
    return mat.get(TAG) or None


def swatch(entry):
    """sRGB colour for the entry's tile until its preview is in."""
    from . import colours
    return colours.srgb(tuple(entry.get("colour") or (0.5, 0.5, 0.5))[:3])


# --- Saving -----------------------------------------------------------------------------

def default_label(obj, index=None):
    i = materials._slot_index(obj, index)
    part = materials.part_label(obj, i)
    preset = materials.PRESETS.get(materials.current_preset(obj, i) or "", {}).get("label")
    return "%s · %s" % (part, preset) if preset else part


def _unique(label, where):
    taken = {e["label"] for e in entries(where).values()}
    if label not in taken:
        return label
    n = 2
    while "%s %d" % (label, n) in taken:
        n += 1
    return "%s %d" % (label, n)


def save(obj, index=None, label=None, where=None):
    """Save the part's material to the library. Returns the new entry's id, or
    None when the part has no material."""
    where = where or folder()
    os.makedirs(where, exist_ok=True)
    i = materials._slot_index(obj, index)
    if i >= len(obj.material_slots) or obj.material_slots[i].material is None:
        return None
    mat = obj.material_slots[i].material
    from . import bake
    if bake.is_baked(obj):                                  # save the procedural it came from
        mat = bake.baked_source(mat) or mat
    label = _unique((label or default_label(obj, i)).strip() or "Material", where)
    entry_id = _slug(label)
    while os.path.exists(os.path.join(where, entry_id + ".json")):
        entry_id += "_"
    colour = materials._colour_of(mat)
    data = {"label": label, "saved": time.time(), "colour": _json(colour) if colour is not None else None}
    if mat.get("plinth_preset") in materials.PRESETS:
        type_key = mat["plinth_type"]
        node = materials.group_node(mat)
        data.update(kind="recipe", preset=mat["plinth_preset"], type=type_key, values={
            name: _json(materials.socket(node, type_key, name).default_value)
            for name, *_ in materials.controls(type_key)})
        image = materials.image_of(mat) if type_key == "IMAGE" else None
        if image is not None and image.filepath:
            data["image"] = bpy.path.abspath(image.filepath)
        maps = {ch: bpy.path.abspath(img.filepath) for ch, img in materials.texture_maps(mat).items()
                if img.filepath}
        if maps:                                            # a texture set: its files, by map
            data["maps"] = maps
    else:
        data["kind"] = "blend"
        copy = mat.copy()                                  # its own name, its own tag in the file
        copy.name = "Plinth_Lib_" + entry_id
        copy[TAG] = entry_id
        try:
            bpy.data.libraries.write(os.path.join(where, entry_id + ".blend"), {copy},
                                     path_remap="ABSOLUTE", fake_user=True)
        finally:
            bpy.data.materials.remove(copy)
    _write(os.path.join(where, entry_id + ".json"), data)
    if data["kind"] == "recipe":
        mat[TAG] = entry_id                                 # this part shows as that entry
    return entry_id


def _read(entry_id, where):
    path = os.path.join(where, entry_id + ".json")
    if not os.path.isfile(path):
        return None, path
    with open(path, encoding="utf-8") as f:
        return json.load(f), path


def _write(path, data):
    data["schema"] = SCHEMA
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def rename(entry_id, label, where=None):
    where = where or folder()
    data, path = _read(entry_id, where)
    label = label.strip()
    if not label or data is None:
        return False
    data["label"] = label
    _write(path, data)
    return True


# --- Organising (0.9) -------------------------------------------------------------------
# Folders are a field on each entry ("folder": "Clients/Acme" style names are
# fine), not directories: moving is a rename of one field, and an entry from
# an older Plinth is simply "unfiled".

def folders(where=None):
    """Folder names in use, sorted."""
    return sorted({e.get("folder", "") for e in entries(where).values()} - {""}, key=str.lower)


def move(entry_id, folder_name, where=None):
    where = where or folder()
    data, path = _read(entry_id, where)
    if data is None:
        return False
    name = (folder_name or "").strip().strip("/")
    if name:
        data["folder"] = name
    else:
        data.pop("folder", None)
    _write(path, data)
    return True


def update(entry_id, obj, index=None, where=None):
    """Replace a saved recipe's values with this part's material (keeps its
    name, folder and id). Saved Blender materials are replaced the same way."""
    where = where or folder()
    old, path = _read(entry_id, where)
    if old is None:
        return False
    fresh = save(obj, index, label="__update__", where=where)
    if fresh is None:
        return False
    new, new_path = _read(fresh, where)
    new.update(label=old["label"], saved=old.get("saved", new.get("saved")))
    if old.get("folder"):
        new["folder"] = old["folder"]
    _write(path, new)
    os.remove(new_path)
    blend = os.path.join(where, fresh + ".blend")
    if os.path.isfile(blend):
        os.replace(blend, os.path.join(where, entry_id + ".blend"))
    elif os.path.isfile(os.path.join(where, entry_id + ".blend")):
        os.remove(os.path.join(where, entry_id + ".blend"))     # it is a recipe now
    import bpy as _bpy
    for mat in _bpy.data.materials:
        if mat.get(TAG) == fresh:
            mat[TAG] = entry_id
    return True


def duplicate(entry_id, where=None):
    """A copy of an entry ("Name 2"), next to it. Returns the new id."""
    import shutil
    where = where or folder()
    data, path = _read(entry_id, where)
    if data is None:
        return None
    label = _unique(data["label"], where)
    new_id = _slug(label)
    while os.path.exists(os.path.join(where, new_id + ".json")):
        new_id += "_"
    data["label"] = label
    data["saved"] = time.time()
    _write(os.path.join(where, new_id + ".json"), data)
    blend = os.path.join(where, entry_id + ".blend")
    if os.path.isfile(blend):
        shutil.copyfile(blend, os.path.join(where, new_id + ".blend"))
    return new_id


def remove(entry_id, where=None):
    """Delete an entry's files. Materials already using it stay as they are."""
    where = where or folder()
    gone = False
    for ext in (".json", ".blend"):
        path = os.path.join(where, entry_id + ext)
        if os.path.isfile(path):
            os.remove(path)
            gone = True
    for mat in bpy.data.materials:
        if mat.get(TAG) == entry_id and "plinth_preset" in mat:
            del mat[TAG]
    if gone:
        from . import catalog
        catalog.forget("mine:" + entry_id)
    return gone


# --- Using --------------------------------------------------------------------------------

def apply(obj, entry_id, index=None, where=None):
    """Put a saved material on a part. Returns False if the entry is gone."""
    where = where or folder()
    entry = entries(where).get(entry_id)
    if entry is None:
        return False
    if not obj.material_slots:
        obj.data.materials.append(None)
    i = materials._slot_index(obj, index)
    if entry["kind"] == "recipe":
        materials.apply_preset(obj, entry["preset"], i, keep_colour=False, fresh=True)
        kinds = {n: k for n, k, *_ in materials.controls(entry["type"])}
        for name, value in entry.get("values", {}).items():
            if name in kinds:
                materials.set_input(obj, name, bool(value) if kinds[name] == "bool" else
                                    (tuple(value) if isinstance(value, list) else value), i)
        if entry.get("image") and os.path.isfile(entry["image"]):
            materials.set_image(obj, bpy.data.images.load(entry["image"], check_existing=True), i)
        maps = {ch: p for ch, p in dict(entry.get("maps") or {}).items() if isinstance(p, str) and os.path.isfile(p)}
        if maps:
            from . import textures
            textures.assign(obj, maps, i, replace=True)
        obj.material_slots[i].material[TAG] = entry_id
        return True
    mat = next((m for m in bpy.data.materials if m.get(TAG) == entry_id and "plinth_preset" not in m), None)
    if mat is None:
        with bpy.data.libraries.load(os.path.join(where, entry_id + ".blend"), link=False) as (src, dst):
            dst.materials = list(src.materials)
        mat = next((m for m in dst.materials if m is not None and m.get(TAG) == entry_id), None)
        if mat is None:
            return False
        mat.use_fake_user = False
    from . import bake
    bake.unbake(obj, i)
    materials._remember_original(obj, i)                    # Original brings back what was there
    current = obj.material_slots[i].material
    if current is not None and current.get("plinth_preset"):
        materials.remember_edits(current)
    slot = obj.material_slots[i]
    slot.link = "OBJECT"
    slot.material = mat
    from . import decals
    if decals.layers(obj, i):
        decals.rewire(obj, i)
    return True
