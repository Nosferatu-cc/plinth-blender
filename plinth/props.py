# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty,
                       PointerProperty, StringProperty)

from . import engine


def _items(table):
    return [(k, v["label"], v["description"]) for k, v in table.items()]


STEPS = [("STUDIO", "Studio", ""), ("LIGHT", "Light", ""), ("MATERIAL", "Material", ""),
         ("CAMERA", "Camera", ""), ("SHOOT", "Shoot", "")]


def tag_viewports(context):
    """Redraw every 3D view (the overlay and sidebar both read Scene.plinth)."""
    wm = context.window_manager
    for win in (wm.windows if wm else ()):
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _on_colour_change(self, context):
    engine.apply_colour(context.scene, self.view_transform)
    tag_viewports(context)


def _on_engine_change(self, context):
    key = self.eevee_preset if self.engine == "EEVEE" else self.cycles_preset
    engine.apply_preset(context.scene, self.engine, key)
    from . import eeveeprep, workspace
    workspace.follow_engine(self.engine)          # Cycles: live in the viewport
    if self.engine == "EEVEE":
        eeveeprep.prepare(context.scene)          # glass refracts properly in Eevee


class PlinthSettings(bpy.types.PropertyGroup):
    step: EnumProperty(name="Step", items=STEPS, default="STUDIO")
    active_light: StringProperty(
        name="Light", description="The light being edited (Key, Rim, Fill or an added one)",
        default="Key")
    active_decal: StringProperty(
        name="Decal", description="The decal or livery layer being edited (its id)", default="")
    active_target: StringProperty(
        name="Target", description="The target new lights and shots aim at",
        default="Plinth_Target")
    material_category: EnumProperty(name="Material type", items=[
        ("PAINT", "Paint", "Car paint: solid, metallic, pearl, candy, wrap"),
        ("METAL", "Metal", "Chrome, alloy, brushed, gold, titanium"),
        ("GLASS", "Glass", "Windows, lenses, crystal (refracts in Eevee)"),
        ("TRIM", "Trim", "Plastics, carbon fibre, leather"),
        ("TYRE", "Tyre", "Tread and sidewall: road, performance, winter, all-terrain, slick"),
        ("BRAKE", "Brake", "Brake discs (plain, drilled, slotted, carbon ceramic) and calipers"),
        ("SAVED", "Saved", "Materials you saved, to use on any part in any file"),
        ("GLOW", "Glow", "Emissive parts: LEDs, tail lights, screens, neon"),
        ("IMAGE", "Image", "A picture made for the part: instrument clusters, screens, labels")],
        default="PAINT")
    material_family: StringProperty(
        name="Material family", description="Compact material picker: the family shown",
        default="PAINT")
    show_overlay: BoolProperty(
        name="Floating controls",
        description="Show Plinth's controls floating over the viewport",
        default=True, update=lambda self, context: tag_viewports(context))

    engine: EnumProperty(name="Engine", items=[
        ("EEVEE", "Eevee", "Real-time"), ("CYCLES", "Cycles", "Path traced")],
        default="EEVEE", update=_on_engine_change)
    eevee_preset: EnumProperty(name="Eevee preset", items=_items(engine.EEVEE_PRESETS),
                               default="STUDIO", update=_on_engine_change)
    cycles_preset: EnumProperty(name="Cycles preset", items=_items(engine.CYCLES_PRESETS),
                                default="PREVIEW", update=_on_engine_change)
    hybrid: BoolProperty(name="Work in Eevee, shoot in Cycles", default=True)
    auto_preset: BoolProperty(
        name="Auto preset",
        description="Pick the Eevee preset from the product's materials and motion",
        default=True)
    compare: BoolProperty(
        name="Compare with Cycles",
        description="Swipe between a Cycles render and the live viewport (camera view)",
        default=False, update=lambda self, context: tag_viewports(context))
    compare_split: FloatProperty(
        name="Swipe", description="Where the Cycles side ends", default=0.5,
        min=0.0, max=1.0, subtype="FACTOR", update=lambda self, context: tag_viewports(context))

    # Explicit numbers: files store the number, and Eevee Final (0.9) went in
    # the middle of the scale.
    quality: EnumProperty(name="Quality", items=[
        ("EEVEE_STUDIO", "Studio", "Eevee: seconds per shot", "", 0),
        ("EEVEE_FINAL", "Final", "Eevee at its best: stills in seconds, close to Cycles", "", 4),
        ("CYCLES_PREVIEW", "Preview", "Cycles: quick accurate check", "", 1),
        ("CYCLES_FINAL", "Final", "Cycles: deliverable quality", "", 2),
        ("CYCLES_HERO", "Hero", "Cycles: print and campaign", "", 3)], default="CYCLES_FINAL")
    size: EnumProperty(name="Size", items=[
        ("SOCIAL", "Social", "1350 px long edge"), ("FULLHD", "Full HD", "1920 px long edge"),
        ("MARKET", "Marketplace", "3000 px long edge"), ("CUSTOM", "Custom", "")], default="SOCIAL")
    custom_long_edge: IntProperty(name="Long edge", default=2048, min=16, max=16384, subtype="PIXEL")
    file_format: EnumProperty(name="Format", items=[
        ("PNG", "PNG", ""), ("JPEG", "JPEG", ""), ("OPEN_EXR", "EXR", "")], default="PNG")
    transparent: BoolProperty(name="Transparent background", default=True)
    shadow_catcher: BoolProperty(name="Keep floor shadow", default=True)
    shoot_scope: EnumProperty(name="Shoot", items=[
        ("ACTIVE", "This shot", "Shoot only the shot you're looking through"),
        ("TICKED", "Ticked", "Shoot every ticked shot in the bar")], default="TICKED",
        update=lambda self, context: tag_viewports(context))
    output_dir: StringProperty(name="Save to", default="//renders/", subtype="DIR_PATH")
    # Shoot › Edit output (REDESIGN.md §3). Applied only while shooting.
    color_depth: EnumProperty(name="Colour depth", items=[
        ("8", "8-bit", "Smaller files; fine for web and shop"),
        ("16", "16-bit", "Smooth gradients for retouching (PNG, EXR)")], default="8")
    # 0.9: colour looks as intent, live in the viewport (engine.COLOUR_LOOKS).
    # Numbers keep 0.8 files' choice: Standard was 0, AgX (now Natural) 1.
    view_transform: EnumProperty(name="Colour look", items=[
        (k, engine.COLOUR_LOOKS[k]["label"], engine.COLOUR_LOOKS[k]["description"], "", n)
        for k, n in (("TRUE", 2), ("NATURAL", 1), ("PUNCHY", 3), ("STANDARD", 0))],
        default="NATURAL", update=lambda self, context: _on_colour_change(self, context))
    save_mask: BoolProperty(
        name="Also save a mask",
        description="A black-and-white cut-out next to each image (transparent shots)",
        default=False)
    file_naming: EnumProperty(name="File names", items=[
        ("SHOT", "Shot", "Hero.png"),
        ("PRODUCT", "Product + shot", "Body_Hero.png"),
        ("DATE", "+ date", "Body_Hero_2026-10-27.png")], default="SHOT")
    open_when_done: BoolProperty(name="Open the folder when done", default=False)
    device: EnumProperty(name="Render on", items=[
        ("GPU", "GPU", "Graphics card (falls back to the CPU if none is set up)"),
        ("CPU", "CPU", "Processor")], default="GPU")
    denoise: BoolProperty(name="Denoise", description="Cycles: remove render noise",
                          default=True)
    sample_boost: FloatProperty(
        name="Sample boost", description="Multiplies the quality preset's samples",
        default=1.0, min=0.25, max=4.0)
    last_look: StringProperty(
        name="Studio look", description="The studio look applied last (Reset goes back to it)",
        default="")


