# SPDX-License-Identifier: GPL-3.0-or-later
"""Navigation in the Plinth workspace: left-drag orbits, middle-drag pans,
right-drag zooms. Everywhere else Blender's mouse is untouched.

Add-on keymap items catch the button *press*; their operators' poll() asks
nav_mode(), so outside the workspace (or over Plinth's floating controls,
or with navigation set to "Blender" in the preferences) the press falls
through to Blender's own keymap. Each press runs a small "press, then decide"
modal: past Blender's drag threshold it navigates, released before that it's
a click (left selects, right and middle do nothing).

FREE mode (not looking through a camera) moves only the view, around the
view's own pivot (view_location), never a camera. SHOT mode (looking through
a camera) moves the shot: Plinth shots through their own orbit (azimuth,
Height, Distance around what they aim at: the Camera card's sliders), pan as
camera shift; one undo step per drag, Esc or right-click restores exactly.
Double-click sets the orbit centre (SHOT: a pivot target of the shot's own).
The maths is plain functions, testable headless.
"""

import math
import time

import bpy
from bpy.props import BoolProperty, EnumProperty
from mathutils import Quaternion, Vector

ORBIT_DEG_PER_PX = 0.4          # Blender's own turntable default
ZOOM_PER_PX = 0.005             # distance *= exp(px * ZOOM_PER_PX)
MIN_VIEW_DISTANCE = 1e-3


def prefs(context):
    addon = context.preferences.addons.get(__package__)
    return addon.preferences if addon is not None else None


def enabled(context):
    p = prefs(context)
    return p is None or getattr(p, "navigation", "PLINTH") == "PLINTH"


def nav_mode(context):
    """"FREE" (moving the view), "SHOT" (looking through a camera: moving the
    shot) or None (not ours: outside the Plinth workspace, over the floating
    controls, not in Object Mode, or navigation set to Blender's)."""
    if not enabled(context):
        return None
    from . import overlay, workspace
    if not workspace.is_active(context):
        return None
    space, region = context.space_data, context.region
    if space is None or space.type != "VIEW_3D" or region is None or region.type != "WINDOW":
        return None
    if context.mode != "OBJECT":
        return None
    if overlay.hovered(context) is not None:
        return None
    return "SHOT" if space.region_3d.view_perspective == "CAMERA" else "FREE"


# --- FREE mode maths (pure) ------------------------------------------------------

def orbit_view(rotation, dx, dy, speed=1.0):
    """Turntable orbit like Blender's: horizontal drag turns around world Z,
    vertical drag tilts around the view's own X. `rotation` is
    RegionView3D.view_rotation (view to world). Returns the new rotation."""
    a = math.radians(ORBIT_DEG_PER_PX * speed)
    flip = -1.0 if (rotation @ Vector((0.0, 1.0, 0.0))).z < 0.0 else 1.0   # upside down
    q = Quaternion((0.0, 0.0, 1.0), -dx * a * flip) @ rotation @ Quaternion((1.0, 0.0, 0.0), dy * a)
    q.normalize()
    return q


def zoom_distance(distance, dy, invert=False):
    """Drag up moves closer (down moves away); `invert` swaps them."""
    if invert:
        dy = -dy
    return max(MIN_VIEW_DISTANCE, distance * math.exp(-dy * ZOOM_PER_PX))


def view_state(rv3d):
    return (rv3d.view_rotation.copy(), rv3d.view_location.copy(), rv3d.view_distance,
            rv3d.view_perspective, rv3d.is_orthographic_side_view)


def set_view_state(rv3d, state):
    rot, loc, dist, persp, side = state
    rv3d.view_perspective = persp
    rv3d.view_rotation, rv3d.view_location, rv3d.view_distance = rot, loc, dist
    rv3d.is_orthographic_side_view = side


