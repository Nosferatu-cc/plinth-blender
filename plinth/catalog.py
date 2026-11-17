# SPDX-License-Identifier: GPL-3.0-or-later
"""The material catalog (0.9): one list of everything a part can wear.

Built-in presets, content-pack presets and the user's own library, sorted into
*families* people recognise (Paint, Metal, Glass, Plastic & trim, ...).
Material *types* (UDIN_PEARL, PLASTIC, ...) stay an implementation detail:
a family can mix types, and a type can serve several families.

Also kept here, per user (across files): favourites, recently used, and which
controls each type pins to the Material card. Read paths are cached on the
file's stamp because the browser reads them while Blender draws; writes only
happen from clicks.
"""

import json
import os
import re
import time

from . import materials

# (key, label, description). Order = the browser's sidebar order.
FAMILIES = [
    ("PAINT", "Paint", "Car paint: solid, metallic, pearl, candy, matte, wrap"),
    ("METAL", "Metal", "Chrome, aluminium, brushed, gold, titanium"),
    ("GLASS", "Glass", "Windows, lenses, crystal, acrylic"),
    ("PLASTIC", "Plastic & trim", "Gloss, satin and textured plastics, piano black"),
    ("CARBON", "Carbon", "Woven and forged carbon fibre"),
    ("FABRIC", "Fabric & leather", "Leather, suede, Alcantara, cloth"),
    ("RUBBER", "Rubber & tyres", "Tyres, sidewalls, seals"),
    ("NATURAL", "Wood & stone", "Wood, ceramic, concrete, paper"),
    ("BRAKE", "Brakes", "Brake discs and calipers"),
    ("GLOW", "Glow & screens", "LEDs, lamps, displays, neon"),
    ("IMAGE", "Image", "Your own picture on a part"),
]
FAMILY_LABELS = {k: label for k, label, _d in FAMILIES}

# Sidebar sections above the families.
SECTIONS = [("FAV", "Favourites"), ("RECENT", "Recent"), ("SCENE", "In this scene"),
            ("MINE", "My library")]

_TRIM_FAMILY = {"PLASTIC": "PLASTIC", "UDIN_PLASTIC": "PLASTIC", "CARBON": "CARBON", "WOOD": "NATURAL",
                "STONE": "NATURAL", "BLACKOUT": "PLASTIC",
                "UDIN_CARBON": "CARBON", "UDIN_FORGED": "CARBON", "LEATHER": "FABRIC",
                "FABRIC": "FABRIC", "SUEDE": "FABRIC", "RUBBER": "RUBBER"}
_CATEGORY_FAMILY = {"TYRE": "RUBBER", "BRAKE": "BRAKE", "GLOW": "GLOW", "IMAGE": "IMAGE",
                    "PAINT": "PAINT", "METAL": "METAL", "GLASS": "GLASS", "FABRIC": "FABRIC",
                    "NATURAL": "NATURAL", "CARBON": "CARBON", "PLASTIC": "PLASTIC", "RUBBER": "RUBBER"}

# Words people search with that a preset's own text may not contain. A query
# word matches itself *or* its synonym (it is widened, never replaced).
SYNONYMS = {
    "alcantara": "suede", "aluminum": "aluminium", "alloy": "aluminium",
    "windshield": "window", "windscreen": "window", "perspex": "acrylic", "plexiglass": "acrylic",
    "led": "glow", "neon": "glow", "screen": "glow", "display": "glow", "tire": "tyre",
    "fiber": "fibre", "color": "colour", "gray": "grey", "mirror": "chrome",
    "microsuede": "alcantara", "cloth": "fabric", "textile": "fabric", "upholstery": "fabric",
    "veneer": "wood", "timber": "wood", "porcelain": "ceramic", "tile": "ceramic",
    "cement": "concrete", "paper": "cardboard", "kraft": "cardboard", "sidewall": "tyre",
    "whitewall": "tyre", "anodized": "anodised", "blackout": "vantablack", "void": "vantablack",
    "texture": "textures", "pbr": "textures", "albedo": "textures",
}


