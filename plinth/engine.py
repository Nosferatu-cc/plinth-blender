# SPDX-License-Identifier: GPL-3.0-or-later
"""Render-engine presets.

Presets describe *intent* (what you are shooting) and map onto real Blender
settings. Every setting is applied defensively: if a property does not exist
in the running Blender version it is skipped and reported, never crashes.
"""

import bpy

# --- Engine identifiers -------------------------------------------------------

def eevee_engine_id():
    """Blender 4.2-4.x calls it BLENDER_EEVEE_NEXT; 5.x calls it BLENDER_EEVEE."""
    items = {i.identifier for i in
             bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    return "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in items else "BLENDER_EEVEE"


# --- Preset tables ------------------------------------------------------------
# Paths are relative to the scene, e.g. "eevee.taa_render_samples". Names are
# Blender 5.2's; older versions skip what they don't have (apply_settings).
#
# 0.9 re-tune (measured: tests/test_render_quality.py, tests/test_parity.py):
# * Ray tracing at full resolution for stills: half resolution plus the
#   denoiser was what made reflections and glass soft.
# * Screen-trace quality 1.0 (Blender's default 0.25 misses thin geometry such
#   as trim and panel gaps, which then fall back to the blurry probe).
# * Overscan: screen-space effects see past the frame edge, so reflections
#   don't fade out near the border.
# * Pixel filter 1.15 px (Blender: 1.5) for crisper edges and texture; TAA's
#   many samples keep edges clean. Motion keeps 1.5 (steadier frame to frame).
# * Anisotropic filtering x16 (5.2: per scene): textures stay sharp at grazing
#   angles, such as a tyre's sidewall or the floor.
# * Bilateral denoise off where reflections should stay sharp (Gloss, Final).

_COMMON_STILL = {
    "eevee.use_raytracing": True,
    "eevee.ray_tracing_method": "SCREEN",
    "eevee.ray_tracing_options.resolution_scale": "1",
    "eevee.ray_tracing_options.screen_trace_quality": 1.0,
    "eevee.ray_tracing_options.screen_trace_thickness": 0.2,
    "eevee.ray_tracing_options.use_denoise": True,
    "eevee.ray_tracing_options.denoise_spatial": True,
    "eevee.ray_tracing_options.denoise_temporal": True,
    "eevee.use_fast_gi": True,
    "eevee.fast_gi_method": "GLOBAL_ILLUMINATION",
    "eevee.indirect_light_intensity": 1.25,
    "eevee.use_shadows": True,
    "eevee.shadow_resolution_scale": 1.0,
    "eevee.use_shadow_jitter_viewport": True,       # Plinth lights' jittered shadows, live
    "eevee.use_overscan": True,
    "eevee.overscan_size": 3.0,
    "render.filter_size": 1.15,
    "render.anisotropic_filter": "FILTER_16",
    "render.use_motion_blur": False,
}

EEVEE_PRESETS = {
    "LIVE": {
        "label": "Live",
        "description": "Framing and blocking. Instant feedback",
        "settings": {
            "eevee.use_raytracing": False,
            "eevee.use_fast_gi": False,
            "eevee.indirect_light_intensity": 1.0,
            "eevee.use_shadows": True,
            "eevee.shadow_resolution_scale": 0.25,
            "eevee.use_shadow_jitter_viewport": False,
            "eevee.use_overscan": False,
            "eevee.taa_samples": 8,
            "eevee.taa_render_samples": 16,
            "render.filter_size": 1.5,
            "render.anisotropic_filter": "FILTER_4",
            "render.use_motion_blur": False,
        },
    },
    "STUDIO": {
        "label": "Studio",
        "description": "Product stills. Clean, soft and accurate",
        "settings": dict(_COMMON_STILL, **{
            "eevee.ray_tracing_options.trace_max_roughness": 0.5,
            "eevee.ray_tracing_options.denoise_bilateral": True,
            "eevee.gi_cubemap_resolution": "1024",
            "eevee.fast_gi_resolution": "2",
            "eevee.fast_gi_step_count": 8,
            "eevee.fast_gi_ray_count": 2,
            "eevee.shadow_ray_count": 2,
            "eevee.shadow_step_count": 12,
            "eevee.taa_samples": 16,
            "eevee.taa_render_samples": 64,
        }),
    },
    "GLOSS": {
        "label": "Gloss",
        "description": "Chrome, glass, lacquer. Sharper reflections",
        "settings": dict(_COMMON_STILL, **{
            "eevee.ray_tracing_options.trace_max_roughness": 1.0,
            "eevee.ray_tracing_options.denoise_bilateral": False,
            "eevee.gi_cubemap_resolution": "1024",
            "eevee.fast_gi_resolution": "1",
            "eevee.fast_gi_step_count": 16,
            "eevee.fast_gi_ray_count": 2,
            "eevee.shadow_ray_count": 3,
            "eevee.shadow_step_count": 16,
            "eevee.clamp_surface_indirect": 0.0,
            "eevee.taa_samples": 16,
            "eevee.taa_render_samples": 128,
        }),
    },
    "MOTION": {
        "label": "Motion",
        "description": "Turntables and loops. Steady frame to frame",
        "settings": dict(_COMMON_STILL, **{
            "eevee.ray_tracing_options.resolution_scale": "2",
            "eevee.ray_tracing_options.screen_trace_quality": 0.5,
            "eevee.ray_tracing_options.trace_max_roughness": 0.5,
            "eevee.ray_tracing_options.denoise_bilateral": True,
            "eevee.shadow_resolution_scale": 0.5,
            "eevee.use_shadow_jitter_viewport": False,
            "eevee.shadow_ray_count": 1,
            "eevee.shadow_step_count": 8,
            "eevee.taa_samples": 16,
            "eevee.taa_render_samples": 32,
            "render.filter_size": 1.5,
            "render.anisotropic_filter": "FILTER_8",
            "render.use_motion_blur": True,
        }),
    },
    "FINAL": {
        "label": "Final",
        "description": "Eevee at its best: stills in seconds, close to Cycles",
        "settings": dict(_COMMON_STILL, **{
            "eevee.ray_tracing_options.trace_max_roughness": 1.0,
            "eevee.ray_tracing_options.denoise_bilateral": False,
            "eevee.gi_cubemap_resolution": "2048",
            "eevee.fast_gi_resolution": "1",
            "eevee.fast_gi_step_count": 16,
            "eevee.fast_gi_ray_count": 4,
            "eevee.fast_gi_quality": 1.0,
            "eevee.shadow_ray_count": 4,
            "eevee.shadow_step_count": 16,
            "eevee.clamp_surface_indirect": 0.0,
            "eevee.taa_samples": 16,
            "eevee.taa_render_samples": 256,
        }),
    },
}

# Eevee Studio/Gloss/Motion/Final add Fast GI, full-roughness tracing and 1.25x
# indirect light. Measured against Cycles (tests/test_parity.py): worst-case
# luminance-histogram distance 0.91 -> 0.32, mean luminance gap 0.10 -> 0.045.
# Not more: what Eevee sees through glass counts as indirect light, and at
# 1.5x clear glass looked frosted (a milky veil the colour look squashes the
# boosted light into; tests/test_glass_clarity.py). 1.0 is clear but leaves
# Eevee too dark against Cycles (distance 0.67).
# The rest of the fix is the Plinth_Probe sphere probe in studio.build_probe.

# Cycles: adaptive sampling (samples are a ceiling; clean pixels stop early),
# the light tree, OIDN with albedo + normal at its best quality, and bounces
# sized for products: glass needs transmission and transparent depth (layered
# windows), diffuse rarely past 4. Filter Glossy trades a little sharpness in
# rough reflections for far less noise in previews; Hero turns it off and adds
# refractive caustics (light focused through glass and lenses) and, on the
# CPU, path guiding.
_CYCLES_COMMON = {
    "cycles.use_adaptive_sampling": True,
    "cycles.use_light_tree": True,
    "cycles.use_denoising": True,
    "cycles.denoiser": "OPENIMAGEDENOISE",
    "cycles.denoising_input_passes": "RGB_ALBEDO_NORMAL",
    "cycles.denoising_prefilter": "ACCURATE",
    "cycles.denoising_use_gpu": True,
    "cycles.use_preview_denoising": True,
    "cycles.preview_denoiser": "AUTO",
    "cycles.preview_denoising_start_sample": 1,
    "cycles.diffuse_bounces": 4,
    "cycles.transmission_bounces": 16,
    "cycles.transparent_max_bounces": 32,
    "cycles.volume_bounces": 2,
    "cycles.sample_clamp_indirect": 10.0,
    "cycles.pixel_filter_type": "BLACKMAN_HARRIS",
}

CYCLES_PRESETS = {
    "PREVIEW": {
        "label": "Preview",
        "description": "A fast, honest look at the final image",
        "settings": dict(_CYCLES_COMMON, **{
            "cycles.samples": 128, "cycles.adaptive_threshold": 0.05, "cycles.adaptive_min_samples": 0,
            "cycles.denoising_quality": "BALANCED", "cycles.max_bounces": 8, "cycles.glossy_bounces": 6,
            "cycles.blur_glossy": 0.5, "cycles.caustics_refractive": False, "cycles.use_guiding": False,
            "cycles.preview_samples": 32, "cycles.filter_width": 1.5}),
    },
    "FINAL": {
        "label": "Final",
        "description": "Deliverable quality for web, shop and social",
        "settings": dict(_CYCLES_COMMON, **{
            "cycles.samples": 512, "cycles.adaptive_threshold": 0.015, "cycles.adaptive_min_samples": 0,
            "cycles.denoising_quality": "HIGH", "cycles.max_bounces": 12, "cycles.glossy_bounces": 8,
            "cycles.blur_glossy": 0.1, "cycles.caustics_refractive": False, "cycles.use_guiding": False,
            "cycles.preview_samples": 128, "cycles.filter_width": 1.2}),
    },
    "HERO": {
        "label": "Hero",
        "description": "Print and campaign shots. Takes its time",
        "settings": dict(_CYCLES_COMMON, **{
            "cycles.samples": 2048, "cycles.adaptive_threshold": 0.005, "cycles.adaptive_min_samples": 64,
            "cycles.denoising_quality": "HIGH", "cycles.max_bounces": 24, "cycles.glossy_bounces": 12,
            "cycles.blur_glossy": 0.0, "cycles.caustics_refractive": True, "cycles.use_guiding": True,
            "cycles.preview_samples": 512, "cycles.filter_width": 1.2}),
    },
}

# The single Faster -> Better scale on the Shoot screen.
QUALITY_LADDER = {
    "EEVEE_STUDIO": ("EEVEE", "STUDIO"),
    "EEVEE_FINAL": ("EEVEE", "FINAL"),
    "CYCLES_PREVIEW": ("CYCLES", "PREVIEW"),
    "CYCLES_FINAL": ("CYCLES", "FINAL"),
    "CYCLES_HERO": ("CYCLES", "HERO"),
}


# --- Applying -----------------------------------------------------------------

def _resolve(root, path):
    *parents, attr = path.split(".")
    obj = root
    for p in parents:
        obj = getattr(obj, p, None)
        if obj is None:
            return None, attr
    return obj, attr


# Settings Plinth's presets use that Blender 4.2 LTS doesn't have (checked on
# 4.2.23): they're skipped there, never renamed. 4.2 always uses fast GI with
# ray tracing, has no indirect-light boost, and keeps anisotropic filtering in
# the user's Preferences (which Plinth never changes).
NOT_IN_4_2 = frozenset({"eevee.use_fast_gi", "eevee.indirect_light_intensity", "render.anisotropic_filter"})


def expected_skips():
    """The settings this Blender is allowed to skip (empty on 5.x)."""
    return NOT_IN_4_2 if bpy.app.version < (5, 0, 0) else frozenset()


def apply_settings(scene, settings):
    """Apply a {path: value} dict. Returns the list of paths that were skipped."""
    skipped = []
    for path, value in settings.items():
        obj, attr = _resolve(scene, path)
        if obj is None or not hasattr(obj, attr):
            skipped.append(path)
            continue
        try:
            setattr(obj, attr, value)
        except (TypeError, ValueError, AttributeError):
            skipped.append(path)
    return skipped


def set_engine(scene, engine):
    """engine: 'EEVEE' or 'CYCLES'."""
    scene.render.engine = eevee_engine_id() if engine == "EEVEE" else "CYCLES"


def apply_preset(scene, engine, key, tier=None):
    """Switch engine and apply a preset, fitted to this computer's tier
    (Eevee: memory-bound settings; see TIERS)."""
    table = EEVEE_PRESETS if engine == "EEVEE" else CYCLES_PRESETS
    set_engine(scene, engine)
    settings = table[key]["settings"]
    if engine == "EEVEE":
        settings = fit_to_tier(settings, tier or current_tier())
    return apply_settings(scene, settings)


# --- Hardware tier ---------------------------------------------------------------
# Presets describe intent; the tier fits the memory-hungry parts to the GPU.
# Blender doesn't report video memory, so Auto guesses from the GPU's name
# (detect_tier) and Preferences > Performance can say otherwise. Each tier sets
# the shadow pool and caps resolutions; it never raises what a preset asks for.

TIERS = {
    "LIGHT": {"label": "Light", "description": "Integrated graphics or under 6 GB: lighter shadows and probes",
              "set": {"eevee.shadow_pool_size": "256"},
              "max": {"eevee.gi_cubemap_resolution": "512"},
              "min": {"eevee.ray_tracing_options.resolution_scale": "2",
                      "eevee.fast_gi_resolution": "2"}},
    "BALANCED": {"label": "Balanced", "description": "Most graphics cards (8 GB)",
                 "set": {"eevee.shadow_pool_size": "512"},
                 "max": {"eevee.gi_cubemap_resolution": "1024"}, "min": {}},
    "MAX": {"label": "Max", "description": "12 GB or more: the sharpest shadows and reflections",
            "set": {"eevee.shadow_pool_size": "1024"}, "max": {}, "min": {}},
}


def _num(v):
    return int(v) if isinstance(v, str) and v.isdigit() else v


def fit_to_tier(settings, tier):
    """A copy of ``settings`` fitted to a tier (pure)."""
    spec = TIERS.get(tier, TIERS["BALANCED"])
    out = dict(settings)
    for path, cap in spec["max"].items():
        if path in out and _num(out[path]) > _num(cap):
            out[path] = cap
    for path, floor in spec["min"].items():           # resolution_scale "2" = half: bigger is lighter
        if path in out and _num(out[path]) < _num(floor):
            out[path] = floor
    out.update(spec["set"])
    return out


_INTEGRATED = ("intel(r) uhd", "intel(r) hd", "iris", "radeon(tm) graphics", "radeon graphics",
               "vega 8", "vega 6", "llvmpipe", "microsoft basic", "softpipe", "mali", "adreno")
_HIGH_END = (r"rtx (30[89]0|40[789]0|50[789]0)|rtx a[456]000|rtx [456]000 ada|"
             r"rx (79[05]0|7800|9070)|radeon pro w7|apple m\d (max|ultra)|h100|a100")
_DISCRETE = r"rtx|gtx|radeon rx|rx \d{4}|arc a\d|arc b\d|quadro|apple m\d pro"
# Discrete cards with 4 GB or less (Light: "under 6 GB"); a GTX 1650 laptop got
# Balanced's 8 GB settings. Checked after _HIGH_END, before _DISCRETE.
_SMALL = (r"gtx (16[35]0|10[35]0|9\d0)|\bmx ?\d{3}\b|rtx (2050|3050)( ti)? laptop|"
          r"rx (5[56]0|6[45]00)\b|arc a3\d0")


def detect_tier(renderer="", devices=()):
    """Guess LIGHT / BALANCED / MAX from the GPU name and Cycles' device names (pure).
    Nothing known (background, no GPU info): BALANCED."""
    import re
    names = " | ".join([renderer or ""] + list(devices)).lower().strip(" |")
    if not names:
        return "BALANCED"
    if re.search(_HIGH_END, names):
        return "MAX"
    if re.search(_SMALL, names):
        return "LIGHT"
    if re.search(_DISCRETE, names):
        return "BALANCED"
    if any(k in names for k in _INTEGRATED) or re.search(r"apple m\d\b", names):
        return "LIGHT"
    return "BALANCED"


GPU_INFO = {"renderer": "", "devices": [], "setup": None, "checked": False}


def _prefs():
    try:
        return bpy.context.preferences.addons[__package__].preferences
    except (KeyError, AttributeError):
        return None


def current_tier():
    prefs = _prefs()
    choice = getattr(prefs, "performance", "AUTO") if prefs is not None else "AUTO"
    if choice in TIERS:
        return choice
    if not GPU_INFO["renderer"]:        # before check_gpu ran: the name is a cheap, safe read
        GPU_INFO["renderer"] = _renderer()
    return detect_tier(GPU_INFO["renderer"], GPU_INFO["devices"])


def _renderer():
    if bpy.app.background:
        return ""
    try:
        import gpu
        return gpu.platform.renderer_get() or ""
    except Exception:
        return ""


# --- Cycles on the graphics card ---------------------------------------------------
# Plinth asks Cycles for the GPU, but Cycles uses the CPU until a compute device
# is chosen in Preferences > System, which most people never open. check_gpu()
# (once, on a timer: refreshing devices is slow and writes preferences) notes
# when a GPU is there but unused; the Shoot page then offers one click.

GPU_TYPES = ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL")


def _cycles_prefs():
    try:
        return bpy.context.preferences.addons["cycles"].preferences
    except (KeyError, AttributeError):
        return None


def gpu_choice(compute_type, devices):
    """(api, name) to switch Cycles to, or None when it already uses a GPU or
    there is none. ``devices``: [(name, type, use)] (pure)."""
    gpus = [d for d in devices if d[1] != "CPU"]
    in_use = any(d[2] for d in gpus if d[1] == compute_type)
    best = next((t for t in GPU_TYPES if any(d[1] == t for d in gpus)), None)
    if in_use or best is None:
        return None
    return best, next(d[0] for d in gpus if d[1] == best)


UNKNOWN_GPU = ("", "")      # GPU_INFO["setup"]: Cycles has no device yet, and we haven't probed


def gpu_setup(compute_type, devices, probed):
    """GPU_INFO["setup"] from Cycles' device list (pure). Without a probe the
    list is only what the user's preferences saved: empty on a computer whose
    devices were never listed, so a CPU-only Cycles may or may not have a GPU
    to switch to (UNKNOWN_GPU: the click probes)."""
    if probed or any(t != "CPU" for _n, t, _u in devices):
        return gpu_choice(compute_type, devices)
    return UNKNOWN_GPU if compute_type == "NONE" else None


def check_gpu(probe=False):
    """Fill GPU_INFO: the viewport GPU's name, Cycles' GPUs, and whether Cycles
    still needs a compute device.

    ``probe`` refreshes Cycles' devices, which loads every GPU runtime (CUDA,
    OptiX, HIP, oneAPI) into Blender. A broken driver can crash Blender there,
    out of reach of try/except (a GTX 1650 + Intel UHD laptop crashed seconds
    after start), so it only happens when the user asks: never on a timer,
    never in draw. Blender itself only probes when Preferences > System opens."""
    GPU_INFO["renderer"] = _renderer()
    cp = _cycles_prefs()
    if cp is not None:
        try:
            if probe:
                cp.refresh_devices()
            devices = [(d.name, d.type, d.use) for d in cp.devices]
            GPU_INFO["devices"] = sorted({n for n, t, _u in devices if t != "CPU"})
            GPU_INFO["setup"] = gpu_setup(cp.compute_device_type, devices, probe)
        except Exception as exc:
            print("Plinth: could not list Cycles devices:", exc)
    GPU_INFO["checked"] = True
    return GPU_INFO


def enable_gpu():
    """Let Cycles render on the graphics card: the best compute API this
    computer has, every GPU of that kind on. Probes the devices (the user
    clicked). Returns the GPU's name, or None when there's none to switch to."""
    cp = _cycles_prefs()
    if cp is None or not GPU_INFO.get("setup"):
        return None
    check_gpu(probe=True)
    setup = GPU_INFO.get("setup")
    if not setup:
        return None
    kind, name = setup
    cp.compute_device_type = kind
    cp.refresh_devices()
    for d in cp.devices:
        if d.type == kind:
            d.use = True
    GPU_INFO["setup"] = None
    return name


# --- Colour --------------------------------------------------------------------------
# Shoot's "Colour look" as intent; each maps onto a view transform and look.
COLOUR_LOOKS = {
    "TRUE": {"label": "True", "description": "Khronos PBR Neutral: colours as you set them (shop, catalogue)",
             "view_transform": "Khronos PBR Neutral", "look": "None"},
    "NATURAL": {"label": "Natural", "description": "AgX: bright highlights roll off softly, like film",
                "view_transform": "AgX", "look": "None"},
    "PUNCHY": {"label": "Punchy", "description": "AgX with more contrast and saturation",
               "view_transform": "AgX", "look": "AgX - Punchy"},
    "STANDARD": {"label": "Standard", "description": "Plain sRGB: bright colours clip",
                 "view_transform": "Standard", "look": "None"},
}


def colour_settings(intent):
    """{path: value} for a colour look (view transform first: looks depend on it)."""
    c = COLOUR_LOOKS.get(intent, COLOUR_LOOKS["NATURAL"])
    return {"view_settings.view_transform": c["view_transform"], "view_settings.look": c["look"]}


def apply_colour(scene, intent):
    """Set the scene's colour look. A look this Blender lacks falls back to
    plain AgX rather than leaving a half-applied look."""
    skipped = apply_settings(scene, colour_settings(intent))
    if skipped:
        apply_settings(scene, colour_settings("NATURAL"))
    return skipped


def apply_quality(scene, quality):
    engine, key = QUALITY_LADDER[quality]
    return apply_preset(scene, engine, key)


# --- Auto preset ---------------------------------------------------------------

def _principled_nodes(tree, seen=None):
    """Principled BSDFs in a node tree, following node groups."""
    seen = seen if seen is not None else set()
    if tree is None or tree.name in seen:
        return
    seen.add(tree.name)
    for node in tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            yield node
        elif node.type == "GROUP":
            yield from _principled_nodes(node.node_tree, seen)


def _value(node, name, default=0.0):
    sock = node.inputs.get(name)
    return sock.default_value if sock is not None and not sock.is_linked else default


def suggest_eevee_preset(scene, objects):
    """Pick the Eevee preset that suits what's being shot.

    Returns (key, reason): MOTION if anything moves (or a turntable is on),
    GLOSS for metal, glass or very shiny surfaces, otherwise STUDIO."""
    from . import materials, studio
    moving = any(o.animation_data and (o.animation_data.action or o.animation_data.drivers)
                 for o in objects)
    turntable = any(c.get("plinth_shot") == studio.TURNTABLE and c.get("plinth_enabled", True)
                    for c in studio.list_shots(scene))
    if moving or turntable:
        return "MOTION", "Something moves, so frames need to stay steady"
    for obj in objects:
        if materials.is_shiny(obj):
            return "GLOSS", "Glossy, metal or glass surfaces need sharp reflections"
        for slot in getattr(obj, "material_slots", ()):
            mat = slot.material
            if mat is None or not mat.node_tree or "plinth_preset" in mat:
                continue
            for bsdf in _principled_nodes(mat.node_tree):
                if _value(bsdf, "Metallic") >= 0.5:
                    return "GLOSS", "Metal surfaces need sharp reflections"
                if _value(bsdf, "Transmission Weight") >= 0.5:
                    return "GLOSS", "Glass needs sharp reflections and refraction"
                if _value(bsdf, "Roughness", 0.5) <= 0.2:
                    return "GLOSS", "Shiny surfaces need sharp reflections"
    return "STUDIO", "Soft, accurate light for everyday products"


class EngineSnapshot:
    """Remember and restore the engine + preset state around a Shoot (hybrid mode)."""

    PATHS = ["render.engine"] + sorted({p for t in (EEVEE_PRESETS, CYCLES_PRESETS)
                                        for v in t.values() for p in v["settings"]}
                                       | {p for t in TIERS.values() for p in t["set"]})

    def __init__(self, scene):
        self.scene = scene
        self.values = {}
        for path in self.PATHS:
            obj, attr = _resolve(scene, path)
            if obj is not None and hasattr(obj, attr):
                self.values[path] = getattr(obj, attr)

    def restore(self):
        engine = self.values.get("render.engine")
        if engine:
            self.scene.render.engine = engine
        apply_settings(self.scene, {k: v for k, v in self.values.items()
                                    if k != "render.engine"})