def pan_view(region, rv3d, a, b):
    """Move the view so the point under region pixel `a` ends up under `b`
    (at the pivot's depth): the scene follows the mouse."""
    from bpy_extras import view3d_utils
    pa = view3d_utils.region_2d_to_location_3d(region, rv3d, a, rv3d.view_location)
    pb = view3d_utils.region_2d_to_location_3d(region, rv3d, b, rv3d.view_location)
    if pa is not None and pb is not None:
        rv3d.view_location = rv3d.view_location + (pa - pb)


# --- SHOT mode (looking through a camera: moving the shot) -----------------------

ORBIT_HEIGHT = (-10.0, 89.0)
MIN_DISTANCE_FACTOR = 1.1       # never closer than 1.1 x the product's radius
WHEEL_PX = 24                   # one wheel notch zooms like a 24 px drag


def orbit_shot(azimuth, height, dx, dy, speed=1.0):
    """New (azimuth, height) in degrees for a drag of (dx, dy) px: the same
    grab-the-product feel as FREE orbit. Azimuth wraps, height clamps."""
    from .studio import wrap_degrees
    k = ORBIT_DEG_PER_PX * speed
    return wrap_degrees(azimuth - dx * k), max(ORBIT_HEIGHT[0], min(ORBIT_HEIGHT[1], height - dy * k))


def dolly(distance, dy, minimum, invert=False):
    """Shot distance after a zoom drag of dy px (up = closer), never inside `minimum`."""
    return max(minimum, zoom_distance(distance, dy, invert))


def pan_shift(shift, d_px, frame_px):
    """Camera shift after a pan of d_px: the picture follows the mouse.
    Shift is in frame widths, so `frame_px` is the camera frame's larger side."""
    return shift - d_px / max(1.0, frame_px)


def shot_camera(context):
    space = context.space_data
    if getattr(space, "use_local_camera", False) and space.camera is not None:
        return space.camera
    return context.scene.camera


def is_plinth_shot(cam):
    return cam is not None and "plinth_shot" in cam


def product_radius(scene):
    from . import studio
    col = bpy.data.collections.get(studio.COLLECTION)
    own = set(col.all_objects) if col else set()
    meshes = [o for o in scene.objects if o.type == "MESH" and o not in own and o.visible_get()]
    return studio.subject_radius(meshes) if meshes else 0.0


def save_camera(cam):
    return (cam.location.copy(), cam.rotation_mode, cam.rotation_euler.copy(),
            cam.rotation_quaternion.copy(), cam.scale.copy(), cam.data.shift_x, cam.data.shift_y)


def restore_camera(cam, saved):
    loc, mode, euler, quat, scale, sx, sy = saved
    cam.rotation_mode = mode
    cam.location, cam.rotation_euler, cam.rotation_quaternion, cam.scale = loc, euler, quat, scale
    cam.data.shift_x, cam.data.shift_y = sx, sy


