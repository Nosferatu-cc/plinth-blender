# SPDX-License-Identifier: GPL-3.0-or-later
"""The content library: packs of material presets, HDRIs, studio looks, light
rigs and scene kits, added without a new add-on release.

How it fits together
- A pack is a .zip installed from disk (Preferences › Library › Install pack
  from file). Plinth keeps what it has in ``library.json`` (user config
  folder) and the files in the Plinth Library folder.
- An install unpacks into ``<library>/.tmp`` and is swapped into place with
  renames; ``recover()`` repairs a swap that was interrupted.
- Each pack is ``pack.toml`` + ``items/<id>/item.toml`` (+ files). Items
  plug into what Plinth already has: material recipes become presets
  (``materials.PRESETS``, so they show on the Material card and its Edit page),
  studio looks and light rigs join ``library.CONTENT`` (the Look grid and the
  Light card's Rig row), HDRIs join ``hdri.CONTENT`` (the HDRI chips).
- Plinth never goes online: everything here is files on this computer.

``Library`` is plain Python, tested on temporary folders; the Blender glue
(registry, asset library) is at the bottom.
"""

import hashlib
import json
import os
import shutil
import time
import tomllib
import zipfile

MANIFEST_FILE = "library.json"
PACK_KINDS = {"materials": "material", "hdris": "hdri", "studios": "studio", "lights": "light", "mixed": None}
ITEM_KINDS = ("material", "hdri", "studio", "light", "kit")
KIND_WORDS = {"material": ("material", "materials"), "hdri": ("HDRI", "HDRIs"),
              "studio": ("studio", "studios"), "light": ("light rig", "light rigs"),
              "kit": ("scene kit", "scene kits")}


class ContentError(Exception):
    """A problem worth showing, in plain words."""


def version_key(text):
    parts = [int(p) if p.isdigit() else 0 for p in str(text or "0").split("-")[0].split(".")]
    return tuple(parts + [0] * (3 - len(parts)))


def item_key(pack_id, item_id):
    return "pk.%s.%s" % (pack_id, item_id)


def _safe_extract(archive, dest):
    """Unzip, refusing absolute paths and '..' (a pack must stay in its folder)."""
    root = os.path.realpath(dest)
    for info in archive.infolist():
        target = os.path.realpath(os.path.join(dest, info.filename))
        if not (target == root or target.startswith(root + os.sep)):
            raise ContentError("A pack tried to write outside its folder; it was not installed")
    archive.extractall(dest)


class Library:
    """Installed packs on disk."""

    def __init__(self, folder, config, now=time.time):
        self.folder, self.config, self.now = folder, config, now
        self.manifest = self._load()

    # --- manifest ---------------------------------------------------------------------
    def _load(self):
        try:
            with open(os.path.join(self.config, MANIFEST_FILE), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        data.setdefault("packs", {})
        data.setdefault("seen", [])
        data.pop("catalog", None)                  # left by an older Plinth
        return data

    def save(self):
        os.makedirs(self.config, exist_ok=True)
        tmp = os.path.join(self.config, MANIFEST_FILE + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.manifest, f, indent=1)
        os.replace(tmp, os.path.join(self.config, MANIFEST_FILE))

    @property
    def installed(self):
        return self.manifest["packs"]

    def pack_dir(self, pack_id):
        return os.path.join(self.folder, pack_id)

    # --- disk housekeeping ---------------------------------------------------------------
    def recover(self):
        """Finish or undo an interrupted install, drop leftovers and packs whose folder is gone."""
        if not os.path.isdir(self.folder):
            return
        for name in os.listdir(self.folder):
            path = os.path.join(self.folder, name)
            if name.startswith(".old-"):
                final = self.pack_dir(name[5:])
                if not os.path.isdir(final):
                    os.replace(path, final)            # the swap stopped halfway: put the old one back
                else:
                    shutil.rmtree(path, ignore_errors=True)
        shutil.rmtree(os.path.join(self.folder, ".tmp"), ignore_errors=True)
        gone = [pid for pid in self.installed if not os.path.isfile(os.path.join(self.pack_dir(pid), "pack.toml"))]
        for pid in gone:
            del self.installed[pid]
        if gone:
            self.save()

    def remove(self, pack_id):
        shutil.rmtree(self.pack_dir(pack_id), ignore_errors=True)
        if self.installed.pop(pack_id, None) is not None:
            self.save()

    def install_file(self, path):
        """Installs a pack from a .zip on this computer (Preferences › Library ›
        Install from file): unpacked, then swapped into place atomically.
        Returns the pack's name."""
        tmp = os.path.join(self.folder, ".tmp")
        os.makedirs(tmp, exist_ok=True)
        try:
            archive = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile):
            raise ContentError("That file isn't a Plinth content pack (.zip)")
        with archive:
            try:
                pack = tomllib.loads(archive.read("pack.toml").decode("utf-8"))
            except (KeyError, UnicodeDecodeError, tomllib.TOMLDecodeError):
                raise ContentError("That .zip has no pack.toml: it isn't a Plinth content pack")
            if not isinstance(pack.get("id"), str) or not pack["id"].replace("-", "").isalnum():
                raise ContentError("That pack's id is missing or not plain letters, digits and dashes")
            staging = os.path.join(tmp, pack["id"])
            shutil.rmtree(staging, ignore_errors=True)
            _safe_extract(archive, staging)
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                digest.update(block)
        final, old = self.pack_dir(pack["id"]), os.path.join(self.folder, ".old-" + pack["id"])
        if os.path.isdir(final):
            os.replace(final, old)
        os.replace(staging, final)
        shutil.rmtree(old, ignore_errors=True)
        self.installed[pack["id"]] = {
            "version": str(pack.get("version", "0")), "sha256": digest.hexdigest(), "size": os.path.getsize(path),
            "name": pack.get("name", pack["id"]), "kind": pack.get("kind", "mixed"), "installed_at": self.now(),
            "source": "file"}
        self.save()
        return pack.get("name", pack["id"])

    # --- reading (main thread) ----------------------------------------------------------------
    def items(self):
        """Every usable item of the installed packs."""
        out = {}
        for pack_id in sorted(self.installed):
            for item in read_pack(self.pack_dir(pack_id)):
                out[item["key"]] = item
        return out

    def new_keys(self, items):
        seen = set(self.manifest["seen"])
        return [k for k, it in items.items() if k not in seen and it["installed"]]

    def mark_seen(self, keys):
        seen = set(self.manifest["seen"])
        if not set(keys) <= seen:
            self.manifest["seen"] = sorted(seen | set(keys))
            self.save()


