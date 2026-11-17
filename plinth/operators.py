# SPDX-License-Identifier: GPL-3.0-or-later
import os

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatVectorProperty, IntProperty, StringProperty

from . import (bake, decals, engine, framing, gobos, hdri, library, materials, matlib, parity, props,
               transform, workspace)
from . import shoot as shoot_mod
from . import scenes, studio


def auto_preset(context, objects):
    """Hybrid 'Auto preset': follow the product's materials and motion."""
    s = context.scene.plinth
    if s.auto_preset:
        key, _reason = engine.suggest_eevee_preset(context.scene, objects)
        if s.eevee_preset != key:
            s.eevee_preset = key


class PLINTH_OT_build_studio(bpy.types.Operator):
    """Build a backdrop, light rig and shots around the selected objects"""
    bl_idname = "plinth.build_studio"
    bl_label = "Build Studio"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        product = [o for o in context.selected_objects if o.type == "MESH"]
        studio.build_studio(context.scene, product)
        framing.forget()                         # a new studio is framed afresh
        from . import eeveeprep
        eeveeprep.prepare(context.scene)
        auto_preset(context, product)
        self.report({"INFO"}, "Studio ready")
        return {"FINISHED"}


class PLINTH_OT_open_workspace(bpy.types.Operator):
    """Switch to the Plinth studio workspace (created the first time)"""
    bl_idname = "plinth.open_workspace"
    bl_label = "Open Plinth Studio"

    @classmethod
    def poll(cls, context):
        return context.window is not None

    def execute(self, context):
        workspace.activate(context)
        return {"FINISHED"}


class PLINTH_OT_set_step(bpy.types.Operator):
    """Go to this step of the flow"""
    bl_idname = "plinth.set_step"
    bl_label = "Set Step"
    bl_options = {"INTERNAL"}

    step: EnumProperty(name="Step", items=props.STEPS)

    def execute(self, context):
        changed = context.scene.plinth.step != self.step
        if changed:
            framing.remember(context, context.scene.plinth.step)
        context.scene.plinth.step = self.step
        if changed and auto_frame_enabled(context):
            frame_for_step(context, self.step)
        if changed:
            transform.sync(context)              # a gizmo moves this step's light or shot
        if changed and self.step == "MATERIAL":
            ensure_visible_parts(context)
        props.tag_viewports(context)
        return {"FINISHED"}


def ensure_visible_parts(context):
    """Material step: every selected product edits a part some face shows."""
    depsgraph = context.evaluated_depsgraph_get()
    for obj in _product_objects(context):
        materials.ensure_visible_part(obj, depsgraph)


def auto_frame_enabled(context):
    addon = context.preferences.addons.get(__package__)
    return addon is None or getattr(addon.preferences, "auto_frame", True)


def frame_for_step(context, step):
    """Issue 6: the first time, Camera and Shoot look through the active shot
    and the other steps fit the product; after that each step comes back as
    you left it (framing.py). Without a 3D view (scripts, tests) nothing."""
    return framing.enter(context, step)


class PLINTH_OT_aim_light(bpy.types.Operator):
    """Place a light so its highlight lands on a surface point (light painting)"""
    bl_idname = "plinth.aim_light"
    bl_label = "Aim Light at Point"
    bl_options = {"REGISTER", "UNDO"}

    light_name: StringProperty(default="Plinth_Key")
    point: FloatVectorProperty(size=3)
    normal: FloatVectorProperty(size=3, default=(0, -1, 0))

    def execute(self, context):
        light = bpy.data.objects.get(self.light_name)
        cam = context.scene.camera
        if light is None or cam is None:
            self.report({"WARNING"}, "Needs a Plinth light and an active camera")
            return {"CANCELLED"}
        # Light painting moves the light itself, so drop the auto-aim constraint.
        studio.untrack(light)
        studio.aim_light_for_highlight(light, self.point, self.normal, cam.location)
        return {"FINISHED"}


