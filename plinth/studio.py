# SPDX-License-Identifier: GPL-3.0-or-later
"""Studio building blocks: procedural cove backdrop, light rig, shot cameras,
and the light-painting maths. Everything Plinth creates lives in the
"Plinth" collection so it can be found, rebuilt or removed cleanly."""

import math
import bpy
from mathutils import Vector

COLLECTION = "Plinth"
TARGET = "Plinth_Target"
BACKDROP = "Plinth_Backdrop"


# --- Helpers ------------------------------------------------------------------

def studio_collection(scene):
    col = bpy.data.collections.get(COLLECTION)
    if col is None:
        col = bpy.data.collections.new(COLLECTION)
    if col.name not in scene.collection.children:
        scene.collection.children.link(col)
    return col


def _link(obj, col):
    if obj.name not in col.objects:
        col.objects.link(obj)
    return obj


def find_target(scene):
    """The main target if it exists, else None. Read-only: safe to call while
    Blender draws or hit-tests, where writing data raises an error."""
    return bpy.data.objects.get(TARGET)


def get_target(scene):
    """The main target (the product's centre), created if missing. Writes only
    when something is missing; UI read paths use find_target() instead."""
    tgt = bpy.data.objects.get(TARGET)
    if tgt is None:
        tgt = bpy.data.objects.new(TARGET, None)
        tgt.empty_display_type = "SPHERE"
        tgt.empty_display_size = 0.05
    if not tgt.get("plinth_target"):
        tgt["plinth_target"] = True
    return _link(tgt, studio_collection(scene))


# --- Targets: points that lights and cameras aim at ------------------------------

def list_targets(scene):
    """Every Plinth target, main first."""
    col = bpy.data.collections.get(COLLECTION)
    found = [o for o in (col.objects if col else ()) if o.type == "EMPTY" and o.get("plinth_target")]
    return sorted(found, key=lambda o: (o.name != TARGET, len(o.name), o.name))


def target_label(target):
    if target.name.startswith(SHOT_PIVOT):
        cam = bpy.data.objects.get(pivot_owner(target.name))
        return (shot_label(cam) if cam is not None else target.name[len(SHOT_PIVOT):]) + " pivot"
    return "Main" if target.name == TARGET else "Target " + target.name[len(TARGET):]


# A shot's own orbit point, set by double-clicking while looking through it.
# One per shot, named after the camera object (labels can change, names don't),
# so Main (which lights may aim at) is never moved.
SHOT_PIVOT = "Plinth_Pivot_"


def pivot_name(cam):
    return SHOT_PIVOT + cam.name[len("Plinth_Shot_"):] if cam.name.startswith("Plinth_Shot_")         else SHOT_PIVOT + cam.name


def pivot_owner(name):
    return "Plinth_Shot_" + name[len(SHOT_PIVOT):]


def set_shot_pivot(scene, cam, location):
    """Create or move `cam`'s own pivot target to `location` and aim the shot
    at it. The camera stays where it is; it turns to look at the new point."""
    name = pivot_name(cam)
    tgt = bpy.data.objects.get(name)
    if tgt is None:
        tgt = bpy.data.objects.new(name, None)
        tgt.empty_display_type = "SPHERE"
        tgt.empty_display_size = 0.05
        tgt["plinth_target"] = True
    tgt.location = Vector(location)
    _link(tgt, studio_collection(scene))
    aim_at(cam, tgt)
    return tgt


def add_target(scene, location=None):
    """Spawn a new target (Plinth_Target2, 3, ...). Defaults to above the product."""
    main = get_target(scene)
    n = 2
    while bpy.data.objects.get("%s%d" % (TARGET, n)) is not None:
        n += 1
    tgt = bpy.data.objects.new("%s%d" % (TARGET, n), None)
    tgt.empty_display_type = "SPHERE"
    tgt.empty_display_size = 0.05
    tgt["plinth_target"] = True
    r = 0.5 * float(main.get("plinth_scale", 1.0))
    tgt.location = Vector(location) if location is not None else main.location + Vector((0, 0, r))
    return _link(tgt, studio_collection(scene))


def aim_target(scene, obj):
    """The target an object aims at (its track constraint), else the main target."""
    con = obj.constraints.get("Plinth_Track")
    if con is not None and con.target is not None:
        return con.target
    name = obj.get("plinth_target")
    return (bpy.data.objects.get(name) if name else None) or find_target(scene)


def aim_at(obj, target):
    """Point a light or camera at a target (and focus a camera on it)."""
    track_to(obj, target)
    obj["plinth_target"] = target.name
    if obj.type == "CAMERA":
        obj.data.dof.focus_object = target
    elif obj.type == "LIGHT":
        from . import gobos
        gobos.follow(obj)                        # its gobo card measures to the new target


def remove_target(scene, target):
    """Delete a target; whatever aimed at it aims at the main target. Main can't go."""
    if target.name == TARGET:
        return False
    main = get_target(scene)
    for obj in bpy.data.objects:
        con = obj.constraints.get("Plinth_Track")
        if con is not None and con.target == target:
            aim_at(obj, main)
        if obj.type == "CAMERA" and obj.data.dof.focus_object == target:
            obj.data.dof.focus_object = main
    bpy.data.objects.remove(target)
    return True


def target_range(scene):
    """{axis: (lo, hi)} in metres for a target's X / Y / Z sliders: around the
    product (the studio is built round the world origin) and its size."""
    tgt = find_target(scene)
    r = 0.5 * float(tgt.get("plinth_scale", 1.0)) if tgt is not None else 0.5
    return {"x": (-4.0 * r, 4.0 * r), "y": (-4.0 * r, 4.0 * r), "z": (0.0, 6.0 * r)}


def set_target_position(target, **axes):
    """Move a target to exact coordinates (x=, y=, z= in metres, any of them).
    Whatever aims at it follows (track constraints)."""
    for axis, value in axes.items():
        setattr(target.location, axis, float(value))
    return target.location.copy()


def product_centre(objects):
    """Centre of the product objects' bounding boxes (world), or None."""
    pts = [o.matrix_world @ Vector(c) for o in objects if o.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"}
           for c in o.bound_box]
    if not pts:
        return None
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return (lo + hi) / 2


def move_target_to_ray(scene, depsgraph, target, origin, direction):
    """Drag a target over surfaces: put it where the ray first hits anything."""
    hit, loc, _n, _i, _obj, _m = scene.ray_cast(depsgraph, Vector(origin),
                                                Vector(direction).normalized())
    if not hit:
        return False
    target.location = loc
    return True


def track_to(obj, target):
    """Keep `obj` pointed at `target` (the rig's default behaviour)."""
    con = obj.constraints.get("Plinth_Track") or obj.constraints.new("TRACK_TO")
    con.name = "Plinth_Track"
    con.target = target
    con.track_axis = "TRACK_NEGATIVE_Z"
    con.up_axis = "UP_Y"


def untrack(obj):
    """Drop the auto-aim so the light can be placed by hand. True if it had one."""
    con = obj.constraints.get("Plinth_Track")
    if con is None:
        return False
    obj.constraints.remove(con)
    return True


def free_aim(obj):
    """Drop the auto-aim but keep pointing where it points now (turning a light
    or shot by hand). True if it was aimed."""
    con = obj.constraints.get("Plinth_Track")
    if con is None:
        return False
    world = obj.matrix_world.copy()
    obj.constraints.remove(con)
    obj.matrix_world = world
    return True


# --- Backdrop -----------------------------------------------------------------

def cove_profile(depth, radius, height, segments):
    """(y, z) points: floor -> quarter-circle curve -> back wall.
    Straight runs are split to match the curve's spacing so shading,
    modifiers and later displacement behave evenly."""
    step = (math.pi / 2) * radius / segments
    floor_n = max(1, math.ceil(depth / step))
    pts = [(-depth + depth * i / floor_n, 0.0) for i in range(floor_n + 1)]
    for i in range(1, segments + 1):
        a = (math.pi / 2) * i / segments
        pts.append((radius * math.sin(a), radius * (1 - math.cos(a))))
    wall = height - radius
    wall_n = max(1, math.ceil(wall / step))
    pts += [(radius, radius + wall * i / wall_n) for i in range(1, wall_n + 1)]
    return pts


def build_backdrop(scene, width=6.0, depth=4.0, radius=1.2, height=4.0,
                   segments=16, color=(0.23, 0.23, 0.25, 1.0)):
    """Create or rebuild the seamless cove backdrop. Returns the object."""
    profile = cove_profile(depth, radius, height, segments)
    verts, faces = [], []
    for x in (-width / 2, width / 2):
        verts += [(x, y, z) for (y, z) in profile]
    n = len(profile)
    for i in range(n - 1):
        faces.append((i, i + 1, n + i + 1, n + i))

    mesh = bpy.data.meshes.get(BACKDROP) or bpy.data.meshes.new(BACKDROP)
    mesh.clear_geometry()
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    for poly in mesh.polygons:
        poly.use_smooth = True

    obj = bpy.data.objects.get(BACKDROP) or bpy.data.objects.new(BACKDROP, mesh)
    obj.data = mesh
    obj["plinth_cove_radius"] = radius

    mat = bpy.data.materials.get("Plinth_Backdrop_Mat") or bpy.data.materials.new("Plinth_Backdrop_Mat")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Roughness"].default_value = 0.6
    if not obj.data.materials:
        obj.data.materials.append(mat)
    mod = obj.modifiers.get(COVE_GN) or obj.modifiers.new(COVE_GN, "NODES")
    group = cove_node_group(mat)
    if mod.node_group is None or current_form(obj) == "SWEEP":
        mod.node_group = group                   # another form: build_studio re-fits it after the rig
    set_backdrop_shape(obj, width=width, depth=depth, curve=radius, height=height,
                       detail=max(32, segments * 4))
    return _link(obj, studio_collection(scene))


# The cove as Geometry Nodes, so the shape is live sliders on the modifier.
# Same profile as cove_profile(): with s the distance along the profile,
#   y = -depth + clamp(s, 0, depth) + r*sin(a),   a = clamp((s - depth)/r, 0, pi/2)
#   z = r*(1 - cos(a)) + max(s - depth - r*pi/2, 0)
# so vertices are spaced evenly by arc length.
COVE_GN = "Plinth_Cove"
HALF_PI = math.pi / 2
COVE_INPUTS = [
    # key, label, type, default, min, max
    ("width", "Width", "NodeSocketFloat", 6.0, 0.1, 1000.0),
    ("depth", "Floor depth", "NodeSocketFloat", 4.0, 0.0, 1000.0),
    ("curve", "Curve", "NodeSocketFloat", 1.2, 0.01, 1000.0),
    ("height", "Wall height", "NodeSocketFloat", 4.0, 0.0, 1000.0),
    ("detail", "Detail", "NodeSocketInt", 64, 4, 1024),
]