def _item(pack_id, raw, folder, pack_name):
    kind = raw.get("kind")
    settings = raw.get(kind, {}) if isinstance(raw.get(kind), dict) else raw.get("settings", {})
    return {"key": item_key(pack_id, raw["id"]), "pack": pack_id, "pack_name": pack_name, "id": raw["id"],
            "kind": kind, "name": raw.get("name", raw["id"]), "description": raw.get("description", ""),
            "category": raw.get("category", ""), "tags": raw.get("tags", []), "settings": settings,
            "builtin": raw.get("builtin"), "dir": folder,
            "installed": folder is not None}


def read_pack(folder):
    """The items of an installed pack (bad items are skipped, never fatal)."""
    try:
        with open(os.path.join(folder, "pack.toml"), "rb") as f:
            pack = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return []
    default_kind = PACK_KINDS.get(pack.get("kind"))
    items_dir = os.path.join(folder, "items")
    out = []
    for name in sorted(os.listdir(items_dir)) if os.path.isdir(items_dir) else []:
        path = os.path.join(items_dir, name, "item.toml")
        try:
            with open(path, "rb") as f:
                raw = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        raw.setdefault("kind", default_kind)
        raw.setdefault("id", name)
        if raw["kind"] in ITEM_KINDS:
            out.append(_item(pack["id"], raw, os.path.join(items_dir, name), pack.get("name", pack["id"])))
    return out


# --- Turning items into Plinth presets ----------------------------------------------------------

def material_preset(item):
    s = item["settings"]
    return {"label": item["name"], "description": item["description"], "category": item["category"] or s.get("category"),
            "type": s["type"], "values": {k: tuple(v) if isinstance(v, list) else v for k, v in s.get("values", {}).items()},
            "keep_colour": bool(s.get("keep_colour", False)), "shiny": bool(s.get("shiny", True)), "content": item["key"]}


def look_preset(item):
    """A studio or light-rig item as a look. 0.9 looks can carry the whole
    studio (floor, shape, hdri, world, extra lights, colour, eevee); a light
    rig never changes the backdrop or the world."""
    from . import library
    s = dict(item["settings"])
    if item["kind"] == "light":
        s = {k: v for k, v in s.items() if k in ("lights", "extra")}
        s["rig"] = True
    s.update(label=item["name"], description=item["description"])
    preset = library.normalise(s)
    preset["content"] = item["key"]
    return preset


def kit_entry(item):
    """A scene kit (scenes.py / kits.py): the scanned assets for one scene."""
    s = item["settings"]
    path = os.path.join(item["dir"], s["file"]) if item["dir"] else None
    return {"label": item["name"], "description": item["description"], "path": path, "content": item["key"],
            "scene": str(s["scene"]).upper(), "pack": item["pack"]}


def hdri_entry(item):
    s = item["settings"]
    path = os.path.join(item["dir"], s["file"]) if item["dir"] else None
    entry = {"label": item["name"], "description": item["description"], "path": path, "content": item["key"],
             "strength": float(s.get("strength", 1.0)), "rotation": float(s.get("rotation", 0.0))}
    if isinstance(s.get("ground"), dict):          # 0.10: a photo scene (scenes.py): a ground to stand on
        entry["ground"] = {k: float(v) for k, v in s["ground"].items() if k in ("height", "radius", "roughness")}
    return entry


# --- Blender glue ---------------------------------------------------------------------------------