class PLINTH_OT_paint_light(bpy.types.Operator):
    """Click the product to place the highlight. Hold Shift to move the light instead"""
    bl_idname = "plinth.paint_light"
    bl_label = "Paint Light"
    bl_options = {"REGISTER", "UNDO"}

    HINT = ("Click or drag on the product to place the highlight · "
            "Shift: move the light · Esc: cancel")

    @classmethod
    def poll(cls, context):
        return (context.area is not None and context.area.type == "VIEW_3D"
                and studio.active_light(context.scene) is not None)

    def invoke(self, context, event):
        self.light = studio.active_light(context.scene)
        self.saved = (self.light.rotation_mode, self.light.matrix_world.copy())
        self.had_track = self.light.constraints.get("Plinth_Track") is not None
        self.painting = False
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set(self.HINT)
        return {"RUNNING_MODAL"}

    def _paint(self, context, event):
        from bpy_extras import view3d_utils
        region, rv3d = context.region, context.region_data
        xy = (event.mouse_region_x, event.mouse_region_y)
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, xy)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, xy)
        studio.paint_light(context.scene, context.evaluated_depsgraph_get(),
                           self.light, origin, direction, move=event.shift)
        context.area.tag_redraw()

    def modal(self, context, event):
        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE",
                          "TRACKPADPAN", "TRACKPADZOOM"}:
            return {"PASS_THROUGH"}                     # keep navigating
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self.light.rotation_mode, self.light.matrix_world = self.saved
            if self.had_track:
                studio.track_to(self.light, studio.get_target(context.scene))
            context.workspace.status_text_set(None)
            return {"CANCELLED"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            self.painting = True
            self._paint(context, event)
        elif event.type == "MOUSEMOVE" and self.painting:
            self._paint(context, event)
        elif event.type == "LEFTMOUSE" and event.value == "RELEASE" and self.painting:
            context.workspace.status_text_set(None)
            return {"FINISHED"}                         # one stroke = one undo step
        return {"RUNNING_MODAL"}


def _product_objects(context):
    """Selected meshes that aren't Plinth's own (backdrop etc.)."""
    col = bpy.data.collections.get(studio.COLLECTION)
    own = set(col.all_objects) if col else set()
    return [o for o in context.selected_objects if o.type == "MESH" and o not in own]


_finish_items = []          # kept alive: Blender holds pointers into the enum items


def _finish_enum(self, context):
    """Every finish, including material presets from the content library (added after register)."""
    _finish_items[:] = ([("ORIGINAL", "Original", "Put back the original material")]
                        + [(k, v["label"], v["description"]) for k, v in materials.FINISH_PRESETS.items()]
                        + [(k, v["label"], v["description"]) for k, v in materials.PRESETS.items()])
    return _finish_items


class PLINTH_OT_set_finish(bpy.types.Operator):
    """Give the selected products this surface finish, keeping their colour"""
    bl_idname = "plinth.set_finish"
    bl_label = "Set Finish"
    bl_options = {"REGISTER", "UNDO"}

    finish: EnumProperty(name="Finish", items=_finish_enum)

    @classmethod
    def poll(cls, context):
        return bool(_product_objects(context))

    def execute(self, context):
        key = None if self.finish == "ORIGINAL" else self.finish
        objects = _product_objects(context)
        depsgraph = context.evaluated_depsgraph_get()
        for obj in objects:
            materials.ensure_visible_part(obj, depsgraph)      # never a slot no face shows
            materials.apply_finish(obj, key)
        auto_preset(context, objects)
        if key and key.startswith("pk."):
            from . import content
            content.mark_seen([key])
        return {"FINISHED"}


class PLINTH_OT_set_part(bpy.types.Operator):
    """Edit this part (material slot) of the product"""
    bl_idname = "plinth.set_part"
    bl_label = "Pick Part"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    index: IntProperty(min=0)

    def execute(self, context):
        obj = context.active_object
        if obj is None or self.index >= len(obj.material_slots):
            return {"CANCELLED"}
        obj.active_material_index = self.index
        return {"FINISHED"}


class PLINTH_OT_edit_material(bpy.types.Operator):
    """Open every control of this part's material"""
    bl_idname = "plinth.edit_material"
    bl_label = "Edit Material"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and materials.part_material(obj) is not None

    def invoke(self, context, event):
        materials.make_own(context.active_object)     # the editor edits sockets directly
        bpy.ops.wm.call_panel(name="PLINTH_PT_material_editor", keep_open=True)
        return {"FINISHED"}

    def execute(self, context):
        materials.make_own(context.active_object)
        return {"FINISHED"}


class PLINTH_OT_enable_gpu(bpy.types.Operator):
    """Render Cycles on your graphics card (sets Preferences > System > Cycles Render Devices)"""
    bl_idname = "plinth.enable_gpu"
    bl_label = "Use the graphics card for Cycles"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        name = engine.enable_gpu()
        if name is None:
            self.report({"INFO"}, "Cycles already uses the graphics card" if engine.GPU_INFO["devices"]
                        else "No graphics card Cycles can use was found")
            return {"CANCELLED"}
        context.scene.plinth.device = "GPU"
        self.report({"INFO"}, "Cycles now renders on the %s" % name)
        return {"FINISHED"}


class PLINTH_OT_glass_thin(bpy.types.Operator):
    """Thin pane (windows) or solid glass (lenses, crystal)"""
    bl_idname = "plinth.glass_thin"
    bl_label = "Glass Thickness"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    thin: BoolProperty(name="Thin pane")

    def execute(self, context):
        obj = context.active_object
        if obj is None or not materials.set_input(obj, "Thin pane", self.thin):
            return {"CANCELLED"}
        return {"FINISHED"}


def _who(context):
    """The light (Light step) or shot (other steps) a target/aim action is for."""
    s = context.scene.plinth
    if s.step == "LIGHT":
        return studio.active_light(context.scene)
    cam = context.scene.camera
    return cam if cam is not None and "plinth_shot" in cam else None


class PLINTH_OT_set_active_light(bpy.types.Operator):
    """Edit this light"""
    bl_idname = "plinth.set_active_light"
    bl_label = "Pick Light"
    bl_options = {"INTERNAL"}

    name: StringProperty()

    def execute(self, context):
        context.scene.plinth.active_light = self.name
        return {"FINISHED"}


class PLINTH_OT_add_light(bpy.types.Operator):
    """Add a light aimed at the active target"""
    bl_idname = "plinth.add_light"
    bl_label = "Add Light"
    bl_options = {"REGISTER", "UNDO"}

    shape: EnumProperty(name="Shape", items=[(k, v["label"], v["description"])
                                             for k, v in studio.LIGHT_SHAPES.items()])

    def execute(self, context):
        scene = context.scene
        tgt = bpy.data.objects.get(scene.plinth.active_target) or studio.get_target(scene)
        light = studio.add_light(scene, self.shape, tgt)
        scene.plinth.active_light = studio.light_label(light)
        return {"FINISHED"}


class PLINTH_OT_duplicate_light(bpy.types.Operator):
    """Duplicate the light being edited"""
    bl_idname = "plinth.duplicate_light"
    bl_label = "Duplicate Light"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return studio.active_light(context.scene) is not None

    def execute(self, context):
        light = studio.duplicate_light(context.scene, studio.active_light(context.scene))
        context.scene.plinth.active_light = studio.light_label(light)
        return {"FINISHED"}


class PLINTH_OT_remove_light(bpy.types.Operator):
    """Remove the light being edited"""
    bl_idname = "plinth.remove_light"
    bl_label = "Remove Light"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return studio.active_light(context.scene) is not None

    def execute(self, context):
        scene = context.scene
        studio.remove_light(scene, studio.active_light(scene))
        rest = studio.list_lights(scene)
        scene.plinth.active_light = studio.light_label(rest[0]) if rest else ""
        return {"FINISHED"}


class PLINTH_OT_add_target(bpy.types.Operator):
    """Add a target (drag its icon in the viewport to place it)"""
    bl_idname = "plinth.add_target"
    bl_label = "Add Target"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        tgt = studio.add_target(context.scene)
        context.scene.plinth.active_target = tgt.name
        return {"FINISHED"}


class PLINTH_OT_remove_target(bpy.types.Operator):
    """Remove a target; anything aimed at it aims at the main target"""
    bl_idname = "plinth.remove_target"
    bl_label = "Remove Target"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty()

    def execute(self, context):
        tgt = bpy.data.objects.get(self.name)
        if tgt is None or not studio.remove_target(context.scene, tgt):
            return {"CANCELLED"}
        context.scene.plinth.active_target = studio.TARGET
        return {"FINISHED"}


class PLINTH_OT_aim_at(bpy.types.Operator):
    """Aim the light (Light step) or the shot (Camera step) at this target"""
    bl_idname = "plinth.aim_at"
    bl_label = "Aim At"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty()

    def execute(self, context):
        obj, tgt = _who(context), bpy.data.objects.get(self.name)
        if obj is None or tgt is None:
            return {"CANCELLED"}
        studio.aim_at(obj, tgt)
        context.scene.plinth.active_target = tgt.name
        return {"FINISHED"}


class PLINTH_OT_add_shot(bpy.types.Operator):
    """Add a shot from the current view"""
    bl_idname = "plinth.add_shot"
    bl_label = "Add Shot From View"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from . import hud
        hud.add_shot_from_view(context)
        return {"FINISHED"}


class PLINTH_OT_duplicate_shot(bpy.types.Operator):
    """Duplicate the active shot"""
    bl_idname = "plinth.duplicate_shot"
    bl_label = "Duplicate Shot"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        cam = context.scene.camera
        return cam is not None and "plinth_shot" in cam

    def execute(self, context):
        context.scene.camera = studio.duplicate_shot(context.scene, context.scene.camera)
        return {"FINISHED"}


class PLINTH_OT_remove_shot(bpy.types.Operator):
    """Remove a shot"""
    bl_idname = "plinth.remove_shot"
    bl_label = "Remove Shot"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty()

    def execute(self, context):
        cam = bpy.data.objects.get(self.name)
        if cam is None or "plinth_shot" not in cam:
            return {"CANCELLED"}
        studio.remove_shot(context.scene, cam)
        return {"FINISHED"}


class PLINTH_OT_set_gobo(bpy.types.Operator):
    """Put a patterned card in front of the light (Eevee and Cycles)"""
    bl_idname = "plinth.set_gobo"
    bl_label = "Set Gobo"
    bl_options = {"REGISTER", "UNDO"}

    gobo: EnumProperty(name="Gobo", items=[(k, v["label"], v["description"])
                                           for k, v in gobos.GOBO_PRESETS.items()])

    @classmethod
    def poll(cls, context):
        return studio.active_light(context.scene) is not None

    def execute(self, context):
        gobos.set_gobo(studio.active_light(context.scene), self.gobo)
        return {"FINISHED"}


class PLINTH_OT_set_hdri(bpy.types.Operator):
    """Light the scene with one of Blender's HDRI images (empty: turn it off)"""
    bl_idname = "plinth.set_hdri"
    bl_label = "Set HDRI"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    image: StringProperty()

    def execute(self, context):
        from . import content
        if not self.image:
            hdri.turn_off(context.scene)
        else:
            first = not (context.scene.world and context.scene.world.get("plinth_hdri"))
            error = hdri.set_image(context.scene, self.image)
            if error:
                self.report({"WARNING"}, error)
                return {"CANCELLED"}
            entry = hdri.CONTENT.get(self.image)
            if entry and first:                         # the pack's suggested strength and turn
                hdri.set_hdri(context.scene, strength=entry["strength"], rotation=entry["rotation"])
            content.mark_seen([self.image])
        context.scene.plinth.active_light = hdri.CHIP
        props.tag_viewports(context)
        return {"FINISHED"}


class PLINTH_OT_bake_lighting(bpy.types.Operator):
    """Bake bounce light, colour bleed and Glow light for Eevee (Cycles doesn't need it)"""
    bl_idname = "plinth.bake_lighting"
    bl_label = "Bake Lighting"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return bpy.data.objects.get("Plinth_Target") is not None and not bake.is_running()

    def execute(self, context):
        context.window.cursor_set("WAIT") if context.window else None
        try:
            done = bake.bake_lighting(context.scene)
        finally:
            context.window.cursor_set("DEFAULT") if context.window else None
        if not done:
            self.report({"WARNING"}, "The bake didn't finish")
            return {"CANCELLED"}
        props.tag_viewports(context)
        return {"FINISHED"}


class PLINTH_OT_bake_material(bpy.types.Operator):
    """Bake this part's material to image textures: fast in Eevee, same look"""
    bl_idname = "plinth.bake_material"
    bl_label = "Bake Material"
    bl_options = {"REGISTER", "UNDO"}

    size: EnumProperty(name="Size", items=[(k, k, "%d px textures" % v) for k, v in bake.SIZES.items()],
                       default="2K")

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and bake.can_bake(obj)[0]

    def execute(self, context):
        wm = context.window_manager
        wm.progress_begin(0, 100)
        try:
            error = bake.bake_material(context.scene, context.active_object, size=self.size,
                                       progress=lambda f: wm.progress_update(int(f * 100)))
        finally:
            wm.progress_end()
        if error:
            self.report({"WARNING"}, error)
            return {"CANCELLED"}
        props.tag_viewports(context)
        return {"FINISHED"}


class PLINTH_OT_unbake_material(bpy.types.Operator):
    """Back to this part's procedural material (editable again)"""
    bl_idname = "plinth.unbake_material"
    bl_label = "Back to Procedural"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH" and bake.is_baked(obj)

    def execute(self, context):
        bake.unbake(context.active_object)
        props.tag_viewports(context)
        return {"FINISHED"}


class PLINTH_OT_clear_lighting(bpy.types.Operator):
    """Remove Plinth's baked lighting"""
    bl_idname = "plinth.clear_lighting"
    bl_label = "Clear Baked Lighting"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bake.volume(context.scene) is not None and not bake.is_running()

    def execute(self, context):
        bake.clear_lighting(context.scene)
        props.tag_viewports(context)
        return {"FINISHED"}


class PLINTH_OT_load_hdri(bpy.types.Operator):
    """Light the scene with your own HDRI (.hdr or .exr)"""
    bl_idname = "plinth.load_hdri"
    bl_label = "Load HDRI"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.hdr;*.exr", options={"HIDDEN"})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        error = hdri.set_image(context.scene, path=self.filepath)
        if error:
            self.report({"WARNING"}, error)
            return {"CANCELLED"}
        context.scene.plinth.active_light = hdri.CHIP
        props.tag_viewports(context)
        return {"FINISHED"}


class PLINTH_OT_add_turntable(bpy.types.Operator):
    """Add a shot that circles the product once (renders one image per frame)"""
    bl_idname = "plinth.add_turntable"
    bl_label = "Add Turntable"
    bl_options = {"REGISTER", "UNDO"}

    frames: IntProperty(name="Frames", default=72, min=4, max=1000)

    def execute(self, context):
        product = _product_objects(context)
        studio.create_turntable(context.scene, studio.subject_radius(product), self.frames)
        auto_preset(context, product)
        return {"FINISHED"}


_studio_items = []          # Blender needs dynamic enum items kept alive


def _studio_preset_items(self, context):
    _studio_items[:] = [(k, v.get("label", k), v.get("description", ""))
                        for k, v in library.all_presets().items()]
    return _studio_items


class PLINTH_OT_apply_studio(bpy.types.Operator):
    """Apply a whole studio look: backdrop and lights"""
    bl_idname = "plinth.apply_studio"
    bl_label = "Studio Look"
    bl_options = {"REGISTER", "UNDO"}
    bl_property = "preset"

    preset: EnumProperty(name="Look", items=_studio_preset_items)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        preset = library.all_presets().get(self.preset)
        if preset is None:
            return {"CANCELLED"}
        skipped = library.apply(context.scene, preset)
        context.scene.plinth.last_look = self.preset          # Reset goes back to it
        if self.preset.startswith("pk."):
            from . import content
            content.mark_seen([self.preset])
        if skipped:
            self.report({"WARNING"}, "Build the studio first (missing: %s)" % ", ".join(skipped))
        return {"FINISHED"}


class PLINTH_OT_set_form(bpy.types.Operator):
    """Change the studio's shape: sweep, corner, three-wall, half-round, room, round, dome or floor"""
    bl_idname = "plinth.set_form"
    bl_label = "Studio Shape"
    bl_options = {"REGISTER", "UNDO"}

    form: EnumProperty(name="Shape", items=[(k, v[0], v[1]) for k, v in studio.FORMS.items()])

    def execute(self, context):
        if not studio.set_form(context.scene, self.form):
            self.report({"WARNING"}, "Build the studio first")
            return {"CANCELLED"}
        return {"FINISHED"}


class PLINTH_OT_set_scene(bpy.types.Operator):
    """Put the product in a scene (the moon, a forest, a house, a photo of a real place), or back in the studio"""
    bl_idname = "plinth.set_scene"
    bl_label = "Scene"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    place: StringProperty()

    def execute(self, context):
        from . import content, kits, scenes
        error = scenes.apply(context.scene, self.place)
        if error:
            self.report({"WARNING"}, error)
            return {"CANCELLED"}
        if self.place:
            content.mark_seen([self.place])
        # A scene kit (scanned assets, from an installed pack) replaces the
        # scene's code-made version; without one the scene is code-made.
        kit = kits.kit_entry(self.place) if self.place else None
        if kit and kits.kit_path(self.place):
            content.mark_seen([kit["content"]])
        props.tag_viewports(context)
        return {"FINISHED"}


class PLINTH_OT_scene_detail(bpy.types.Operator):
    """How detailed the scene is: more polygons, plants and rocks look more real but weigh more"""
    bl_idname = "plinth.scene_detail"
    bl_label = "Scene Detail"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    level: EnumProperty(name="Detail", items=[(k, lab, d, i) for i, (k, lab, d) in enumerate(scenes.DETAILS)])

    def execute(self, context):
        from . import scenes
        wm = context.window_manager
        wm.progress_begin(0, 1)
        try:
            scenes.set_detail(context.scene, self.level)
        finally:
            wm.progress_end()
        props.tag_viewports(context)
        return {"FINISHED"}


class PLINTH_OT_scene_shuffle(bpy.types.Operator):
    """Variation: the same scene with another random layout of rocks, plants, trees and props"""
    bl_idname = "plinth.scene_shuffle"
    bl_label = "Shuffle Scene"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    original: BoolProperty(name="Original", description="Back to the scene's original layout", default=False)

    def execute(self, context):
        from . import scenes
        wm = context.window_manager
        wm.progress_begin(0, 1)
        try:
            ok = (scenes.set_setting(context.scene, rebuild=True, seed=0) if self.original
                  else scenes.shuffle(context.scene))
        finally:
            wm.progress_end()
        props.tag_viewports(context)
        return {"FINISHED"} if ok else {"CANCELLED"}


class PLINTH_OT_save_studio(bpy.types.Operator):
    """Save the current backdrop and lights as a look you can reuse"""
    bl_idname = "plinth.save_studio"
    bl_label = "Save Look"

    label: StringProperty(name="Name", default="My studio")
    description: StringProperty(name="Description", default="")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        path = library.save_user(context.scene, self.label, self.description)
        self.report({"INFO"}, "Saved to %s" % path)
        return {"FINISHED"}


class PLINTH_OT_compare(bpy.types.Operator):
    """Render a quick Cycles preview of the camera and swipe it over the live viewport"""
    bl_idname = "plinth.compare"
    bl_label = "Compare with Cycles"

    long_edge: IntProperty(name="Size", default=512, min=16, max=4096)

    @classmethod
    def poll(cls, context):
        return context.scene.camera is not None

    def execute(self, context):
        scene = context.scene
        parity.render_compare_image(scene, scene.camera, long_edge=self.long_edge)
        scene.plinth.compare = True
        rv3d = context.region_data
        if rv3d is not None and rv3d.view_perspective != "CAMERA":
            rv3d.view_perspective = "CAMERA"          # the swipe lives in the camera frame
        props.tag_viewports(context)
        return {"FINISHED"}


def shots_to_shoot(scene):
    """What Shoot renders: the active shot, or every ticked one (scope switch)."""
    if scene.plinth.shoot_scope == "ACTIVE":
        cam = scene.camera
        return [cam] if cam is not None and "plinth_shot" in cam else []
    return shoot_mod.enabled_shots(scene)


def make_job(scene):
    s = scene.plinth
    if s.hybrid:
        # Hybrid: whatever you worked in, the Shoot quality ladder decides.
        eng, key = engine.QUALITY_LADDER[s.quality]
    else:
        eng = s.engine
        key = s.eevee_preset if eng == "EEVEE" else s.cycles_preset
    long_edge = s.custom_long_edge if s.size == "CUSTOM" else shoot_mod.LONG_EDGE[s.size]
    options = {"color_depth": s.color_depth, "view_transform": s.view_transform,
               "device": s.device, "denoise": s.denoise, "sample_boost": s.sample_boost,
               "save_mask": s.save_mask, "naming": s.file_naming,
               "product": shoot_mod.product_name(scene)}
    return shoot_mod.ShootJob(scene, shots_to_shoot(scene), eng, key, long_edge, s.output_dir,
                              transparent=s.transparent, shadow_catcher=s.shadow_catcher,
                              file_format=s.file_format, options=options)


def _shoot_tick():
    """One image per timer tick, so the shot bar redraws and Stop works between images."""
    from . import overlay
    job = shoot_mod.current_job()
    if job is None or job.state != "running":
        overlay.PUMP.release("shoot")
        return None
    job.step()
    props.tag_viewports(bpy.context)
    if job.state != "running":
        overlay.PUMP.release("shoot")
        if job.error is not None:
            print("Plinth: Shoot stopped by an error:", job.error)
        elif bpy.context.scene.plinth.open_when_done and os.path.isdir(job.out):
            bpy.ops.wm.path_open(filepath=job.out)
        return None
    return 0.01


class PLINTH_OT_shoot(bpy.types.Operator):
    """Render the active shot or every ticked shot"""
    bl_idname = "plinth.shoot"
    bl_label = "Shoot"

    @classmethod
    def poll(cls, context):
        job = shoot_mod.current_job()
        return job is None or job.state != "running"

    def execute(self, context):
        job = make_job(context.scene)
        if not job.items:
            self.report({"WARNING"}, "No shots ticked")
            return {"CANCELLED"}
        files = job.run()
        self.report({"INFO"}, "Shot %d image(s)" % len(files))
        return {"FINISHED"}

    def invoke(self, context, event):
        """From the floating UI: render in steps with progress in the shot bar."""
        job = make_job(context.scene)
        if not job.items:
            self.report({"WARNING"}, "No shots ticked")
            return {"CANCELLED"}
        from . import overlay
        shoot_mod.JOB["job"] = job
        job.start()
        overlay.PUMP.hold("shoot")
        bpy.app.timers.register(_shoot_tick, first_interval=0.05)
        props.tag_viewports(context)
        return {"FINISHED"}


class PLINTH_OT_shoot_stop(bpy.types.Operator):
    """Stop shooting after the current image (finished files are kept)"""
    bl_idname = "plinth.shoot_stop"
    bl_label = "Stop Shoot"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        job = shoot_mod.current_job()
        if job is not None:
            job.stop()
        props.tag_viewports(context)
        return {"FINISHED"}


class PLINTH_OT_choose_folder(bpy.types.Operator):
    """Choose where Shoot saves images"""
    bl_idname = "plinth.choose_folder"
    bl_label = "Choose Folder"
    bl_options = {"INTERNAL", "UNDO"}

    directory: StringProperty(subtype="DIR_PATH")

    def invoke(self, context, event):
        self.directory = bpy.path.abspath(context.scene.plinth.output_dir)
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        context.scene.plinth.output_dir = self.directory
        props.tag_viewports(context)
        return {"FINISHED"}


class PLINTH_OT_shoot_done(bpy.types.Operator):
    """Open the folder with the images and go back to the Shoot button"""
    bl_idname = "plinth.shoot_done"
    bl_label = "Open Folder"
    bl_options = {"INTERNAL"}

    open_folder: BoolProperty(default=True)

    def execute(self, context):
        job = shoot_mod.current_job()
        shoot_mod.JOB["job"] = None
        if self.open_folder and job is not None and os.path.isdir(job.out):
            bpy.ops.wm.path_open(filepath=job.out)
        props.tag_viewports(context)
        return {"FINISHED"}


class PLINTH_OT_load_image(bpy.types.Operator):
    """Choose an image: for the part's Image material, a new decal or livery,
    or to replace the decal being edited"""
    bl_idname = "plinth.load_image"
    bl_label = "Choose Image"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.png;*.jpg;*.jpeg;*.tif;*.tiff;*.exr;*.webp;*.bmp;*.tga",
                                options={"HIDDEN"})
    purpose: EnumProperty(name="For", items=[
        ("MATERIAL", "Material", "The part's Image material"),
        ("DECAL", "Decal", "A new decal projected onto the part"),
        ("LIVERY", "Livery", "A new livery layer on the part's UVs"),
        ("REPLACE", "Replace", "A new image for the decal being edited")], default="MATERIAL")

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH"

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        try:
            image = bpy.data.images.load(self.filepath, check_existing=True)
        except RuntimeError as exc:
            self.report({"WARNING"}, "Couldn't open that image: %s" % str(exc).strip().splitlines()[-1])
            return {"CANCELLED"}
        obj = context.active_object
        scene = context.scene
        materials.ensure_visible_part(obj, context.evaluated_depsgraph_get())
        if self.purpose == "MATERIAL":
            materials.set_image(obj, image)
            scene.plinth.material_category = "IMAGE"
        elif self.purpose == "REPLACE":
            owner, slot, _rec = decals.owner(scene.plinth.active_decal)
            if owner is None:
                return {"CANCELLED"}
            decals.update(owner, scene.plinth.active_decal, slot, image=image.name)
        else:
            rv3d = getattr(context, "region_data", None)
            origin = rv3d.view_matrix.inverted().translation if rv3d is not None else None
            rec = decals.add(scene, obj, image, "PROJECT" if self.purpose == "DECAL" else "UV",
                             view_origin=origin)
            scene.plinth.active_decal = rec["id"]
        props.tag_viewports(context)
        return {"FINISHED"}