def cove_node_group(material=None):
    """Create or rebuild the Plinth_Cove geometry node group."""
    ng = bpy.data.node_groups.get(COVE_GN) or bpy.data.node_groups.new(COVE_GN, "GeometryNodeTree")
    ng.nodes.clear()
    ng.interface.clear()
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    for _key, label, kind, default, lo, hi in COVE_INPUTS:
        s = ng.interface.new_socket(label, in_out="INPUT", socket_type=kind)
        s.default_value, s.min_value, s.max_value = default, lo, hi
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    nodes, links = ng.nodes, ng.links
    gin, gout = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput")
    gin.location, gout.location = (-1400, 0), (600, 0)
    width, depth, r, height, detail = (gin.outputs[label] for _k, label, *_ in COVE_INPUTS)

    def op(kind, a, b=None):
        n = nodes.new("ShaderNodeMath")
        n.operation = kind
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                n.inputs[i].default_value = v
            else:
                links.new(v, n.inputs[i])
        return n.outputs[0]

    grid = nodes.new("GeometryNodeMeshGrid")
    grid.inputs["Size Y"].default_value = 1.0
    grid.inputs["Vertices X"].default_value = 2
    links.new(width, grid.inputs["Size X"])
    links.new(detail, grid.inputs["Vertices Y"])

    pos = nodes.new("ShaderNodeSeparateXYZ")
    links.new(nodes.new("GeometryNodeInputPosition").outputs[0], pos.inputs[0])

    arc = op("MULTIPLY", r, HALF_PI)
    wall = op("MAXIMUM", op("SUBTRACT", height, r), 0.0)
    length = op("ADD", op("ADD", depth, arc), wall)
    s = op("MULTIPLY", op("ADD", pos.outputs["Y"], 0.5), length)
    floor_run = op("MINIMUM", op("MAXIMUM", s, 0.0), depth)
    a = op("MINIMUM", op("MAXIMUM", op("DIVIDE", op("SUBTRACT", s, depth), r), 0.0),
             HALF_PI)
    y = op("ADD", op("ADD", op("MULTIPLY", depth, -1.0), floor_run),
             op("MULTIPLY", r, op("SINE", a)))
    z = op("ADD", op("MULTIPLY", r, op("SUBTRACT", 1.0, op("COSINE", a))),
             op("MAXIMUM", op("SUBTRACT", s, op("ADD", depth, arc)), 0.0))

    comb = nodes.new("ShaderNodeCombineXYZ")
    links.new(pos.outputs["X"], comb.inputs["X"])
    links.new(y, comb.inputs["Y"])
    links.new(z, comb.inputs["Z"])
    setpos = nodes.new("GeometryNodeSetPosition")
    links.new(grid.outputs["Mesh"], setpos.inputs["Geometry"])
    links.new(comb.outputs[0], setpos.inputs["Position"])
    smooth = nodes.new("GeometryNodeSetShadeSmooth")
    links.new(setpos.outputs[0], smooth.inputs["Geometry"])
    setmat = nodes.new("GeometryNodeSetMaterial")
    setmat.inputs["Material"].default_value = material
    links.new(smooth.outputs[0], setmat.inputs["Geometry"])
    links.new(setmat.outputs[0], gout.inputs["Geometry"])
    return ng


# --- Studio forms (0.9) ------------------------------------------------------------
# The backdrop's mesh: the classic sweep (Plinth_Cove above), or a room built
# by Plinth_Room: the same floor -> curve -> wall profile swept all the way
# round a plan with rounded or slanted (45 degree) corners, each of the four
# walls on or off, and an optional dome roof. A wall that's off leaves the
# floor running on flat. One group serves every room form; the form only sets
# its inputs, so the card's Curve and Wall height sliders work on all of them.
#
# Plan: half sizes A (x, Width/2) and B (y, Floor depth/2) to where the floor
# meets the curve, corner size k. For a column at angle t round the centre
# (t = 0 faces the back wall), the floor edge is at distance
#   straight = min(A/|sin t|, B/|cos t|)
#   rounded: where the ray meets the corner circle (centre (A-k, B-k), radius k)
#   slanted: where the ray meets the 45 degree cut x + y = A + B - k
# and the profile runs out from the centre: floor to the edge, quarter curve,
# wall, then the roof closing over the top (an ellipse back to the centre).

ROOM_GN = "Plinth_Room"
ROOM_VERSION = 2          # 2: walls on the grown plan, clean mitres
ROOM_INPUTS = COVE_INPUTS + [
    ("corner", "Corner", "NodeSocketFloat", 3.0, 0.0, 1000.0),
    ("slant", "Slanted corners", "NodeSocketFloat", 0.0, 0.0, 1.0),
    ("back", "Back wall", "NodeSocketFloat", 1.0, 0.0, 1.0),
    ("left", "Left wall", "NodeSocketFloat", 1.0, 0.0, 1.0),
    ("right", "Right wall", "NodeSocketFloat", 1.0, 0.0, 1.0),
    ("front", "Front wall", "NodeSocketFloat", 1.0, 0.0, 1.0),
    ("roof", "Roof", "NodeSocketFloat", 0.0, 0.0, 1.0),
]
FORM_KEYS = ("width", "depth", "curve", "height", "corner")     # sizes: multiples of the product radius

# key: (label, description, node group, sizes (x product radius), settings)
FORMS = {
    "SWEEP": ("Sweep", "The classic seamless sweep: a floor curving up into one back wall", COVE_GN,
              {"width": 24, "depth": 12, "curve": 3, "height": 12}, {}),
    "CORNER": ("Corner cyc", "Two walls meeting in a soft curved corner behind the product", ROOM_GN,
               {"width": 16, "depth": 16, "curve": 3, "height": 12, "corner": 4},
               {"back": 1, "right": 1, "left": 0, "front": 0, "slant": 0, "roof": 0}),
    "THREE": ("Three-wall", "Back and side walls with rounded corners: light wraps round the product",
              ROOM_GN, {"width": 16, "depth": 12, "curve": 3, "height": 12, "corner": 4},
              {"back": 1, "left": 1, "right": 1, "front": 0, "slant": 0, "roof": 0}),
    "HALFROUND": ("Half-round", "A semicircular wall behind the product: an even horizon from any angle",
                  ROOM_GN, {"width": 18, "depth": 20, "curve": 3, "height": 12, "corner": 9},
                  {"back": 1, "left": 1, "right": 1, "front": 0, "slant": 0, "roof": 0}),
    "ROOM": ("Slanted room", "A closed room whose four corners are cut at 45 degrees", ROOM_GN,
             {"width": 20, "depth": 16, "curve": 3, "height": 12, "corner": 5},
             {"back": 1, "left": 1, "right": 1, "front": 1, "slant": 1, "roof": 0}),
    "ROUND": ("Round cyc", "A circular wall all the way round: seamless in every direction", ROOM_GN,
              {"width": 20, "depth": 20, "curve": 3, "height": 12, "corner": 10},
              {"back": 1, "left": 1, "right": 1, "front": 1, "slant": 0, "roof": 0}),
    "DOME": ("Dome", "A round studio under a dome: no edges anywhere, soft reflections all round",
             ROOM_GN, {"width": 20, "depth": 20, "curve": 3, "height": 5, "corner": 10},
             {"back": 1, "left": 1, "right": 1, "front": 1, "slant": 0, "roof": 1}),
    "FLOOR": ("Endless floor", "Just a floor running out to the horizon, no walls", ROOM_GN,
              {"width": 160, "depth": 160, "curve": 3, "height": 3, "corner": 80},
              {"back": 0, "left": 0, "right": 0, "front": 0, "slant": 0, "roof": 0}),
}


def form_defaults(key, radius):
    """A form's sizes and settings for a product of this radius."""
    _label, _d, _group, sizes, settings = FORMS[key]
    return {**{k: v * radius for k, v in sizes.items()}, **settings}


def current_form(obj):
    """The backdrop's form key (read-only)."""
    key = obj.get("plinth_form", "SWEEP") if obj is not None else "SWEEP"
    return key if key in FORMS else "SWEEP"