class ShotMove:
    """One gesture on the shot being looked through. Plinth shots move through
    their own orbit (azimuth, Height, Distance around what they aim at: the
    Camera card's sliders), so the Track-To and Aim at stay. Other cameras
    orbit around the point they look at. Pan is camera shift; Lens never
    changes. restore() puts everything back exactly."""

    def __init__(self, context, cam, frame_px=None):
        from . import studio
        self.scene, self.cam = context.scene, cam
        self.saved = save_camera(cam)
        self.plinth = is_plinth_shot(cam)
        self.minimum = MIN_DISTANCE_FACTOR * product_radius(self.scene)
        if frame_px is None:
            from . import overlay
            frame = overlay.camera_frame_rect(context) if getattr(context, "region_data", None) else None
            frame_px = max(frame[2], frame[3]) if frame else getattr(context.region, "width", 1000)
        self.frame_px = frame_px
        if self.plinth:
            self.az = studio.shot_azimuth(self.scene, cam)
            self.height, self.dist = studio.shot_orbit(self.scene, cam)
        else:
            mw = cam.matrix_world
            self.rot = mw.to_quaternion()
            fwd = self.rot @ Vector((0.0, 0.0, -1.0))
            tgt = studio.find_target(self.scene)
            d = (tgt.location - mw.translation).dot(fwd) if tgt is not None else 0.0
            self.dist = d if d > 1e-3 else (cam.data.dof.focus_distance or 5.0)
            self.centre = mw.translation + fwd * self.dist

    def _place(self):
        from . import studio
        if self.plinth:
            studio.set_shot_orbit(self.scene, self.cam, elevation=self.height, distance=self.dist,
                                  azimuth=self.az)
            return
        from mathutils import Matrix
        pos = self.centre + self.rot @ Vector((0.0, 0.0, self.dist))
        self.cam.matrix_world = Matrix.LocRotScale(pos, self.rot, self.cam.matrix_world.to_scale())

    def orbit(self, dx, dy, speed=1.0):
        if self.plinth:
            self.az, self.height = orbit_shot(self.az, self.height, dx, dy, speed)
        else:
            self.rot = orbit_view(self.rot, dx, dy, speed)
        self._place()

    def zoom(self, dy, invert=False):
        self.dist = dolly(self.dist, dy, self.minimum if self.plinth else MIN_VIEW_DISTANCE, invert)
        self._place()

    def pan(self, dx, dy):
        data = self.cam.data
        data.shift_x = pan_shift(data.shift_x, dx, self.frame_px)
        data.shift_y = pan_shift(data.shift_y, dy, self.frame_px)

    def restore(self):
        restore_camera(self.cam, self.saved)


_undo = {"time": 0.0}


def push_undo(message, coalesce=False):
    """One undo step. Trackpad/wheel gestures arrive as many events: with
    `coalesce`, events closer than half a second share one step."""
    import time
    now = time.monotonic()
    last, _undo["time"] = _undo["time"], now
    if coalesce and now - last < 0.5:
        return
    try:
        bpy.ops.ed.undo_push(message=message)
    except RuntimeError:
        pass


# --- First-run hint and status ---------------------------------------------------

HINT = "Drag to orbit · Right-drag to zoom · Middle-drag to pan · Double-click to set the centre"
_hint = {"seen": False}         # when the add-on isn't in the preferences (dev / tests)


def show_hint(context):
    if not enabled(context):
        return False
    p = prefs(context)
    return not (getattr(p, "nav_hint_seen", False) if p is not None else _hint["seen"])


def hint_done(context):
    p = prefs(context)
    if p is not None and hasattr(p, "nav_hint_seen") and not p.nav_hint_seen:
        p.nav_hint_seen = True
        context.preferences.is_dirty = True         # saved with auto-save preferences
    _hint["seen"] = True


def status(scene):
    """The status line while looking through a camera with Plinth navigation on."""
    cam = scene.camera
    if cam is None:
        return None
    from . import studio
    label = studio.shot_label(cam) if is_plinth_shot(cam) else cam.name
    return "DRAG MOVES " + label.upper()


# --- Operators -------------------------------------------------------------------

ACTIONS = [("ORBIT", "Orbit", "Turn around the centre"),
           ("PAN", "Pan", "Slide the view (in a shot: reframe with camera shift)"),
           ("ZOOM", "Zoom", "Move closer or further")]


def apply_view(region, rv3d, action, x, y, dx, dy, speed=1.0, invert=False):
    """FREE: one step of orbit/pan/zoom on the view; (x, y) is the mouse in region px."""
    if action == "ORBIT":
        rv3d.view_rotation = orbit_view(rv3d.view_rotation, dx, dy, speed)
    elif action == "ZOOM":
        rv3d.view_distance = zoom_distance(rv3d.view_distance, dy, invert)
    else:
        pan_view(region, rv3d, (x - dx, y - dy), (x, y))


def apply_shot(shot, action, dx, dy, speed=1.0, invert=False):
    """SHOT: one step of orbit/pan/zoom on a ShotMove."""
    if action == "ORBIT":
        shot.orbit(dx, dy, speed)
    elif action == "ZOOM":
        shot.zoom(dy, invert)
    else:
        shot.pan(dx, dy)