def _texture_part(context):
    obj = context.active_object
    if obj is None or obj.type != "MESH":
        return None
    if not obj.material_slots:
        obj.data.materials.append(None)
    materials.ensure_visible_part(obj, context.evaluated_depsgraph_get())
    return obj


class PLINTH_OT_find_textures(bpy.types.Operator):
    """Find the texture maps this part came with (its imported material, broken
    paths searched for nearby, the rest of the set beside them) and use them"""
    bl_idname = "plinth.find_textures"
    bl_label = "Find Textures"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH"

    def execute(self, context):
        from . import textures
        obj = _texture_part(context)
        found = textures.find_for_part(obj, obj.active_material_index)
        if not found:
            self.report({"WARNING"}, "No textures found for this part. Try From a folder…, or pick each map")
            return {"CANCELLED"}
        maps = textures.assign(obj, found, obj.active_material_index)
        context.scene.plinth.material_category = "IMAGE"
        props.tag_viewports(context)
        self.report({"INFO"}, "Textures: " + textures.summary(maps))
        return {"FINISHED"}


class PLINTH_OT_texture_folder(bpy.types.Operator):
    """Pick a folder of textures: the set whose name best matches this part is
    sorted into colour, roughness, metal, normal and the rest by file name"""
    bl_idname = "plinth.texture_folder"
    bl_label = "Use Textures From Folder"
    bl_options = {"REGISTER", "UNDO"}

    directory: StringProperty(subtype="DIR_PATH")

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH"

    def invoke(self, context, event):
        from . import textures
        if not self.directory and context.scene.get(textures.FOLDER_PROP):
            self.directory = context.scene[textures.FOLDER_PROP]
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        from . import textures
        obj = _texture_part(context)
        found = textures.match_folder(obj, self.directory, obj.active_material_index)
        if not found:
            self.report({"WARNING"}, "No texture maps in that folder (names like _BaseColor, _Roughness, _Normal)")
            return {"CANCELLED"}
        context.scene[textures.FOLDER_PROP] = self.directory
        maps = textures.assign(obj, found, obj.active_material_index, replace=True)
        context.scene.plinth.material_category = "IMAGE"
        props.tag_viewports(context)
        self.report({"INFO"}, "Textures: " + textures.summary(maps))
        return {"FINISHED"}