def room_node_group(material=None):
    """Plinth_Room, built once per ROOM_VERSION (never cleared while it's current)."""
    ng = bpy.data.node_groups.get(ROOM_GN)
    if ng is not None and ng.get("plinth_version") == ROOM_VERSION:
        return ng
    ng = ng or bpy.data.node_groups.new(ROOM_GN, "GeometryNodeTree")
    ng.nodes.clear()
    ng.interface.clear()
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    for _key, label, kind, default, lo, hi in ROOM_INPUTS:
        s = ng.interface.new_socket(label, in_out="INPUT", socket_type=kind)
        s.default_value, s.min_value, s.max_value = default, lo, hi
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nodes, links = ng.nodes, ng.links
    gin, gout = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput")
    gin.location, gout.location = (-2400, 0), (800, 0)
    i = {key: gin.outputs[label] for key, label, *_ in ROOM_INPUTS}

    def op(kind, a, b=None, c=None):
        n = nodes.new("ShaderNodeMath")
        n.operation = kind
        for k, v in enumerate((a, b, c)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                n.inputs[k].default_value = v
            else:
                links.new(v, n.inputs[k])
        return n.outputs[0]

    def mix(f, a, b):                                   # a + f * (b - a)
        return op("ADD", a, op("MULTIPLY", f, op("SUBTRACT", b, a)))

    cols = op("MAXIMUM", op("MULTIPLY", i["detail"], 6.0), 64.0)
    grid = nodes.new("GeometryNodeMeshGrid")
    grid.inputs["Size X"].default_value = 1.0
    grid.inputs["Size Y"].default_value = 1.0
    links.new(op("ADD", cols, 1.0), grid.inputs["Vertices X"])
    links.new(i["detail"], grid.inputs["Vertices Y"])
    pos = nodes.new("ShaderNodeSeparateXYZ")
    links.new(nodes.new("GeometryNodeInputPosition").outputs[0], pos.inputs[0])
    theta = op("ADD", op("MULTIPLY", op("ADD", pos.outputs["X"], 0.5), 2 * math.pi), math.pi)  # seam at the front
    sx, cy = op("SINE", theta), op("COSINE", theta)
    ax, ay = op("MAXIMUM", op("ABSOLUTE", sx), 1e-5), op("MAXIMUM", op("ABSOLUTE", cy), 1e-5)
    A = op("MAXIMUM", op("MULTIPLY", i["width"], 0.5), 1e-3)
    B = op("MAXIMUM", op("MULTIPLY", i["depth"], 0.5), 1e-3)
    k = op("MINIMUM", op("MINIMUM", op("MAXIMUM", i["corner"], 0.0), A), B)
    straight = op("MINIMUM", op("DIVIDE", A, ax), op("DIVIDE", B, ay))
    ccx, ccy = op("SUBTRACT", A, k), op("SUBTRACT", B, k)               # corner circle centre
    dc = op("ADD", op("MULTIPLY", ax, ccx), op("MULTIPLY", ay, ccy))
    disc = op("ADD", op("SUBTRACT", op("MULTIPLY", dc, dc),
                        op("ADD", op("MULTIPLY", ccx, ccx), op("MULTIPLY", ccy, ccy))), op("MULTIPLY", k, k))
    circle = op("ADD", dc, op("SQRT", op("MAXIMUM", disc, 0.0)))
    in_corner = op("MULTIPLY", op("GREATER_THAN", op("MULTIPLY", straight, ax), ccx),
                   op("GREATER_THAN", op("MULTIPLY", straight, ay), ccy))
    rounded = mix(in_corner, straight, circle)
    cut = op("DIVIDE", op("SUBTRACT", op("ADD", A, B), k), op("ADD", ax, ay))
    slanted = op("MINIMUM", straight, cut)
    edge = mix(i["slant"], rounded, slanted)                           # where the floor meets the curve
    r = op("MAXIMUM", i["curve"], 1e-4)
    A2, B2 = op("ADD", A, r), op("ADD", B, r)                           # where the curve meets the wall
    straight2 = op("MINIMUM", op("DIVIDE", A2, ax), op("DIVIDE", B2, ay))
    k2 = op("ADD", k, r)
    dc2 = op("ADD", op("MULTIPLY", ax, ccx), op("MULTIPLY", ay, ccy))
    disc2 = op("ADD", op("SUBTRACT", op("MULTIPLY", dc2, dc2),
                         op("ADD", op("MULTIPLY", ccx, ccx), op("MULTIPLY", ccy, ccy))), op("MULTIPLY", k2, k2))
    circle2 = op("ADD", dc2, op("SQRT", op("MAXIMUM", disc2, 0.0)))
    in_corner2 = op("MULTIPLY", op("GREATER_THAN", op("MULTIPLY", straight2, ax), ccx),
                    op("GREATER_THAN", op("MULTIPLY", straight2, ay), ccy))
    cut2 = op("DIVIDE", op("ADD", op("SUBTRACT", op("ADD", A, B), k), op("MULTIPLY", r, math.sqrt(2.0))),
              op("ADD", ax, ay))
    edge_out = mix(i["slant"], mix(in_corner2, straight2, circle2), op("MINIMUM", straight2, cut2))
    # Which wall this column is: a straight side has its own; a corner has a
    # wall only when both its sides do (an open side ends the wall there).
    px, py = op("MULTIPLY", edge, sx), op("MULTIPLY", edge, cy)
    eps = op("ADD", op("MULTIPLY", k, 0.001), 1e-5)
    in_x = op("GREATER_THAN", op("ABSOLUTE", px), op("SUBTRACT", ccx, eps))   # past the corner start in x
    in_y = op("GREATER_THAN", op("ABSOLUTE", py), op("SUBTRACT", ccy, eps))
    flag_y = mix(op("GREATER_THAN", py, 0.0), i["front"], i["back"])
    flag_x = mix(op("GREATER_THAN", px, 0.0), i["left"], i["right"])
    wall_on = op("ADD", op("ADD", op("MULTIPLY", op("MULTIPLY", in_y, op("SUBTRACT", 1.0, in_x)), flag_y),
                               op("MULTIPLY", op("MULTIPLY", in_x, op("SUBTRACT", 1.0, in_y)), flag_x)),
                 op("MULTIPLY", op("MULTIPLY", in_x, in_y), op("MULTIPLY", flag_x, flag_y)))
    # The profile, by distance s along it.
    arc = op("MULTIPLY", r, HALF_PI)
    wall = op("MAXIMUM", op("SUBTRACT", i["height"], r), 0.0)
    rho_wall = edge_out
    dome_h = op("MULTIPLY", i["roof"], op("ADD", op("MAXIMUM", A, B), r))
    roof_len = op("MULTIPLY", op("GREATER_THAN", i["roof"], 0.0),
                  op("MULTIPLY", HALF_PI, op("SQRT", op("MULTIPLY", 0.5, op(
                      "ADD", op("MULTIPLY", rho_wall, rho_wall), op("MULTIPLY", dome_h, dome_h))))))
    length = op("ADD", op("ADD", edge, arc), op("ADD", wall, roof_len))
    s = op("MULTIPLY", op("ADD", pos.outputs["Y"], 0.5), length)
    a = op("MINIMUM", op("MAXIMUM", op("DIVIDE", op("SUBTRACT", s, edge), r), 0.0), HALF_PI)
    w = op("MINIMUM", op("MAXIMUM", op("SUBTRACT", s, op("ADD", edge, arc)), 0.0), wall)
    past = op("SUBTRACT", s, op("ADD", op("ADD", edge, arc), wall))
    b = op("MULTIPLY", op("MINIMUM", op("MAXIMUM", op("DIVIDE", past, op("MAXIMUM", roof_len, 1e-4)), 0.0),
                          1.0), HALF_PI)
    # The wall stands on the same plan grown by the curve's radius (its
    # corners concentric, or the 45 degree cut moved out square to itself),
    # so straight and slanted walls are flat panels meeting in clean mitres;
    # the curve runs between the two plans, the roof draws the wall's top in.
    run = op("ADD", op("MINIMUM", s, edge), op("MULTIPLY", op("SINE", a), op("SUBTRACT", edge_out, edge)))
    shrink = op("COSINE", b)
    wx = op("MULTIPLY", op("MULTIPLY", run, sx), shrink)
    wy = op("MULTIPLY", op("MULTIPLY", run, cy), shrink)
    z = op("ADD", op("ADD", op("MULTIPLY", r, op("SUBTRACT", 1.0, op("COSINE", a))), w),
           op("MULTIPLY", dome_h, op("SINE", b)))
    px_ = mix(wall_on, op("MULTIPLY", s, sx), wx)                     # no wall: the floor runs on
    py_ = mix(wall_on, op("MULTIPLY", s, cy), wy)
    z = op("MULTIPLY", z, wall_on)
    comb = nodes.new("ShaderNodeCombineXYZ")
    links.new(px_, comb.inputs["X"])
    links.new(py_, comb.inputs["Y"])
    links.new(z, comb.inputs["Z"])
    setpos = nodes.new("GeometryNodeSetPosition")
    links.new(grid.outputs["Mesh"], setpos.inputs["Geometry"])
    links.new(comb.outputs[0], setpos.inputs["Position"])
    merge = nodes.new("GeometryNodeMergeByDistance")                   # close the seam and the centre
    merge.inputs["Distance"].default_value = 1e-5
    links.new(setpos.outputs[0], merge.inputs["Geometry"])
    smooth = nodes.new("GeometryNodeSetShadeSmooth")
    links.new(merge.outputs[0], smooth.inputs["Geometry"])
    last = smooth.outputs[0]
    if hasattr(smooth, "domain"):                    # 4.1+: slanted corners stay crisp
        sharp = nodes.new("GeometryNodeSetShadeSmooth")
        sharp.domain = "EDGE"
        sharp.inputs["Shade Smooth"].default_value = False
        angle = nodes.new("GeometryNodeInputMeshEdgeAngle")
        links.new(op("GREATER_THAN", angle.outputs["Unsigned Angle"], math.radians(20)),
                  sharp.inputs["Selection"])
        links.new(last, sharp.inputs["Geometry"])
        last = sharp.outputs[0]
    setmat = nodes.new("GeometryNodeSetMaterial")
    setmat.inputs["Material"].default_value = material
    links.new(last, setmat.inputs["Geometry"])
    links.new(setmat.outputs[0], gout.inputs["Geometry"])
    ng["plinth_version"] = ROOM_VERSION
    return ng


def set_form(scene, key, sizes=None):
    """Make the backdrop one of FORMS, sized for the product (``sizes``:
    multiples of the form's own sizes). False without a backdrop."""
    obj = bpy.data.objects.get(BACKDROP)
    mod = obj.modifiers.get(COVE_GN) if obj is not None else None
    if mod is None or key not in FORMS:
        return False
    mat = obj.active_material
    group = room_node_group(mat) if FORMS[key][2] == ROOM_GN else (
        bpy.data.node_groups.get(COVE_GN) or cove_node_group(mat))
    if mod.node_group != group:
        mod.node_group = group
    tgt = find_target(scene)
    radius = 0.5 * float(tgt.get("plinth_scale", 1.0)) if tgt is not None else 0.5
    values = form_defaults(key, radius)
    for k, m in (sizes or {}).items():
        if k in values and k in FORM_KEYS:
            values[k] *= m
    values["detail"] = 64
    obj["plinth_form"] = key
    set_backdrop_shape(obj, **values)
    return True


def backdrop_inputs(obj):
    """[(key, label, identifier)] for the backdrop's sliders (its form's group)."""
    mod = obj.modifiers.get(COVE_GN) if obj else None
    if mod is None or mod.node_group is None:
        return []
    by_name = {i.name: i.identifier for i in mod.node_group.interface.items_tree
               if getattr(i, "in_out", None) == "INPUT" and i.socket_type != "NodeSocketGeometry"}
    return [(k, label, by_name[label]) for k, label, *_ in ROOM_INPUTS if label in by_name]


def modifier_input(mod, ident):
    """(data, property) for a Geometry Nodes modifier input, for get/set/UI.

    Blender 5.x: mod.properties.inputs.<ident>.value. 4.x: mod["<ident>"]."""
    inputs = getattr(getattr(mod, "properties", None), "inputs", None)
    if inputs is not None and hasattr(inputs, ident):
        return getattr(inputs, ident), "value"
    return mod, '["%s"]' % ident


def _get_input(mod, ident):
    data, prop = modifier_input(mod, ident)
    return data.value if prop == "value" else mod[ident]


def _set_input(mod, ident, value):
    data, prop = modifier_input(mod, ident)
    if prop == "value":
        data.value = value
    else:
        mod[ident] = value


def set_backdrop_shape(obj, **values):
    """Set cove sliders by key (width, depth, curve, height, detail)."""
    mod = obj.modifiers[COVE_GN]
    for key, _label, ident in backdrop_inputs(obj):
        if key in values:
            _set_input(mod, ident, int(values[key]) if key == "detail" else float(values[key]))
        if key == "curve":
            obj["plinth_cove_radius"] = _get_input(mod, ident)
    _sync_fade(obj)
    obj.update_tag()


def set_shadow_catcher(obj, enabled):
    """Cycles-only feature; harmless elsewhere."""
    if hasattr(obj, "is_shadow_catcher"):
        obj.is_shadow_catcher = enabled


# --- Light rig ----------------------------------------------------------------

LIGHT_RIG = {
    # name: (shape, size, size_y, power W, offset from target, warmth K)
    "Key":  ("SQUARE",    1.2, 1.2, 450.0, Vector((-2.0, -2.2, 2.2)), 5000),
    "Rim":  ("RECTANGLE", 0.3, 1.6, 300.0, Vector((2.2, 1.6, 1.8)),   7500),
    "Fill": ("SQUARE",    1.6, 1.6, 120.0, Vector((-2.6, 1.0, 0.8)),  6500),
}


# Light types (0.13): Blender's own lights, each with what it can do. Area is
# a glowing panel in a shape; Spot a cone with a soft edge; Point a bare bulb;
# Sun parallel light from far away. Switching type keeps how bright and how
# soft the light looks: see light_power and light_size.
LIGHT_TYPES = {
    "AREA": {"label": "Area", "description": "A glowing panel: soft, even light in the shape you pick"},
    "SPOT": {"label": "Spot", "description": "A cone of light with a soft edge: pools and accents"},
    "POINT": {"label": "Point", "description": "A bare bulb: light in every direction"},
    "SUN": {"label": "Sun", "description": "Parallel light from far away: crisp shadows, even across the set"},
}

# Light shapes: one key per look a light can have (looks and packs store it).
# Area shapes set what the panel looks like in reflections; Spot, Point and Sun
# are their own types. Softness (size) is kept when switching; the shape sets
# the rest. "RING" is the disc (its key from before 0.13).
LIGHT_SHAPES = {
    "SOFTBOX": {"label": "Softbox", "type": "AREA", "description": "Big and even: soft shadows, broad highlights",
                "settings": {"shape": "SQUARE"}, "aspect": 1.0},
    "STRIP": {"label": "Strip", "type": "AREA", "description": "Long and thin: crisp edge highlights",
              "settings": {"shape": "RECTANGLE"}, "aspect": 5.0},
    "RING": {"label": "Disc", "type": "AREA", "description": "Round catchlight, like a ring light or beauty dish",
             "settings": {"shape": "DISK"}, "aspect": 1.0},
    "OVAL": {"label": "Oval", "type": "AREA", "description": "A long, rounded catchlight",
             "settings": {"shape": "ELLIPSE"}, "aspect": 2.0},
    "SPOT": {"label": "Spot", "type": "SPOT", "description": LIGHT_TYPES["SPOT"]["description"],
             "settings": {}, "aspect": 1.0},
    "POINT": {"label": "Point", "type": "POINT", "description": LIGHT_TYPES["POINT"]["description"],
              "settings": {}, "aspect": 1.0},
    "SUN": {"label": "Sun", "type": "SUN", "description": LIGHT_TYPES["SUN"]["description"],
            "settings": {}, "aspect": 1.0},
}
AREA_SHAPES = tuple(k for k, v in LIGHT_SHAPES.items() if v["type"] == "AREA")
SPOT_DEFAULTS = {"spot_size": math.radians(50.0), "spot_blend": 0.3}
SPOT_RANGE = (math.radians(5.0), math.radians(160.0))       # Beam
SUN_ANGLE_MAX = math.radians(30.0)

# Brightness across types, in "area watts": what an area light (spread 180)
# needs for the same light on its axis. Point and spot lights spread their
# watts over the whole sphere, an area light over a hemisphere with cosine
# falloff: 4x. A sun's strength is irradiance (W/m2): what an area light gives
# at the sun's distance, kept from when it became a sun (SUN_DISTANCE).
SPHERE_FACTOR = 4.0
SUN_DISTANCE = "plinth_sun_distance"


def light_object(light_data):
    return next((o for o in bpy.data.objects if o.data == light_data), None)


def fresh(light_data):
    """The datablock again after a type change (an old reference keeps its old type)."""
    return bpy.data.lights.get(light_data.name, light_data)


def _sun_distance(data):
    d = data.get(SUN_DISTANCE)
    if d:
        return float(d)
    main = bpy.data.objects.get(TARGET)
    return 3.0 * float(main.get("plinth_scale", 1.0)) if main is not None else 3.0


def _distance_to_target(light_data):
    obj = light_object(light_data)
    scene = bpy.context.scene
    tgt = aim_target(scene, obj) if obj is not None and scene is not None else None
    if obj is None or tgt is None:
        return _sun_distance(light_data)
    return max(0.05, (obj.location - tgt.location).length)   # unparented; matrix_world may be stale


def power_factor(light_data):
    """The light's own unit (W, or a sun's W/m2) per area watt."""
    t = light_data.type
    if t == "SUN":
        d = _sun_distance(light_data)
        return 1.0 / (math.pi * d * d)
    return SPHERE_FACTOR if t in {"SPOT", "POINT"} else 1.0


def light_power(light_data):
    """Brightness in area watts (the same number whatever the type)."""
    return light_data.energy / power_factor(light_data)


def set_light_power(light_data, watts):
    light_data.energy = watts * power_factor(light_data)


def power_label(light_data):
    """The light's own number: watts, or a sun's strength."""
    if light_data.type == "SUN":
        return "%.2f W/m²" % light_data.energy if light_data.energy < 10 else "%.1f W/m²" % light_data.energy
    return "%d W" % round(light_data.energy)


def light_size(light_data):
    """How soft the light is, as an area light's width in metres (every type):
    a bulb's diameter, or the width the sun's disc has at its distance."""
    data = fresh(light_data)
    t = data.type
    if t == "AREA":
        return data.size
    if t == "SUN":
        return 2.0 * _sun_distance(data) * math.tan(data.angle / 2.0)
    return 2.0 * data.shadow_soft_size


def set_light_size(light_data, size):
    """Set softness as light_size measures it; an area light keeps its aspect."""
    data = fresh(light_data)
    size = max(0.0, size)
    t = data.type
    if t == "AREA":
        aspect = data.size_y / data.size if data.size else 1.0
        data.size = size
        data.size_y = size * aspect
    elif t == "SUN":
        data.angle = min(SUN_ANGLE_MAX, 2.0 * math.atan(size / 2.0 / _sun_distance(data)))
    else:
        data.shadow_soft_size = size / 2.0


def set_light_type(light_data, light_type):
    """Turn a light into another Blender light type, keeping its brightness and
    softness. Returns the datablock (re-fetched: its Python type changed)."""
    data = fresh(light_data)
    if data.type == light_type:
        return data
    watts, size = light_power(data), light_size(data)
    if light_type == "SUN":
        data[SUN_DISTANCE] = _distance_to_target(data)
    data.type = light_type
    data = fresh(data)
    if light_type == "SPOT" and not data.get("plinth_spot"):
        for attr, value in SPOT_DEFAULTS.items():
            setattr(data, attr, value)
        data["plinth_spot"] = True
    tune_light(data)
    set_light_power(data, watts)
    set_light_size(data, size)
    return data


def set_light_shape(light_data, key):
    """Apply a LIGHT_SHAPES key: the light type, and for an area light its
    shape and aspect. Returns skipped settings."""
    preset = LIGHT_SHAPES[key]
    data = set_light_type(light_data, preset["type"])
    skipped = []
    for attr, value in preset["settings"].items():
        if hasattr(data, attr):
            setattr(data, attr, value)
        else:
            skipped.append(attr)
    if preset["type"] == "AREA":
        data.size_y = data.size * preset["aspect"]
        data["plinth_area_shape"] = key
    data["plinth_shape"] = key
    return skipped


def light_shape(light_data):
    """Current LIGHT_SHAPES key, guessed from the light if Plinth never set it."""
    data = fresh(light_data)
    if data.type != "AREA":
        return data.type
    key = data.get("plinth_shape")
    if key in AREA_SHAPES:
        return key
    shape = getattr(data, "shape", "")
    if shape == "RECTANGLE":
        return "STRIP"
    if shape == "ELLIPSE":
        return "OVAL"
    if shape == "DISK":
        return "RING"                    # also an area "Spot" from before 0.13
    return "SOFTBOX"


def area_shape(light_data):
    """The shape an area light has (or had, before it became a spot or sun)."""
    data = fresh(light_data)
    if data.type == "AREA":
        return light_shape(data)
    key = data.get("plinth_area_shape")
    return key if key in AREA_SHAPES else "SOFTBOX"


def kelvin_to_rgb(kelvin):
    """Colour of a black body at `kelvin`, brightest channel = 1.
    (Tanner Helland's fit; good to a few percent from 1000 K to 40000 K.)"""
    t = max(1000.0, min(40000.0, kelvin)) / 100.0
    r = 255.0 if t <= 66 else 329.698727446 * (t - 60) ** -0.1332047592
    g = (99.4708025861 * math.log(t) - 161.1195681661 if t <= 66
         else 288.1221695283 * (t - 60) ** -0.0755148492)
    b = 255.0 if t >= 66 else (0.0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307)
    rgb = [max(0.0, min(255.0, c)) / 255.0 for c in (r, g, b)]
    peak = max(rgb) or 1.0
    return tuple(c / peak for c in rgb)


def rgb_to_kelvin(rgb, lo=1500, hi=12000, step=50):
    """Nearest colour temperature to `rgb` (inverse of kelvin_to_rgb)."""
    peak = max(rgb[:3]) or 1.0
    target = [c / peak for c in rgb[:3]]
    return min(range(lo, hi + 1, step),
               key=lambda k: sum((a - b) ** 2 for a, b in zip(kelvin_to_rgb(k), target)))


def light_label(light):
    return light.name[len("Plinth_"):] if light.name.startswith("Plinth_") else light.name


def list_lights(scene):
    """Every Plinth light: Key, Rim, Fill first, then added lights by name."""
    col = bpy.data.collections.get(COLLECTION)
    found = [o for o in (col.objects if col else ()) if o.type == "LIGHT" and o.get("plinth_light")]
    order = {k: i for i, k in enumerate(LIGHT_RIG)}
    return sorted(found, key=lambda o: (order.get(light_label(o), 99), len(o.name), o.name))


def active_light(scene):
    """The light the Light step is editing (Scene.plinth.active_light). When
    that light is gone (deleted in the outliner, X in the viewport), the first
    light left stands in, so the Light step never thinks the studio is gone.
    None only when there are no Plinth lights at all. Read-only."""
    from . import hdri
    if scene.plinth.active_light == hdri.CHIP:   # the HDRI is being edited, not a light
        return None
    obj = bpy.data.objects.get("Plinth_" + scene.plinth.active_light)
    if obj is not None and obj.type == "LIGHT":
        return obj
    rest = list_lights(scene)
    return rest[0] if rest else None


def restore_lights(scene, look=None):
    """Bring the studio's lights back after they were all removed: Key, Rim and
    Fill at the rig's places for this product's size, with the look's balance
    (and its extra lights). Returns the lights."""
    from . import gobos, library
    tgt = find_target(scene)
    scale = float(tgt.get("plinth_scale", 1.0)) if tgt is not None else 1.0
    lights = build_light_rig(scene, scale=scale)
    preset = library.all_presets().get(look) if look else None
    if preset:
        library.apply_lights(scene, preset)
    gobos.relink()
    return lights


NEW_LIGHT = {"power": 300.0, "size": 0.8}


def light_base(light):
    """(power W, size m) at rig scale 1: what the sliders scale from."""
    base = light.data.get("plinth_base")
    if base:
        return tuple(base)
    rig = LIGHT_RIG.get(light_label(light))
    return (rig[3], rig[1]) if rig else (NEW_LIGHT["power"], NEW_LIGHT["size"])


def _free_light_label():
    n = len(LIGHT_RIG) + 1
    while bpy.data.objects.get("Plinth_Light%d" % n) is not None:
        n += 1
    return "Light%d" % n


def add_light(scene, shape="SOFTBOX", target=None):
    """A new area light in front of and above the product, aimed at `target`."""
    tgt = target or get_target(scene)
    scale = float(get_target(scene).get("plinth_scale", 1.0))
    label = _free_light_label()
    data = bpy.data.lights.new("Plinth_" + label, "AREA")
    data.energy = NEW_LIGHT["power"] * scale * scale
    data.size = NEW_LIGHT["size"] * scale
    data.color = kelvin_to_rgb(5600)
    tune_light(data)
    data["plinth_base"] = (NEW_LIGHT["power"], NEW_LIGHT["size"])
    obj = bpy.data.objects.new("Plinth_" + label, data)
    obj["plinth_light"] = True
    n = len(list_lights(scene))
    angle = math.radians(40 * n)
    obj.location = tgt.location + Vector((math.sin(angle) * 2.5, -math.cos(angle) * 2.5, 2.0)) * scale
    _link(obj, studio_collection(scene))
    aim_at(obj, tgt)
    set_light_shape(data, shape)               # after aiming: a sun keeps its distance to the target
    from . import gobos
    gobos.relink()                               # other lights' gobo cards don't shade it
    return obj


def duplicate_light(scene, light):
    """A copy of a light (own light data), nudged sideways, same target."""
    label = _free_light_label()
    data = light.data.copy()
    data.name = "Plinth_" + label
    obj = light.copy()
    obj.data = data
    obj.name = "Plinth_" + label
    obj["plinth_light"] = True
    if not data.get("plinth_base"):
        data["plinth_base"] = light_base(light)
    obj.location = light.location + Vector((0.4, 0, 0)) * float(get_target(scene).get("plinth_scale", 1.0))
    _link(obj, studio_collection(scene))
    if hasattr(obj, "light_linking"):
        obj.light_linking.blocker_collection = None   # its own, set by relink
    aim_at(obj, aim_target(scene, light) or get_target(scene))
    from . import gobos
    gobos.sync(obj)                              # same pattern, its own card
    gobos.relink()
    return obj


def remove_light(scene, light):
    from . import gobos
    data = light.data
    gobos.remove_card(light)
    ll = getattr(light, "light_linking", None)
    blockers = ll.blocker_collection if ll is not None else None
    bpy.data.objects.remove(light)
    if blockers is not None and blockers.name.startswith(gobos.BLOCKERS):
        bpy.data.collections.remove(blockers)
    gobos.relink()
    if data is not None and data.users == 0:
        bpy.data.lights.remove(data)


# Eevee shadow quality on every Plinth light (0.9). Jittered shadows are
# Eevee's physically based soft shadows: its default shadow-map tracing
# over-darkened a big area light grazing the cove (the Car look's rim lit the
# floor at a third of Cycles' brightness; jittered: within 15%). They converge
# over the render's samples, and in the viewport when the preset asks for it
# (eevee.use_shadow_jitter_viewport). Cycles ignores these settings.
LIGHT_QUALITY = {"use_shadow_jitter": True, "shadow_jitter_overblur": 10.0}


def tune_light(data):
    """Give a light Plinth's shadow quality; skips what this Blender lacks."""
    for attr, value in LIGHT_QUALITY.items():
        if hasattr(data, attr) and getattr(data, attr) != value:
            setattr(data, attr, value)
    return data


def upgrade_lights(scene=None):
    """Older files: bring every Plinth light up to LIGHT_QUALITY. Returns how many changed."""
    changed = 0
    for obj in (scene.objects if scene is not None else bpy.data.objects):
        if obj.type == "LIGHT" and obj.get("plinth_light"):
            before = {a: getattr(obj.data, a, None) for a in LIGHT_QUALITY}
            tune_light(obj.data)
            changed += before != {a: getattr(obj.data, a, None) for a in LIGHT_QUALITY}
    return changed


def build_light_rig(scene, scale=1.0):
    target = get_target(scene)
    target["plinth_scale"] = scale            # presets are stored relative to this
    col = studio_collection(scene)
    lights = {}
    for name, (shape, size, size_y, power, offset, kelvin) in LIGHT_RIG.items():
        full = "Plinth_" + name
        data = bpy.data.lights.get(full) or bpy.data.lights.new(full, "AREA")
        if data.type != "AREA":                  # a spot or sun from before: the rig is area lights
            data.type = "AREA"
            data = fresh(data)
        data.shape = shape
        data.size = size * scale
        data.size_y = size_y * scale
        data.energy = power * scale * scale
        data.color = kelvin_to_rgb(kelvin)
        tune_light(data)
        data.pop("plinth_shape", None)
        obj = bpy.data.objects.get(full) or bpy.data.objects.new(full, data)
        obj.location = target.location + offset * scale
        obj["plinth_light"] = True
        aim_at(obj, target)
        lights[name] = _link(obj, col)
    return lights


def aim_light_for_highlight(light, point, normal, view_origin, distance=None):
    """Light painting: place `light` so its specular highlight, seen from
    `view_origin`, lands at `point` on a surface with `normal`.

    Mirror reflection: the light must sit along the view ray reflected about
    the surface normal."""
    point, normal, view_origin = Vector(point), Vector(normal).normalized(), Vector(view_origin)
    incoming = (point - view_origin).normalized()
    reflected = incoming - 2 * incoming.dot(normal) * normal
    if distance is None:
        distance = (light.location - point).length or 3.0
    light.location = point + reflected * distance
    # Point the light back at the spot.
    direction = point - light.location
    light.rotation_mode = "QUATERNION"
    light.rotation_quaternion = direction.to_track_quat("-Z", "Y")
    return light.location.copy()


# --- Light painting interaction ---------------------------------------------

def cast_to_product(scene, depsgraph, origin, direction, max_hops=16):
    """First surface along the ray that isn't Plinth's own (the backdrop).

    Returns (location, normal, object) or None. Clicks on the backdrop pass
    straight through: you paint highlights on the product, not the cove."""
    col = bpy.data.collections.get(COLLECTION)
    own = {o.name for o in col.all_objects} if col else set()
    origin, direction = Vector(origin), Vector(direction).normalized()
    for _ in range(max_hops):
        hit, loc, normal, _i, obj, _m = scene.ray_cast(depsgraph, origin, direction)
        if not hit:
            return None
        obj = obj.original
        if obj.name not in own:
            if normal.dot(direction) > 0:          # back face: flip to face the viewer
                normal = -normal
            return loc, normal, obj
        origin = loc + direction * 1e-4
    return None


def light_on_view_ray(light, target, origin, direction):
    """Shift-drag: put the light under the mouse, keeping its distance from
    the target and pointing at it. Uses the first point of that sphere in
    front of the viewer (the far side when the viewer is inside it)."""
    target, origin = Vector(target), Vector(origin)
    d = Vector(direction).normalized()
    r = (light.location - target).length or 3.0
    oc = origin - target
    b = oc.dot(d)
    disc = b * b - (oc.dot(oc) - r * r)
    roots = [t for t in ((-b - math.sqrt(disc), -b + math.sqrt(disc)) if disc >= 0 else ())
             if t > 0]
    if roots:
        p = origin + d * roots[0]
    else:                                     # ray misses the sphere: nearest point
        p = origin + d * max(-b, 0.0)
        off = p - target
        p = target + (off.normalized() if off.length else Vector((0, 0, 1))) * r
    light.location = p
    light.rotation_mode = "QUATERNION"
    light.rotation_quaternion = (target - p).to_track_quat("-Z", "Y")
    return p.copy()


def paint_light(scene, depsgraph, light, origin, direction, move=False):
    """One light-painting step from a view ray (origin, direction).

    move=False: put the highlight where the ray meets the product.
    move=True:  move the light itself onto the ray (Shift).
    Returns True if the light moved."""
    if move:
        light_on_view_ray(light, get_target(scene).location, origin, direction)
        return True
    found = cast_to_product(scene, depsgraph, origin, direction)
    if found is None:
        return False
    loc, normal, _obj = found
    untrack(light)
    aim_light_for_highlight(light, loc, normal, origin)
    return True


# --- Shots --------------------------------------------------------------------

SHOT_PRESETS = {
    # key: (label, lens mm, azimuth deg, elevation deg, aspect (w, h), frame fill)
    # frame fill > 1 crops into the product (detail shots).
    "HERO":   ("Hero", 85.0, -25.0, 8.0, (4, 5), 0.7),
    "THREEQ": ("Three-quarter", 50.0, -45.0, 20.0, (4, 5), 0.7),
    "TOP":    ("Top", 35.0, 0.0, 89.0, (1, 1), 0.6),
    "MACRO":  ("Macro", 100.0, -15.0, 5.0, (4, 5), 1.8),
}


def subject_radius(objects):
    """Bounding-sphere radius of the product objects (fallback 0.5 m)."""
    pts = [o.matrix_world @ Vector(c) for o in objects if o.type == "MESH"
           for c in o.bound_box]
    if not pts:
        return 0.5
    centre = sum(pts, Vector()) / len(pts)
    return max((p - centre).length for p in pts)


def create_shot(scene, key, radius=0.5):
    label, lens, az, el, aspect, fill = SHOT_PRESETS[key]
    target = get_target(scene)
    name = "Plinth_Shot_" + label.replace(" ", "")
    cam_data = bpy.data.cameras.get(name) or bpy.data.cameras.new(name)
    cam_data.lens = lens
    cam_data.sensor_width = 36.0
    cam = bpy.data.objects.get(name) or bpy.data.objects.new(name, cam_data)

    # Distance so the subject fills `fill` of the frame for this lens.
    half_fov = math.atan(cam_data.sensor_width / (2 * lens))
    dist = (radius / fill) / math.tan(half_fov)
    a, e = math.radians(az), math.radians(el)
    cam.location = target.location + Vector((
        dist * math.cos(e) * math.sin(a),
        -dist * math.cos(e) * math.cos(a),
        dist * math.sin(e),
    ))
    aim_at(cam, target)
    cam["plinth_shot"] = key
    cam["plinth_enabled"] = True
    cam["plinth_aspect"] = list(aspect)
    return _link(cam, studio_collection(scene))


TURNTABLE = "TURNTABLE"
PIVOT = "Plinth_Turntable_Pivot"


def create_turntable(scene, radius=0.5, frames=72, elevation=15.0, lens=50.0):
    """A camera that circles the product once over `frames` frames.

    The pivot spins with a frame driver (rotation = (frame - start) / frames
    * 360 deg) rather than keyframes: perfectly linear, loops seamlessly, and
    works the same on every Blender version (no Action API differences)."""
    target = get_target(scene)
    col = studio_collection(scene)
    pivot = bpy.data.objects.get(PIVOT) or bpy.data.objects.new(PIVOT, None)
    pivot.empty_display_type = "CIRCLE"
    pivot.empty_display_size = radius * 2
    pivot.location = target.location
    _link(pivot, col)

    name = "Plinth_Shot_Turntable"
    cam_data = bpy.data.cameras.get(name) or bpy.data.cameras.new(name)
    cam_data.lens, cam_data.sensor_width = lens, 36.0
    cam = bpy.data.objects.get(name) or bpy.data.objects.new(name, cam_data)
    fill = 0.65
    dist = (radius / fill) / math.tan(math.atan(cam_data.sensor_width / (2 * lens)))
    e = math.radians(elevation)
    cam.parent = pivot
    cam.matrix_parent_inverse.identity()
    cam.location = Vector((0, -dist * math.cos(e), dist * math.sin(e)))   # pivot space
    aim_at(cam, target)
    cam["plinth_shot"] = TURNTABLE
    cam["plinth_enabled"] = True
    cam["plinth_aspect"] = [1, 1]
    cam["plinth_frames"] = frames

    # Spin = (frame - start) / n turns, with n read live from the camera's
    # frame count so editing it in the sidebar keeps the loop seamless.
    pivot.driver_remove("rotation_euler", 2)
    drv = pivot.driver_add("rotation_euler", 2).driver
    drv.type = "SCRIPTED"
    var = drv.variables.new()
    var.name, var.type = "n", "SINGLE_PROP"
    var.targets[0].id = cam
    var.targets[0].data_path = '["plinth_frames"]'
    drv.expression = "(frame - %d) * %r / max(n, 1)" % (scene.frame_start, 2 * math.pi)
    return _link(cam, col)


def shot_orbit(scene, cam):
    """(elevation degrees, distance) of a shot camera around what it aims at."""
    off = _shot_offset(scene, cam)
    return math.degrees(math.asin(max(-1.0, min(1.0, off.z / (off.length or 1.0))))), off.length


def shot_azimuth(scene, cam):
    """Direction around the aim point in degrees (0 = in front, -Y; 90 = +X)."""
    off = _shot_offset(scene, cam)
    return math.degrees(math.atan2(off.x, -off.y))


def _shot_centre(scene, cam):
    """What a shot orbits: the target it aims at (its own pivot, a target, or
    Main). Read-only: the Camera card calls this while drawing."""
    tgt = aim_target(scene, cam)
    return tgt.location.copy() if tgt is not None else Vector()


def _shot_offset(scene, cam):
    # Turntable cameras live in pivot space (the pivot sits on the target).
    if cam.parent is not None and cam.parent.name == PIVOT:
        return cam.location.copy()
    return cam.location - _shot_centre(scene, cam)


def wrap_degrees(a):
    """-180 <= a < 180."""
    return (a + 180.0) % 360.0 - 180.0


def set_shot_orbit(scene, cam, elevation=None, distance=None, azimuth=None):
    """Move a shot camera to a new height (degrees), distance and/or azimuth
    (degrees, wraps) around what it aims at; what isn't given stays."""
    off = _shot_offset(scene, cam)
    el, dist = shot_orbit(scene, cam)
    el = math.radians(el if elevation is None else max(-10.0, min(TOP_ELEVATION, elevation)))
    dist = max(0.01, dist if distance is None else distance)
    az = math.atan2(off.x, -off.y) if azimuth is None else math.radians(wrap_degrees(azimuth))
    new = Vector((dist * math.cos(el) * math.sin(az), -dist * math.cos(el) * math.cos(az),
                  dist * math.sin(el)))
    if cam.parent is not None and cam.parent.name == PIVOT:
        cam.location = new
    else:
        if find_target(scene) is None:
            get_target(scene)
        cam.location = _shot_centre(scene, cam) + new
        if cam.constraints.get("Plinth_Track") is None:         # untracked: re-aim by hand
            cam.rotation_mode = "QUATERNION"
            cam.rotation_quaternion = (-new).to_track_quat("-Z", "Y")
    if cam.get("plinth_focus", "TARGET") != "TARGET" or cam.get("plinth_focus_offset"):
        bpy.context.view_layer.update()
        sync_focus(scene, cam)
    if bpy.data.objects.get("Plinth_Scene_Dome") is not None:      # 0.10: a photo scene holds every shot
        from . import scenes
        scenes.fit_dome(scene)


# --- Projection and views (0.9.1) ------------------------------------------------
# Perspective or orthographic per shot, and one-click Front/Side/Rear/Top. Views
# follow Blender's (Front looks along +Y from -Y; Side from +X). Top stops just
# short of straight down: at 90 degrees the shot's Track-To has no up and flips.

TOP_ELEVATION = 89.9
VIEWS = {"FRONT": ("Front", 0.0, 0.0), "SIDE": ("Side", 90.0, 0.0),
         "REAR": ("Rear", 180.0, 0.0), "TOP": ("Top", 0.0, TOP_ELEVATION)}


def is_ortho(cam):
    return cam.data.type == "ORTHO"


def set_shot_projection(scene, cam, ortho):
    """Switch a shot between perspective and orthographic, keeping the product
    the same size in the frame (at what the shot aims at)."""
    _el, dist = shot_orbit(scene, cam)
    data = cam.data
    if ortho and data.type != "ORTHO":
        data.ortho_scale = max(0.01, dist * data.sensor_width / data.lens)
        data.type = "ORTHO"
    elif not ortho and data.type == "ORTHO":
        data.lens = max(18.0, min(200.0, dist * data.sensor_width / max(0.01, data.ortho_scale)))
        data.type = "PERSP"


def shot_view(scene, cam):
    """The VIEWS key this shot is looking from (within half a degree), or None."""
    el, _d = shot_orbit(scene, cam)
    az = shot_azimuth(scene, cam)
    for key, (_label, vaz, vel) in VIEWS.items():
        if key == "TOP":
            if el > TOP_ELEVATION - 0.5:
                return key
        elif abs(el - vel) < 0.5 and abs(wrap_degrees(az - vaz)) < 0.5:
            return key
    return None


def set_shot_view(scene, cam, key):
    _label, az, el = VIEWS[key]
    set_shot_orbit(scene, cam, elevation=el, azimuth=az)


ASPECTS = [(4, 5), (1, 1), (16, 9), (3, 2), (9, 16)]
CUSTOM = "CUSTOM"


def shot_label(cam):
    """Short display name of a shot camera (its own name once renamed)."""
    key = cam.get("plinth_shot")
    if cam.get("plinth_label"):
        return cam["plinth_label"]
    if key == TURNTABLE:
        return "Turntable"
    if key == CUSTOM:
        return cam.get("plinth_label", cam.name.replace("Plinth_Shot_", ""))
    return SHOT_PRESETS[key][0] if key in SHOT_PRESETS else cam.name.replace("Plinth_Shot_", "")


def _free_shot_number(scene):
    n = len(list_shots(scene)) + 1
    while bpy.data.objects.get("Plinth_Shot_Shot%d" % n) is not None:
        n += 1
    return n


def add_shot(scene, matrix_world, lens=50.0, aspect=(4, 5)):
    """A new shot exactly where the view is (e.g. the viewport). Not tracked:
    it keeps the framing you saw. Aim it at a target to make it follow one."""
    n = _free_shot_number(scene)
    name = "Plinth_Shot_Shot%d" % n
    data = bpy.data.cameras.new(name)
    data.lens, data.sensor_width = lens, 36.0
    cam = bpy.data.objects.new(name, data)
    cam.matrix_world = matrix_world
    cam["plinth_shot"] = CUSTOM
    cam["plinth_label"] = "Shot %d" % n
    cam["plinth_enabled"] = True
    cam["plinth_aspect"] = list(aspect)
    data.dof.focus_object = get_target(scene)
    _link(cam, studio_collection(scene))
    _place_after(scene, cam, None)
    return cam


def duplicate_shot(scene, cam):
    """A copy of a shot (own camera data) you can then change."""
    n = _free_shot_number(scene)
    name = "Plinth_Shot_Shot%d" % n
    data = cam.data.copy()
    data.name = name
    new = cam.copy()
    new.data = data
    new.name = name
    new["plinth_shot"] = CUSTOM
    new["plinth_label"] = "%s copy" % shot_label(cam) if len(shot_label(cam)) < 14 else "Shot %d" % n
    if cam.parent is not None:                      # turntable copies stay on the pivot
        new.parent = cam.parent
    _link(new, studio_collection(scene))
    _place_after(scene, new, cam)                   # right after the original
    return new


def remove_shot(scene, cam):
    if scene.camera == cam:
        others = [c for c in list_shots(scene) if c != cam]
        scene.camera = others[0] if others else None
    data = cam.data
    pivot = bpy.data.objects.get(pivot_name(cam))
    bpy.data.objects.remove(cam)
    if data is not None and data.users == 0:
        bpy.data.cameras.remove(data)
    if pivot is not None:                           # its own pivot goes with it
        remove_target(scene, pivot)


def set_shot_aspect(scene, cam, aspect):
    """Change a shot's aspect; the viewport frame follows if it's the active shot."""
    cam["plinth_aspect"] = list(aspect)
    if scene.camera == cam:
        from .shoot import resolution_for
        r = scene.render
        r.resolution_x, r.resolution_y = resolution_for(tuple(aspect), max(r.resolution_x, r.resolution_y))


def set_depth_of_field(scene, cam, on, fstop=None):
    """Background blur focused on the shot's target."""
    dof = cam.data.dof
    dof.use_dof = on
    if fstop is not None:
        dof.aperture_fstop = fstop
    if dof.focus_object is None:
        dof.focus_object = aim_target(scene, cam)


def list_shots(scene):
    """Shots in shot-bar order: ``plinth_order`` once the user has reordered or
    added shots, by name before that (files from older versions)."""
    col = bpy.data.collections.get(COLLECTION)
    if col is None:
        return []
    return sorted((o for o in col.objects if o.type == "CAMERA" and "plinth_shot" in o),
                  key=lambda o: (o.get("plinth_order", 1e9), o.name))


def _renumber(shots):
    for i, cam in enumerate(shots):
        cam["plinth_order"] = i


def _place_after(scene, cam, after):
    """Put ``cam`` right after ``after`` in the bar (at the end if None)."""
    shots = [c for c in list_shots(scene) if c != cam]
    i = shots.index(after) + 1 if after in shots else len(shots)
    shots.insert(i, cam)
    _renumber(shots)


def move_shot(scene, cam, delta):
    """Move a shot left (-1) or right (+1) in the bar. Returns True if it moved."""
    shots = list_shots(scene)
    i = shots.index(cam)
    j = max(0, min(len(shots) - 1, i + delta))
    if i == j:
        return False
    shots.insert(j, shots.pop(i))
    _renumber(shots)
    return True


def rename_shot(cam, label):
    """Give a shot its own name (what the bar, frame label and files use)."""
    label = " ".join((label or "").split())[:32]
    if not label:
        return False
    cam["plinth_label"] = label
    return True


WORLD = "Plinth_World"


def ensure_world(scene):
    """Give the scene a neutral studio world if it has none, so reflections
    aren't black. An existing world is the user's: left alone."""
    if scene.world is not None:
        return scene.world
    world = bpy.data.worlds.get(WORLD) or bpy.data.worlds.new(WORLD)
    world.color = (0.05, 0.05, 0.055)
    if world.node_tree is not None:
        bg = world.node_tree.nodes.get("Background")
        if bg is not None:
            bg.inputs["Color"].default_value = (0.05, 0.05, 0.055, 1)
    scene.world = world
    return world


PROBE = "Plinth_Probe"


def build_probe(scene, radius=0.5):
    """Sphere light probe at the product: Eevee's reflections of the cove.
    Captured live (no baking). Clip start sits outside the product so the
    probe sees the studio, not the inside of the product."""
    data = bpy.data.lightprobes.get(PROBE) or bpy.data.lightprobes.new(PROBE, "SPHERE")
    obj = bpy.data.objects.get(PROBE) or bpy.data.objects.new(PROBE, data)
    obj.location = get_target(scene).location
    data.influence_distance = 30 * radius
    data.clip_start = 1.6 * radius
    return _link(obj, studio_collection(scene))


def build_studio(scene, product_objects=()):
    """One call: backdrop + lights + default shots, scaled to the product.
    In a scene (0.10), the scene is rebuilt around the new size afterwards."""
    from . import scenes
    place = scene.get(scenes.KEY, "")
    if place:
        scenes.clear(scene)
    built = _build_studio(scene, product_objects)
    if place:
        scenes.apply(scene, place)
    return built


def _build_studio(scene, product_objects=()):
    radius = subject_radius(product_objects)
    scale = max(radius / 0.5, 0.2)
    target = get_target(scene)
    target.location = Vector((0, 0, radius))
    backdrop = build_backdrop(scene, width=24 * radius, depth=12 * radius,
                              radius=3.0 * radius, height=12 * radius)
    lights = build_light_rig(scene, scale=scale)
    if current_form(backdrop) != "SWEEP":
        set_form(scene, current_form(backdrop))  # 0.9: the form, refitted to this product
    shots = [create_shot(scene, k, radius) for k in SHOT_PRESETS]
    probe = build_probe(scene, radius)
    ensure_world(scene)
    if shots and (scene.camera is None or "plinth_shot" not in scene.camera):
        scene.camera = shots[0]                  # Camera and Shoot start on Hero (yours stays in the scene)
        set_shot_aspect(scene, shots[0], tuple(shots[0]["plinth_aspect"]))    # the frame shows its crop
    return {"backdrop": backdrop, "lights": lights, "shots": shots, "probe": probe}


# --- Framing the product (REDESIGN.md §6, issue 6) ------------------------------------

def view_distance(radius, lens=50.0, fill=0.45, sensor=36.0, aspect=1.0):
    """How far a perspective view must stand back so a subject of ``radius``
    fills ``fill`` of the view's shorter side (``aspect`` = width / height)."""
    half_fov = math.atan(sensor / (2.0 * lens))
    if aspect > 1.0:                                  # the height is the shorter side
        half_fov = math.atan(math.tan(half_fov) / aspect)
    return radius / max(1e-6, fill) / math.tan(half_fov)


# Blender's viewport turns its lens into a field of view as if the sensor were
# 72 mm (twice a camera's 36 mm). The subject radius is a bounding-box corner,
# so round products sit a little smaller than FILL suggests.
VIEW_SENSOR, VIEW_FILL = 72.0, 0.55


def frame_view(rv3d, scene, lens=50.0, aspect=1.0):
    """Centre an orbit view on the product and fit it, keeping the view's
    direction (the scene never animates: this is instant). False if there's
    no studio yet."""
    tgt = find_target(scene)
    if tgt is None:
        return False
    radius = 0.5 * float(tgt.get("plinth_scale", 1.0))
    if rv3d.view_perspective == "CAMERA":
        rv3d.view_perspective = "PERSP"
    rv3d.view_location = tgt.matrix_world.translation.copy()
    rv3d.view_distance = view_distance(radius, lens, VIEW_FILL, VIEW_SENSOR, aspect)
    return True



# (REDESIGN.md §3, Studio › Floor.) Reflection is the Principled specular
# level; the edge fade blends the floor into transparency near its front and
# side edges, in the backdrop's own coordinates (real units: the backdrop is
# never scaled).

FADE_PREFIX = "Plinth_Fade"
FLOOR_DEFAULTS = {"reflection": 0.5, "roughness": 0.6, "fade": False}
SPECULAR = ("Specular IOR Level", "Specular")          # 4.x+ name, then older


def _backdrop_tree(obj):
    mat = obj.active_material if obj else None
    return mat.node_tree if mat is not None and mat.node_tree is not None else None


def _bsdf(tree):
    return next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None) if tree else None