LIBRARY = None                 # Library, created in register()
STATE = {}                     # busy: pack name, error: message, blocked: [names], new: [keys]
ITEMS = {}                     # the registry as last built (the UI reads this while drawing)
NEW_IDS = set()                # element ids that get a "new" dot: look_<key>, preset_<key>, hdri_<key>, rig_<key>
_INJECTED = set()              # material preset keys added to materials.PRESETS
ASSET_LIBRARY_NAME = "Plinth Library"


def default_folder():
    import bpy
    return bpy.utils.user_resource("DATAFILES", path="plinth_library", create=True)


def library_folder():
    from . import props
    p = props.prefs()
    custom = getattr(p, "library_path", "") if p is not None else ""
    if custom:
        import bpy
        return bpy.path.abspath(custom)
    return default_folder()


def refresh_registry():
    """Main thread: rebuild what the UI offers from the library. Never touches the network."""
    from . import hdri, kits, library, materials
    if LIBRARY is None:
        return
    items = LIBRARY.items()
    ITEMS.clear()
    ITEMS.update(items)
    for key in _INJECTED:
        materials.PRESETS.pop(key, None)
    _INJECTED.clear()
    looks, images, scene_kits = {}, {}, {}
    for key, item in items.items():
        try:
            if item["kind"] == "material":
                if item["builtin"] and item["builtin"] in materials.PRESETS:
                    continue                           # the add-on already has it
                materials.PRESETS[key] = material_preset(item)
                _INJECTED.add(key)
            elif item["kind"] in {"studio", "light"}:
                if item["builtin"] in library.BUILTIN:
                    continue
                looks[key] = look_preset(item)
            elif item["kind"] == "hdri":
                images[key] = hdri_entry(item)
            elif item["kind"] == "kit":
                entry = kit_entry(item)
                have = scene_kits.get(entry["scene"])
                if have is None or (entry["path"] and not have["path"]):   # an installed kit wins
                    scene_kits[entry["scene"]] = entry
        except (KeyError, TypeError, ValueError) as e:  # a bad item never breaks the rest
            print("Plinth: skipped content item %s: %s" % (key, e))
    library.CONTENT.clear()
    library.CONTENT.update(looks)
    hdri.CONTENT.clear()
    hdri.CONTENT.update(images)
    kits.KITS.clear()
    kits.KITS.update(scene_kits)
    kit_keys = {e["content"] for e in scene_kits.values()}
    STATE["new"] = LIBRARY.new_keys({k: v for k, v in items.items()
                                     if k in _INJECTED or k in looks or k in images or k in kit_keys})
    _new_ids()
    ensure_asset_library()


def _new_ids():
    NEW_IDS.clear()
    for key in STATE.get("new", ()):
        NEW_IDS.update(prefix + key for prefix in ("look_", "preset_", "hdri_", "rig_"))


def new_summary():
    """'3 new materials · 1 new studio' (or '')."""
    if LIBRARY is None or not STATE.get("new"):
        return ""
    counts = {}
    for key in STATE["new"]:
        kind = ITEMS.get(key, {}).get("kind")
        if kind:
            counts[kind] = counts.get(kind, 0) + 1
    order = ("material", "studio", "light", "hdri", "kit")
    return " · ".join("%d new %s" % (counts[k], KIND_WORDS[k][counts[k] != 1]) for k in order if k in counts)


def is_new(key):
    return key in STATE.get("new", ())


def mark_seen(keys=None):
    if LIBRARY is None:
        return
    keys = list(STATE.get("new", ())) if keys is None else [k for k in keys if k]
    LIBRARY.mark_seen(keys)
    STATE["new"] = [k for k in STATE.get("new", ()) if k not in keys]
    _new_ids()


def ensure_asset_library():
    """Registers the library folder as a Blender asset library when a pack has .blend files."""
    import bpy
    if LIBRARY is None or not any(f.endswith(".blend") for _r, _d, files in os.walk(LIBRARY.folder) for f in files):
        return
    libs = bpy.context.preferences.filepaths.asset_libraries
    folder = os.path.normcase(os.path.abspath(LIBRARY.folder))
    if any(os.path.normcase(os.path.abspath(bpy.path.abspath(lib.path))) == folder for lib in libs):
        return
    try:
        libs.new(name=ASSET_LIBRARY_NAME, directory=LIBRARY.folder)
    except (TypeError, RuntimeError) as e:
        print("Plinth: couldn't add the asset library:", e)


def install_file(path):
    """Main thread: install a pack .zip from disk, then offer what it holds."""
    if LIBRARY is None:
        raise ContentError("The content library isn't ready")
    name = LIBRARY.install_file(path)
    refresh_registry()
    return name


def config_folder():
    import bpy
    return bpy.utils.user_resource("CONFIG", path="plinth", create=True)


def register():
    global LIBRARY
    LIBRARY = Library(library_folder(), config_folder())
    try:
        LIBRARY.recover()
    except OSError as e:
        print("Plinth: library clean-up:", e)
    refresh_registry()


def unregister():
    from . import hdri, library, materials
    for key in _INJECTED:
        materials.PRESETS.pop(key, None)
    _INJECTED.clear()
    library.CONTENT.clear()
    hdri.CONTENT.clear()
    STATE.clear()
    ITEMS.clear()
    NEW_IDS.clear()