class PLINTH_OT_nav_drag(bpy.types.Operator):
    """Plinth navigation: drag to orbit (left), pan (middle) or zoom (right).
    Looking through a shot, the drag moves the shot. A click passes through"""
    bl_idname = "plinth.nav_drag"
    bl_label = "Plinth Navigate"
    bl_options = {"INTERNAL"}

    action: EnumProperty(name="Action", items=ACTIONS, default="ORBIT")

    @classmethod
    def poll(cls, context):
        return nav_mode(context) is not None

    def invoke(self, context, event):
        if event.type == "LEFTMOUSE" and second_click(context, event):
            return pivot_at(context, event, self.report)
        self.button = event.type
        self.press = (event.mouse_x, event.mouse_y)
        self.last = self.press
        self.dragging = False
        self.rv3d = context.space_data.region_3d
        self.region = context.region
        self.mode = nav_mode(context)
        self.shot = None
        self.start = view_state(self.rv3d)
        p = prefs(context)
        self.speed = getattr(p, "orbit_speed", 1.0)
        self.invert = getattr(p, "invert_zoom", False)
        self.threshold = context.preferences.inputs.drag_threshold_mouse * context.preferences.system.ui_scale
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _begin(self, context):
        self.dragging = True
        if self.mode == "SHOT":
            cam = shot_camera(context)
            self.shot = ShotMove(context, cam) if cam is not None else None
        elif self.action == "ORBIT" and self.rv3d.is_orthographic_side_view \
                and context.preferences.inputs.use_auto_perspective:
            self.rv3d.view_perspective = "PERSP"           # like Blender: orbiting leaves a side view
            self.rv3d.is_orthographic_side_view = False

    def _move(self, context, event):
        x, y = event.mouse_x, event.mouse_y
        if not self.dragging:
            if max(abs(x - self.press[0]), abs(y - self.press[1])) < self.threshold:
                return
            self._begin(context)
        dx, dy = x - self.last[0], y - self.last[1]
        self.last = (x, y)
        if self.mode == "SHOT":
            if self.shot is not None:
                apply_shot(self.shot, self.action, dx, dy, self.speed, self.invert)
                from . import props
                props.tag_viewports(context)                # the Camera card follows live
            return
        apply_view(self.region, self.rv3d, self.action, x - self.region.x, y - self.region.y,
                   dx, dy, self.speed, self.invert)
        context.area.tag_redraw()

    def _cancel(self, context):
        if self.shot is not None:
            self.shot.restore()
        set_view_state(self.rv3d, self.start)
        from . import props
        props.tag_viewports(context)
        return {"CANCELLED"}

    def modal(self, context, event):
        if event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
            self._move(context, event)
            return {"RUNNING_MODAL"}
        if event.type == self.button and event.value == "RELEASE":
            if not self.dragging:
                if self.button == "LEFTMOUSE":
                    _clicks.update(time=time.monotonic(), xy=self.press)
                    # A click selects, as Blender's would. (Blender doesn't send the
                    # CLICK on once an operator took the press, so select here.)
                    try:
                        bpy.ops.view3d.select("INVOKE_DEFAULT", deselect_all=True)
                    except RuntimeError:
                        pass
                    if context.scene.plinth.step == "MATERIAL":
                        pick_part(context, (event.mouse_region_x, event.mouse_region_y))
                return {"FINISHED"}         # right/middle clicks do nothing
            if self.action == "ORBIT":
                hint_done(context)
            if self.shot is not None:
                push_undo("Move shot")
            return {"FINISHED"}
        if event.value == "PRESS" and (event.type == "ESC" or
                                       (event.type == "RIGHTMOUSE" and self.button != "RIGHTMOUSE")):
            return self._cancel(context)
        return {"RUNNING_MODAL"}