class PLINTH_OT_texture_file(bpy.types.Operator):
    """Choose an image file for one map of this part"""
    bl_idname = "plinth.texture_file"
    bl_label = "Choose Texture"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.png;*.jpg;*.jpeg;*.tif;*.tiff;*.exr;*.hdr;*.webp;*.bmp;*.tga;*.dds",
                                options={"HIDDEN"})
    channel: StringProperty(default="COLOUR")

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH"

    def invoke(self, context, event):
        from . import textures
        if not self.filepath and context.scene.get(textures.FOLDER_PROP):
            self.filepath = os.path.join(context.scene[textures.FOLDER_PROP], "")
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        from . import textures
        image = textures.load(self.filepath)
        if image is None:
            self.report({"WARNING"}, "Couldn't open that image")
            return {"CANCELLED"}
        obj = _texture_part(context)
        context.scene[textures.FOLDER_PROP] = os.path.dirname(self.filepath)
        textures.set_map(obj, self.channel if self.channel in textures.LABELS else "COLOUR", image,
                         obj.active_material_index)
        props.tag_viewports(context)
        return {"FINISHED"}


class PLINTH_OT_remove_decal(bpy.types.Operator):
    """Remove the decal or livery layer being edited"""
    bl_idname = "plinth.remove_decal"
    bl_label = "Remove Decal"
    bl_options = {"REGISTER", "UNDO"}

    decal: StringProperty()

    def execute(self, context):
        decal_id = self.decal or context.scene.plinth.active_decal
        owner, slot, _rec = decals.owner(decal_id)
        if owner is None or not decals.remove(owner, decal_id, slot):
            return {"CANCELLED"}
        if context.scene.plinth.active_decal == decal_id:
            context.scene.plinth.active_decal = ""
        props.tag_viewports(context)
        return {"FINISHED"}


