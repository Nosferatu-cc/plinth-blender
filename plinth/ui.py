# SPDX-License-Identifier: GPL-3.0-or-later
"""Native panels. In the Plinth workspace: the sidebar panel that follows the
five-step flow (fallback/accessibility path under the floating overlay) and
the More popover. Everywhere else: one button that opens the workspace."""

import bpy

from . import gobos, hdri, materials, overlay, studio, workspace


class PLINTH_PT_open(bpy.types.Panel):
    """Outside the Plinth workspace: the way in."""
    bl_label = "plinth"
    bl_idname = "PLINTH_PT_open"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Plinth"

    @classmethod
    def poll(cls, context):
        return not workspace.is_active(context)

    def draw(self, context):
        layout = self.layout
        layout.label(text="Plinth works in its own workspace.")
        row = layout.row()
        row.scale_y = 1.6
        row.operator("plinth.open_workspace", icon="WORKSPACE")


def draw_material_editor(layout, context):
    """The full material editor: part, type, preset and every input by section.
    Drawn in the floating card's 'Edit material' popover and in the sidebar."""
    s = context.scene.plinth
    obj = context.active_object
    col = bpy.data.collections.get(studio.COLLECTION)
    if obj is None or obj.type != "MESH" or (col and obj.name in col.all_objects):
        layout.label(text="Select the product", icon="INFO")
        return
    if len(obj.material_slots) > 1:
        layout.label(text="Part")
        grid = layout.grid_flow(columns=2, align=True)
        for i in range(len(obj.material_slots)):
            grid.operator("plinth.set_part", text=materials.part_label(obj, i),
                          depress=i == obj.active_material_index).index = i
    layout.row().prop(s, "material_category", expand=True)
    current = materials.current_preset(obj)
    grid = layout.grid_flow(columns=2, align=True)
    for key, preset in materials.presets_in(s.material_category).items():
        grid.operator("plinth.set_finish", text=preset["label"], depress=key == current).finish = key
    mat = materials.part_material(obj)
    if mat is None or mat.get("plinth_type") not in materials.TYPES:
        layout.label(text="Pick a preset to start editing", icon="INFO")
        return
    node = materials.group_node(mat)
    spec = materials.TYPES[mat["plinth_type"]]
    sections = {}
    for name, kind, _d, _lo, _hi, section in materials.controls(mat["plinth_type"]):
        sections.setdefault(section, []).append((name, kind))
    for section, items in sections.items():
        box = layout.box()
        box.label(text=section)
        column = box.column(align=True)
        for name, kind in items:
            if name == "Thin pane":
                row = box.row(align=True)
                thin = bool(materials.socket(node, mat["plinth_type"], name).default_value)
                row.operator("plinth.glass_thin", text="Thin pane", depress=thin).thin = True
                row.operator("plinth.glass_thin", text="Solid", depress=not thin).thin = False
            elif kind == "colour":
                column.prop(materials.socket(node, mat["plinth_type"], name), "default_value", text=name)
            else:
                column.prop(materials.socket(node, mat["plinth_type"], name), "default_value", text=name,
                            slider=True)
    if mat["plinth_type"] == "GLASS":
        layout.label(text="Eevee: panes stay sharp; solid and frosted glass refract", icon="INFO")
    layout.operator("plinth.set_finish", text="Back to original", icon="LOOP_BACK").finish = "ORIGINAL"


class PLINTH_PT_material_editor(bpy.types.Panel):
    """Every control of the selected part's material."""
    bl_label = "Material"
    bl_idname = "PLINTH_PT_material_editor"
    bl_space_type = "VIEW_3D"
    bl_region_type = "HEADER"            # popovers need a header-region panel
    bl_ui_units_x = 16

    @classmethod
    def poll(cls, context):
        return workspace.is_active(context)

    def draw(self, context):
        draw_material_editor(self.layout, context)