def prefs(context=None):
    """Plinth's add-on preferences, or None (e.g. while unregistering)."""
    addon = (context or bpy.context).preferences.addons.get(__package__)
    return addon.preferences if addon else None


class PlinthPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    reduce_motion: BoolProperty(
        name="Reduce motion",
        description="Controls change instantly instead of sliding and fading",
        default=False)
    auto_frame: BoolProperty(
        name="Frame the product on every step",
        description="Changing step centres and fits the product (Camera and Shoot look "
                    "through the active shot)",
        default=True)
    navigation: EnumProperty(
        name="Mouse in the studio",
        description="How the mouse moves the view in the Plinth workspace (other workspaces "
                    "always use Blender's)",
        items=[("PLINTH", "Plinth",
                "Left-drag orbits, middle-drag pans, right-drag zooms, double-click sets the centre"),
               ("BLENDER", "Blender", "Blender's own mouse and keyboard navigation")],
        default="PLINTH")
    invert_zoom: BoolProperty(
        name="Invert zoom",
        description="Right-drag up moves away instead of closer (Plinth only; Blender's "
                    "own setting is untouched)",
        default=False)
    orbit_speed: FloatProperty(
        name="Orbit speed", description="How far a drag turns the view",
        default=1.0, min=0.1, max=4.0)
    nav_hint_seen: BoolProperty(
        name="Navigation hint seen",
        description="The studio's 'Drag to orbit' hint was dismissed by a first orbit",
        default=False)

    library_path: StringProperty(
        name="Library folder",
        description="Where installed content packs (HDRIs, materials, studios, scene kits) are kept. Empty: inside "
                    "Blender's user data folder",
        subtype="DIR_PATH", default="")
    material_picker: EnumProperty(
        name="Material picker",
        description="How the Material card picks materials",
        items=[("BROWSER", "Browser", "The card shows the part's material; Browse opens every material "
                                      "with search, favourites and your library"),
               ("COMPACT", "Compact", "Families and their materials right on the card")],
        default="BROWSER")
    performance: EnumProperty(
        name="Performance",
        description="How hard Eevee works your graphics card (shadow memory, probe and "
                    "ray-tracing resolution). Auto picks from the card's name",
        items=[("AUTO", "Auto", "Pick from this computer's graphics card")]
              + [(k, v["label"], v["description"]) for k, v in engine.TIERS.items()],
        default="AUTO")
    def draw(self, context):
        draw_help(self.layout)
        draw_library(self.layout, self)
        draw_performance(self.layout, self)
        row = self.layout.row()
        row.prop(self, "material_picker", expand=True)
        self.layout.prop(self, "reduce_motion")
        self.layout.prop(self, "auto_frame")
        col = self.layout.column(heading="Navigation")
        col.prop(self, "navigation", expand=True)
        sub = col.column()
        sub.active = self.navigation == "PLINTH"
        sub.prop(self, "orbit_speed")
        sub.prop(self, "invert_zoom")
        sub.prop(self, "nav_hint_seen", text="Hide the navigation hint")