def gesture_delta(event_type, mouse, prev, inverted=False):
    """(dx, dy) in drag pixels for a trackpad or wheel event."""
    if event_type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
        return 0, WHEEL_PX if event_type == "WHEELUPMOUSE" else -WHEEL_PX
    dx, dy = mouse[0] - prev[0], mouse[1] - prev[1]
    if event_type == "TRACKPADZOOM":
        return 0, dx                                # Blender reads a pinch from x
    return (-dx, -dy) if inverted else (dx, dy)     # 'natural' scrolling


class PLINTH_OT_nav_gesture(bpy.types.Operator):
    """Trackpad and wheel in the studio: two-finger swipe orbits (Shift pans,
    Ctrl zooms), pinch and the wheel zoom. Looking through a shot, they move it"""
    bl_idname = "plinth.nav_gesture"
    bl_label = "Plinth Navigate (Trackpad)"
    bl_options = {"INTERNAL"}

    action: EnumProperty(name="Action", items=ACTIONS, default="ORBIT")

    @classmethod
    def poll(cls, context):
        return nav_mode(context) is not None

    def invoke(self, context, event):
        mode = nav_mode(context)
        if event.type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"} and mode != "SHOT":
            return {"PASS_THROUGH"}                 # FREE: Blender's wheel zoom (zoom to mouse etc.)
        p = prefs(context)
        speed, invert = getattr(p, "orbit_speed", 1.0), getattr(p, "invert_zoom", False)
        dx, dy = gesture_delta(event.type, (event.mouse_x, event.mouse_y),
                               (event.mouse_prev_x, event.mouse_prev_y),
                               getattr(event, "is_direction_inverted", False))   # not in every Blender
        if mode == "SHOT":
            cam = shot_camera(context)
            if cam is None:
                return {"CANCELLED"}
            apply_shot(ShotMove(context, cam), self.action, dx, dy, speed, invert)
            push_undo("Move shot", coalesce=True)
            from . import props
            props.tag_viewports(context)
        else:
            apply_view(context.region, context.space_data.region_3d, self.action,
                       event.mouse_region_x, event.mouse_region_y, dx, dy, speed, invert)
            context.area.tag_redraw()
        if self.action == "ORBIT":
            hint_done(context)
        return {"FINISHED"}


def part_hit(scene, depsgraph, origin, direction):
    """(object, slot) of the first surface the camera sees along a ray, or None."""
    from . import materials
    for _ in range(8):
        hit, loc, _n, face, obj, _m = scene.ray_cast(depsgraph, origin, direction)
        if not hit:
            return None
        if getattr(obj, "visible_camera", True):
            if obj.type != "MESH":
                return None
            if not obj.material_slots:              # no material yet (an import): its one part
                return obj, 0
            slot = materials.slot_of_face(obj, depsgraph, face)
            return (obj, slot) if slot is not None else None
        origin = loc + direction * 1e-4
    return None


def pick_part(context, xy):
    """Material step: clicking a product edits the part (material slot) under
    the mouse. Returns the slot picked, or None."""
    from bpy_extras import view3d_utils
    region, rv3d = context.region, context.space_data.region_3d
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, xy)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, xy).normalized()
    hit = part_hit(context.scene, context.evaluated_depsgraph_get(), origin, direction)
    if hit is None or hit[0] != context.active_object:
        return None
    obj, slot = hit
    obj.active_material_index = slot
    from . import props
    props.tag_viewports(context)
    return slot


def ray_hit(context, xy):
    """First surface under region pixel xy that the camera sees (gobo cards
    and other camera-invisible helpers are skipped), or None."""
    from bpy_extras import view3d_utils
    region, rv3d = context.region, context.space_data.region_3d
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, xy)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, xy).normalized()
    depsgraph = context.evaluated_depsgraph_get()
    for _ in range(8):
        hit, loc, _n, _i, obj, _m = context.scene.ray_cast(depsgraph, origin, direction)
        if not hit:
            return None
        if getattr(obj, "visible_camera", True):
            return loc
        origin = loc + direction * 1e-4
    return None


