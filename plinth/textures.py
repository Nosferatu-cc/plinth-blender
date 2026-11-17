# SPDX-License-Identifier: GPL-3.0-or-later
"""Texture sets (0.13): put the image maps a model came with onto its parts.

Imported models (FBX, OBJ, glTF rips) often carry textures Blender didn't
hook up: the file stores *paths*, and when the images moved, or the importer
only wired the colour map, the part renders flat. Plinth finds them four ways,
and manual always works:

1. **Automatically** (``find_for_part``): the images in the part's original
   material, classified by the socket they fed (Base Color, Roughness, Normal
   Map...) or else by file name; broken paths are searched for next to the
   .blend, the images' own folders and their textures/maps subfolders, like
   Blender's File › External Data › Find Missing Files; then the rest of the set
   is filled from sibling files with the same stem (an FBX that only linked the
   diffuse still gets its normal and roughness).
2. **From a folder** (``match_folder``): every image in a folder (two levels
   deep), grouped into sets by stem, the set whose name best matches the part.
3. **Images already in the file** (``images_in_file``), picked per map.
4. **By hand**: any channel, any image, from the file or from disk.

Channels follow Node Wrangler's Principled-setup tags (the convention most
Blender users and texture sites use), matched as whole words in the file name,
case-insensitive, plus packed ORM/ARM maps. Pure functions over names where
possible (tested without images); the rest takes bpy objects.
"""
import os
import re

import bpy

from . import materials

# (channel, label, tags). Order matters: more specific first ("basecolor"
# before "color", "orm" before "ao", "normal" before "nor").
CHANNELS = [
    ("ORM", "ORM", ("orm", "arm", "occlusionroughnessmetallic", "aorm")),
    ("COLOUR", "Colour", ("basecolor", "basecolour", "albedo", "diffuse", "diff", "dif", "col", "color",
                          "colour", "base", "alb", "bc")),
    ("ALPHA", "Opacity", ("opacity", "alpha", "transparency", "transparent")),
    ("METAL", "Metal", ("metallic", "metalness", "metal", "mtl", "met")),
    ("ROUGH", "Roughness", ("roughness", "rough", "rgh")),
    ("GLOSS", "Gloss", ("glossiness", "glossy", "gloss")),
    ("NORMAL", "Normal", ("normal", "normalgl", "normaldx", "nor", "nrm", "nrml", "norm", "nml")),
    ("BUMP", "Height", ("displacement", "displace", "disp", "dsp", "height", "heightmap", "bump", "bmp")),
    ("AO", "Occlusion", ("ambientocclusion", "occlusion", "ambient", "ao", "occ")),
    ("GLOW", "Glow", ("emission", "emissive", "emit", "glow", "illum", "illumination")),
]
LABELS = {k: label for k, label, _t in CHANNELS}
ORDER = [k for k, _l, _t in CHANNELS]
SHOWN = ("COLOUR", "ROUGH", "METAL", "NORMAL", "BUMP", "AO", "ALPHA", "GLOW", "GLOSS", "ORM")
EXTENSIONS = (".png", ".jpg", ".jpeg", ".tga", ".tif", ".tiff", ".exr", ".hdr", ".bmp", ".webp", ".dds",
              ".psd")
FOLDERS = ("textures", "texture", "tex", "maps", "images", "materials", "source", "sourceimages")
FOLDER_PROP = "plinth_texture_folder"     # scene[...]: the last folder textures came from
# Principled input (or what fed it) -> channel, for images in an imported material.
SOCKETS = {"Base Color": "COLOUR", "Color": "COLOUR", "Alpha": "ALPHA", "Metallic": "METAL",
           "Roughness": "ROUGH", "Normal": "NORMAL", "Height": "BUMP", "Emission Color": "GLOW",
           "Emission": "GLOW"}


# --- Names ----------------------------------------------------------------------------

# Colour words that name the map outright (not "base", "col": those are often
# part of a set's name).
GENERIC_FREE_COLOUR = {"basecolor", "basecolour", "albedo", "diffuse", "diff"}


def _ordered_words(name):
    """[(word, position)]: each word, and each joined pair at its second word's position."""
    stem = os.path.splitext(os.path.basename(name))[0]
    stem = re.sub(r"([a-z])([A-Z])", r"\1 \2", stem)
    parts = [w for w in re.split(r"[^A-Za-z]+", stem.lower()) if w]
    return [(w, i) for i, w in enumerate(parts)] + [(a + b, i + 1) for i, (a, b) in enumerate(zip(parts, parts[1:]))]


