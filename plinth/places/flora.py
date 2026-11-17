# SPDX-License-Identifier: GPL-3.0-or-later
"""Trees and plants made in code (0.12) with real leaves.

A broadleaf tree is grown, not drawn: a flared, tapering trunk; main limbs
spiralling out (golden angle) and curving up toward the light; side
branches; twigs; and on the twigs thousands of separate leaf cards cut from a
scanned leaf atlas (scenemaps.leaves: each card is one leaf or twig of the
atlas, folded along its midrib), facing mostly up and out as real leaves do.
A conifer has whorls of drooping branches carrying scanned needle sprays,
bare and dead low down where the crown shades itself.

Meshes are in metres (a tree's real height); scatter sizes them by the
scene's unit. Slot 0 is bark, slot 1 the leaves. Each leaf card carries a
``leaf_rand`` point attribute (scenemats.foliage varies hue and value by it).
"""
import math

import bpy
from mathutils import Matrix, Vector

GOLDEN = math.pi * (3.0 - math.sqrt(5.0))


class _Mesh:
    """Vertices, quads (with per-corner UVs), slots and a per-vertex float."""

    def __init__(self):
        self.co, self.faces, self.uvs, self.slots, self.rand = [], [], [], [], []

    def vert(self, p, rnd=0.0):
        self.co.append((p[0], p[1], p[2]))
        self.rand.append(rnd)
        return len(self.co) - 1

    def face(self, idx, uvs, slot):
        self.faces.append(idx)
        self.uvs.append(uvs)
        self.slots.append(slot)

    def tube(self, spine, radii, sides, slot=0, v0=0.0):
        """A tapering tube through spine points (Vectors), bark UVs in metres
        (u around the mean circumference, v along)."""
        rings = []
        length = v0
        circ = 2.0 * math.pi * (sum(radii) / len(radii))
        prev_side = None
        for k, (p, rad) in enumerate(zip(spine, radii)):
            if k < len(spine) - 1:
                d = (spine[k + 1] - p).normalized()
            else:
                d = (p - spine[k - 1]).normalized()
            side = prev_side if prev_side is not None else d.orthogonal().normalized()
            side = (side - d * side.dot(d)).normalized()
            prev_side = side
            up = d.cross(side)
            ring = [self.vert(p + (side * math.cos(2 * math.pi * i / sides) + up * math.sin(2 * math.pi * i / sides))
                              * rad) for i in range(sides)]
            if k:
                length += (p - spine[k - 1]).length
            rings.append((ring, length))
        for (ra, va), (rb, vb) in zip(rings, rings[1:]):
            for i in range(sides):
                j = (i + 1) % sides
                ua, ub = circ * i / sides, circ * (i + 1) / sides
                self.face((ra[i], ra[j], rb[j], rb[i]), ((ua, va), (ub, va), (ub, vb), (ua, vb)), slot)
        return length

    def card(self, base, along, across, rect, length, rnd, fold=0.18, slot=1):
        """A leaf card from an atlas rect [u0, v0, u1, v1, stem_u]: its stem
        at ``base``, its length along ``along``, folded along the midrib."""
        u0, v0, u1, v1, su = rect
        k = length / max(1e-6, v1 - v0)
        left, right = k * (su - u0), k * (u1 - su)
        n = along.cross(across).normalized()
        lift = n * (fold * (left + right) * 0.5)
        tip = base + along * length
        a = self.vert(base - across * left + lift, rnd)
        b = self.vert(base, rnd)
        c = self.vert(base + across * right + lift, rnd)
        d = self.vert(tip - across * left + lift, rnd)
        e = self.vert(tip, rnd)
        f = self.vert(tip + across * right + lift, rnd)
        self.face((a, b, e, d), ((u0, v0), (su, v0), (su, v1), (u0, v1)), slot)
        self.face((b, c, f, e), ((su, v0), (u1, v0), (u1, v1), (su, v1)), slot)

    def mesh(self, name):
        import numpy as np
        old = bpy.data.meshes.get(name)
        if old is not None and old.users == 0:
            bpy.data.meshes.remove(old)
        me = bpy.data.meshes.new(name)
        co = np.asarray(self.co, np.float32)
        sizes = np.fromiter((len(f) for f in self.faces), np.int32, len(self.faces))
        flat = np.fromiter((i for f in self.faces for i in f), np.int32, int(sizes.sum()))
        me.vertices.add(len(co))
        me.vertices.foreach_set("co", co.ravel())
        me.loops.add(len(flat))
        me.loops.foreach_set("vertex_index", flat)
        me.polygons.add(len(sizes))
        starts = np.zeros(len(sizes), np.int32)
        starts[1:] = np.cumsum(sizes)[:-1]
        me.polygons.foreach_set("loop_start", starts)
        me.polygons.foreach_set("material_index", np.asarray(self.slots, np.int32))
        me.update(calc_edges=True)
        me.polygons.foreach_set("use_smooth", np.ones(len(sizes), bool))
        layer = me.uv_layers.new(name="UVMap")
        layer.data.foreach_set("uv", np.asarray([uv for f in self.uvs for uv in f], np.float32).ravel())
        a = me.attributes.new("leaf_rand", "FLOAT", "POINT")
        a.data.foreach_set("value", np.asarray(self.rand, np.float32))
        me.update()
        return me