class PLINTH_OT_save_material(bpy.types.Operator):
    """Save this part's material to your library, to use on any part in any file"""
    bl_idname = "plinth.save_material"
    bl_label = "Save Material to Library"
    bl_options = {"REGISTER"}

    label: StringProperty(name="Name", default="")

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH" and obj.active_material is not None

    def execute(self, context):
        obj = context.active_object
        materials.ensure_visible_part(obj, context.evaluated_depsgraph_get())
        entry_id = matlib.save(obj, label=self.label or None)
        if entry_id is None:
            return {"CANCELLED"}
        self.report({"INFO"}, "Saved \"%s\" to your materials" % matlib.entries()[entry_id]["label"])
        context.scene.plinth.material_category = "SAVED"
        context.scene.plinth.material_family = "SAVED"         # compact picker: show it
        from . import uistate
        if uistate.browser.get("open"):
            uistate.browser.update(section="MINE", query="", row=0)
        props.tag_viewports(context)
        return {"FINISHED"}


class PLINTH_OT_use_saved_material(bpy.types.Operator):
    """Put this saved material on the part"""
    bl_idname = "plinth.use_saved_material"
    bl_label = "Use Saved Material"
    bl_options = {"REGISTER", "UNDO"}

    entry: StringProperty()

    @classmethod
    def poll(cls, context):
        return bool(_product_objects(context))

    def execute(self, context):
        depsgraph = context.evaluated_depsgraph_get()
        done = False
        for obj in _product_objects(context):
            materials.ensure_visible_part(obj, depsgraph)
            done = matlib.apply(obj, self.entry) or done
        if not done:
            self.report({"WARNING"}, "That saved material is gone")
            return {"CANCELLED"}
        props.tag_viewports(context)
        return {"FINISHED"}