def draw_performance(layout, prefs):
    """Preferences > Performance: the hardware tier, and Cycles on the GPU."""
    box = layout.box()
    box.label(text="Performance", icon="MEMORY")
    row = box.row()
    row.prop(prefs, "performance", expand=True)
    tier = engine.current_tier()
    info = engine.GPU_INFO
    if prefs.performance == "AUTO":
        gpu_name = (info["devices"] or [info["renderer"]])[0] if (info["devices"] or info["renderer"]) else ""
        box.label(text="Auto: %s%s" % (engine.TIERS[tier]["label"], (" (" + gpu_name + ")") if gpu_name else ""))
    if info.get("setup"):
        kind, name = info["setup"]
        r = box.row()
        r.label(text="Cycles renders on the CPU: your %s isn't set up for it" % (name or "graphics card"),
                icon="INFO")
        r.operator("plinth.enable_gpu", text="Use it")


def draw_help(layout):
    """Preferences › Help: where to ask, and what to paste into a bug report."""
    from . import support
    box = layout.box()
    head = box.row()
    head.label(text="Help", icon="HELP")
    head.operator("plinth.copy_support_info", text="Copy support info", icon="COPYDOWN")
    row = box.row()
    for label, url, icon in support.LINKS:
        row.operator("wm.url_open", text=label, icon=icon).url = url


def _size(n):
    return "%.1f MB" % (n / 1e6) if n >= 1e5 else "%d KB" % max(1, round(n / 1e3))


def draw_library(layout, prefs):
    """Preferences › Library: the content packs installed on this computer."""
    from . import content
    box = layout.box()
    head = box.row()
    head.label(text="Library", icon="ASSET_MANAGER")
    head.operator("plinth.library_install_file", text="Install from file…", icon="FILE_FOLDER")
    box.prop(prefs, "library_path")
    if content.STATE.get("error"):
        box.label(text=content.STATE["error"], icon="ERROR")
    installed = content.LIBRARY.installed if content.LIBRARY else {}
    if not installed:
        box.label(text="No content packs yet. Install one from its .zip to add its materials, looks, HDRIs or scene kits.")
        return
    col = box.column(align=True)
    for pack_id in sorted(installed):
        info = installed[pack_id]
        row = col.row(align=True)
        row.label(text="%s  ·  v%s  ·  %s" % (info.get("name", pack_id), info["version"], _size(info["size"])),
                  icon="CHECKMARK")
        row.operator("plinth.library_remove", text="", icon="TRASH").pack = pack_id


def _check_gpu():
    """Once, shortly after start: which GPU this is and whether Cycles uses it,
    from saved preferences only (engine.check_gpu: never probes devices here)."""
    try:
        engine.check_gpu()
    except Exception as exc:
        print("Plinth: GPU check failed:", exc)
    tag_viewports(bpy.context)
    return None


def register():
    bpy.utils.register_class(PlinthPreferences)
    bpy.utils.register_class(PlinthSettings)
    bpy.types.Scene.plinth = PointerProperty(type=PlinthSettings)
    if not bpy.app.background and not bpy.app.timers.is_registered(_check_gpu):
        bpy.app.timers.register(_check_gpu, first_interval=3.0)


def unregister():
    if bpy.app.timers.is_registered(_check_gpu):
        bpy.app.timers.unregister(_check_gpu)
    del bpy.types.Scene.plinth
    bpy.utils.unregister_class(PlinthSettings)
    bpy.utils.unregister_class(PlinthPreferences)