def _curve(start, direction, length, steps, rng, up_pull=0.0, droop=0.0, wobble=0.08):
    """Points along a branch: starting along ``direction``, bending upward
    (``up_pull``) or down (``droop``) as it goes, with a little wobble."""
    pts = [start.copy()]
    d = direction.normalized()
    step = length / steps
    for k in range(steps):
        t = (k + 1) / steps
        d = (d + Vector((0.0, 0.0, up_pull - droop * t)) * (1.0 / steps) * 3.0
             + Vector(rng.normal(0.0, wobble, 3)) * (1.0 / steps) * 3.0).normalized()
        pts.append(pts[-1] + d * step)
    return pts


def _leaf_frame(rng, outward):
    """A leaf's length direction and its width direction: blades face up and
    out toward the light, tipped this way and that."""
    face = (Vector((0.0, 0.0, 1.0)) * 0.9 + outward * 0.45 + Vector(rng.normal(0.0, 0.45, 3))).normalized()
    along = (outward + Vector(rng.normal(0.0, 0.7, 3)) + Vector((0.0, 0.0, -0.25))).normalized()
    along = (along - face * along.dot(face)).normalized()
    across = face.cross(along).normalized()
    return along, across


LEAVES = {"LOW": 4000, "MEDIUM": 7500, "HIGH": 12000, "ULTRA": 22000}