class PLINTH_OT_apply_catalog(bpy.types.Operator):
    """Put this material on the part (or on every part like it, or the selection)"""
    bl_idname = "plinth.apply_catalog"
    bl_label = "Apply Material"
    bl_options = {"REGISTER", "UNDO"}

    entry: StringProperty()
    scope: EnumProperty(items=[("PART", "This part", ""), ("SHARING", "Parts like it", ""),
                               ("SELECTED", "Selected", "")], default="PART")

    @classmethod
    def poll(cls, context):
        return bool(_product_objects(context))

    def execute(self, context):
        from . import browser, catalog
        objects = _product_objects(context)
        act = context.active_object if context.active_object in objects else objects[0]
        depsgraph = context.evaluated_depsgraph_get()
        materials.ensure_visible_part(act, depsgraph)
        if self.scope == "SHARING":
            parts = materials.parts_sharing(browser.scene_products(context.scene), act)
        elif self.scope == "SELECTED":
            parts = [(act, act.active_material_index)]
            for o in objects:
                if o is not act:
                    materials.ensure_visible_part(o, depsgraph)
                    parts.append((o, o.active_material_index))
        else:
            parts = [(act, act.active_material_index)]
        if not catalog.apply(act, self.entry, parts[0][1]):
            self.report({"WARNING"}, "That material is gone")
            return {"CANCELLED"}
        if len(parts) > 1:
            materials.link_parts(parts)            # one material: an edit reaches them all
        auto_preset(context, [o for o, _i in parts])
        if self.entry.startswith("preset:pk."):
            from . import content
            content.mark_seen([self.entry[7:]])
        props.tag_viewports(context)
        return {"FINISHED"}


