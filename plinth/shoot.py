# SPDX-License-Identifier: GPL-3.0-or-later
"""The Shoot step: render every enabled shot with one call, then put the
scene back exactly as it was (engine, presets, camera, resolution)."""

import os
from contextlib import contextmanager
import bpy

from . import engine, studio

LONG_EDGE = {"SOCIAL": 1350, "FULLHD": 1920, "MARKET": 3000}


def resolution_for(aspect, long_edge):
    w, h = aspect
    if w >= h:
        return long_edge, round(long_edge * h / w)
    return round(long_edge * w / h), long_edge


# Output settings Shoot changes besides the engine presets (Edit output page).
OUTPUT_PATHS = ("view_settings.view_transform", "view_settings.look",
                "render.image_settings.color_depth",
                "render.image_settings.color_mode", "cycles.device", "cycles.use_denoising",
                "cycles.samples", "eevee.taa_render_samples", "render.use_persistent_data")


@contextmanager
def preserved(scene):
    """Everything a Plinth render touches, put back afterwards (even on error).
    Extend this when a render path starts changing a new setting."""
    snap = engine.EngineSnapshot(scene)
    output = {}
    for path in OUTPUT_PATHS:
        obj, attr = engine._resolve(scene, path)
        if obj is not None and hasattr(obj, attr):
            output[path] = getattr(obj, attr)
    r = scene.render
    saved = (scene.camera, r.resolution_x, r.resolution_y,
             r.resolution_percentage, r.film_transparent, r.filepath,
             r.image_settings.file_format, scene.frame_start, scene.frame_end,
             scene.frame_current)
    backdrop = bpy.data.objects.get(studio.BACKDROP)
    saved_catcher = getattr(backdrop, "is_shadow_catcher", None) if backdrop else None
    try:
        yield
    finally:
        snap.restore()
        # file format first: colour depth's allowed values depend on it
        r.image_settings.file_format = saved[6]
        engine.apply_settings(scene, output)
        (scene.camera, r.resolution_x, r.resolution_y, r.resolution_percentage,
         r.film_transparent, r.filepath, r.image_settings.file_format,
         scene.frame_start, scene.frame_end, frame) = saved
        scene.frame_set(frame)
        if backdrop is not None and saved_catcher is not None:
            backdrop.is_shadow_catcher = saved_catcher


def render_still(scene, cam, engine_name, preset_key, long_edge, path, transparent=False,
                 aspect=None):
    """Render one camera to `path` (PNG) and restore the scene.
    `aspect` (w, h) defaults to the shot's own aspect."""
    with preserved(scene):
        engine.apply_preset(scene, engine_name, preset_key)
        r = scene.render
        r.film_transparent = transparent
        r.resolution_percentage = 100
        r.image_settings.file_format = "PNG"
        scene.camera = cam
        r.resolution_x, r.resolution_y = resolution_for(
            aspect or tuple(cam.get("plinth_aspect", (4, 5))), long_edge)
        r.filepath = path
        bpy.ops.render.render(write_still=True)
    return path


EXT = {"PNG": ".png", "JPEG": ".jpg", "OPEN_EXR": ".exr"}


def file_stem(cam):
    """A shot's file name: its label, made safe for every file system."""
    import re
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "", studio.shot_label(cam)).strip(" .")
    return stem or cam.name.replace("Plinth_Shot_", "")


DEFAULT_OPTIONS = {"color_depth": "8", "view_transform": "NATURAL", "device": "GPU", "denoise": True,
                   "sample_boost": 1.0, "save_mask": False, "naming": "SHOT", "product": ""}


# 0.8 options named the view transform; 0.9 names the intent (engine.COLOUR_LOOKS).
LEGACY_COLOUR = {"AgX": "NATURAL", "Standard": "STANDARD"}


def product_name(scene):
    """The product's name for file names: the active object if it's the
    product, else the biggest mesh that isn't Plinth's."""
    col = bpy.data.collections.get(studio.COLLECTION)
    own = set(col.all_objects) if col else set()
    meshes = [o for o in scene.objects if o.type == "MESH" and o not in own]
    act = bpy.context.view_layer.objects.active if bpy.context.view_layer else None
    if act in meshes:
        return act.name
    if not meshes:
        return ""
    return max(meshes, key=lambda o: max(o.dimensions)).name


