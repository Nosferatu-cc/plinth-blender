# SPDX-License-Identifier: GPL-3.0-or-later
"""Live 3D previews for the Material card's preset tiles.

Each tile shows its preset as Blender renders it (its material preview: a
shaded sphere in Blender's preview studio), in the part's own colour and with
the edits this part has for that preset; the current preset follows its
sliders live. A flat colour dot stands in until a preview is ready.

How:
- hud.build() asks for a preview by key (``wanted``). That only reads data
  and queues the request in plain Python: building the UI never writes data.
- A timer (``pump``) renders one at a time on a private material per preset,
  ``Plinth_Preview_<KEY>``. It has no users, so it is never saved in the
  .blend. Blender renders previews in the background; firing several at once
  cancels them, and the first render of a material can come back empty while
  Eevee compiles its shaders, so an empty result is retried.
- The pixels are cached by key (LRU); the overlay turns them into a GPU
  texture when it draws.
"""

import os
import time
from collections import OrderedDict

import bpy

from . import materials

PREFIX = "Plinth_Preview_"
HOLDER = "Plinth_PreviewHolder"
CACHE_SIZE = 96
RETRY_AFTER = 2.0           # s without pixels: render again (shaders were compiling)
TRIES = 4
TICK = 0.1

_cache = OrderedDict()      # key -> {"pixels": [floats], "size": (w, h), "texture": GPUTexture | None}
_queue = OrderedDict()      # preset key -> (key, colour, values): newest request per preset
_busy = {}                  # {"key", "preset", "material", "since", "tries"}
_failed = set()
_latest = {}                # preset key -> its newest cached key (shown while a new one renders)
_timer = {"on": False}


def _round(v):
    if isinstance(v, (tuple, list)) or hasattr(v, "__len__"):
        return tuple(round(float(x), 3) for x in v)
    return round(float(v), 3)


def request_for(obj, preset_key):
    """(key, colour, values) for a tile: the preset as it would look on this
    part. Read-only (runs while Blender draws)."""
    preset = materials.PRESETS[preset_key]
    spec = materials.TYPES[preset["type"]]
    mat = materials.part_material(obj) if obj is not None else None
    values = {}
    if mat is not None:
        if mat.get("plinth_preset") == preset_key and mat.get("plinth_type") == preset["type"]:
            node = materials.group_node(mat)          # the current preset: its live sliders
            if node is not None:
                for name, kind, *_ in materials.controls(preset["type"]):
                    values[name] = _round(materials.socket(node, preset["type"], name).default_value)
        else:
            values = {k: _round(v) for k, v in materials.remembered(mat, preset_key).items()}
    if preset["type"] == "IMAGE":                       # the part's own picture on the sphere
        image = materials.image_of(mat) if mat is not None else None
        if image is not None:
            values["__image"] = image.name
    colour = None
    if preset["keep_colour"] and spec["colour"] and spec["colour"] not in values and obj is not None:
        colour = _part_colour(obj)
    key = "%s|%s|%s" % (preset_key, colour, sorted(values.items()))
    return key, colour, values


def _part_colour(obj):
    mat = obj.active_material if obj.material_slots else None
    c = materials._colour_of(mat)
    return _round(c) if c is not None else None


def wanted(obj, preset_key):
    """The preview key for a tile; queues it if it isn't cached yet."""
    key, colour, values = request_for(obj, preset_key)
    if key in _cache:
        _cache.move_to_end(key)
    elif _load_thumb(key):
        pass
    elif key not in _failed and (_busy.get("key") != key):
        _queue[preset_key] = (key, colour, values, preset_key)
        _queue.move_to_end(preset_key)
        _start()
    return key


def wanted_entry(entry):
    """The preview key for a saved material's tile (a recipe); queues it. Saved
    blend materials have no preview (their tile shows the colour)."""
    if entry.get("kind") != "recipe":
        return ""
    slot = "LIB_" + entry["id"]
    values = {k: _round(v) for k, v in entry.get("values", {}).items()}
    key = "%s|None|%s" % (slot, sorted(values.items()))
    if key in _cache:
        _cache.move_to_end(key)
    elif _load_thumb(key):
        pass
    elif key not in _failed and _busy.get("key") != key:
        _queue[slot] = (key, None, values, entry["preset"])
        _queue.move_to_end(slot)
        _start()
    return key