class PLINTH_OT_update_saved_material(bpy.types.Operator):
    """Replace this saved material with the part's material as it is now"""
    bl_idname = "plinth.update_saved_material"
    bl_label = "Update Saved Material"
    bl_options = {"REGISTER"}

    entry: StringProperty()

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH" and obj.active_material is not None

    def execute(self, context):
        if not matlib.update(self.entry, context.active_object):
            self.report({"WARNING"}, "That saved material is gone")
            return {"CANCELLED"}
        self.report({"INFO"}, "Updated \"%s\"" % matlib.entries()[self.entry]["label"])
        props.tag_viewports(context)
        return {"FINISHED"}


class PLINTH_OT_link_parts(bpy.types.Operator):
    """Make every part like this one share its material (an edit changes all of
    them), or give this part its own copy again"""
    bl_idname = "plinth.link_parts"
    bl_label = "Link Parts"
    bl_options = {"REGISTER", "UNDO"}

    unlink: BoolProperty(default=False)

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH" and obj.material_slots

    def execute(self, context):
        from . import browser
        obj = context.active_object
        if self.unlink:
            done = materials.unlink_part(obj) is not None
        else:
            parts = materials.parts_sharing(browser.scene_products(context.scene), obj)
            done = len(parts) > 1 and materials.link_parts(parts) is not None
            if done:
                self.report({"INFO"}, "%d parts now share one material" % len(parts))
        props.tag_viewports(context)
        return {"FINISHED"} if done else {"CANCELLED"}


