# SPDX-License-Identifier: GPL-3.0-or-later
"""Hybrid parity: does the Eevee preview look like the Cycles final?

Renders the same shot in both engines at low resolution and compares
luminance histograms. Used by the automated parity test and by the viewport
compare-swipe (which overlays a Cycles render on the live Eevee viewport).
"""

import os
import tempfile

import bpy

from . import shoot

# Which Cycles preset each Eevee preset is meant to preview.
PAIRS = {"STUDIO": "PREVIEW", "GLOSS": "FINAL"}

COMPARE_IMAGE = "Plinth_Compare"


def luminance_histogram(image, bins=16):
    """Normalised histogram of Rec.709 luminance, 0..1 in `bins` buckets.
    Fully transparent pixels are ignored."""
    px = [0.0] * (len(image.pixels))
    image.pixels.foreach_get(px)
    hist = [0] * bins
    n = 0
    for i in range(0, len(px), 4):
        if px[i + 3] <= 0.0:
            continue
        y = 0.2126 * px[i] + 0.7152 * px[i + 1] + 0.0722 * px[i + 2]
        hist[min(bins - 1, max(0, int(y * bins)))] += 1
        n += 1
    return [h / n for h in hist] if n else hist


def histogram_distance(a, b):
    """Total variation distance: 0 = identical, 1 = no overlap."""
    return 0.5 * sum(abs(x - y) for x, y in zip(a, b))


def mean_from_histogram(h):
    bins = len(h)
    return sum(v * (i + 0.5) / bins for i, v in enumerate(h))


def _load(path):
    img = bpy.data.images.load(path, check_existing=False)
    try:
        return luminance_histogram(img)
    finally:
        bpy.data.images.remove(img)


def compare(scene, cam, eevee_key="STUDIO", cycles_key=None, long_edge=64):
    """Render `cam` in Eevee and Cycles; return {distance, mean_eevee, mean_cycles}."""
    cycles_key = cycles_key or PAIRS.get(eevee_key, "PREVIEW")
    folder = tempfile.mkdtemp(prefix="plinth_parity_")
    ev = shoot.render_still(scene, cam, "EEVEE", eevee_key, long_edge,
                            os.path.join(folder, "eevee.png"))
    cy = shoot.render_still(scene, cam, "CYCLES", cycles_key, long_edge,
                            os.path.join(folder, "cycles.png"))
    he, hc = _load(ev), _load(cy)
    return {"distance": histogram_distance(he, hc),
            "mean_eevee": mean_from_histogram(he),
            "mean_cycles": mean_from_histogram(hc)}


def render_compare_image(scene, cam, cycles_key="PREVIEW", long_edge=512):
    """Render the Cycles side of the compare-swipe into the Plinth_Compare image."""
    path = os.path.join(tempfile.gettempdir(), "plinth_compare.png")
    # Match the camera frame the viewport shows, which follows the scene's
    # resolution, so the swipe lines up pixel for pixel.
    r = scene.render
    shoot.render_still(scene, cam, "CYCLES", cycles_key, long_edge, path,
                       aspect=(r.resolution_x * r.pixel_aspect_x, r.resolution_y * r.pixel_aspect_y))
    img = bpy.data.images.get(COMPARE_IMAGE)
    if img is None:
        img = bpy.data.images.load(path, check_existing=False)
        img.name = COMPARE_IMAGE
    else:
        img.filepath = path
        img.reload()
    img["plinth_camera"] = cam.name
    return img


def swipe_split(frame, split):
    """Split the camera frame rect (x, y, w, h) at `split` (0..1, left to right).

    Returns (cycles_rect, u_max): the left part shows Cycles, texture u in 0..u_max."""
    split = min(1.0, max(0.0, split))
    x, y, w, h = frame
    return (x, y, w * split, h), split