def broadleaf(name, rng, rects, leaf_length, height=22.0, detail="HIGH", spread=0.52, droop=0.3, far=False,
              rise=(0.2, 0.7), limbs=(7, 11), leaf_scale=3.6, leaf_k=1.0):
    """A broadleaf tree ``height`` m tall (beech/oak-like): see the module doc.
    ``far``: a light copy for the distance (a fifth of the leaves, bigger)."""
    m = _Mesh()
    H = height * rng.uniform(0.92, 1.08)
    r0 = H * 0.017
    lean = Vector((rng.normal(0, 0.04), rng.normal(0, 0.04), 1.0)).normalized()
    trunk = _curve(Vector((0, 0, -0.3)), lean, H * 0.78, 14, rng, up_pull=0.15, wobble=0.05)
    radii = [r0 * (1.0 - 0.72 * (k / 14)) * (1.0 + 0.7 * math.exp(-(p.z + 0.3) / (0.06 * H)))
             for k, p in enumerate(trunk)]
    m.tube(trunk, radii, 7 if far else 14)
    anchors = []                                   # (point, outward) where leaves grow
    n_main = int(rng.integers(*limbs))
    first = 0.32 + rng.uniform(-0.05, 0.05)
    for b in range(n_main):
        f = first + (0.95 - first) * (b / max(1, n_main - 1)) ** 0.85
        k = min(13, int(f * 14))
        base = trunk[k]
        az = b * GOLDEN * 2.0 + rng.uniform(-0.4, 0.4)
        out = Vector((math.cos(az), math.sin(az), 0.0))
        rise_k = rng.uniform(*rise) * (0.45 + 0.8 * f)
        direction = (out + Vector((0, 0, rise_k))).normalized()
        L1 = H * spread * (1.0 - 0.55 * (f - first)) * rng.uniform(0.8, 1.15)
        limb = _curve(base, direction, L1, 7 if far else 9, rng, up_pull=0.35, droop=droop, wobble=0.12)
        rb = radii[k] * rng.uniform(0.45, 0.6)
        m.tube(limb, [rb * (1.0 - 0.8 * t / (len(limb) - 1)) + 0.01 for t in range(len(limb))], 5 if far else 8)
        n_sub = int(rng.integers(5, 10))
        for s in range(n_sub):
            t = rng.uniform(0.3, 0.92)
            i = min(len(limb) - 2, int(t * (len(limb) - 1)))
            p0 = limb[i]
            d0 = (limb[i + 1] - limb[i]).normalized()
            side = d0.cross(Vector((0, 0, 1))).normalized() * (1 if s % 2 else -1)
            sdir = (d0 * 0.7 + side * 0.7 + Vector((0, 0, rng.uniform(0.0, 0.5)))).normalized()
            L2 = L1 * rng.uniform(0.3, 0.55) * (1.1 - 0.4 * t)
            sub = _curve(p0, sdir, L2, 4 if far else 6, rng, up_pull=0.2, droop=droop * 1.4, wobble=0.18)
            rs = rb * 0.45 * (1.0 - 0.6 * t)
            if not far:
                m.tube(sub, [rs * (1.0 - 0.85 * q / (len(sub) - 1)) + 0.004 for q in range(len(sub))], 5)
            # Twigs: leaf clumps along the outer two thirds of each side branch,
            # two or three twigs a step reaching out and up from it.
            for q in range(1, len(sub)):
                if q / (len(sub) - 1) < 0.25:
                    continue
                outward = Vector((sub[q].x, sub[q].y, 0.0))
                outward = outward.normalized() if outward.length > 1e-6 else out
                for _t in range(int(rng.integers(2, 4))):
                    twig = (outward + Vector(rng.normal(0.0, 0.6, 3)) + Vector((0.0, 0.0, 0.45))).normalized()
                    anchors.append((sub[q] + twig * rng.uniform(0.3, 0.9) * (H / 22.0), outward))
        anchors.append((limb[-1], out))
    # Leaves: shared among the anchors, a cloud round each twig.
    count = max(300, int(LEAVES.get(detail, 8500) * leaf_k / (8 if far else 1))) if leaf_k > 0 and rects else 0
    size = leaf_length * (leaf_scale if not far else leaf_scale * 2.4)
    for n in range(count):
        p, outward = anchors[int(rng.integers(0, len(anchors)))]
        off = Vector(rng.normal(0.0, 0.3 if not far else 0.55, 3)) * (H / 22.0)
        off.z = abs(off.z) * 0.6 - 0.1
        along, across = _leaf_frame(rng, outward)
        rect = rects[int(rng.integers(0, len(rects)))]
        m.card(p + off, along, across, rect, size * rng.uniform(0.75, 1.25), float(rng.uniform()))
    return m.mesh(name)


SPRAYS = {"LOW": 0.7, "MEDIUM": 1.0, "HIGH": 1.5, "ULTRA": 2.2}


def conifer(name, rng, rects, height=26.0, detail="HIGH", far=False, width=0.2):
    """A spruce-like conifer ``height`` m tall: straight trunk, whorls of
    drooping branches (a cone), needle sprays along them, bare low down."""
    m = _Mesh()
    H = height * rng.uniform(0.9, 1.1)
    r0 = H * 0.015
    trunk = _curve(Vector((0, 0, -0.3)), Vector((rng.normal(0, 0.02), rng.normal(0, 0.02), 1.0)), H, 16, rng,
                   wobble=0.015)
    radii = [r0 * (1.0 - 0.95 * (k / 16)) * (1.0 + 0.5 * math.exp(-(p.z + 0.3) / (0.05 * H))) + 0.01
             for k, p in enumerate(trunk)]
    m.tube(trunk, radii, 6 if far else 10)
    density = SPRAYS.get(detail, 1.0) * (0.3 if far else 1.0)
    z = H * 0.18
    w = 0
    spray_len = 0.75 if not far else 1.4
    while z < H * 0.97:
        t = z / H
        reach = H * width * (1.0 - t) ** 0.95 + 0.25
        per = int(rng.integers(4, 7))
        for b in range(per):
            az = 2 * math.pi * b / per + w * GOLDEN + rng.uniform(-0.3, 0.3)
            out = Vector((math.cos(az), math.sin(az), 0.0))
            start = Vector((0, 0, z)) + out * (r0 * (1 - t))
            direction = (out + Vector((0, 0, rng.uniform(-0.15, 0.25)))).normalized()
            L = reach * rng.uniform(0.8, 1.1)
            br = _curve(start, direction, L, 4 if far else 6, rng, droop=0.35 + 0.3 * (1 - t), wobble=0.06)
            if not far:
                m.tube(br, [max(0.006, r0 * 0.25 * (1 - t) * (1 - q / (len(br) - 1))) for q in range(len(br))], 4)
            if t < 0.3 and rng.uniform() < 0.55:          # shaded low branches: bare and dead
                continue
            steps = max(2, int(L / 0.22 * density))
            for s in range(steps):
                q = (s + 0.5) / steps
                i = min(len(br) - 2, int(q * (len(br) - 1)))
                p = br[i].lerp(br[i + 1], q * (len(br) - 1) - i)
                d = (br[i + 1] - br[i]).normalized()
                side = d.cross(Vector((0, 0, 1))).normalized() * (1 if s % 2 else -1)
                along = (d * 0.6 + side * 0.8 + Vector((0, 0, -0.25))).normalized()
                across = along.cross(Vector((0, 0, 1))).normalized()
                if across.length < 1e-6:
                    across = side
                rect = rects[int(rng.integers(0, len(rects)))]
                m.card(p, along, across, rect, spray_len * rng.uniform(0.7, 1.2) * (0.6 + 0.6 * (1 - q)),
                       float(rng.uniform()), fold=0.1)
        z += rng.uniform(0.35, 0.55) * (1.0 + 0.6 * (1 - t)) / max(0.5, density) ** 0.3
        w += 1
    # The leader: sprays round the top.
    for s in range(int(10 * density) + 3):
        p = Vector((0, 0, H * rng.uniform(0.9, 1.0)))
        along = Vector(rng.normal(0, 1, 3)).normalized()
        along.z = abs(along.z) + 0.5
        along = along.normalized()
        across = along.cross(Vector((0, 0, 1))).normalized()
        if across.length > 1e-6:
            m.card(p, along, across, rects[int(rng.integers(0, len(rects)))], spray_len * 0.6, float(rng.uniform()))
    return m.mesh(name)