class PLINTH_PT_more(bpy.types.Panel):
    """The 'More' popover from the floating card: this step's full native controls."""
    bl_label = "plinth"
    bl_idname = "PLINTH_PT_more"
    bl_space_type = "VIEW_3D"
    bl_region_type = "HEADER"            # popovers need a header-region panel
    bl_ui_units_x = 14

    @classmethod
    def poll(cls, context):
        return workspace.is_active(context)

    def draw(self, context):
        s = context.scene.plinth
        self.layout.label(text=s.bl_rna.properties["step"].enum_items[s.step].name)
        getattr(PLINTH_PT_main, "draw_" + s.step.lower())(self, self.layout, context, s)


class PLINTH_PT_main(bpy.types.Panel):
    bl_label = "plinth"
    bl_idname = "PLINTH_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Plinth"

    @classmethod
    def poll(cls, context):
        return workspace.is_active(context)

    def draw(self, context):
        s = context.scene.plinth
        layout = self.layout
        layout.use_property_split = False

        row = layout.row()
        row.prop(s, "show_overlay", icon="OVERLAY")
        if s.show_overlay and overlay.stats["samples"]:
            # Phase 2 spike instrumentation: CPU cost of one overlay redraw.
            row.label(text="%.2f ms" % overlay.stats["avg_ms"])

        row = layout.row(align=True)
        row.prop(s, "step", expand=True)
        layout.separator()

        getattr(self, "draw_" + s.step.lower())(layout, context, s)

    def draw_studio(self, layout, context, s):
        layout.operator("plinth.build_studio", icon="SCENE")
        row = layout.row(align=True)
        row.operator("plinth.apply_studio", icon="PRESET")
        row.operator("plinth.save_studio", text="", icon="ADD")
        bd = bpy.data.objects.get(studio.BACKDROP)
        if bd:
            mat = bd.active_material
            if mat and mat.node_tree and "Principled BSDF" in mat.node_tree.nodes:
                layout.prop(mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"],
                            "default_value", text="Backdrop")
            mod = bd.modifiers.get(studio.COVE_GN)
            col = layout.column(align=True)
            for _key, label, ident in studio.backdrop_inputs(bd):
                col.prop(*studio.modifier_input(mod, ident), text=label)

    def draw_targets(self, layout, context, s, who=None):
        layout.label(text="Targets")
        current = studio.aim_target(context.scene, who) if who is not None else None
        for tgt in studio.list_targets(context.scene):
            row = layout.row(align=True)
            if who is not None:
                row.operator("plinth.aim_at", text=studio.target_label(tgt),
                             depress=tgt == current).name = tgt.name
            else:
                row.label(text=studio.target_label(tgt))
            row.prop(tgt, "location", text="")
            if tgt.name != studio.TARGET:
                row.operator("plinth.remove_target", text="", icon="X").name = tgt.name
        layout.operator("plinth.add_target", icon="ADD")

    def draw_light(self, layout, context, s):
        grid = layout.grid_flow(columns=3, align=True)
        for light in studio.list_lights(context.scene):
            label = studio.light_label(light)
            grid.operator("plinth.set_active_light", text=label,
                          depress=label == s.active_light).name = label
        row = layout.row(align=True)
        row.operator("plinth.add_light", text="Add", icon="ADD")
        row.operator("plinth.duplicate_light", text="Duplicate", icon="DUPLICATE")
        row.operator("plinth.remove_light", text="Remove", icon="X")
        row = layout.row()
        row.scale_y = 1.4
        row.operator("plinth.paint_light", icon="LIGHT_AREA")
        light = studio.active_light(context.scene)
        if light is not None:
            box = layout.box()
            box.label(text=s.active_light)
            box.prop(light.data, "energy", text="Intensity")
            box.prop(light.data, "size", text="Softness")
            box.prop(light.data, "color", text="")
            box.prop(light.data, "shape", text="Shape")
            self.draw_targets(layout, context, s, light)
        if light is not None:
            current = gobos.current_gobo(light.data)
            layout.label(text="Gobo")
            row = layout.row(align=True)
            for key, preset in gobos.GOBO_PRESETS.items():
                row.operator("plinth.set_gobo", text=preset["label"],
                             depress=key == current).gobo = key
        self.draw_hdri(layout, context)

    def draw_hdri(self, layout, context):
        st = hdri.settings(context.scene)
        box = layout.box()
        box.label(text="HDRI", icon="WORLD")
        grid = box.grid_flow(columns=3, align=True)
        grid.operator("plinth.set_hdri", text="Off", depress=not st["on"]).image = ""
        for key in hdri.builtins():
            grid.operator("plinth.set_hdri", text=hdri.label(key),
                          depress=st["on"] and st["key"] == key).image = key
        box.operator("plinth.load_hdri", text="Your own…", icon="FILEBROWSER")
        world = context.scene.world
        light = world.node_tree.nodes.get(hdri.PREFIX + "Light") if st["on"] and world.node_tree else None
        mapping = world.node_tree.nodes.get(hdri.PREFIX + "Map") if light is not None else None
        if light is not None and mapping is not None:
            box.prop(light.inputs["Strength"], "default_value", text="Brightness")
            box.prop(mapping.inputs["Rotation"], "default_value", index=2, text="Rotation")

    def draw_material(self, layout, context, s):
        draw_material_editor(layout, context)

    def draw_camera(self, layout, context, s):
        for cam in studio.list_shots(context.scene):
            row = layout.row(align=True)
            row.label(text=studio.shot_label(cam), icon="RADIOBUT_ON" if cam == context.scene.camera
                      else "RADIOBUT_OFF")
            row.prop(cam.data, "lens", text="")
            if cam.get("plinth_shot") == studio.TURNTABLE:
                row.prop(cam, '["plinth_frames"]', text="Frames")
            row.operator("plinth.remove_shot", text="", icon="X").name = cam.name
        row = layout.row(align=True)
        row.operator("plinth.add_shot", text="From view", icon="ADD")
        row.operator("plinth.duplicate_shot", text="Duplicate", icon="DUPLICATE")
        layout.operator("plinth.add_turntable", icon="CON_ROTLIKE")
        cam = context.scene.camera
        if cam is not None and "plinth_shot" in cam:
            box = layout.box()
            box.label(text="Focus")
            box.prop(cam.data.dof, "use_dof", text="Blur background")
            box.prop(cam.data.dof, "aperture_fstop", text="f-stop")
            box.prop(cam.data.dof, "focus_object", text="Focus on")
            self.draw_targets(layout, context, s, cam)

    def draw_shoot(self, layout, context, s):
        row = layout.row(align=True)
        row.prop(s, "engine", expand=True)
        layout.prop(s, "eevee_preset" if s.engine == "EEVEE" else "cycles_preset", text="")
        layout.prop(s, "hybrid")
        layout.prop(s, "auto_preset")
        row = layout.row(align=True)
        row.operator("plinth.compare", icon="SPLIT_VERTICAL")
        if s.compare:
            row.prop(s, "compare", text="", icon="X", invert_checkbox=True)
            layout.prop(s, "compare_split", slider=True)
        layout.separator()
        layout.prop(s, "quality", expand=True)
        layout.prop(s, "size", text="")
        if s.size == "CUSTOM":
            layout.prop(s, "custom_long_edge")
        layout.prop(s, "file_format", expand=True)
        layout.prop(s, "transparent")
        layout.prop(s, "shadow_catcher")
        layout.prop(s, "output_dir", text="")
        layout.separator()
        layout.scale_y = 1.6
        layout.operator("plinth.shoot", icon="RENDER_STILL")


classes = (PLINTH_PT_open, PLINTH_PT_main, PLINTH_PT_more, PLINTH_PT_material_editor)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