class PLINTH_OT_remove_saved_material(bpy.types.Operator):
    """Remove this saved material from your library (parts using it keep it)"""
    bl_idname = "plinth.remove_saved_material"
    bl_label = "Remove Saved Material"
    bl_options = {"REGISTER"}

    entry: StringProperty()

    def execute(self, context):
        if not matlib.remove(self.entry):
            return {"CANCELLED"}
        props.tag_viewports(context)
        return {"FINISHED"}


class PLINTH_OT_copy_support_info(bpy.types.Operator):
    """Copy your Plinth and Blender versions, system and content packs, to paste into a bug report"""
    bl_idname = "plinth.copy_support_info"
    bl_label = "Copy Support Info"

    def execute(self, context):
        from . import content, support
        packs = content.LIBRARY.installed if content.LIBRARY else {}
        context.window_manager.clipboard = support.support_info(packs=packs)
        self.report({"INFO"}, "Support info copied: paste it into your bug report")
        return {"FINISHED"}


# --- Content library (content.py) --------------------------------------------------------

class PLINTH_OT_library_install_file(bpy.types.Operator):
    """Install a Plinth content pack from a .zip on this computer (a scene kit, a pack someone sent you)"""
    bl_idname = "plinth.library_install_file"
    bl_label = "Install Pack from File…"

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.zip", options={"HIDDEN"})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        from . import content
        try:
            name = content.install_file(bpy.path.abspath(self.filepath))
        except (content.ContentError, OSError) as e:
            self.report({"WARNING"}, str(e))
            return {"CANCELLED"}
        self.report({"INFO"}, "Installed %s" % name)
        return {"FINISHED"}


class PLINTH_OT_library_remove(bpy.types.Operator):
    """Remove this pack's files from your computer (install it from its .zip again to bring it back)"""
    bl_idname = "plinth.library_remove"
    bl_label = "Remove Pack"

    pack: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        from . import content
        content.LIBRARY.remove(self.pack)
        content.refresh_registry()
        return {"FINISHED"}


class PLINTH_OT_library_seen(bpy.types.Operator):
    """Clear the "new" marks on newly installed content"""
    bl_idname = "plinth.library_seen"
    bl_label = "Mark Content as Seen"

    def execute(self, context):
        from . import content
        content.mark_seen()
        props.tag_viewports(context)
        return {"FINISHED"}


classes = (PLINTH_OT_open_workspace, PLINTH_OT_build_studio, PLINTH_OT_set_step, PLINTH_OT_aim_light,
           PLINTH_OT_paint_light, PLINTH_OT_set_finish, PLINTH_OT_set_part,
           PLINTH_OT_edit_material, PLINTH_OT_glass_thin, PLINTH_OT_enable_gpu,
           PLINTH_OT_apply_catalog, PLINTH_OT_update_saved_material, PLINTH_OT_link_parts, PLINTH_OT_set_active_light,
           PLINTH_OT_add_light, PLINTH_OT_duplicate_light, PLINTH_OT_remove_light,
           PLINTH_OT_add_target, PLINTH_OT_remove_target, PLINTH_OT_aim_at,
           PLINTH_OT_add_shot, PLINTH_OT_duplicate_shot, PLINTH_OT_remove_shot, PLINTH_OT_set_gobo,
           PLINTH_OT_add_turntable, PLINTH_OT_apply_studio, PLINTH_OT_set_form, PLINTH_OT_set_scene, PLINTH_OT_scene_detail, PLINTH_OT_scene_shuffle,
           PLINTH_OT_save_studio,
           PLINTH_OT_compare, PLINTH_OT_shoot, PLINTH_OT_shoot_stop, PLINTH_OT_shoot_done,
           PLINTH_OT_choose_folder, PLINTH_OT_set_hdri, PLINTH_OT_load_hdri,
           PLINTH_OT_bake_lighting, PLINTH_OT_clear_lighting, PLINTH_OT_bake_material,
           PLINTH_OT_unbake_material, PLINTH_OT_load_image, PLINTH_OT_find_textures,
           PLINTH_OT_texture_folder, PLINTH_OT_texture_file, PLINTH_OT_remove_decal,
           PLINTH_OT_save_material, PLINTH_OT_use_saved_material, PLINTH_OT_remove_saved_material,
           PLINTH_OT_library_install_file, PLINTH_OT_library_remove,
           PLINTH_OT_library_seen, PLINTH_OT_copy_support_info)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    job = shoot_mod.current_job()
    if job is not None:
        job.stop()                                  # puts the scene back
    shoot_mod.JOB["job"] = None
    if bpy.app.timers.is_registered(_shoot_tick):
        bpy.app.timers.unregister(_shoot_tick)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