def file_name(cam, naming="SHOT", product="", date=None):
    """'Hero', 'Body_Hero' or 'Body_Hero_2026-10-27' (Edit output › File names)."""
    import datetime
    import re
    parts = [file_stem(cam)]
    if naming in {"PRODUCT", "DATE"} and product:
        parts.insert(0, re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "", product).strip(" .") or "Product")
    if naming == "DATE":
        parts.append((date or datetime.date.today()).isoformat())
    return "_".join(parts)


def _depth_for(file_format, depth):
    """The colour depth setting that means 8- or 16-bit for this format."""
    if file_format == "JPEG":
        return None                                   # JPEG is always 8-bit
    if file_format == "OPEN_EXR":
        return "16" if depth == "8" else "32"         # half / full float
    return depth


def save_mask(path):
    """Write <name>_mask.png next to ``path``: white where the image is opaque."""
    import numpy as np
    img = bpy.data.images.load(path, check_existing=False)
    try:
        w, h = img.size
        px = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(px)
        px = px.reshape(-1, 4)
        alpha = px[:, 3].copy()
        px[:, 0] = px[:, 1] = px[:, 2] = alpha
        px[:, 3] = 1.0
        mask = bpy.data.images.new("Plinth_Mask", w, h, alpha=False)
        try:
            mask.pixels.foreach_set(px.ravel())
            out = os.path.splitext(path)[0] + "_mask.png"
            mask.filepath_raw = out
            mask.file_format = "PNG"
            mask.save()
        finally:
            bpy.data.images.remove(mask)
    finally:
        bpy.data.images.remove(img)
    return out