def _specular_socket(bsdf):
    return next((bsdf.inputs[n] for n in SPECULAR if n in bsdf.inputs), None)


def floor_settings(obj):
    """{'reflection', 'roughness', 'fade'} of the backdrop (read-only)."""
    tree = _backdrop_tree(obj)
    bsdf = _bsdf(tree)
    out = dict(FLOOR_DEFAULTS)
    if bsdf is not None:
        spec = _specular_socket(bsdf)
        out["reflection"] = spec.default_value if spec is not None else 0.5
        out["roughness"] = bsdf.inputs["Roughness"].default_value
    node = tree.nodes.get(FADE_PREFIX + "_On") if tree else None
    out["fade"] = bool(node is not None and node.outputs[0].default_value > 0.5)
    return out


def _fade_nodes(obj):
    """Build (once) the edge-fade nodes in the backdrop material; returns the
    'on' value node. Rebuilding is idempotent: nodes are found by name."""
    tree = _backdrop_tree(obj)
    bsdf = _bsdf(tree)
    out = next((n for n in tree.nodes if n.type == "OUTPUT_MATERIAL"), None)
    nodes, links = tree.nodes, tree.links
    on = nodes.get(FADE_PREFIX + "_On")
    if on is not None:
        return on

    def node(kind, name, loc):
        n = nodes.new(kind)
        n.name = n.label = FADE_PREFIX + "_" + name
        n.location = (bsdf.location.x + loc[0], bsdf.location.y + loc[1])
        return n

    def math(op, name, loc, a=None, b=None):
        n = node("ShaderNodeMath", name, loc)
        n.operation = op
        n.use_clamp = op in {"DIVIDE", "SUBTRACT"}
        for i, v in enumerate((a, b)):
            if isinstance(v, (int, float)):
                n.inputs[i].default_value = v
            elif v is not None:
                links.new(v, n.inputs[i])
        return n

    coord = node("ShaderNodeTexCoord", "Coord", (-1400, -500))
    sep = node("ShaderNodeSeparateXYZ", "Split", (-1200, -500))
    links.new(coord.outputs["Object"], sep.inputs[0])
    depth = node("ShaderNodeValue", "Depth", (-1200, -700))
    width = node("ShaderNodeValue", "Width", (-1200, -800))
    on = node("ShaderNodeValue", "On", (-1200, -900))
    # front: 0 at the front edge (y = -depth) -> 1 a quarter of the way in
    front = math("DIVIDE", "Front", (-800, -500),
                 math("ADD", "FrontDist", (-1000, -500), sep.outputs["Y"], depth.outputs[0]).outputs[0],
                 math("MULTIPLY", "FrontBand", (-1000, -650), depth.outputs[0], 0.25).outputs[0])
    absx = math("ABSOLUTE", "AbsX", (-1000, -350), sep.outputs["X"])
    half = math("MULTIPLY", "Half", (-1000, -800), width.outputs[0], 0.5)
    side = math("DIVIDE", "Side", (-800, -350),
                math("SUBTRACT", "SideDist", (-900, -350), half.outputs[0], absx.outputs[0]).outputs[0],
                math("MULTIPLY", "SideBand", (-900, -800), width.outputs[0], 0.12).outputs[0])
    edge = math("MULTIPLY", "Edge", (-600, -450), front.outputs[0], side.outputs[0])
    smooth = node("ShaderNodeMapRange", "Smooth", (-450, -450))
    smooth.interpolation_type = "SMOOTHSTEP"
    links.new(edge.outputs[0], smooth.inputs["Value"])
    # fac = 1 - on * (1 - edge): off -> the plain floor everywhere
    inv = math("SUBTRACT", "Inv", (-300, -450), 1.0, smooth.outputs[0])
    amount = math("MULTIPLY", "Amount", (-200, -450), inv.outputs[0], on.outputs[0])
    fac = math("SUBTRACT", "Fac", (-100, -450), 1.0, amount.outputs[0])
    clear = node("ShaderNodeBsdfTransparent", "Clear", (0, -300))
    mix = node("ShaderNodeMixShader", "Mix", (300, 0))
    links.new(fac.outputs[0], mix.inputs[0])
    links.new(clear.outputs[0], mix.inputs[1])
    links.new(bsdf.outputs[0], mix.inputs[2])
    if out is not None:
        links.new(mix.outputs[0], out.inputs["Surface"])
    on.outputs[0].default_value = 0.0
    _sync_fade(obj)
    return on