def family_of(preset):
    """The family a preset belongs to."""
    cat = preset.get("category", "")
    if cat == "TRIM":
        return _TRIM_FAMILY.get(preset.get("type", ""), "PLASTIC")
    return _CATEGORY_FAMILY.get(cat, cat or "PLASTIC")


# --- Entries -----------------------------------------------------------------------------

def preset_entry(key, preset):
    fam = family_of(preset)
    spec = materials.TYPES.get(preset["type"], {})
    words = " ".join([preset["label"], preset.get("description", ""), FAMILY_LABELS.get(fam, ""),
                      spec.get("label", ""), " ".join(preset.get("tags", ()))])
    return {"id": "preset:" + key, "kind": "preset", "key": key, "label": preset["label"],
            "description": preset.get("description", ""), "family": fam,
            "source": "pack" if key.startswith("pk.") else "builtin", "type": preset["type"],
            "words": words.lower(), "folder": ""}


def library_entry(entry_id, entry):
    preset = materials.PRESETS.get(entry.get("preset", ""), {})
    fam = family_of(preset) if preset else entry.get("family", "MINE")
    words = " ".join([entry["label"], entry.get("folder", ""), FAMILY_LABELS.get(fam, ""),
                      preset.get("label", ""), " ".join(entry.get("tags", ()))])
    return {"id": "mine:" + entry_id, "kind": "saved", "key": entry_id, "label": entry["label"],
            "description": "Saved" + ((" · " + preset["label"]) if preset else " material"),
            "family": fam, "source": "mine", "type": entry.get("type", ""),
            "words": words.lower(), "folder": entry.get("folder", ""), "entry": entry}


def all_entries(library=None):
    """Every catalog entry: presets first (table order), then the library."""
    from . import matlib
    lib = matlib.entries() if library is None else library
    out = [preset_entry(k, p) for k, p in materials.PRESETS.items()]
    out += [library_entry(i, e) for i, e in lib.items()]
    return out


def _tokens(text):
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def matches(entry, query):
    """Every word of the query starts a word in the entry (prefix match), or
    its synonym does: "pea glow" finds Pearl, "alcantara" finds Suede."""
    want = _tokens(query)
    if not want:
        return True
    have = re.split(r"[^a-z0-9]+", entry["words"])
    return all(any(h.startswith(alt) for h in have for alt in {w, SYNONYMS.get(w, w)})
               for w in want)


def search(entries, query):
    """Matching entries, best first: label prefix, then label, then the rest."""
    hits = [e for e in entries if matches(e, query)]
    q = " ".join(_tokens(query))

    def rank(e):
        label = e["label"].lower()
        return (0 if q and label.startswith(q) else 1 if q and q in label else 2)
    return sorted(hits, key=rank) if q else hits


def section(entries, key, state=None, scene_ids=()):
    """Entries for a sidebar section or a family."""
    state = state if state is not None else load_state()
    if key == "FAV":
        fav = state.get("favourites", [])
        by_id = {e["id"]: e for e in entries}
        return [by_id[i] for i in fav if i in by_id]
    if key == "RECENT":
        by_id = {e["id"]: e for e in entries}
        return [by_id[i] for i in state.get("recent", []) if i in by_id]
    if key == "SCENE":
        by_id = {e["id"]: e for e in entries}
        return [by_id[i] for i in scene_ids if i in by_id]
    if key == "MINE":
        return [e for e in entries if e["source"] == "mine"]
    if key.startswith("FOLDER:"):
        return [e for e in entries if e["source"] == "mine" and e["folder"] == key[7:]]
    return [e for e in entries if e["family"] == key]


def counts(entries):
    """{family: number of entries} for the sidebar."""
    out = {}
    for e in entries:
        out[e["family"]] = out.get(e["family"], 0) + 1
    return out