def words(name):
    """Lower-case words of a file or material name: split on separators,
    digits and camelCase ("WoodFloor_BaseColor.1001.png" -> wood, floor,
    base, color, basecolor...). Joined neighbours are added so "Base_Color"
    and "BaseColor" both read as basecolor."""
    stem = os.path.splitext(os.path.basename(name))[0]
    stem = re.sub(r"([a-z])([A-Z])", r"\1 \2", stem)
    parts = [w for w in re.split(r"[^A-Za-z]+", stem.lower()) if w]
    joined = [a + b for a, b in zip(parts, parts[1:])]
    return parts + joined


def classify(name):
    """The channel a file name says it is, or None. Whole words only, so
    "collar" is never colour; the most specific tag wins."""
    ordered = _ordered_words(name)
    ws = {w for w, _pos in ordered}
    last = {}                                            # channel -> the latest word naming it
    for word, pos in ordered:
        for ch, _label, tags in CHANNELS:
            if word in tags:
                last[ch] = max(last.get(ch, -1), pos)
                break
    if "ORM" in last and len(last) > 1 and not ws & {"orm", "aorm", "occlusionroughnessmetallic"}:
        del last["ORM"]                   # "robot_arm_basecolor": an arm, not a packed map
    if "COLOUR" in last and len(last) > 1 and not ws & GENERIC_FREE_COLOUR:
        del last["COLOUR"]                # "Normal_Base": base is the set's name, normal the map
    if not last:
        return None
    # Maps are named set first, channel last ("Metal07_Roughness"): the last word wins.
    return max(last, key=lambda ch: (last[ch], -ORDER.index(ch)))


def is_directx(name):
    """A DirectX normal map (green points down): "_dx" / "normaldx" in the name."""
    ws = set(words(name))
    return bool(ws & {"dx", "directx", "normaldx"})


def set_stem(name):
    """A texture set's name: the file name without its channel words, resolution
    and UDIM tile ("Wood_Floor_Rough_2K.png" -> "wood floor")."""
    stem = os.path.splitext(os.path.basename(name))[0]
    stem = re.sub(r"([a-z])([A-Z])", r"\1 \2", stem)
    tags = {t for _c, _l, ts in CHANNELS for t in ts} | {"gl", "dx", "directx", "map", "tex", "texture"}
    keep = [w for w in re.split(r"[^A-Za-z0-9]+", stem.lower())
            if w and w not in tags and not re.fullmatch(r"\d+k|\d+|\d+x\d+", w)]
    return " ".join(keep)


def score(stem, hint_words):
    """How well a set's stem matches a part (material/object name words)."""
    ws = set(re.split(r"\s+", stem)) - {""}
    hint = {w for w in hint_words if len(w) > 2}
    return len(ws & hint)


def group_sets(paths):
    """{stem: {channel: path}} for image files, by set stem. A channel seen
    twice keeps the first (sorted order; the shortest name)."""
    sets = {}
    for p in sorted(paths, key=lambda q: (len(os.path.basename(q)), q)):
        ch = classify(p)
        if ch is None:
            continue
        sets.setdefault(set_stem(p), {}).setdefault(ch, p)
    return sets


def best_set(paths, hint_words=()):
    """The set (channel -> path) a part should get from these files: best name
    match, then the most maps. {} when nothing is a texture map."""
    sets = group_sets(paths)
    if not sets:
        return {}
    stem = max(sets, key=lambda s: (score(s, hint_words), "COLOUR" in sets[s], len(sets[s]), -len(s)))
    return dict(sets[stem])


# --- Files ----------------------------------------------------------------------------

def scan_folder(folder, depth=2):
    """Image files in a folder and its subfolders (``depth`` levels)."""
    found = []
    if not folder or not os.path.isdir(folder):
        return found
    base = os.path.normpath(folder).count(os.sep)
    for root, dirs, files in os.walk(folder):
        if os.path.normpath(root).count(os.sep) - base >= depth:
            dirs[:] = []
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        found += [os.path.join(root, f) for f in files if f.lower().endswith(EXTENSIONS)]
        if len(found) > 4000:                            # a whole drive picked by mistake
            break
    return found