class PLINTH_OT_nav_pivot(bpy.types.Operator):
    """Double-click: set the orbit centre at the surface under the mouse. Looking
    through a shot, the shot gets its own pivot and aims at it"""
    bl_idname = "plinth.nav_pivot"
    bl_label = "Set Orbit Centre"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return nav_mode(context) is not None

    def invoke(self, context, event):
        _clicks.clear()
        return pivot_at(context, event, self.report)


_clicks = {}


def second_click(context, event):
    """True if this left press completes a double-click. Blender doesn't send
    DOUBLE_CLICK once an operator took the first press (the overlay found the
    same), so the drag operator times it: Blender's double-click time, and
    within the drag threshold of the first click."""
    last, now = _clicks.get("time"), time.monotonic()
    if last is None:
        return False
    near = context.preferences.inputs.drag_threshold_mouse * context.preferences.system.ui_scale * 2
    x, y = _clicks["xy"]
    double = (now - last) * 1000.0 < context.preferences.inputs.mouse_double_click_time         and abs(event.mouse_x - x) <= near and abs(event.mouse_y - y) <= near
    _clicks.clear()
    return double


def pivot_at(context, event, report=None):
    """Set the orbit centre at the surface under the mouse (nothing there: no change)."""
    loc = ray_hit(context, (event.mouse_region_x, event.mouse_region_y))
    if loc is None:
        return {"CANCELLED"}
    if nav_mode(context) == "FREE":
        try:
            return bpy.ops.view3d.view_center_pick("INVOKE_DEFAULT")
        except RuntimeError:
            context.space_data.region_3d.view_location = loc
            return {"FINISHED"}
    return set_pivot(context, shot_camera(context), loc, report)


def set_pivot(context, cam, loc, report=None):
    """SHOT double-click: give the shot its own pivot at `loc` and aim at it."""
    from . import studio
    if not is_plinth_shot(cam) or (cam.parent is not None and cam.parent.name == studio.PIVOT):
        if report is not None:
            report({"INFO"}, "Double-click sets the centre of Plinth's own shots")
        return {"CANCELLED"}
    studio.set_shot_pivot(context.scene, cam, loc)
    push_undo("Set shot centre")
    from . import props
    props.tag_viewports(context)
    return {"FINISHED"}


# --- Material copy/paste and isolate (0.9.1) ---------------------------------------

def part_under_mouse(context, event):
    """(object, slot) of the product part under the mouse, or None."""
    from bpy_extras import view3d_utils
    xy = (event.mouse_region_x, event.mouse_region_y)
    region, rv3d = context.region, context.space_data.region_3d
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, xy)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, xy).normalized()
    hit = part_hit(context.scene, context.evaluated_depsgraph_get(), origin, direction)
    if hit is None:
        return None
    col = bpy.data.collections.get("Plinth")
    return None if col is not None and hit[0] in set(col.all_objects) else hit


class PLINTH_OT_nav_copy_material(bpy.types.Operator):
    """M + click: copy the material of the part under the mouse"""
    bl_idname = "plinth.nav_copy_material"
    bl_label = "Copy Material"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return nav_mode(context) is not None

    def invoke(self, context, event):
        from . import clipboard
        hit = part_under_mouse(context, event)
        obj, slot = hit if hit else (context.active_object, None)
        label = clipboard.copy(obj, slot)
        if label is None:
            self.report({"INFO"}, "Nothing to copy here: M + click a part of the product")
            return {"CANCELLED"}
        self.report({"INFO"}, "Copied %s. M + right-click a part to paste it" % label)
        return {"FINISHED"}