def _sync_fade(obj):
    """Keep the fade's size inputs in step with the cove sliders."""
    tree = _backdrop_tree(obj)
    if tree is None or tree.nodes.get(FADE_PREFIX + "_On") is None:
        return
    mod = obj.modifiers.get(COVE_GN)
    idents = {k: i for k, _l, i in backdrop_inputs(obj)}
    if mod is None or not {"depth", "width"} <= set(idents):
        return
    tree.nodes[FADE_PREFIX + "_Depth"].outputs[0].default_value = _get_input(mod, idents["depth"])
    tree.nodes[FADE_PREFIX + "_Width"].outputs[0].default_value = _get_input(mod, idents["width"])


def set_floor(obj, reflection=None, roughness=None, fade=None):
    """Studio › Floor: reflection (specular), roughness, fade the floor edge."""
    tree = _backdrop_tree(obj)
    bsdf = _bsdf(tree)
    if bsdf is None:
        return False
    if reflection is not None:
        spec = _specular_socket(bsdf)
        if spec is not None:
            spec.default_value = max(0.0, min(1.0, reflection))
    if roughness is not None:
        bsdf.inputs["Roughness"].default_value = max(0.0, min(1.0, roughness))
    if fade is not None and (fade or tree.nodes.get(FADE_PREFIX + "_On") is not None):
        _fade_nodes(obj).outputs[0].default_value = 1.0 if fade else 0.0
        mat = obj.active_material
        if fade and hasattr(mat, "surface_render_method"):
            mat.surface_render_method = "DITHERED"        # Eevee: let the fade show through
    return True