def _abspath(image):
    return os.path.normpath(bpy.path.abspath(image.filepath, library=image.library)) if image.filepath else ""


def is_missing(image):
    """An image whose file can't be found and isn't packed in the .blend."""
    if image.packed_file is not None or image.source not in {"FILE", "TILED", "SEQUENCE"}:
        return False
    path = _abspath(image)
    return not path or not os.path.exists(path.replace("<UDIM>", "1001"))


def search_folders(images=(), extra=()):
    """Where missing textures usually are: next to the .blend, the images' own
    (stored) folders, a texture folder used before, and the usual subfolders
    of each (textures, maps, ...), plus one level up."""
    roots = [f for f in extra if f]
    if bpy.data.filepath:
        roots.append(os.path.dirname(bpy.data.filepath))
    for img in images:
        if img.filepath:
            roots.append(os.path.dirname(_abspath(img)))
    scene = bpy.context.scene if bpy.context else None
    if scene is not None and scene.get(FOLDER_PROP):
        roots.append(scene[FOLDER_PROP])
    out = []
    for r in roots:
        for d in [r, os.path.dirname(r)] + [os.path.join(r, s) for s in FOLDERS] + \
                [os.path.join(r, s.capitalize()) for s in FOLDERS]:
            d = os.path.normpath(d)
            if d not in out and os.path.isdir(d):
                out.append(d)
    return out


def relink(image, folders):
    """Find a missing image's file by name in ``folders`` (two levels deep,
    case-insensitive) and point the image at it. True when found."""
    want = os.path.basename(image.filepath.replace("\\", "/")).lower() or image.name.lower()
    if not want:
        return False
    for folder in folders:
        for path in scan_folder(folder):
            if os.path.basename(path).lower() == want:
                image.filepath = path
                try:
                    image.reload()
                except RuntimeError:
                    pass
                return True
    return False


def load(path):
    """A bpy image for a file (reused if already loaded). None if unreadable."""
    try:
        return bpy.data.images.load(path, check_existing=True)
    except RuntimeError:
        return None


# --- Parts ----------------------------------------------------------------------------

def original_material(obj, index):
    """The material the part wore before Plinth (or wears, if not Plinth's)."""
    slot = obj.material_slots[index] if index < len(obj.material_slots) else None
    if slot is None:
        return None
    if slot.material is not None and "plinth_preset" not in slot.material:
        return slot.material
    entry = obj.get(materials.ORIGINALS, {}).get(str(index))
    name = entry.get("name") if entry else ""
    mat = bpy.data.materials.get(name) if name else None
    if mat is None and slot.material is not None and "plinth_preset" in slot.material:
        mat = slot.material if slot.material.get(materials.TEX_MAPS) else None
    return mat


def _fed_socket(node, seen=None):
    """The shader input an image node ends up feeding, following one step
    through Normal Map / Bump / Separate / Invert / math nodes."""
    seen = seen or set()
    for out in node.outputs:
        for lk in out.links:
            to = lk.to_node
            if to in seen:
                continue
            seen.add(to)
            if to.type in {"BSDF_PRINCIPLED", "EMISSION", "BSDF_DIFFUSE", "GROUP"}:
                return lk.to_socket.name
            if to.type == "NORMAL_MAP":
                return "Normal"
            if to.type == "BUMP":
                return "Height"
            found = _fed_socket(to, seen)
            if found:
                return found
    return None


def images_in_material(mat):
    """{channel: image} from an (imported) material's image nodes: by what each
    one feeds, else by its file name."""
    out = {}
    if mat is None or mat.node_tree is None:
        return out
    for node in mat.node_tree.nodes:
        if node.type != "TEX_IMAGE" or node.image is None or node.name.startswith(materials.TEX_PREFIX):
            continue
        ch = SOCKETS.get(_fed_socket(node) or "") or classify(node.image.filepath or node.image.name)
        if ch == "ROUGH" and classify(node.image.filepath or node.image.name) == "GLOSS":
            ch = "GLOSS"
        if ch is not None:
            out.setdefault(ch, node.image)
    if mat.get(materials.TEX_MAPS):                     # ours already: what it has
        out.update(materials.texture_maps(mat))
    return out


def hint_words(obj, index):
    mat = original_material(obj, index)
    ws = words(obj.name) + words(obj.data.name if obj.data else "")
    if mat is not None:
        ws += words(mat.name)
    return set(ws)


