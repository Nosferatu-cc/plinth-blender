# SPDX-License-Identifier: GPL-3.0-or-later
"""Scanned maps for scenes (0.12): shipped with Plinth, 4K from kits at Ultra.

``assets/scenes/maps/<set>/<map>.webp`` (diff, nor, rough, disp; leaf atlases
carry their opacity in diff's alpha) and ``index.json`` (each set's real size
in metres, leaf rectangles, credits), built by ``tools/build_scene_maps.py``
from Poly Haven and ambientCG (CC0) and NASA (public domain).

Images load once as ``Plinth_Scene_Map_<set>_<map>`` (diff in sRGB, the rest
Non-Color). At Ultra a scene's kit may hold 4K copies (``tex4k_<set>_<map>``),
which win. clear() drops the ones nothing uses any more.
"""
import json
import os

import bpy

FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "scenes")
IMAGE = "Plinth_Scene_Map_"
MAPS = ("diff", "nor", "rough", "disp")
_INDEX = {}


def index():
    """The shipped index ({"sets", "leaves", "earth"}), read once."""
    if not _INDEX:
        path = os.path.join(FOLDER, "index.json")
        try:
            with open(path, encoding="utf-8") as f:
                _INDEX.update(json.load(f))
        except (OSError, ValueError):
            _INDEX.update({"sets": {}, "leaves": {}})
    return _INDEX


def has(name):
    return name in index().get("sets", {}) or name in index().get("leaves", {})


def size(name):
    """A set's real size in metres (w, h); 2 m when unknown."""
    entry = index().get("sets", {}).get(name)
    return tuple(entry["size"]) if entry else (2.0, 2.0)


def leaves(name):
    """A leaf atlas: {"what", "length" (m), "rects": [[u0, v0, u1, v1, stem_u], ...]} or None."""
    return index().get("leaves", {}).get(name)


def _load(name, m, path):
    full = "%s%s_%s" % (IMAGE, name, m)
    img = bpy.data.images.get(full)
    if img is not None and img.filepath and os.path.normcase(bpy.path.abspath(img.filepath)) == os.path.normcase(path):
        return img
    if img is not None:
        bpy.data.images.remove(img)
    try:
        img = bpy.data.images.load(path, check_existing=False)
    except RuntimeError:
        return None
    img.name = full
    try:
        img.colorspace_settings.name = "sRGB" if m in ("diff", "day", "night") else "Non-Color"
    except TypeError:
        pass
    if m == "diff" and name in index().get("leaves", {}):
        img.alpha_mode = "CHANNEL_PACKED"
    return img


def textures(name, kit=None, ultra=False):
    """{map: image} for a set: a kit's 4K copy at Ultra, else the shipped
    2K/1K maps, else the kit's own set of that name (older kits), else {}."""
    out = {}
    images = (kit or {}).get("images", {})
    if ultra:
        for m in MAPS:
            img = images.get("tex4k_%s_%s" % (name, m))
            if img is not None:
                out[m] = img
        if out.get("diff"):
            _colourspaces(out)
            return out
        out = {}
    folder = os.path.join(FOLDER, "maps", name)
    for m in MAPS:
        path = os.path.join(folder, m + ".webp")
        if os.path.isfile(path):
            img = _load(name, m, path)
            if img is not None:
                out[m] = img
    if out.get("diff"):
        return out
    out = {}
    for m in MAPS + ("ao",):
        img = images.get("tex_%s_%s" % (name, m))
        if img is not None:
            out[m] = img
    _colourspaces(out)
    return out if out.get("diff") else {}


def _colourspaces(maps):
    for m, img in maps.items():
        if m != "diff":
            try:
                img.colorspace_settings.name = "Non-Color"
            except TypeError:
                pass


def earth(m, kit=None, ultra=False):
    """Earth's map (day, night, water): the kit's 4K at Ultra, else shipped."""
    if ultra:
        img = (kit or {}).get("images", {}).get("tex4k_earth_" + m)
        if img is not None:
            return img
    path = os.path.join(FOLDER, "maps", "earth", m + ".webp")
    return _load("earth", m, path) if os.path.isfile(path) else None


def unload():
    """Drop shipped map images nothing uses any more."""
    dead = [img for img in bpy.data.images if img.name.startswith(IMAGE) and img.users == 0]
    if dead:
        bpy.data.batch_remove(dead)