def cove_defaults(radius, form="SWEEP"):
    """The backdrop sizes for a product of this radius: the form's own (the
    sweep's is what build_studio() makes)."""
    return {k: v for k, v in form_defaults(form, radius).items() if k in FORM_KEYS}


def reset_backdrop(scene, look=None):
    """Studio › Reset: the default cove for this product, the active look's
    colours (or the build colours) for the backdrop and the background, and
    the default floor."""
    obj = bpy.data.objects.get(BACKDROP)
    if obj is None:
        return False
    from . import hdri, library
    set_form(scene, current_form(obj))          # the form's own sizes for this product
    bsdf = _bsdf(_backdrop_tree(obj))
    preset = library.all_presets().get(look, {})
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = preset.get("backdrop", (0.23, 0.23, 0.25, 1.0))
    if hdri.studio_colour(scene) is not None:    # Plinth's world (a world of your own is left alone)
        hdri.set_studio_colour(scene, preset.get("world", hdri.STUDIO_COLOUR))
        library._tag(scene.world, "world", "world" in preset)
    set_floor(obj, **FLOOR_DEFAULTS)
    return True


# --- Edit pages: light colour, gels, visibility ---------------------------------------

def set_light_colour(light_data, rgb, gel=None):
    """Light colour (linear). A gel name marks it as a gel, which overrides warmth;
    setting a colour without one (warmth, a typed hex) clears the gel."""
    light_data.color = tuple(rgb[:3])
    if gel:
        light_data["plinth_gel"] = gel
    elif "plinth_gel" in light_data:
        del light_data["plinth_gel"]