def entry_for_part(obj, index=None):
    """The catalog id this part's material shows (a saved entry wins), or None."""
    from . import matlib
    saved = matlib.current_entry(obj, index)
    if saved:
        return "mine:" + saved
    key = materials.current_preset(obj, index)
    return ("preset:" + key) if key in materials.PRESETS else None


def scene_ids(objects):
    """Catalog ids of what the product's parts wear now (first seen first). Read-only."""
    seen = []
    for obj in objects:
        if obj.type != "MESH":
            continue
        for i in sorted(materials.used_slots(obj)):
            cid = entry_for_part(obj, i)
            if cid and cid not in seen:
                seen.append(cid)
    return seen


# --- Per-user state: favourites, recent, pinned controls ---------------------------------

RECENT_MAX = 12
_state = {"path": None, "stamp": None, "data": None}


def state_path():
    from . import matlib
    return os.path.join(matlib.folder(), ".state", "catalog.json")


def load_state():
    """{"favourites": [ids], "recent": [ids], "pins": {type: [names]}}. Read-only, cached."""
    path = state_path()
    try:
        stamp = os.stat(path).st_mtime_ns
    except OSError:
        stamp = None
    if _state["path"] == path and _state["stamp"] == stamp and _state["data"] is not None:
        return _state["data"]
    data = {"favourites": [], "recent": [], "pins": {}}
    if stamp is not None:
        try:
            with open(path, encoding="utf-8") as f:
                got = json.load(f)
            if isinstance(got, dict):
                data.update({k: got[k] for k in data if isinstance(got.get(k), type(data[k]))})
        except (OSError, ValueError):
            pass
    _state.update(path=path, stamp=stamp, data=data)
    return data


def _save_state(data):
    path = state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)
    _state.update(path=None, data=None)            # re-read on next load


def is_favourite(cid, state=None):
    return cid in (state or load_state()).get("favourites", [])


def toggle_favourite(cid):
    data = json.loads(json.dumps(load_state()))
    fav = data["favourites"]
    if cid in fav:
        fav.remove(cid)
    else:
        fav.append(cid)
    _save_state(data)
    return cid in fav


def used(cid):
    """Note that an entry was just applied (Recent)."""
    data = json.loads(json.dumps(load_state()))
    recent = [c for c in data["recent"] if c != cid]
    data["recent"] = ([cid] + recent)[:RECENT_MAX]
    data["used_at"] = time.time()
    _save_state(data)


def forget(cid):
    """An entry went away (deleted from the library): drop it everywhere."""
    data = json.loads(json.dumps(load_state()))
    data["favourites"] = [c for c in data["favourites"] if c != cid]
    data["recent"] = [c for c in data["recent"] if c != cid]
    _save_state(data)


def pinned(type_key, state=None):
    """The controls the Material card shows for a type: the user's pins, else
    the type's own three keys. Only names the type still has."""
    names = {n for n, kind, *_ in materials.controls(type_key) if kind != "colour"}
    pins = [n for n in (state or load_state()).get("pins", {}).get(type_key, []) if n in names]
    return pins or [k for k in materials.TYPES[type_key]["keys"] if k in names]


PIN_MAX = 4


def toggle_pin(type_key, name):
    """Show or hide a control on the card (at most PIN_MAX). Returns the new list."""
    data = json.loads(json.dumps(load_state()))
    current = list(pinned(type_key, data))
    if name in current:
        current.remove(name)
    elif len(current) < PIN_MAX:
        current.append(name)
    data.setdefault("pins", {})[type_key] = current
    _save_state(data)
    return current


# --- Applying ------------------------------------------------------------------------------

def apply(obj, cid, index=None):
    """Put a catalog entry on a part. Returns False when the entry is gone."""
    from . import matlib
    kind, _sep, key = cid.partition(":")
    if kind == "preset":
        if key not in materials.PRESETS:
            return False
        materials.apply_finish(obj, key, index)
    elif kind == "mine":
        if not matlib.apply(obj, key, index):
            return False
    else:
        return False
    used(cid)
    return True