def best(key):
    """The key to draw for a tile: its own preview, else the newest one of the
    same preset (no flicker back to a dot while a slider drags), else None."""
    if not key:
        return None
    if key in _cache:
        return key
    latest = _latest.get(key.split("|", 1)[0])
    return latest if latest in _cache else None


def texture(key):
    """GPU texture for a cached preview (made on first draw), or None."""
    entry = _cache.get(key)
    if entry is None:
        return None
    if entry.get("texture") is None:
        import gpu
        w, h = entry["size"]
        buf = gpu.types.Buffer("FLOAT", w * h * 4, entry["pixels"])
        entry["texture"] = gpu.types.GPUTexture((w, h), format="RGBA16F", data=buf)
    return entry["texture"]


# --- Rendering (timer) ------------------------------------------------------------

def _start():
    if _timer["on"] or bpy.app.background:
        return
    _timer["on"] = True
    bpy.app.timers.register(pump, first_interval=TICK)


def _holder():
    obj = bpy.data.objects.get(HOLDER)
    if obj is None or obj.type != "MESH":
        obj = bpy.data.objects.new(HOLDER, bpy.data.meshes.new(HOLDER))
    return obj


def preview_material(preset_key, colour=None, values=None, slot=None):
    """The private material that renders a preset's preview, set up with the
    part's colour and values. Unused (never saved). ``slot`` names it when it
    isn't the preset itself (a saved material)."""
    holder = _holder()
    name = PREFIX + (slot or preset_key)
    mat = bpy.data.materials.get(name)
    if not holder.material_slots:
        holder.data.materials.append(None)
    holder.material_slots[0].link = "OBJECT"
    holder.material_slots[0].material = mat
    materials.apply_preset(holder, preset_key, index=0, keep_colour=False, fresh=True)
    mat = holder.material_slots[0].material
    holder.material_slots[0].material = None          # back to no users
    mat.name = name
    type_key = materials.PRESETS[preset_key]["type"]
    node = materials.group_node(mat)
    spec = materials.TYPES[type_key]
    kinds = {n: k for n, k, *_ in spec["inputs"]}
    todo = dict(values or {})
    image = bpy.data.images.get(todo.pop("__image", ""))
    if type_key == "IMAGE":
        materials.sync_image(mat)
        mat.node_tree.nodes[materials.IMAGE_NODE].image = image
    if colour is not None and spec["colour"]:
        todo[spec["colour"]] = colour
    for n, v in todo.items():
        if n in kinds:
            materials.socket(node, type_key, n).default_value = bool(v) if kinds[n] == "bool" else v
    if type_key == "IMAGE":
        materials.sync_image(mat)                      # size/rotation/offset onto the mapping
    return mat


def _alpha(mat):
    p = mat.preview
    if p is None or p.image_size[0] == 0:
        return 0.0
    import numpy as np
    a = np.array(p.image_pixels_float[:], np.float32)
    return float(a[3::4].mean()) if len(a) else 0.0


def _render(mat):
    mat.preview_ensure()
    with bpy.context.temp_override(id=mat):
        bpy.ops.ed.lib_id_generate_preview()


def _store(key, mat):
    import numpy as np
    p = mat.preview
    w, h = p.image_size
    px = np.array(p.image_pixels_float[:], np.float32).reshape(-1, 4)
    rgb = px[:, :3]                                    # display (sRGB) -> linear for the overlay
    px[:, :3] = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    _cache[key] = {"pixels": px.ravel().tolist(), "size": (w, h), "texture": None}
    _latest[key.split("|", 1)[0]] = key
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)
    _save_thumb(key, px, (w, h))