def light_gel(light_data):
    return light_data.get("plinth_gel")


def light_visible(light):
    return bool(getattr(light, "visible_camera", False))


def set_light_visible(light, on):
    """'Visible in shots': the light itself shows to the camera (Cycles)."""
    if hasattr(light, "visible_camera"):
        light.visible_camera = on
    return hasattr(light, "visible_camera")


def reset_light(scene, light, look=None):
    """Light › Reset: rig defaults for Key/Rim/Fill (with the active look's
    balance), the default for added lights; shape, gobo and visibility too."""
    from . import gobos, library
    tgt = find_target(scene)
    sc = float(tgt.get("plinth_scale", 1.0)) if tgt is not None else 1.0
    data = light.data
    label = light_label(light)
    rig = LIGHT_RIG.get(label)
    gobos.forget_size(data)                      # Reset's size is the light's own
    gobos.set_gobo_params(data, scale=1.0, rotation=0.0, softness=0.4)
    if rig is not None:
        shape, size, size_y, power, _off, kelvin = rig
        values = library.BUILTIN.get(look, {}).get("lights", {}).get(label, {})
        set_light_shape(data, values.get("shape") if values.get("shape") in LIGHT_SHAPES
                        else ("STRIP" if shape == "RECTANGLE" else "SOFTBOX"))
        data = fresh(data)
        set_light_power(data, power * sc * sc * values.get("power", 1.0))
        set_light_size(data, size * sc * values.get("softness", 1.0))
        if data.type == "AREA" and "shape" not in values:
            data.size_y = size_y * sc * values.get("softness", 1.0)
        set_light_colour(data, values.get("color", kelvin_to_rgb(kelvin)))
        gobos.set_gobo(data, values.get("gobo", "NONE"))
    else:
        set_light_shape(data, "SOFTBOX")
        data = fresh(data)
        data.energy = NEW_LIGHT["power"] * sc * sc
        set_light_size(data, NEW_LIGHT["size"] * sc)
        set_light_colour(data, kelvin_to_rgb(5000))
        gobos.set_gobo(data, "NONE")
    set_light_visible(light, False)
    return True