class PLINTH_OT_nav_paste_material(bpy.types.Operator):
    """M + right-click: paste the copied material, with all its settings, onto the part under the mouse"""
    bl_idname = "plinth.nav_paste_material"
    bl_label = "Paste Material"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    @classmethod
    def poll(cls, context):
        return nav_mode(context) is not None

    def invoke(self, context, event):
        from . import clipboard, props
        if clipboard.label() is None:
            self.report({"INFO"}, "Copy a material first: M + click a part")
            return {"CANCELLED"}
        hit = part_under_mouse(context, event)
        if hit is None:
            self.report({"INFO"}, "M + right-click a part of the product to paste")
            return {"CANCELLED"}
        obj, slot = hit
        if not clipboard.paste(obj, slot):
            self.report({"WARNING"}, "The copied material is gone")
            return {"CANCELLED"}
        props.tag_viewports(context)
        self.report({"INFO"}, "Pasted %s onto %s" % (clipboard.label(), obj.name))
        return {"FINISHED"}


class PLINTH_OT_nav_hold_m(bpy.types.Operator):
    """M is held for M + click (copy/paste material) in the Plinth workspace"""
    bl_idname = "plinth.nav_hold_m"
    bl_label = "Material Copy Key"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return nav_mode(context) is not None

    def invoke(self, context, event):
        return {"FINISHED"}            # swallowed: no Move to Collection menu here


class PLINTH_OT_nav_isolate(bpy.types.Operator):
    """/: show only the selected part(s) of the product, keeping the studio and its
    lights; / again shows everything"""
    bl_idname = "plinth.nav_isolate"
    bl_label = "Isolate Product Part"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    @classmethod
    def poll(cls, context):
        return nav_mode(context) is not None

    def invoke(self, context, event):
        return self.execute(context)

    def execute(self, context):
        from . import isolate, props
        keep = list(context.selected_objects) or ([context.active_object] if context.active_object else [])
        what, n = isolate.toggle(context.scene, context.view_layer, keep)
        if what == "NOTHING":
            self.report({"INFO"}, "Select a part of the product to isolate it")
            return {"CANCELLED"}
        props.tag_viewports(context)
        self.report({"INFO"}, "Isolated: / shows everything again" if what == "ISOLATED"
                    else "Showing everything")
        return {"FINISHED"}


class PLINTH_OT_nav_hide(bpy.types.Operator):
    """H: hide the selected part(s), or the part under the mouse, to get at what's
    underneath; H again (or Show) brings them back. Shift+H hides more"""
    bl_idname = "plinth.nav_hide"
    bl_label = "Hide Product Part"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    more: BoolProperty(name="Hide more", description="Hide these too, keeping what's hidden hidden",
                       default=False)

    @classmethod
    def poll(cls, context):
        return nav_mode(context) is not None

    def invoke(self, context, event):
        objects = list(context.selected_objects)
        if not objects:
            hit = part_under_mouse(context, event) if context.region is not None else None
            objects = [hit[0]] if hit else []
        return self._run(context, objects)

    def execute(self, context):
        return self._run(context, list(context.selected_objects) or
                         ([context.active_object] if context.active_object else []))

    def _run(self, context, objects):
        from . import isolate, props
        scene, layer = context.scene, context.view_layer
        if self.more:
            n = isolate.hide(scene, layer, objects)
            what = "HIDDEN" if n else "NOTHING"
        else:
            what, n = isolate.toggle_hide(scene, layer, objects)
        if what == "NOTHING":
            self.report({"INFO"}, "Select a part (or point at one) and press H to hide it")
            return {"CANCELLED"}
        props.tag_viewports(context)
        self.report({"INFO"}, ("Hidden %d: H brings %s back" % (n, "it" if n == 1 else "them"))
                    if what == "HIDDEN" else "Showing %d again" % n)
        return {"FINISHED"}


# --- Keymap ----------------------------------------------------------------------