# --- On disk (0.9) ---------------------------------------------------------------------
# Rendered previews are kept between sessions, so the browser opens with every
# sphere already there instead of re-rendering them one at a time. A preview's
# key already says everything that shapes it (preset, colour, values), so a
# file per key never goes stale; THUMB_VERSION drops them all when the look of
# the preview scene changes. Plain numpy files: loading one while Blender draws
# writes no Blender data, and they hold plain arrays only (never pickles).

THUMB_VERSION = "1"
THUMBS_MAX = 600


def _thumb_dir():
    from . import matlib
    return os.path.join(matlib.folder(), ".thumbs")


def _thumb_path(key):
    import hashlib
    digest = hashlib.sha1((THUMB_VERSION + "|" + bpy.app.version_string + "|" + key).encode()).hexdigest()
    return os.path.join(_thumb_dir(), digest[:24] + ".npz")


def _save_thumb(key, px, size):
    import numpy as np
    try:
        os.makedirs(_thumb_dir(), exist_ok=True)
        data = np.clip(np.asarray(px, np.float32) * 255.0 + 0.5, 0, 255).astype(np.uint8)
        path = _thumb_path(key)
        tmp = path[:-4] + ".tmp.npz"
        np.savez(tmp, size=np.array(size, np.int32), px=data)      # plain arrays: no pickle
        os.replace(tmp, path)
        _trim_thumbs()
    except OSError:
        pass


def _trim_thumbs():
    """Keep the newest THUMBS_MAX files."""
    try:
        files = [os.path.join(_thumb_dir(), f) for f in os.listdir(_thumb_dir()) if f.endswith(".npz")]
    except OSError:
        return
    if len(files) <= THUMBS_MAX:
        return
    files.sort(key=lambda f: os.path.getmtime(f))
    for f in files[:len(files) - THUMBS_MAX]:
        try:
            os.remove(f)
        except OSError:
            pass


def _load_thumb(key):
    """Put a preview from disk in the cache. True when it was there."""
    import numpy as np
    path = _thumb_path(key)
    if not os.path.isfile(path):
        return False
    try:
        with np.load(path) as got:                        # allow_pickle stays off
            px = got["px"].astype(np.float32) / 255.0
            size = tuple(int(v) for v in got["size"])
        _cache[key] = {"pixels": px.ravel().tolist(), "size": size, "texture": None}
        _latest[key.split("|", 1)[0]] = key
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
        return True
    except (OSError, ValueError, KeyError, AttributeError):
        return False


def _redraw():
    wm = bpy.context.window_manager
    for win in (wm.windows if wm else ()):
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def pump():
    """One step: finish the preview being rendered, or start the next."""
    try:
        return _pump()
    except Exception:
        import traceback
        traceback.print_exc()
        _busy.clear()
        _timer["on"] = False
        return None


def _pump():
    now = time.monotonic()
    if _busy:
        mat = bpy.data.materials.get(_busy["material"])
        if mat is None:
            _busy.clear()
        elif _alpha(mat) > 0.02:
            _store(_busy["key"], mat)
            _busy.clear()
            _redraw()
        elif now - _busy["since"] > RETRY_AFTER:
            if _busy["tries"] >= TRIES:
                _failed.add(_busy["key"])
                _busy.clear()
            else:
                _busy["tries"] += 1
                _busy["since"] = now
                _render(mat)
        if _busy:
            return TICK
    while _queue:
        slot, (key, colour, values, preset_key) = _queue.popitem(last=False)
        if key in _cache or key in _failed:
            continue
        mat = preview_material(preset_key, colour, values, slot)
        _busy.update(key=key, preset=preset_key, material=mat.name, since=now, tries=1)
        _render(mat)
        return TICK
    _timer["on"] = False
    return None


def clear():
    _cache.clear()
    _queue.clear()
    _busy.clear()
    _failed.clear()
    _latest.clear()


def register():
    pass


def unregister():
    if bpy.app.timers.is_registered(pump):
        bpy.app.timers.unregister(pump)
    _timer["on"] = False
    clear()