def fern(name, rng, fronds=9, detail="HIGH"):
    """A fern 1 m tall: arching fronds of paired leaflets (green geometry,
    no atlas): slot 1 (leaves)."""
    m = _Mesh()
    segs = {"LOW": 7, "MEDIUM": 10, "HIGH": 14, "ULTRA": 20}.get(detail, 14)
    for f in range(fronds):
        az = 2 * math.pi * f / fronds + rng.uniform(-0.25, 0.25)
        out = Vector((math.cos(az), math.sin(az), 0.0))
        L = rng.uniform(0.7, 1.1)
        rise = rng.uniform(0.9, 1.6)
        spine = _curve(Vector((0, 0, 0)), (out * 0.5 + Vector((0, 0, rise))).normalized(), L, segs, rng,
                       droop=1.4, wobble=0.04)
        for k in range(1, len(spine)):
            t = k / (len(spine) - 1)
            p = spine[k]
            d = (spine[k] - spine[k - 1]).normalized()
            side = d.cross(Vector((0, 0, 1))).normalized()
            pin = 0.16 * math.sin(math.pi * min(1.0, t * 1.15)) * L + 0.01
            for sgn in (-1, 1):
                along = (side * sgn + d * 0.35 + Vector((0, 0, -0.1))).normalized()
                across = d.cross(along).normalized()
                rect = (0.0, 0.0, 1.0, 1.0, 0.5)
                m.card(p, along, across * 0.35, rect, pin, float(rng.uniform()), fold=0.08)
    return m.mesh(name)


def ball(name, rng, rects, leaf_length, count=2600, squash=0.85):
    """A clipped shrub (box ball) one unit across, sitting on z=0: leaf
    cards on and just under a sphere's surface, pointing out, a few short
    stems at its foot."""
    m = _Mesh()
    for _ in range(count):
        d = Vector(rng.normal(0.0, 1.0, 3)).normalized()
        depth = abs(rng.normal(0.0, 0.05))
        p = Vector((d.x * 0.5, d.y * 0.5, d.z * 0.5 * squash)) * (1.0 - depth) + Vector((0, 0, 0.5 * squash))
        along = (d + Vector(rng.normal(0.0, 0.5, 3))).normalized()
        across = along.cross(d).normalized() if along.cross(d).length > 1e-6 else Vector((1, 0, 0))
        rect = rects[int(rng.integers(0, len(rects)))]
        m.card(p - along * leaf_length * 0.3, along, across, rect, leaf_length * rng.uniform(0.8, 1.2),
               float(rng.uniform()), fold=0.12)
    for k in range(3):
        a = 2 * math.pi * k / 3
        m.tube([Vector((0, 0, -0.05)), Vector((0.08 * math.cos(a), 0.08 * math.sin(a), 0.25 * squash))],
               [0.02, 0.012], 4)
    return m.mesh(name)