def find_for_part(obj, index=None, extra_folders=()):
    """{channel: image} for a part, found automatically (see the module
    docstring). Repairs broken image paths it can. {} when nothing is found."""
    i = obj.active_material_index if index is None else index
    mat = original_material(obj, i)
    found = images_in_material(mat)
    folders = search_folders(list(found.values()), extra_folders)
    for img in found.values():
        if is_missing(img):
            relink(img, folders)
    found = {ch: img for ch, img in found.items() if not is_missing(img)}
    # The rest of the set: files beside a found map with the same stem.
    anchor = found.get("COLOUR") or next(iter(found.values()), None)
    hints = hint_words(obj, i)
    if anchor is not None and anchor.filepath:
        stem = set_stem(_abspath(anchor))
        sibling = group_sets(scan_folder(os.path.dirname(_abspath(anchor)), depth=1)).get(stem, {})
        for ch, path in sibling.items():
            if ch not in found:
                img = load(path)
                if img is not None:
                    found[ch] = img
    elif not found:                                     # nothing in the material: look nearby by name
        paths = [p for f in folders for p in scan_folder(f, depth=1)]
        chosen = best_set(paths, hints)
        if chosen and score(set_stem(next(iter(chosen.values()))), hints) > 0:
            found = {ch: img for ch, img in ((ch, load(p)) for ch, p in chosen.items()) if img is not None}
    return found


def match_folder(obj, folder, index=None):
    """{channel: image} from a folder for this part: the best-matching set."""
    i = obj.active_material_index if index is None else index
    chosen = best_set(scan_folder(folder), hint_words(obj, i))
    out = {}
    for ch, path in chosen.items():
        img = load(path)
        if img is not None:
            out[ch] = img
    return out


def images_in_file():
    """[(image, channel guess)] for every image in the .blend a part could use
    (not render results, previews or Plinth's own bakes), maps first."""
    out = []
    for img in bpy.data.images:
        if img.type not in {"IMAGE", "UV_TEST"} or img.name.startswith(("Plinth_Bake", "Plinth_Preview")):
            continue
        out.append((img, classify(img.filepath or img.name)))
    out.sort(key=lambda t: (t[1] is None, ORDER.index(t[1]) if t[1] in ORDER else 99, t[0].name.lower()))
    return out


def assign(obj, maps, index=None, replace=False):
    """Put maps ({channel: image or path}) on a part, as a Texture set (the
    part becomes one if it isn't). ``replace`` clears maps not given. Returns
    the part's maps."""
    i = obj.active_material_index if index is None else index
    mat = materials.part_material(obj, i)
    if mat is None or mat.get("plinth_type") != "TEXTURES":
        mat_maps = dict(maps)
        materials.apply_preset(obj, "IMAGE_TEXTURES", i)    # may find maps of its own
        mat = materials.part_material(obj, i)
        maps = {**({} if replace else {k: v for k, v in materials.texture_maps(mat).items()}), **mat_maps}
        replace = True
    mat = materials.make_own(obj, i)
    current = {} if replace else dict(mat.get(materials.TEX_MAPS, {}))
    for ch, img in maps.items():
        if isinstance(img, str):
            img = load(img)
        if img is None or ch not in materials.TEX_CHANNELS:
            continue
        current[ch] = img.name
        if ch == "NORMAL":
            mat[materials.TEX_MAPS + "_dx"] = is_directx(img.filepath or img.name)
    mat[materials.TEX_MAPS] = current
    materials.sync_textures(mat, obj)
    return materials.texture_maps(mat)


def set_map(obj, channel, image, index=None):
    """One map by hand (an image, or a file path)."""
    return assign(obj, {channel: image}, index)


def clear_map(obj, channel, index=None):
    i = obj.active_material_index if index is None else index
    mat = materials.part_material(obj, i)
    if mat is None or mat.get("plinth_type") != "TEXTURES":
        return {}
    mat = materials.make_own(obj, i)
    current = dict(mat.get(materials.TEX_MAPS, {}))
    current.pop(channel, None)
    mat[materials.TEX_MAPS] = current
    materials.sync_textures(mat, obj)
    return materials.texture_maps(mat)


def summary(maps):
    """'Colour, Normal, Roughness' for the card (in display order)."""
    return ", ".join(LABELS[ch] for ch in SHOWN if ch in maps)