class ShootJob:
    """A batch render as steps, one image per step, so the floating UI can show
    progress and a Stop button between images (shot bar v2, REDESIGN.md §4).

    The scene is put back exactly as found when the job finishes, is stopped,
    or fails: ``preserved()`` is entered in ``start()`` and left in
    ``_finish()``. ``run()`` does it all in one go (scripts and tests)."""

    def __init__(self, scene, shots, engine_name, preset_key, long_edge, output_dir,
                 transparent=True, shadow_catcher=True, file_format="PNG", options=None):
        self.scene = scene
        self.settings = (engine_name, preset_key, long_edge, transparent, shadow_catcher, file_format)
        self.options = dict(DEFAULT_OPTIONS, **(options or {}))
        self.out = bpy.path.abspath(output_dir)
        self.shots = [cam.name for cam in shots]
        self.labels = [studio.shot_label(cam) for cam in shots]
        ext = EXT.get(file_format, "")
        self.items = []                   # (shot index, camera name, frame or None, path)
        self._bases = {}                  # turntable shots: camera matrix to put back
        for i, cam in enumerate(shots):
            name = file_name(cam, self.options["naming"], self.options["product"])
            if studio.turntable_settings(cam)["on"]:
                # One image per frame in its own folder: Turntable/0001.png ...
                for f in range(int(cam.get("plinth_frames", 72))):
                    self.items.append((i, cam.name, f, os.path.join(self.out, name, "%04d%s" % (f + 1, ext))))
            else:
                self.items.append((i, cam.name, None, os.path.join(self.out, name + ext)))
        self.index = 0
        self.written = []
        self.state = "idle"               # idle -> running -> done
        self.stopped = False
        self.error = None
        self._keep = None

    # --- Progress (what the shot bar shows) -------------------------------------------

    @property
    def count(self):
        return len(self.shots)

    def _shot_items(self, shot):
        return [it for it in self.items if it[0] == shot]

    @property
    def current(self):
        """Index of the shot being rendered (or next), None when finished."""
        return self.items[self.index][0] if self.index < len(self.items) else None

    @property
    def fraction(self):
        """0..1 over the whole job: (shot index + fraction of that shot) / shots."""
        if not self.items:
            return 1.0
        if self.index >= len(self.items):
            return 1.0
        shot = self.items[self.index][0]
        mine = self._shot_items(shot)
        done_in_shot = sum(1 for it in mine if self.items.index(it) < self.index)
        return (shot + done_in_shot / len(mine)) / self.count

    def finished_shots(self):
        """Names of shots whose every image is written."""
        done = set()
        for i, name in enumerate(self.shots):
            mine = self._shot_items(i)
            if mine and all(self.items.index(it) < self.index for it in mine):
                done.add(name)
        return done

    @property
    def saved(self):
        return len(self.finished_shots())

    def label(self):
        cur = self.current
        if cur is None:
            return "%d / %d" % (self.count, self.count)
        return "%d / %d · %s" % (cur + 1, self.count, self.labels[cur].upper())

    # --- Running ---------------------------------------------------------------------

    def start(self):
        if self.state != "idle":
            return
        engine_name, preset_key, _long, transparent, shadow_catcher, file_format = self.settings
        self._keep = preserved(self.scene)
        self._keep.__enter__()
        self.state = "running"
        try:
            scene, r = self.scene, self.scene.render
            engine.apply_preset(scene, engine_name, preset_key)
            r.film_transparent = transparent
            r.resolution_percentage = 100
            r.image_settings.file_format = file_format
            backdrop = bpy.data.objects.get(studio.BACKDROP)
            if backdrop is not None:
                studio.set_shadow_catcher(backdrop, shadow_catcher and transparent)
            o = self.options
            engine.apply_colour(scene, LEGACY_COLOUR.get(o["view_transform"], o["view_transform"]))
            engine.apply_settings(scene, {"cycles.device": o["device"],
                                          "cycles.use_denoising": bool(o["denoise"]),
                                          # Cycles keeps the scene between shots of a
                                          # batch: only the first one builds the BVH
                                          "render.use_persistent_data": len(self.items) > 1})
            depth = _depth_for(file_format, o["color_depth"])
            if depth is not None:
                engine.apply_settings(scene, {"render.image_settings.color_depth": depth})
            boost = float(o["sample_boost"])
            if abs(boost - 1.0) > 1e-6:
                if engine_name == "CYCLES":
                    scene.cycles.samples = max(1, round(scene.cycles.samples * boost))
                elif hasattr(scene, "eevee"):
                    scene.eevee.taa_render_samples = max(1, round(scene.eevee.taa_render_samples * boost))
            os.makedirs(self.out, exist_ok=True)
        except Exception as exc:
            self._fail(exc)

    def step(self):
        """Render the next image. Returns True while there is more to do."""
        if self.state != "running":
            return False
        if self.index >= len(self.items):
            self._finish()
            return False
        _shot, cam_name, frame, path = self.items[self.index]
        scene, r = self.scene, self.scene.render
        try:
            cam = bpy.data.objects[cam_name]
            scene.camera = cam
            r.resolution_x, r.resolution_y = resolution_for(
                tuple(cam.get("plinth_aspect", (4, 5))), self.settings[2])
            if frame is not None:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if cam.get("plinth_shot") == studio.TURNTABLE:
                    scene.frame_set(scene.frame_start + frame)       # the pivot's driver turns it
                else:
                    # Any other shot turns about the target, then is put back.
                    if cam.name not in self._bases:
                        self._bases[cam.name] = (cam.matrix_basis.copy(), cam.matrix_world.copy())
                    cam.matrix_world = studio.turntable_matrix(scene, cam, self._bases[cam.name][1], frame)
                    bpy.context.view_layer.update()
            r.filepath = path
            bpy.ops.render.render(write_still=True)
            self.written.append(path)
            if self.options["save_mask"] and self.settings[3]:
                self.written.append(save_mask(path))
        except Exception as exc:
            self._fail(exc)
            return False
        self.index += 1
        if self.index >= len(self.items):
            self._finish()
            return False
        return True

    def stop(self):
        """Stop after the current image; finished files are kept."""
        if self.state == "running":
            self.stopped = True
            self._finish()

    def _fail(self, exc):
        self.error = exc
        self._finish()

    def _finish(self):
        for name, (basis, _world) in self._bases.items():
            cam = bpy.data.objects.get(name)
            if cam is not None:
                cam.matrix_basis = basis
        self._bases.clear()
        if self._keep is not None:
            keep, self._keep = self._keep, None
            keep.__exit__(None, None, None)
        self.state = "done"

    def run(self):
        self.start()
        while self.step():
            pass
        if self.error is not None:
            raise self.error
        return self.written


def enabled_shots(scene):
    return [s for s in studio.list_shots(scene) if s.get("plinth_enabled", True)]


# The job the floating UI is showing (running, or done until dismissed).
JOB = {"job": None}


def current_job():
    return JOB["job"]


def shoot(scene, engine_name, preset_key, long_edge, output_dir, transparent=True,
          shadow_catcher=True, file_format="PNG", shots=None, options=None):
    """Render Plinth shots (default: every ticked one). Returns the written files."""
    shots = enabled_shots(scene) if shots is None else list(shots)
    if not shots:
        return []
    from . import scenes
    scenes.fit_dome(scene)                       # 0.10: every shot inside a photo scene's dome
    return ShootJob(scene, shots, engine_name, preset_key, long_edge, output_dir,
                    transparent, shadow_catcher, file_format, options).run()