# --- Edit pages: focus ----------------------------------------------------------------

FOCUS_MODES = ("TARGET", "NEAREST")


def focus_settings(cam):
    return {"mode": cam.get("plinth_focus", "TARGET"),
            "offset": float(cam.get("plinth_focus_offset", 0.0))}


def set_focus(scene, cam, mode=None, offset=None):
    """Camera › Focus: on the shot's target or the nearest surface it sees,
    plus an offset in metres (positive = further away)."""
    if mode is not None:
        cam["plinth_focus"] = mode
    if offset is not None:
        cam["plinth_focus_offset"] = float(offset)
    sync_focus(scene, cam)


def sync_focus(scene, cam, depsgraph=None):
    """Re-aim the focus after the shot or its target moved."""
    if cam is None or cam.type != "CAMERA" or "plinth_shot" not in cam:
        return
    dof = cam.data.dof
    st = focus_settings(cam)
    if st["mode"] == "TARGET" and abs(st["offset"]) < 1e-6:
        dof.focus_object = aim_target(scene, cam) or find_target(scene)
        return
    origin = cam.matrix_world.translation
    if st["mode"] == "NEAREST":
        direction = (cam.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized()
        dg = depsgraph or bpy.context.evaluated_depsgraph_get()
        hit, loc, *_ = scene.ray_cast(dg, origin, direction)
        distance = (loc - origin).length if hit else 10.0
    else:
        tgt = aim_target(scene, cam) or find_target(scene)
        distance = (tgt.matrix_world.translation - origin).length if tgt else 10.0
    dof.focus_object = None
    dof.focus_distance = max(0.01, distance + st["offset"])


# --- Edit pages: turntable on any shot -------------------------------------------------
# Any shot can render as a 360° loop: Shoot turns the camera around the target
# frame by frame (and puts it back). The original Turntable shot spins with its
# pivot's driver; its frames/direction/start live in the same properties.

def turntable_settings(cam):
    return {"on": cam.get("plinth_shot") == TURNTABLE or bool(cam.get("plinth_turntable", False)),
            "frames": int(cam.get("plinth_frames", 72)),
            "clockwise": bool(cam.get("plinth_clockwise", True)),
            "start": float(cam.get("plinth_start_angle", 0.0))}


def set_turntable(scene, cam, on=None, frames=None, clockwise=None, start=None):
    if on is not None and cam.get("plinth_shot") != TURNTABLE:
        cam["plinth_turntable"] = bool(on)
    if frames is not None:
        cam["plinth_frames"] = max(2, int(round(frames)))
    if clockwise is not None:
        cam["plinth_clockwise"] = bool(clockwise)
    if start is not None:
        cam["plinth_start_angle"] = float(start) % 360.0
    pivot = cam.parent if cam.parent is not None and cam.parent.name == PIVOT else None
    if pivot is not None and pivot.animation_data is not None:
        st = turntable_settings(cam)
        for fc in pivot.animation_data.drivers:
            if fc.data_path == "rotation_euler" and fc.array_index == 2:
                sign = -1.0 if st["clockwise"] else 1.0
                fc.driver.expression = "%r + %r * (frame - %d) * %r / max(n, 1)" % (
                    math.radians(st["start"]), sign, scene.frame_start, 2 * math.pi)


def turntable_matrix(scene, cam, base, index):
    """World matrix of a turntable frame ``index``: ``base`` turned about the
    target's vertical axis by start + (clockwise ? -1 : 1) * 360 * index / frames."""
    from mathutils import Matrix
    st = turntable_settings(cam)
    sign = -1.0 if st["clockwise"] else 1.0
    angle = math.radians(st["start"]) + sign * 2 * math.pi * index / max(1, st["frames"])
    tgt = aim_target(scene, cam) or find_target(scene)
    centre = tgt.matrix_world.translation.copy() if tgt is not None else Vector()
    centre.z = 0.0
    turn = Matrix.Translation(centre) @ Matrix.Rotation(angle, 4, "Z") @ Matrix.Translation(-centre)
    return turn @ base


def reset_shot(scene, cam):
    """Camera › Reset: the preset's lens and aspect (50 mm, 4:5 for your own
    shots), focus on the target, no blur, no turntable."""
    key = cam.get("plinth_shot")
    if key in SHOT_PRESETS:
        _label, lens, _az, _el, aspect, _fill = SHOT_PRESETS[key]
    else:
        lens, aspect = 50.0, (1, 1) if key == TURNTABLE else (4, 5)
    cam.data.type = "PERSP"
    cam.data.lens = lens
    set_shot_aspect(scene, cam, aspect)
    for k in ("plinth_focus", "plinth_focus_offset", "plinth_turntable", "plinth_clockwise",
              "plinth_start_angle"):
        if k in cam:
            del cam[k]
    cam.data.dof.use_dof = False
    sync_focus(scene, cam)
    if key == TURNTABLE:
        set_turntable(scene, cam, frames=72)
    return True