# "Object Mode", not "3D View": Blender runs the mode keymaps first, and Object
# Mode's right-click context menu would take the press before a "3D View" item
# saw it. Navigation is Object Mode only anyway (nav_mode).
KEYMAP = "Object Mode"
KEYS = (
    # (type, value, operator, properties, modifiers). The double-click comes
    # first: unhandled, Blender sends it on as a press to the drag below.
    # 0.9.1: M + click copies/pastes materials (M held: key_modifier), so M
    # alone no longer opens Move to Collection here; / isolates the product.
    ("LEFTMOUSE", "PRESS", "plinth.nav_copy_material", {}, {"key_modifier": "M"}),
    ("RIGHTMOUSE", "PRESS", "plinth.nav_paste_material", {}, {"key_modifier": "M"}),
    ("M", "PRESS", "plinth.nav_hold_m", {}, {}),
    ("SLASH", "PRESS", "plinth.nav_isolate", {}, {}),
    ("NUMPAD_SLASH", "PRESS", "plinth.nav_isolate", {}, {}),
    # 0.13: H hides the part to reach what's under it; H again shows it.
    ("H", "PRESS", "plinth.nav_hide", {"more": True}, {"shift": True}),
    ("H", "PRESS", "plinth.nav_hide", {}, {}),
    ("LEFTMOUSE", "DOUBLE_CLICK", "plinth.nav_pivot", {}, {}),
    ("LEFTMOUSE", "PRESS", "plinth.nav_drag", {"action": "ORBIT"}, {}),
    ("MIDDLEMOUSE", "PRESS", "plinth.nav_drag", {"action": "PAN"}, {}),
    ("RIGHTMOUSE", "PRESS", "plinth.nav_drag", {"action": "ZOOM"}, {}),
    ("TRACKPADPAN", "ANY", "plinth.nav_gesture", {"action": "ORBIT"}, {}),
    ("TRACKPADPAN", "ANY", "plinth.nav_gesture", {"action": "PAN"}, {"shift": True}),
    ("TRACKPADPAN", "ANY", "plinth.nav_gesture", {"action": "ZOOM"}, {"ctrl": True}),
    ("TRACKPADZOOM", "ANY", "plinth.nav_gesture", {"action": "ZOOM"}, {}),
    ("WHEELUPMOUSE", "PRESS", "plinth.nav_gesture", {"action": "ZOOM"}, {}),
    ("WHEELDOWNMOUSE", "PRESS", "plinth.nav_gesture", {"action": "ZOOM"}, {}),
)
_keymap_items = []


def ours(kmi):
    return kmi.idname.startswith("plinth.nav_")


def register_keymap(keyconfig=None):
    """Add Plinth's navigation items to the add-on keyconfig (never twice)."""
    kc = keyconfig or bpy.context.window_manager.keyconfigs.addon
    if kc is None:                      # some background sessions have none
        return []
    unregister_keymap(kc)
    km = kc.keymaps.new(name=KEYMAP, space_type="EMPTY")
    # Blender adds add-on items to the head of the user keymap one by one,
    # which reverses them: register backwards so KEYS' order is the one used.
    for type_, value, idname, props, mods in reversed(KEYS):
        kmi = km.keymap_items.new(idname, type_, value, **mods)
        for k, v in props.items():
            setattr(kmi.properties, k, v)
        _keymap_items.append((km, kmi))
    return [kmi for _km, kmi in _keymap_items]


def unregister_keymap(keyconfig=None):
    """Remove every Plinth navigation item, including ones a previous load of
    the add-on left behind (a reload loses the old _keymap_items list)."""
    _keymap_items.clear()
    kc = keyconfig or bpy.context.window_manager.keyconfigs.addon
    if kc is None:
        return
    km = kc.keymaps.get(KEYMAP)
    if km is None:
        return
    for kmi in [k for k in km.keymap_items if ours(k)]:
        km.keymap_items.remove(kmi)


classes = (PLINTH_OT_nav_drag, PLINTH_OT_nav_gesture, PLINTH_OT_nav_pivot, PLINTH_OT_nav_copy_material,
           PLINTH_OT_nav_paste_material, PLINTH_OT_nav_hold_m, PLINTH_OT_nav_isolate, PLINTH_OT_nav_hide)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    register_keymap()


def unregister():
    unregister_keymap()
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
