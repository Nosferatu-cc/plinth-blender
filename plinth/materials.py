# SPDX-License-Identifier: GPL-3.0-or-later
"""Procedural materials, built for automotive and product work.

A material *type* (car paint, metal, glass, plastic, rubber, carbon fibre,
leather) is one shader node group, ``Plinth_<Type>``, with many exposed
inputs. Groups are built once and versioned: a newer Plinth rebuilds a
group's insides in place, but never clears its inputs, so every material
using it keeps its values (clearing them is what turned other objects black
when switching finishes, see CLAUDE.md Phase 8). Groups are marked as assets.

A *preset* is a type plus input values, with a label, a one-line description
and a category (Paint / Metal / Glass / Trim).

Each *part* (an object's material slot) gets its own material holding one
group node. The slot is switched to object-linked, so objects sharing a mesh
don't overwrite each other, and the original slot state is remembered so
"Original" puts it back. Plinth's material is found through the slot, never by
name, so renaming objects is safe. A material used by several parts (e.g. after
Shift+D) is copied before it's changed.
"""

import math
import os

import bpy

GROUP_NODE = "Plinth_Finish"        # v0.2 materials: the single group node
# Each material keeps one group node per type it has used ("Plinth_PAINT", ...)
# and switching type only rewires which one feeds the output. Never freeing
# them matters: an open "Edit material" popover still points at their sockets,
# and Blender doesn't rebuild an open popup when data changes underneath it
# (freeing them crashed Blender 5.2 in widget_swatch, see CLAUDE.md Phase 10).
ORIGINALS = "plinth_originals"      # obj[...] = {slot index: {"link", "name"}}
UNUSED = "plinth_unused"            # obj[...] = {slot index: name of our unused material}
EDITS = "plinth_edits"              # mat[...] = {preset key: {input: value}}, as the user left it
LINKED_TAG = "plinth_linked"       # mat[...]: parts deliberately share it (edits reach all of them)
HALF_PI = math.pi / 2

# --- Types: inputs ---------------------------------------------------------------
# (name, kind, default, min, max, section). kind: colour | float | bool | log
# ("log": a real-world size; the floating slider moves through it exponentially
# so every part of its travel makes a visible change).
#
# Patterns use world-space position, so their size is in real units whatever
# scale a model was imported at (car models often carry a 0.01 import scale,
# which used to make every pattern 100x too small).

TYPES = {
    "PAINT": {
        "label": "Car paint", "version": 2, "colour": "Base colour",
        "keys": ("Metallic flake", "Clearcoat", "Base roughness"),
        "inputs": [
            ("Base colour", "colour", (0.6, 0.02, 0.02, 1), None, None, "Colour"),
            ("Flip colour", "colour", (0.2, 0.0, 0.05, 1), None, None, "Colour"),
            ("Flip amount", "float", 0.0, 0.0, 1.0, "Colour"),
            ("Pearl", "float", 0.0, 0.0, 1.0, "Colour"),
            ("Flake colour", "colour", (0.9, 0.9, 0.9, 1), None, None, "Flake"),
            ("Metallic flake", "float", 0.0, 0.0, 1.0, "Flake"),
            ("Flake size", "float", 0.5, 0.0, 1.0, "Flake"),
            ("Flake sparkle", "float", 0.5, 0.0, 1.0, "Flake"),
            ("Base roughness", "float", 0.35, 0.0, 1.0, "Base"),
            ("Clearcoat", "float", 1.0, 0.0, 1.0, "Clearcoat"),
            ("Clearcoat roughness", "float", 0.03, 0.0, 1.0, "Clearcoat"),
            ("Orange peel", "float", 0.2, 0.0, 1.0, "Clearcoat"),
            ("Orange peel scale", "float", 0.5, 0.0, 1.0, "Clearcoat"),
        ],
    },
    "METAL": {
        "label": "Metal", "version": 3, "colour": "Colour",
        "keys": ("Roughness", "Brushed", "Brush direction"),
        "inputs": [
            ("Colour", "colour", (0.91, 0.92, 0.92, 1), None, None, "Surface"),
            ("Edge tint", "colour", (1.0, 1.0, 1.0, 1), None, None, "Surface"),
            ("Roughness", "float", 0.1, 0.0, 1.0, "Surface"),
            ("Cast grain", "float", 0.0, 0.0, 1.0, "Surface"),
            ("Brushed", "float", 0.0, 0.0, 1.0, "Brushing"),
            ("Brush direction", "float", 0.0, 0.0, 1.0, "Brushing"),
            ("Brush scale", "float", 0.5, 0.0, 1.0, "Brushing"),
            ("Clearcoat", "float", 0.0, 0.0, 1.0, "Clearcoat"),
            ("Clearcoat roughness", "float", 0.05, 0.0, 1.0, "Clearcoat"),
        ],
    },
    # 0.9: glass on the UDIN Windshield, split by engine (_build_glass). Cycles
    # gets the real thing; Eevee gets a thin pane that doesn't refract (no
    # magnification, sharp, visible through other glass) unless the part is
    # solid or frosted. The "Linked" inputs are set by Plinth from the part's
    # shape and roughness (glass_state), never shown.
    "GLASS": {
        "label": "Glass", "version": 3, "colour": None, "glass": True,
        "outputs": ("Surface", "Volume", "Eevee"),
        "keys": ("Roughness", "Reflection", "Imperfections"),
        "inputs": [
            ("Tint", "colour", (1.0, 1.0, 1.0, 1), None, None, "Glass"),
            ("Roughness", "float", 0.0, 0.0, 1.0, "Glass"),
            ("IOR", "float", 1.52, 1.0, 2.5, "Glass"),
            ("Reflection", "float", 0.5, 0.0, 1.0, "Glass"),
            ("Thin pane", "bool", True, None, None, "Glass"),
            ("Absorption colour", "colour", (0.8, 0.9, 0.85, 1), None, None, "Absorption"),
            ("Absorption", "float", 0.0, 0.0, 10.0, "Absorption"),
            ("Imperfections", "float", 0.0, 0.0, 1.0, "Wear"),
            ("Wear scale", "float", 3.0, 0.1, 10.0, "Wear"),
            ("Smudges", "float", 1.0, 0.0, 1.0, "Wear"),
            ("Scratches", "float", 1.0, 0.0, 1.0, "Wear"),
            ("Stains", "float", 0.25, 0.0, 1.0, "Wear"),
            ("Open shell", "bool", False, None, None, "Linked"),
            ("Eevee refraction", "bool", False, None, None, "Linked"),
        ],
    },
    "PLASTIC": {
        "label": "Plastic", "version": 3, "colour": "Colour",
        "keys": ("Roughness", "Texture depth", "Texture scale"),
        "inputs": [
            ("Colour", "colour", (0.02, 0.02, 0.02, 1), None, None, "Surface"),
            ("Roughness", "float", 0.4, 0.0, 1.0, "Surface"),
            ("Soft touch", "float", 0.0, 0.0, 1.0, "Texture"),
            ("Texture depth", "float", 0.0, 0.0, 1.0, "Texture"),
            ("Texture scale", "float", 0.5, 0.0, 1.0, "Texture"),
            ("Clearcoat", "float", 0.0, 0.0, 1.0, "Clearcoat"),
        ],
    },
    "RUBBER": {
        "label": "Rubber", "version": 2, "colour": "Colour",
        "keys": ("Tyre shine", "Dust", "Roughness"),
        "inputs": [
            ("Colour", "colour", (0.018, 0.018, 0.019, 1), None, None, "Rubber"),
            ("Roughness", "float", 0.65, 0.0, 1.0, "Rubber"),
            ("Tyre shine", "float", 0.0, 0.0, 1.0, "Rubber"),
            ("Micro texture", "float", 0.5, 0.0, 1.0, "Rubber"),
            ("Sheen", "float", 0.25, 0.0, 1.0, "Rubber"),
            ("Dust", "float", 0.3, 0.0, 1.0, "Dust"),
            ("Dust colour", "colour", (0.32, 0.30, 0.27, 1), None, None, "Dust"),
            ("Dust size (cm)", "log", 5.0, 0.5, 50.0, "Dust"),
        ],
    },
    "CARBON": {
        "label": "Carbon fibre", "version": 2, "colour": "Colour",
        "keys": ("Weave size (cm)", "Weave contrast", "Clearcoat"),
        "inputs": [
            ("Colour", "colour", (0.03, 0.03, 0.035, 1), None, None, "Weave"),
            ("Weave size (cm)", "log", 1.0, 0.1, 20.0, "Weave"),
            ("Weave contrast", "float", 0.6, 0.0, 1.0, "Weave"),
            ("Fibre shine", "float", 0.6, 0.0, 1.0, "Weave"),
            ("Clearcoat", "float", 1.0, 0.0, 1.0, "Clearcoat"),
            ("Clearcoat roughness", "float", 0.03, 0.0, 1.0, "Clearcoat"),
        ],
    },
    "LEATHER": {
        "label": "Leather", "version": 3, "colour": "Colour",
        "keys": ("Grain depth", "Grain scale", "Roughness"),
        "inputs": [
            ("Colour", "colour", (0.08, 0.05, 0.035, 1), None, None, "Surface"),
            ("Roughness", "float", 0.55, 0.0, 1.0, "Surface"),
            ("Grain depth", "float", 0.3, 0.0, 1.0, "Grain"),
            ("Grain scale", "float", 0.5, 0.0, 1.0, "Grain"),
            ("Sheen", "float", 0.2, 0.0, 1.0, "Surface"),
            ("Perforation", "float", 0.0, 0.0, 1.0, "Perforation"),
            ("Hole spacing (mm)", "log", 5.0, 2.0, 20.0, "Perforation"),
        ],
    },
    # Emissive parts: LEDs, tail lights, screens, neon. The surface under the
    # glow (what it looks like switched off, its lens) is a normal material.
    "GLOW": {
        "label": "Glow", "version": 1, "colour": "Glow colour",
        "keys": ("Brightness", "Edge fade", "LED dots"),
        "inputs": [
            ("Glow colour", "colour", (1.0, 0.95, 0.9, 1), None, None, "Colour"),
            ("Surface colour", "colour", (0.02, 0.02, 0.02, 1), None, None, "Colour"),
            ("Brightness", "float", 10.0, 0.0, 100.0, "Glow"),
            ("Edge fade", "float", 0.0, 0.0, 1.0, "Glow"),
            ("LED dots", "float", 0.0, 0.0, 1.0, "Pattern"),
            ("Dot size (cm)", "log", 0.8, 0.1, 5.0, "Pattern"),
            ("Roughness", "float", 0.3, 0.0, 1.0, "Surface"),
            ("Lens", "float", 0.0, 0.0, 1.0, "Surface"),
        ],
    },
    # Image assets already made for the part (instrument clusters, screens,
    # labels, printed panels): the image on the part's own UVs, with a size,
    # rotation and offset, over a backing colour where it's transparent. The
    # image node and its mapping live in the material (a group can't hold a
    # per-part image); the group's "Linked" inputs are wired from them.
    "IMAGE": {
        "label": "Image", "version": 1, "colour": None,
        "keys": ("Brightness", "Glow", "Roughness"),
        "inputs": [
            ("Image colour", "colour", (0.8, 0.8, 0.8, 1), None, None, "Linked"),
            ("Image alpha", "float", 1.0, 0.0, 1.0, "Linked"),
            ("Tint", "colour", (1.0, 1.0, 1.0, 1), None, None, "Colour"),
            ("Backing colour", "colour", (0.02, 0.02, 0.02, 1), None, None, "Colour"),
            ("Size", "float", 1.0, 0.1, 10.0, "Image"),
            ("Rotation", "float", 0.0, 0.0, 360.0, "Image"),
            ("Offset X", "float", 0.0, -1.0, 1.0, "Image"),
            ("Offset Y", "float", 0.0, -1.0, 1.0, "Image"),
            ("Brightness", "float", 1.0, 0.0, 2.0, "Image"),
            ("Roughness", "float", 0.4, 0.0, 1.0, "Surface"),
            ("Metallic", "float", 0.0, 0.0, 1.0, "Surface"),
            ("Clearcoat", "float", 0.0, 0.0, 1.0, "Surface"),
            ("Glow", "float", 0.0, 0.0, 50.0, "Surface"),
        ],
    },
    # Tyres: tread on the running surface, sidewall on the sides. The pattern
    # lives in the tyre's own coordinates (the plinth_tyre mesh attribute, fitted
    # by fit_tyre when the preset is applied: around, across, out from the rim),
    # so it turns with the wheel and fits any tyre size. Not world space, on
    # purpose: a tread fixed in the world would slide as the wheel rotates.
    "TYRE": {
        "label": "Tyre", "version": 2, "colour": "Colour",
        "keys": ("Tread depth", "Sidewall shine", "Dust"),
        "inputs": [
            ("Colour", "colour", (0.018, 0.018, 0.019, 1), None, None, "Surface"),
            ("Dust colour", "colour", (0.32, 0.30, 0.27, 1), None, None, "Surface"),
            ("Tread depth", "float", 0.6, 0.0, 1.0, "Tread"),
            ("Blocks around", "float", 60.0, 0.0, 120.0, "Tread"),
            ("Grooves", "float", 3.0, 0.0, 5.0, "Tread"),
            ("Groove width", "float", 0.4, 0.0, 1.0, "Tread"),
            ("Direction", "float", 0.0, 0.0, 1.0, "Tread"),
            ("Sipes", "float", 0.5, 0.0, 1.0, "Tread"),
            ("Roughness", "float", 0.7, 0.0, 1.0, "Surface"),
            ("Wear", "float", 0.1, 0.0, 1.0, "Surface"),
            ("Sidewall shine", "float", 0.15, 0.0, 1.0, "Surface"),
            ("Sidewall ribs", "float", 0.3, 0.0, 1.0, "Surface"),
            ("Dust", "float", 0.2, 0.0, 1.0, "Surface"),
            ("Whitewall", "float", 0.0, 0.0, 1.0, "Sidewall"),
            ("Whitewall colour", "colour", (0.82, 0.82, 0.8, 1), None, None, "Sidewall"),
        ],
    },
    # Brake discs: a friction ring around a hat, in the disc's own coordinates
    # (the plinth_disc mesh attribute, fitted by fit_disc: around, across the
    # ring's thickness, out from the centre as a fraction of the radius), so
    # holes and slots turn with the wheel. Drilled holes and slots are cut into
    # the faces, vents show on the edge; machining, heat tint, rust and the
    # carbon-ceramic speckle are surface. Object space on purpose, like tyres.
    "DISC": {
        "label": "Brake disc", "version": 1, "colour": "Colour",
        "keys": ("Hat size", "Heat tint", "Rust"),
        "inputs": [
            ("Colour", "colour", (0.30, 0.30, 0.31, 1), None, None, "Colour"),
            ("Hat colour", "colour", (0.30, 0.30, 0.31, 1), None, None, "Colour"),
            ("Hat size", "float", 0.55, 0.0, 0.95, "Disc"),
            ("Drilled holes", "float", 0.0, 0.0, 4.0, "Disc"),
            ("Holes around", "float", 24.0, 4.0, 60.0, "Disc"),
            ("Hole size", "float", 0.5, 0.0, 1.0, "Disc"),
            ("Slots", "float", 0.0, 0.0, 24.0, "Disc"),
            ("Vents", "float", 0.0, 0.0, 80.0, "Disc"),
            ("Machining", "float", 0.6, 0.0, 1.0, "Surface"),
            ("Roughness", "float", 0.3, 0.0, 1.0, "Surface"),
            ("Heat tint", "float", 0.0, 0.0, 1.0, "Surface"),
            ("Rust", "float", 0.0, 0.0, 1.0, "Surface"),
            ("Speckle", "float", 0.0, 0.0, 1.0, "Surface"),
        ],
    },
}

# Fabric (0.9): suede/Alcantara, woven cloth, knit, open mesh and velvet in one
# type. Pattern picks the construction (0 brushed nap, 1 woven, 2 knit, 3 open
# mesh); threads are a real size, projected from three sides like carbon.
# Fuzz is Principled sheen (the soft rim light cloth has); nap is the light and
# dark patches of a brushed pile, along Nap direction.
TYPES["FABRIC"] = {
    "label": "Fabric", "version": 1, "colour": "Colour",
    "keys": ("Fuzz", "Nap", "Thread size (cm)"),
    "inputs": [
        ("Colour", "colour", (0.05, 0.05, 0.055, 1), None, None, "Colour"),
        ("Sheen colour", "colour", (0.5, 0.5, 0.5, 1), None, None, "Colour"),
        ("Pattern", "float", 0.0, 0.0, 3.0, "Weave"),
        ("Thread size (cm)", "log", 0.1, 0.02, 2.0, "Weave"),
        ("Weave depth", "float", 0.5, 0.0, 1.0, "Weave"),
        ("Openness", "float", 0.0, 0.0, 1.0, "Weave"),
        ("Fuzz", "float", 0.6, 0.0, 1.0, "Pile"),
        ("Nap", "float", 0.3, 0.0, 1.0, "Pile"),
        ("Nap direction", "float", 0.0, 0.0, 1.0, "Pile"),
        ("Roughness", "float", 0.9, 0.0, 1.0, "Pile"),
    ],
}
# Wood (0.9): growth rings and streaks along the grain, pores cut across it.
# Open-pore wood shows the pores; a gloss lacquer (Clearcoat) floats over them.
TYPES["WOOD"] = {
    "label": "Wood", "version": 1, "colour": "Light colour",
    "keys": ("Pores", "Clearcoat", "Grain size (cm)"),
    "inputs": [
        ("Light colour", "colour", (0.17, 0.085, 0.04, 1), None, None, "Colour"),
        ("Dark colour", "colour", (0.06, 0.026, 0.012, 1), None, None, "Colour"),
        ("Grain size (cm)", "log", 0.8, 0.1, 10.0, "Grain"),
        ("Figure", "float", 0.4, 0.0, 1.0, "Grain"),
        ("Grain direction", "float", 0.0, 0.0, 1.0, "Grain"),
        ("Pores", "float", 0.3, 0.0, 1.0, "Surface"),
        ("Roughness", "float", 0.45, 0.0, 1.0, "Surface"),
        ("Clearcoat", "float", 1.0, 0.0, 1.0, "Lacquer"),
        ("Clearcoat roughness", "float", 0.04, 0.0, 1.0, "Lacquer"),
    ],
}
# Stone & ceramic (0.9): concrete, marble, glazed and matte ceramic, card. A
# mottled mix of two colours, optional veins and speckle, pits, and a glaze.
TYPES["STONE"] = {
    "label": "Stone and ceramic", "version": 1, "colour": "Colour",
    "keys": ("Mottle", "Pits", "Glaze"),
    "inputs": [
        ("Colour", "colour", (0.42, 0.41, 0.39, 1), None, None, "Colour"),
        ("Second colour", "colour", (0.28, 0.27, 0.26, 1), None, None, "Colour"),
        ("Mottle", "float", 0.5, 0.0, 1.0, "Pattern"),
        ("Mottle size (cm)", "log", 8.0, 0.2, 100.0, "Pattern"),
        ("Veins", "float", 0.0, 0.0, 1.0, "Pattern"),
        ("Speckle", "float", 0.0, 0.0, 1.0, "Pattern"),
        ("Pits", "float", 0.3, 0.0, 1.0, "Surface"),
        ("Roughness", "float", 0.8, 0.0, 1.0, "Surface"),
        ("Glaze", "float", 0.0, 0.0, 1.0, "Glaze"),
        ("Glaze roughness", "float", 0.05, 0.0, 1.0, "Glaze"),
    ],
}


# --- UDIN material types (Phase 19) ------------------------------------------------
# Automotive materials from the UDIN SHADERS free pack (MIT, (c) 2024 Odilon
# Loiez & Esa Mustonen; plinth/assets/udin/LICENSE), shipped as node groups in
# plinth/assets/udin/plinth_udin.blend (tools/build_udin.py: world-space
# coordinates, Plinth_UDIN_* names). Each type lists the controls Plinth shows
# (plain words, at most six per page tab) and which group socket each one
# drives: UDIN reuses names inside a group (two "roughness", two "scale").

UDIN_LIBRARY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "udin", "plinth_udin.blend")
# Blackout: a surface that swallows light, like Vantablack (99.96% absorbed):
# no diffuse, no specular, no coat, so a part reads as a flat black hole.
# Blackness 1 is total; Reflection gives back a faint sheen if the shape
# must still read.
TYPES["BLACKOUT"] = {
    "label": "Blackout", "version": 1, "colour": "Colour",
    "keys": ("Blackness", "Reflection", "Roughness"),
    "inputs": [
        ("Colour", "colour", (0.0, 0.0, 0.0, 1), None, None, "Surface"),
        ("Blackness", "float", 1.0, 0.0, 1.0, "Surface"),
        ("Reflection", "float", 0.0, 0.0, 1.0, "Highlight"),
        ("Roughness", "float", 1.0, 0.0, 1.0, "Highlight"),
    ],
}

# Texture set (0.13): the image maps a model came with (or any you pick), each
# on its own image node in the material, wired into the group's "Linked"
# inputs like the Image type (textures.py finds them and sync_textures wires
# them). Missing maps leave the Linked defaults: a flat normal, no glow.
TYPES["TEXTURES"] = {
    "label": "Texture set", "version": 1, "colour": None,
    "keys": ("Roughness", "Normal strength", "Tiling"),
    "inputs": [
        ("Map colour", "colour", (0.8, 0.8, 0.8, 1), None, None, "Linked"),
        ("Map alpha", "float", 1.0, 0.0, 1.0, "Linked"),
        ("Map metallic", "float", 0.0, 0.0, 1.0, "Linked"),
        ("Map roughness", "float", 0.5, 0.0, 1.0, "Linked"),
        ("Map normal", "colour", (0.5, 0.5, 1.0, 1), None, None, "Linked"),
        ("Map height", "float", 0.5, 0.0, 1.0, "Linked"),
        ("Map occlusion", "float", 1.0, 0.0, 1.0, "Linked"),
        ("Map glow", "colour", (0.0, 0.0, 0.0, 1), None, None, "Linked"),
        ("Tiling", "float", 1.0, 0.1, 20.0, "Maps"),
        ("Tile size (cm)", "log", 100.0, 1.0, 1000.0, "Maps"),
        ("Normal strength", "float", 1.0, 0.0, 2.0, "Maps"),
        ("Bump strength", "float", 0.2, 0.0, 1.0, "Maps"),
        ("Occlusion", "float", 1.0, 0.0, 1.0, "Maps"),
        ("Roughness", "float", 1.0, 0.0, 2.0, "Surface"),
        ("Metallic", "float", 1.0, 0.0, 1.0, "Surface"),
        ("Glow", "float", 1.0, 0.0, 50.0, "Surface"),
        ("Opacity", "float", 1.0, 0.0, 1.0, "Surface"),
        ("Clearcoat", "float", 0.0, 0.0, 1.0, "Surface"),
    ],
}

UDIN_VERSION = "2.0.2+plinth1"


def _udin(label, group, colour, keys, controls, glass=False):
    """controls: [(name, kind, default, lo, hi, section, socket index)]"""
    return {"label": label, "version": 1, "library": group, "colour": colour, "keys": keys,
            "glass": glass, "inputs": [c[:6] for c in controls],
            "sockets": {c[0]: c[6] for c in controls}}


def _wear(first, intensity=0.03):
    """UDIN's shared imperfection controls: intensity, scale, smudges, scratches, stains."""
    return [("Imperfections", "float", intensity, 0.0, 1.0, "Wear", first),
            ("Wear scale", "float", 3.0, 0.1, 10.0, "Wear", first + 1),
            ("Smudges", "float", 1.0, 0.0, 1.0, "Wear", first + 2),
            ("Scratches", "float", 1.0, 0.0, 1.0, "Wear", first + 3),
            ("Stains", "float", 0.25, 0.0, 1.0, "Wear", first + 4)]


def _coat(rough, peel, peel_scale):
    return [("Clearcoat roughness", "float", 0.0, 0.0, 1.0, "Paint", rough),
            ("Orange peel", "float", 0.0, 0.0, 1.0, "Paint", peel),
            ("Orange peel scale", "float", 150.0, 10.0, 400.0, "Paint", peel_scale)]


TYPES.update({
    "UDIN_METALLIC": _udin("Metallic car paint", "Plinth_UDIN_Metallic", "Colour",
                           ("Flake roughness", "Clearcoat roughness", "Imperfections"), [
        ("Colour", "colour", (0.5, 0.0, 0.002, 1), None, None, "Colour", 1),
        ("Flake roughness", "float", 0.3, 0.0, 1.0, "Paint", 2)] + _coat(4, 6, 7) + _wear(9)),
    "UDIN_FLAKES": _udin("Heavy flake paint", "Plinth_UDIN_Flakes", "Colour",
                         ("Flake roughness", "Flake density", "Clearcoat roughness"), [
        ("Colour", "colour", (0.024, 0.024, 0.024, 1), None, None, "Colour", 1),
        ("Flake roughness", "float", 0.3, 0.0, 1.0, "Paint", 2),
        ("Flake density", "float", 20.0, 2.0, 200.0, "Paint", 3)] + _coat(5, 7, 8) + _wear(10)),
    "UDIN_PEARL": _udin("Pearl car paint", "Plinth_UDIN_Pearl", "Colour",
                        ("Pearl blend", "Clearcoat roughness", "Imperfections"), [
        ("Colour", "colour", (0.128, 0.0, 0.376, 1), None, None, "Colour", 1),
        ("Pearl colour", "colour", (0.0, 1.0, 0.014, 1), None, None, "Colour", 2),
        ("Pearl blend", "float", 0.65, 0.0, 1.0, "Paint", 3)] + _coat(5, 7, 8) + _wear(10)),
    "UDIN_SOLID": _udin("Solid car paint", "Plinth_UDIN_Solid", "Colour",
                        ("Clearcoat roughness", "Orange peel", "Imperfections"), [
        ("Colour", "colour", (0.024, 0.024, 0.024, 1), None, None, "Colour", 0)] + _coat(2, 4, 5) + _wear(7)),
    "UDIN_IRIDESCENT": _udin("Iridescent paint", "Plinth_UDIN_Iridescent", "Colour",
                             ("Colour shift", "Flake roughness", "Clearcoat roughness"), [
        ("Colour", "colour", (0.004, 0.051, 0.001, 1), None, None, "Colour", 0),
        ("Second colour", "colour", (0.0, 0.12, 0.497, 1), None, None, "Colour", 1),
        ("Colour shift", "float", 0.57, 0.0, 1.0, "Paint", 2),
        ("Flake roughness", "float", 0.25, 0.0, 1.0, "Paint", 3)] + _coat(5, 7, 8) + _wear(10)),
    "UDIN_CARBON": _udin("Carbon weave", "Plinth_UDIN_Carbon", "Fibre colour",
                         ("Weave", "Weave density", "Clearcoat roughness"), [
        ("Fibre colour", "colour", (0.012, 0.012, 0.012, 1), None, None, "Colour", 20),
        ("Tint", "colour", (1.0, 1.0, 1.0, 1), None, None, "Colour", 21),
        ("Weave", "float", 2.0, 1.0, 5.0, "Weave", 1),
        ("Weave density", "float", 600.0, 100.0, 2000.0, "Weave", 2),
        ("Fibre curve", "float", 0.0, 0.0, 1.0, "Weave", 3),
        ("Rotation", "float", 180.0, 0.0, 360.0, "Weave", 6),
        ("Fishbone", "float", 1.0, 0.0, 1.0, "Weave", 7),
        ("Clearcoat roughness", "float", 0.0, 0.0, 1.0, "Weave", 9)] + _wear(12, 0.0)),
    "UDIN_FORGED": _udin("Forged carbon", "Plinth_UDIN_Forged", "Fibre colour",
                         ("Chip size", "Fibre roughness", "Clearcoat roughness"), [
        ("Fibre colour", "colour", (0.025, 0.025, 0.025, 1), None, None, "Colour", 20),
        ("Tint", "colour", (1.0, 1.0, 1.0, 1), None, None, "Colour", 21),
        ("Chip size", "float", 5.0, 0.5, 20.0, "Pattern", 1),
        ("Waviness", "float", 1.0, 0.0, 1.0, "Pattern", 2),
        ("Fibre roughness", "float", 0.35, 0.0, 1.0, "Pattern", 3),
        ("Clearcoat roughness", "float", 0.0, 0.0, 1.0, "Pattern", 7),
        ("Orange peel", "float", 0.0, 0.0, 1.0, "Pattern", 9),
        ("Orange peel scale", "float", 150.0, 10.0, 400.0, "Pattern", 10)] + _wear(12, 0.05)),
    # Legacy (0.7-0.8): presets moved to GLASS v3, which wraps this group;
    # materials still using it are moved over by migrate().
    "UDIN_WINDSHIELD": _udin("Automotive glass", "Plinth_UDIN_Windshield", "Tint",
                             ("Roughness", "IOR", "Imperfections"), [
        ("Tint", "colour", (0.648, 0.752, 0.787, 1), None, None, "Colour", 1),
        ("Roughness", "float", 0.0, 0.0, 1.0, "Glass", 2),
        ("IOR", "float", 1.45, 1.0, 2.5, "Glass", 3)] + _wear(5, 0.025), glass=True),
    "UDIN_CHROME": _udin("Coated chrome", "Plinth_UDIN_Chrome", "Colour",
                         ("Imperfections", "Smudges", "Scratches"), [
        ("Colour", "colour", (1.0, 1.0, 1.0, 1), None, None, "Colour", 1)] + _wear(3, 0.05)),
    "UDIN_PLASTIC": _udin("Glossy plastic", "Plinth_UDIN_Plastic", "Colour",
                          ("Roughness", "Imperfections", "Scratches"), [
        ("Colour", "colour", (0.005, 0.015, 0.051, 1), None, None, "Colour", 1),
        ("Roughness", "float", 0.0, 0.0, 1.0, "Plastic", 2)] + _wear(4, 0.05)),
})
GLASS_TYPES = {k for k, v in TYPES.items() if v.get("glass")} | {"GLASS"}


# Eevee material settings for glass: dithered (stochastic) transparency, light
# through it in shadows, and thickness (slab for panes, sphere for solids).
# Raytraced refraction is on only where the Eevee branch refracts (solid or
# frosted glass, glass_state): Eevee sees one refraction layer, so refracting
# panes hid the glass behind them and magnified what they showed.
GLASS_MATERIAL = {"surface_render_method": "DITHERED", "use_transparent_shadow": True}
GLASS_SETTINGS = tuple(GLASS_MATERIAL) + ("use_raytrace_refraction", "thickness_mode",
                                          "show_transparent_back")
FROSTED = 0.12          # roughness above which a pane needs refraction in Eevee (the blur)
EEVEE_OUT = "Plinth_Out_Eevee"     # glass: the Eevee-only Material Output

# --- Presets -------------------------------------------------------------------------
# keep_colour: switching to this preset keeps the part's colour (paint is the
# colour; a named metal like Gold brings its own).

def _p(label, description, category, type_, values, keep_colour=False, shiny=True):
    return {"label": label, "description": description, "category": category, "type": type_,
            "values": values, "keep_colour": keep_colour, "shiny": shiny}


PRESETS = {
    # Paint
    "PAINT_SOLID": _p("Solid gloss", "Classic single colour under a deep, clear coat", "PAINT",
                      "UDIN_SOLID", {"Clearcoat roughness": 0.0, "Orange peel": 0.15,
                                     "Imperfections": 0.03}, keep_colour=True),
    "PAINT_METALLIC": _p("Metallic", "Fine metallic flake that sparkles under a clear coat",
                         "PAINT", "UDIN_METALLIC", {"Flake roughness": 0.3, "Clearcoat roughness": 0.0,
                                                    "Orange peel": 0.15, "Imperfections": 0.03},
                         keep_colour=True),
    "PAINT_PEARL": _p("Pearl", "Pearlescent: a second colour glows toward the edges", "PAINT",
                      "UDIN_PEARL", {"Pearl colour": (0.85, 0.75, 0.55, 1), "Pearl blend": 0.65,
                                     "Imperfections": 0.03}, keep_colour=True),
    "PAINT_CANDY": _p("Candy", "Deep, glowing colour over a bright flake base", "PAINT", "PAINT",
                      {"Metallic flake": 0.9, "Flake colour": (1, 1, 1, 1), "Flake sparkle": 0.3,
                       "Base roughness": 0.15, "Flip amount": 0.5, "Flip colour": (0.02, 0.0, 0.0, 1),
                       "Clearcoat": 1.0, "Clearcoat roughness": 0.02, "Pearl": 0.0}, keep_colour=True),
    "PAINT_SATIN": _p("Satin wrap", "Low-sheen vinyl wrap", "PAINT", "PAINT",
                      {"Metallic flake": 0.0, "Clearcoat": 0.3, "Clearcoat roughness": 0.35,
                       "Base roughness": 0.45, "Orange peel": 0.0, "Flip amount": 0.0, "Pearl": 0.0},
                      keep_colour=True),
    "PAINT_MATTE": _p("Matte", "Flat matte paint: soft, satin-free highlights", "PAINT", "UDIN_SOLID",
                      {"Clearcoat roughness": 0.8, "Orange peel": 0.0, "Imperfections": 0.03},
                      keep_colour=True),
    "PAINT_FLAKE": _p("Heavy flake", "Coarse, glittering metallic flake", "PAINT", "UDIN_FLAKES",
                      {"Flake roughness": 0.3, "Flake density": 20.0, "Imperfections": 0.03},
                      keep_colour=True),
    "PAINT_IRIDESCENT": _p("Iridescent", "Colour-shifting flip paint: two colours by angle", "PAINT",
                           "UDIN_IRIDESCENT", {"Colour": (0.004, 0.051, 0.001, 1),
                                               "Second colour": (0.0, 0.12, 0.497, 1),
                                               "Colour shift": 0.57, "Imperfections": 0.03}),
    # Metal
    "METAL_CHROME": _p("Chrome", "Coated mirror chrome for trim and badges", "METAL", "UDIN_CHROME",
                       {"Colour": (1.0, 1.0, 1.0, 1), "Imperfections": 0.03}),
    "METAL_POLISHED": _p("Polished alloy", "Bright polished aluminium, like alloy wheels", "METAL", "METAL",
                         {"Colour": (0.91, 0.92, 0.92, 1), "Roughness": 0.08, "Brushed": 0.0}),
    "METAL_BRUSHED": _p("Brushed alloy", "Fine directional brushing", "METAL", "METAL",
                        {"Colour": (0.9, 0.91, 0.92, 1), "Roughness": 0.25, "Brushed": 0.8,
                         "Brush scale": 0.5}),
    "METAL_STEEL": _p("Brushed steel", "Darker, coarser brushed steel", "METAL", "METAL",
                      {"Colour": (0.55, 0.56, 0.58, 1), "Roughness": 0.3, "Brushed": 0.7,
                       "Brush scale": 0.3}),
    "METAL_GUNMETAL": _p("Gunmetal", "Dark satin wheel finish", "METAL", "METAL",
                         {"Colour": (0.18, 0.19, 0.2, 1), "Roughness": 0.35, "Brushed": 0.0,
                          "Clearcoat": 0.6, "Clearcoat roughness": 0.1}),
    "METAL_GOLD": _p("Gold", "Warm polished gold", "METAL", "METAL",
                     {"Colour": (1.0, 0.77, 0.34, 1), "Roughness": 0.12, "Brushed": 0.0}),
    "METAL_COPPER": _p("Copper", "Rosy polished copper", "METAL", "METAL",
                       {"Colour": (0.95, 0.64, 0.54, 1), "Roughness": 0.15, "Brushed": 0.0}),
    "METAL_TITANIUM": _p("Burnt titanium", "Heat-tinted exhaust titanium", "METAL", "METAL",
                         {"Colour": (0.62, 0.58, 0.55, 1), "Edge tint": (0.45, 0.35, 0.85, 1),
                          "Roughness": 0.2, "Brushed": 0.3}),
    "METAL_SATIN_CHROME": _p("Satin chrome", "Chrome with a soft, blurred reflection: trim, handles, taps",
                             "METAL", "METAL", {"Colour": (0.86, 0.87, 0.88, 1), "Edge tint": (1, 1, 1, 1),
                                               "Roughness": 0.2, "Brushed": 0.0, "Cast grain": 0.0,
                                               "Clearcoat": 0.0}),
    "METAL_ANODISED": _p("Anodised blue", "Coloured anodised aluminium: bike parts, phones, tools",
                         "METAL", "METAL", {"Colour": (0.08, 0.25, 0.7, 1), "Edge tint": (0.6, 0.75, 1.0, 1),
                                           "Roughness": 0.28, "Brushed": 0.15, "Cast grain": 0.0,
                                           "Clearcoat": 0.0}),
    "METAL_ANODISED_RED": _p("Anodised red", "Red anodised aluminium with a fine satin sheen", "METAL",
                             "METAL", {"Colour": (0.7, 0.04, 0.03, 1), "Edge tint": (1.0, 0.5, 0.45, 1),
                                       "Roughness": 0.28, "Brushed": 0.15, "Cast grain": 0.0,
                                       "Clearcoat": 0.0}),
    "METAL_ANODISED_BLACK": _p("Anodised black", "Deep black anodised aluminium: camera bodies, laptops",
                               "METAL", "METAL", {"Colour": (0.03, 0.03, 0.032, 1),
                                                 "Edge tint": (0.3, 0.3, 0.32, 1), "Roughness": 0.32,
                                                 "Brushed": 0.1, "Cast grain": 0.0, "Clearcoat": 0.0}),
    "METAL_TITANIUM_BRUSHED": _p("Satin titanium", "Warm grey titanium with a fine directional brush",
                                 "METAL", "METAL", {"Colour": (0.54, 0.51, 0.47, 1), "Edge tint": (1, 1, 1, 1),
                                                   "Roughness": 0.26, "Brushed": 0.8, "Brush scale": 0.6,
                                                   "Cast grain": 0.0}),
    "METAL_BRASS": _p("Brass", "Yellow polished brass: fittings, watches, instruments", "METAL", "METAL",
                      {"Colour": (0.89, 0.71, 0.38, 1), "Edge tint": (1, 1, 1, 1), "Roughness": 0.14,
                       "Brushed": 0.0, "Cast grain": 0.0}),
    "METAL_GOLD_BRUSHED": _p("Brushed gold", "Satin brushed gold, as on watch cases and jewellery", "METAL",
                             "METAL", {"Colour": (1.0, 0.77, 0.34, 1), "Edge tint": (1, 1, 1, 1),
                                       "Roughness": 0.24, "Brushed": 0.75, "Brush scale": 0.7,
                                       "Cast grain": 0.0}),
    "METAL_CAST": _p("Cast aluminium", "Sand-cast alloy with a fine grainy finish: wheels, engine parts",
                     "METAL", "METAL", {"Colour": (0.78, 0.79, 0.8, 1), "Edge tint": (1, 1, 1, 1),
                                       "Roughness": 0.42, "Brushed": 0.0, "Cast grain": 0.7,
                                       "Clearcoat": 0.0}),
    # Glass
    # All glass is one type (UDIN Windshield underneath). Pane or solid comes
    # from the part's shape unless a preset says otherwise (Crystal).
    "GLASS_CLEAR": _p("Clear glass", "Clear, colourless glass: a pane or a solid, from the part's shape",
                      "GLASS", "GLASS",
                      {"Tint": (1, 1, 1, 1), "Roughness": 0.0, "IOR": 1.52, "Absorption": 0.0,
                       "Imperfections": 0.0}),
    "GLASS_WINDOW": _p("Window", "Automotive window glass with a faint green-blue tint", "GLASS",
                       "GLASS", {"Tint": (0.648, 0.752, 0.787, 1), "Roughness": 0.0, "IOR": 1.52,
                                 "Imperfections": 0.01}),
    "GLASS_PRIVACY": _p("Privacy tint", "Dark tinted rear window", "GLASS", "GLASS",
                        {"Tint": (0.08, 0.085, 0.09, 1), "Roughness": 0.0, "IOR": 1.52,
                         "Imperfections": 0.01}),
    "GLASS_BRONZE": _p("Bronze tint", "Warm bronze architectural tint", "GLASS", "GLASS",
                       {"Tint": (0.55, 0.38, 0.22, 1), "Roughness": 0.0, "IOR": 1.52,
                        "Imperfections": 0.01}),
    "GLASS_FROSTED": _p("Frosted", "Blurred, sandblasted glass", "GLASS", "GLASS",
                        {"Tint": (1, 1, 1, 1), "Roughness": 0.35, "IOR": 1.52, "Absorption": 0.0,
                         "Imperfections": 0.0}),
    "GLASS_LENS": _p("Headlight lens", "Clear polycarbonate headlight lens", "GLASS", "GLASS",
                     {"Tint": (1, 1, 1, 1), "Roughness": 0.02, "IOR": 1.58, "Absorption": 0.0,
                      "Imperfections": 0.005}),
    "GLASS_SMOKED": _p("Smoked lens", "Dark smoked lens for lights and visors", "GLASS", "GLASS",
                       {"Tint": (0.18, 0.18, 0.19, 1), "Roughness": 0.02, "IOR": 1.58,
                        "Absorption": 2.0, "Absorption colour": (0.3, 0.3, 0.31, 1),
                        "Imperfections": 0.005}),
    "GLASS_AMBER": _p("Amber lens", "Indicator amber: colour deepens with thickness", "GLASS", "GLASS",
                      {"Tint": (1.0, 0.5, 0.08, 1), "Roughness": 0.02, "IOR": 1.58,
                       "Absorption": 3.0, "Absorption colour": (1.0, 0.45, 0.05, 1),
                       "Imperfections": 0.005}),
    "GLASS_TAIL": _p("Tail-light red", "Deep red rear-light lens", "GLASS", "GLASS",
                     {"Tint": (0.8, 0.02, 0.02, 1), "Roughness": 0.02, "IOR": 1.58,
                      "Absorption": 4.0, "Absorption colour": (0.9, 0.02, 0.02, 1),
                      "Imperfections": 0.005}),
    "GLASS_ACRYLIC": _p("Acrylic", "Clear acrylic (Perspex): displays, cases, light guides",
                        "GLASS", "GLASS",
                        {"Tint": (1, 1, 1, 1), "Roughness": 0.0, "IOR": 1.49, "Absorption": 0.0,
                         "Imperfections": 0.02}),
    "GLASS_FROSTED_ACRYLIC": _p("Satin acrylic", "Satin-frosted acrylic: light diffusers, signs, cases",
                                "GLASS", "GLASS",
                                {"Tint": (0.96, 0.97, 0.98, 1), "Roughness": 0.3, "IOR": 1.49,
                                 "Absorption": 0.0, "Imperfections": 0.0}),
    "GLASS_CRYSTAL": _p("Crystal", "Solid lead crystal that bends light: perfume bottles, glassware",
                        "GLASS", "GLASS",
                        {"Tint": (1, 1, 1, 1), "Roughness": 0.0, "IOR": 1.6, "Thin pane": False,
                         "Absorption": 0.0, "Imperfections": 0.0}),
    # Trim
    "TRIM_GLOSS": _p("Gloss plastic", "Lacquer, plastic, ceramic. Clean, crisp highlights", "TRIM",
                     "PLASTIC", {"Roughness": 0.12, "Texture depth": 0.0, "Clearcoat": 0.5}),
    "TRIM_PIANO": _p("Piano black", "Deep gloss black trim", "TRIM", "UDIN_PLASTIC",
                     {"Colour": (0.004, 0.004, 0.004, 1), "Roughness": 0.0, "Imperfections": 0.03}),
    "TRIM_TEXTURED": _p("Textured trim", "Grained black bumper and dash plastic", "TRIM", "PLASTIC",
                        {"Colour": (0.03, 0.03, 0.03, 1), "Roughness": 0.6, "Texture depth": 0.4,
                         "Texture scale": 0.7, "Clearcoat": 0.0}, shiny=False),
    "TRIM_MATTE": _p("Matte plastic", "Paper, fabric, unglazed clay. No shine", "TRIM", "PLASTIC",
                     {"Roughness": 0.85, "Texture depth": 0.05, "Clearcoat": 0.0}, shiny=False),
    "TRIM_SOFT_TOUCH": _p("Soft-touch", "Rubberised, velvety matte plastic: remotes, mice, dashboards",
                          "TRIM", "PLASTIC", {"Colour": (0.025, 0.025, 0.027, 1), "Roughness": 0.7,
                                              "Soft touch": 0.8, "Texture depth": 0.06,
                                              "Texture scale": 0.9, "Clearcoat": 0.0}, shiny=False),
    "TRIM_SATIN": _p("Satin plastic", "Smooth satin plastic, between gloss and matte: appliances, toys",
                     "TRIM", "PLASTIC", {"Roughness": 0.35, "Soft touch": 0.0, "Texture depth": 0.0,
                                         "Clearcoat": 0.0}),
    "TRIM_SEAL": _p("Rubber seal", "Soft black door and window seal rubber", "TRIM", "RUBBER",
                    {"Colour": (0.015, 0.015, 0.016, 1), "Roughness": 0.6, "Tyre shine": 0.0,
                     "Dust": 0.05, "Micro texture": 0.7, "Sheen": 0.3}, shiny=False),
    "TRIM_TYRE": _p("Rubber", "Plain black tyre rubber with a satin sheen (no tread)", "TYRE", "RUBBER",
                    {"Colour": (0.018, 0.018, 0.019, 1), "Roughness": 0.65, "Tyre shine": 0.0,
                     "Dust": 0.15, "Micro texture": 0.5, "Sheen": 0.25}, shiny=False),
    "TRIM_TYRE_DUSTY": _p("Dusty rubber", "Driven-on rubber: grey bloom settled in the grooves", "TYRE",
                          "RUBBER", {"Colour": (0.02, 0.02, 0.02, 1), "Roughness": 0.75,
                                     "Tyre shine": 0.0, "Dust": 0.65, "Micro texture": 0.6,
                                     "Sheen": 0.35}, shiny=False),
    "TYRE_SIDEWALL": _p("Tyre sidewall", "Satin moulded sidewall rubber, for a sidewall that is its own part",
                        "TYRE", "RUBBER", {"Colour": (0.016, 0.016, 0.017, 1), "Roughness": 0.55,
                                           "Tyre shine": 0.15, "Dust": 0.08, "Micro texture": 0.6,
                                           "Sheen": 0.3}, shiny=False),
    "TYRE_SIDEWALL_DRESSED": _p("Shiny sidewall", "Tyre-shine dressed sidewall: wet-look deep black",
                                "TYRE", "RUBBER", {"Colour": (0.01, 0.01, 0.011, 1), "Roughness": 0.45,
                                                   "Tyre shine": 0.9, "Dust": 0.0, "Micro texture": 0.35,
                                                   "Sheen": 0.1}),
    "TYRE_SIDEWALL_DUSTY": _p("Dusty sidewall", "Sidewall after a dry drive: brown-grey road dust",
                              "TYRE", "RUBBER", {"Colour": (0.02, 0.02, 0.02, 1), "Roughness": 0.75,
                                                 "Tyre shine": 0.0, "Dust": 0.75, "Micro texture": 0.6,
                                                 "Sheen": 0.4, "Dust colour": (0.36, 0.32, 0.27, 1)},
                              shiny=False),
    "TRIM_TYRE_DRESSED": _p("Shiny rubber", "Freshly dressed, deep black showroom shine", "TYRE",
                            "RUBBER", {"Colour": (0.012, 0.012, 0.013, 1), "Roughness": 0.55,
                                       "Tyre shine": 0.8, "Dust": 0.0, "Micro texture": 0.3,
                                       "Sheen": 0.1}),
    "TRIM_CARBON": _p("Carbon fibre", "2x2 twill carbon weave under a glossy clear coat", "TRIM",
                      "UDIN_CARBON", {"Weave": 2.0, "Weave density": 600.0, "Clearcoat roughness": 0.0,
                                      "Imperfections": 0.0}),
    "TRIM_CARBON_MATTE": _p("Matte carbon", "Carbon weave with a satin, matte clear coat", "TRIM",
                            "UDIN_CARBON", {"Weave": 2.0, "Weave density": 600.0,
                                            "Clearcoat roughness": 0.52, "Imperfections": 0.0}),
    "TRIM_CARBON_FINE": _p("Fine carbon", "Small real-size twill, sized in centimetres",
                           "TRIM", "CARBON", {"Weave size (cm)": 1.0, "Weave contrast": 0.6,
                                              "Clearcoat": 1.0}),
    "TRIM_FORGED": _p("Forged carbon", "Marbled chopped-fibre carbon, as on supercar trim", "TRIM",
                      "UDIN_FORGED", {"Chip size": 5.0, "Waviness": 1.0, "Fibre roughness": 0.35,
                                      "Clearcoat roughness": 0.0, "Imperfections": 0.03}),
    "TRIM_VANTABLACK": _p("Vantablack", "Total blackout that reflects nothing: gaps, grilles", "TRIM", "BLACKOUT",
                          {"Colour": (0.0, 0.0, 0.0, 1), "Blackness": 1.0, "Reflection": 0.0, "Roughness": 1.0},
                          shiny=False),
    "TRIM_LEATHER": _p("Leather", "Grained interior leather", "TRIM", "LEATHER",
                       {"Colour": (0.08, 0.05, 0.035, 1), "Roughness": 0.55, "Grain depth": 0.3,
                        "Perforation": 0.0}, shiny=False),
    # Fabric & leather (0.9)
    "LEATHER_NAPPA": _p("Nappa leather", "Smooth, fine-grained luxury seat leather with a soft sheen",
                        "FABRIC", "LEATHER", {"Colour": (0.02, 0.02, 0.021, 1), "Roughness": 0.45,
                                              "Grain depth": 0.12, "Grain scale": 0.85, "Sheen": 0.3,
                                              "Perforation": 0.0}, shiny=False),
    "LEATHER_PERFORATED": _p("Perforated", "Seat leather with rows of small breathing holes",
                             "FABRIC", "LEATHER", {"Colour": (0.025, 0.025, 0.026, 1), "Roughness": 0.5,
                                                   "Grain depth": 0.15, "Grain scale": 0.8, "Sheen": 0.25,
                                                   "Perforation": 1.0, "Hole spacing (mm)": 5.0},
                             shiny=False),
    "LEATHER_TAN": _p("Saddle tan", "Warm tan full-grain leather: bags, straps, watch bands", "FABRIC",
                      "LEATHER", {"Colour": (0.32, 0.14, 0.05, 1), "Roughness": 0.5, "Grain depth": 0.35,
                                  "Grain scale": 0.55, "Sheen": 0.2, "Perforation": 0.0}, shiny=False),
    "FABRIC_ALCANTARA": _p("Alcantara", "Soft black microsuede: steering wheels, seat centres, headliners",
                           "FABRIC", "FABRIC", {"Colour": (0.022, 0.022, 0.024, 1),
                                                "Sheen colour": (0.1, 0.1, 0.11, 1), "Pattern": 0.0,
                                                "Fuzz": 0.85, "Nap": 0.5, "Roughness": 0.95,
                                                "Openness": 0.0}, shiny=False),
    "FABRIC_SUEDE": _p("Suede", "Brushed tan suede with a velvety nap", "FABRIC", "FABRIC",
                       {"Colour": (0.3, 0.16, 0.07, 1), "Sheen colour": (0.45, 0.32, 0.22, 1),
                        "Pattern": 0.0, "Fuzz": 0.7, "Nap": 0.65, "Roughness": 0.95, "Openness": 0.0},
                       shiny=False),
    "FABRIC_VELVET": _p("Velvet", "Deep pile velvet that glows at the edges", "FABRIC", "FABRIC",
                        {"Colour": (0.22, 0.008, 0.03, 1), "Sheen colour": (0.6, 0.15, 0.2, 1),
                         "Pattern": 0.0, "Fuzz": 1.0, "Nap": 0.25, "Roughness": 0.85, "Openness": 0.0},
                        shiny=False),
    "FABRIC_WOVEN": _p("Woven fabric", "Fine woven upholstery cloth", "FABRIC", "FABRIC",
                       {"Colour": (0.12, 0.12, 0.13, 1), "Sheen colour": (0.3, 0.3, 0.31, 1),
                        "Pattern": 1.0, "Thread size (cm)": 0.08, "Weave depth": 0.6, "Fuzz": 0.35,
                        "Nap": 0.1, "Roughness": 0.9, "Openness": 0.0}, shiny=False),
    "FABRIC_CANVAS": _p("Canvas", "Coarse cotton canvas: bags, sneakers, covers", "FABRIC", "FABRIC",
                        {"Colour": (0.62, 0.56, 0.45, 1), "Sheen colour": (0.6, 0.58, 0.54, 1),
                         "Pattern": 1.0, "Thread size (cm)": 0.15, "Weave depth": 0.8, "Fuzz": 0.25,
                         "Nap": 0.1, "Roughness": 0.92, "Openness": 0.0}, shiny=False),
    "FABRIC_KNIT": _p("Knit", "Stretchy knitted textile: sneaker uppers, beanies, sportswear", "FABRIC",
                      "FABRIC", {"Colour": (0.05, 0.08, 0.16, 1), "Sheen colour": (0.25, 0.3, 0.45, 1),
                                 "Pattern": 2.0, "Thread size (cm)": 0.25, "Weave depth": 0.8, "Fuzz": 0.4,
                                 "Nap": 0.05, "Roughness": 0.85, "Openness": 0.0}, shiny=False),
    "FABRIC_MESH": _p("Open mesh", "See-through sports mesh: shoe panels, grilles, speaker cloth",
                      "FABRIC", "FABRIC", {"Colour": (0.03, 0.03, 0.032, 1),
                                           "Sheen colour": (0.2, 0.2, 0.21, 1), "Pattern": 3.0,
                                           "Thread size (cm)": 0.2, "Weave depth": 0.6, "Openness": 0.6,
                                           "Fuzz": 0.2, "Nap": 0.0, "Roughness": 0.7}, shiny=False),
    # Tyres: tread pattern + sidewall in one material, fitted to the tyre's shape.
    "TYRE_ROAD": _p("Road", "Everyday touring tread: straight grooves and fine sipes", "TYRE", "TYRE",
                    {"Tread depth": 0.6, "Blocks around": 70.0, "Grooves": 3.0, "Groove width": 0.35,
                     "Direction": 0.0, "Sipes": 0.6, "Wear": 0.1, "Sidewall shine": 0.15,
                     "Sidewall ribs": 0.3, "Dust": 0.2, "Roughness": 0.7}, shiny=False),
    "TYRE_SPORT": _p("Performance", "Directional V tread with big shoulder blocks", "TYRE", "TYRE",
                     {"Tread depth": 0.5, "Blocks around": 46.0, "Grooves": 2.0, "Groove width": 0.45,
                      "Direction": 0.8, "Sipes": 0.0, "Wear": 0.05, "Sidewall shine": 0.3,
                      "Sidewall ribs": 0.2, "Dust": 0.1, "Roughness": 0.65}, shiny=False),
    "TYRE_WINTER": _p("Winter", "Dense blocks cut with many sipes for grip on snow", "TYRE", "TYRE",
                      {"Tread depth": 0.8, "Blocks around": 56.0, "Grooves": 3.0, "Groove width": 0.4,
                       "Direction": 0.5, "Sipes": 1.0, "Wear": 0.05, "Sidewall shine": 0.1,
                       "Sidewall ribs": 0.3, "Dust": 0.25, "Roughness": 0.75}, shiny=False),
    "TYRE_OFFROAD": _p("All-terrain", "Chunky staggered blocks with wide, open grooves", "TYRE", "TYRE",
                       {"Tread depth": 1.0, "Blocks around": 32.0, "Grooves": 2.0, "Groove width": 0.7,
                        "Direction": 0.15, "Sipes": 0.4, "Wear": 0.1, "Sidewall shine": 0.05,
                        "Sidewall ribs": 0.5, "Dust": 0.5, "Roughness": 0.8}, shiny=False),
    "TYRE_SLICK": _p("Racing slick", "Smooth, scuffed racing rubber with no tread", "TYRE", "TYRE",
                     {"Tread depth": 0.0, "Blocks around": 0.0, "Grooves": 0.0, "Sipes": 0.0,
                      "Wear": 0.6, "Sidewall shine": 0.2, "Sidewall ribs": 0.0, "Dust": 0.15,
                      "Roughness": 0.6}, shiny=False),
    "TYRE_WHITEWALL": _p("Whitewall", "Classic road tyre with a white band on the sidewall", "TYRE",
                         "TYRE", {"Tread depth": 0.6, "Blocks around": 70.0, "Grooves": 3.0,
                                  "Groove width": 0.35, "Direction": 0.0, "Sipes": 0.5, "Wear": 0.05,
                                  "Sidewall shine": 0.2, "Sidewall ribs": 0.0, "Dust": 0.1,
                                  "Roughness": 0.7, "Whitewall": 0.6}, shiny=False),
    "TYRE_WORN": _p("Worn", "A road tyre near the end of its life: shallow, scuffed tread", "TYRE",
                    "TYRE", {"Tread depth": 0.6, "Blocks around": 70.0, "Grooves": 3.0,
                             "Groove width": 0.35, "Direction": 0.0, "Sipes": 0.6, "Wear": 0.85,
                             "Sidewall shine": 0.0, "Sidewall ribs": 0.3, "Dust": 0.6,
                             "Roughness": 0.8}, shiny=False),
    # Wood & stone (0.9)
    "WOOD_WALNUT": _p("Walnut gloss", "Dark walnut veneer under a deep gloss lacquer: dashboards, speakers",
                      "NATURAL", "WOOD", {"Light colour": (0.17, 0.085, 0.04, 1),
                                          "Dark colour": (0.06, 0.026, 0.012, 1), "Grain size (cm)": 0.8,
                                          "Figure": 0.5, "Pores": 0.15, "Roughness": 0.4, "Clearcoat": 1.0,
                                          "Clearcoat roughness": 0.03}),
    "WOOD_WALNUT_OPEN": _p("Walnut oiled", "Walnut with an oiled, open-pore matte finish", "NATURAL",
                           "WOOD", {"Light colour": (0.17, 0.085, 0.04, 1),
                                    "Dark colour": (0.06, 0.026, 0.012, 1), "Grain size (cm)": 0.8,
                                    "Figure": 0.5, "Pores": 0.8, "Roughness": 0.6, "Clearcoat": 0.0},
                           shiny=False),
    "WOOD_OAK": _p("Oak gloss", "Light oak under a clear gloss lacquer", "NATURAL", "WOOD",
                   {"Light colour": (0.5, 0.3, 0.13, 1), "Dark colour": (0.24, 0.12, 0.045, 1),
                    "Grain size (cm)": 1.2, "Figure": 0.3, "Pores": 0.25, "Roughness": 0.4, "Clearcoat": 1.0,
                    "Clearcoat roughness": 0.04}),
    "WOOD_OAK_OPEN": _p("Oak oiled", "Raw-look oiled oak with deep open pores: furniture, floors",
                        "NATURAL", "WOOD", {"Light colour": (0.5, 0.3, 0.13, 1),
                                            "Dark colour": (0.24, 0.12, 0.045, 1), "Grain size (cm)": 1.2,
                                            "Figure": 0.3, "Pores": 0.9, "Roughness": 0.65,
                                            "Clearcoat": 0.0}, shiny=False),
    "WOOD_ASH_GREY": _p("Grey ash", "Grey-stained ash veneer with a satin finish", "NATURAL", "WOOD",
                        {"Light colour": (0.3, 0.29, 0.27, 1), "Dark colour": (0.13, 0.125, 0.12, 1),
                         "Grain size (cm)": 1.0, "Figure": 0.2, "Pores": 0.6, "Roughness": 0.5,
                         "Clearcoat": 0.4, "Clearcoat roughness": 0.3}),
    "STONE_CONCRETE": _p("Concrete", "Raw grey concrete with soft mottling and small air pits", "NATURAL",
                         "STONE", {"Colour": (0.22, 0.215, 0.2, 1), "Second colour": (0.14, 0.135, 0.13, 1),
                                   "Mottle": 0.6, "Mottle size (cm)": 12.0, "Veins": 0.0, "Speckle": 0.2,
                                   "Pits": 0.5, "Roughness": 0.85, "Glaze": 0.0}, shiny=False),
    "STONE_CONCRETE_POLISHED": _p("Satin concrete", "Smooth sealed concrete with a soft sheen",
                                  "NATURAL", "STONE",
                                  {"Colour": (0.24, 0.235, 0.22, 1), "Second colour": (0.15, 0.145, 0.14, 1),
                                   "Mottle": 0.5, "Mottle size (cm)": 20.0, "Veins": 0.0, "Speckle": 0.4,
                                   "Pits": 0.1, "Roughness": 0.4, "Glaze": 0.6, "Glaze roughness": 0.15}),
    "STONE_MARBLE": _p("White marble", "Polished white marble with grey veins", "NATURAL", "STONE",
                       {"Colour": (0.82, 0.81, 0.79, 1), "Second colour": (0.35, 0.35, 0.36, 1),
                        "Mottle": 0.1, "Mottle size (cm)": 30.0, "Veins": 0.8, "Speckle": 0.0, "Pits": 0.0,
                        "Roughness": 0.25, "Glaze": 1.0, "Glaze roughness": 0.03}),
    "STONE_MARBLE_BLACK": _p("Black marble", "Polished black marble with white veins", "NATURAL", "STONE",
                             {"Colour": (0.02, 0.02, 0.021, 1), "Second colour": (0.7, 0.7, 0.68, 1),
                              "Mottle": 0.05, "Mottle size (cm)": 30.0, "Veins": 0.6, "Speckle": 0.0,
                              "Pits": 0.0, "Roughness": 0.25, "Glaze": 1.0, "Glaze roughness": 0.03}),
    "STONE_CERAMIC": _p("Glazed ceramic", "Glossy white glazed ceramic: mugs, tiles, sanitaryware",
                        "NATURAL", "STONE", {"Colour": (0.85, 0.85, 0.84, 1), "Second colour": (0.8, 0.8, 0.78, 1),
                                             "Mottle": 0.05, "Veins": 0.0, "Speckle": 0.0, "Pits": 0.0,
                                             "Roughness": 0.3, "Glaze": 1.0, "Glaze roughness": 0.02}),
    "STONE_STONEWARE": _p("Stoneware", "Speckled matte stoneware: plates, vases, cups", "NATURAL",
                          "STONE", {"Colour": (0.45, 0.41, 0.36, 1), "Second colour": (0.36, 0.32, 0.27, 1),
                                    "Mottle": 0.3, "Mottle size (cm)": 3.0, "Veins": 0.0, "Speckle": 0.6,
                                    "Pits": 0.1, "Roughness": 0.75, "Glaze": 0.15,
                                    "Glaze roughness": 0.5}, shiny=False),
    "STONE_CARDBOARD": _p("Cardboard", "Brown kraft cardboard: boxes, packaging, sleeves", "NATURAL",
                          "STONE", {"Colour": (0.32, 0.18, 0.08, 1), "Second colour": (0.26, 0.14, 0.06, 1),
                                    "Mottle": 0.35, "Mottle size (cm)": 1.5, "Veins": 0.0, "Speckle": 0.35,
                                    "Pits": 0.0, "Roughness": 0.9, "Glaze": 0.0}, shiny=False),
    # Brakes: discs (fitted to the disc's shape) and a painted caliper.
    "BRAKE_PLAIN": _p("Plain disc", "Machined cast iron with fine turning rings", "BRAKE", "DISC",
                      {"Drilled holes": 0.0, "Slots": 0.0, "Vents": 40.0, "Machining": 0.7,
                       "Roughness": 0.28, "Heat tint": 0.0, "Rust": 0.0, "Speckle": 0.0,
                       "Colour": (0.30, 0.30, 0.31, 1), "Hat colour": (0.30, 0.30, 0.31, 1)}),
    "BRAKE_DRILLED": _p("Cross-drilled", "Rows of drilled holes around a black hat", "BRAKE", "DISC",
                        {"Drilled holes": 3.0, "Holes around": 24.0, "Hole size": 0.5, "Slots": 0.0,
                         "Vents": 40.0, "Machining": 0.6, "Roughness": 0.3, "Heat tint": 0.0,
                         "Rust": 0.0, "Speckle": 0.0, "Colour": (0.30, 0.30, 0.31, 1), "Hat colour": (0.02, 0.02, 0.022, 1)}),
    "BRAKE_SLOTTED": _p("Slotted", "Curved slots that sweep across the face", "BRAKE", "DISC",
                        {"Drilled holes": 0.0, "Slots": 8.0, "Vents": 40.0, "Machining": 0.6,
                         "Roughness": 0.3, "Heat tint": 0.0, "Rust": 0.0, "Speckle": 0.0,
                         "Colour": (0.30, 0.30, 0.31, 1), "Hat colour": (0.30, 0.30, 0.31, 1)}),
    "BRAKE_SPORT": _p("Drilled + slot", "Holes and slots together, for a performance look", "BRAKE",
                      "DISC", {"Drilled holes": 2.0, "Holes around": 18.0, "Hole size": 0.45,
                               "Slots": 6.0, "Vents": 40.0, "Machining": 0.6, "Roughness": 0.3,
                               "Heat tint": 0.15, "Rust": 0.0, "Speckle": 0.0,
                               "Colour": (0.30, 0.30, 0.31, 1), "Hat colour": (0.02, 0.02, 0.022, 1)}),
    "BRAKE_CERAMIC": _p("Carbon ceramic", "Dark speckled ceramic disc with drilled holes", "BRAKE", "DISC",
                        {"Drilled holes": 3.0, "Holes around": 30.0, "Hole size": 0.4, "Slots": 0.0,
                         "Vents": 48.0, "Machining": 0.1, "Roughness": 0.5, "Heat tint": 0.0,
                         "Rust": 0.0, "Speckle": 1.0, "Colour": (0.07, 0.07, 0.075, 1),
                         "Hat colour": (0.25, 0.25, 0.26, 1)}),
    "BRAKE_RACE": _p("Race heat", "Slotted race disc, blued and gold from heat", "BRAKE", "DISC",
                     {"Drilled holes": 0.0, "Slots": 12.0, "Vents": 60.0, "Machining": 0.4,
                      "Roughness": 0.35, "Heat tint": 0.9, "Rust": 0.0, "Speckle": 0.0,
                      "Colour": (0.28, 0.28, 0.29, 1), "Hat colour": (0.30, 0.30, 0.31, 1)}),
    "BRAKE_RUSTY": _p("Weathered", "A disc that sat in the rain: flash rust on the face", "BRAKE", "DISC",
                      {"Drilled holes": 0.0, "Slots": 0.0, "Vents": 40.0, "Machining": 0.4,
                       "Roughness": 0.45, "Heat tint": 0.0, "Rust": 0.7, "Speckle": 0.0,
                       "Colour": (0.30, 0.30, 0.31, 1), "Hat colour": (0.30, 0.30, 0.31, 1)}, shiny=False),
    "BRAKE_CALIPER": _p("Caliper", "Glossy painted brake caliper (red until you change it)", "BRAKE",
                        "PAINT", {"Base colour": (0.55, 0.01, 0.01, 1), "Metallic flake": 0.0,
                                  "Flip amount": 0.0, "Pearl": 0.0, "Base roughness": 0.3,
                                  "Clearcoat": 1.0, "Clearcoat roughness": 0.05, "Orange peel": 0.05}),
    # Image: a picture already made for the part (Choose image… on the card).
    "IMAGE_PRINT": _p("Printed", "The image printed on a satin surface", "IMAGE", "IMAGE",
                      {"Roughness": 0.5, "Clearcoat": 0.0, "Glow": 0.0, "Brightness": 1.0}, shiny=False),
    "IMAGE_GLOSS": _p("Gloss print", "The image under a glossy clear coat", "IMAGE", "IMAGE",
                      {"Roughness": 0.3, "Clearcoat": 1.0, "Glow": 0.0, "Brightness": 1.0}),
    "IMAGE_SCREEN": _p("Screen", "A lit display: instrument clusters, infotainment, phones", "IMAGE",
                       "IMAGE", {"Roughness": 0.05, "Clearcoat": 1.0, "Glow": 4.0, "Brightness": 0.3,
                                 "Backing colour": (0.003, 0.003, 0.004, 1)}),
    "IMAGE_LABEL": _p("Label", "A paper or vinyl label with see-through edges", "IMAGE", "IMAGE",
                      {"Roughness": 0.6, "Clearcoat": 0.0, "Glow": 0.0, "Brightness": 1.0,
                       "Backing colour": (0.8, 0.8, 0.8, 1)}, shiny=False),
    "IMAGE_TEXTURES": _p("Texture set", "The part's own texture maps: colour, roughness, metal, normal and more",
                         "IMAGE", "TEXTURES", {}, shiny=False),
    # Glow: emissive parts. They light the scene in Cycles, and in Eevee with
    # ray tracing and baked lighting (Shoot › Engine › Bake lighting). Coloured
    # glows stay modest: bright saturated emission washes out to white/pink.
    "GLOW_LED": _p("LED", "Clean cool-white LED light", "GLOW", "GLOW",
                   {"Glow colour": (0.9, 0.95, 1.0, 1), "Brightness": 25.0, "Edge fade": 0.0,
                    "LED dots": 0.0, "Surface colour": (0.6, 0.6, 0.62, 1), "Lens": 1.0}),
    "GLOW_LED_BAR": _p("LED light bar", "A row of bright LED dots behind a clear lens", "GLOW", "GLOW",
                       {"Glow colour": (0.92, 0.96, 1.0, 1), "Brightness": 40.0, "LED dots": 1.0,
                        "Dot size (cm)": 0.8, "Surface colour": (0.03, 0.03, 0.035, 1), "Lens": 1.0}),
    "GLOW_TAIL": _p("Tail light", "Deep red glow under a glossy red lens", "GLOW", "GLOW",
                    {"Glow colour": (1.0, 0.02, 0.01, 1), "Brightness": 2.5, "Edge fade": 0.3,
                     "LED dots": 0.0, "Surface colour": (0.25, 0.0, 0.0, 1), "Roughness": 0.1,
                     "Lens": 1.0}),
    "GLOW_AMBER": _p("Indicator", "Amber turn-signal glow", "GLOW", "GLOW",
                     {"Glow colour": (1.0, 0.35, 0.0, 1), "Brightness": 3.0, "Edge fade": 0.3,
                      "LED dots": 0.0, "Surface colour": (0.3, 0.12, 0.0, 1), "Roughness": 0.1,
                      "Lens": 1.0}),
    "GLOW_NEON": _p("Neon", "Saturated neon tube, brightest face-on", "GLOW", "GLOW",
                    {"Glow colour": (1.0, 0.08, 0.55, 1), "Brightness": 6.0, "Edge fade": 0.6,
                     "LED dots": 0.0, "Surface colour": (0.5, 0.2, 0.35, 1), "Roughness": 0.2,
                     "Lens": 0.0}),
    "GLOW_SCREEN": _p("Screen", "A lit display under glossy black glass", "GLOW", "GLOW",
                      {"Glow colour": (0.75, 0.85, 1.0, 1), "Brightness": 2.0, "Edge fade": 0.0,
                       "LED dots": 0.0, "Surface colour": (0.005, 0.005, 0.006, 1), "Roughness": 0.05,
                       "Lens": 1.0}),
    "GLOW_PANEL": _p("Light panel", "Soft, even glow from a diffused panel", "GLOW", "GLOW",
                     {"Glow colour": (1.0, 0.97, 0.92, 1), "Brightness": 8.0, "Edge fade": 0.0,
                      "LED dots": 0.0, "Surface colour": (0.85, 0.85, 0.85, 1), "Roughness": 0.6,
                      "Lens": 0.0}, shiny=False),
}

CATEGORIES = {"PAINT": "Paint", "METAL": "Metal", "GLASS": "Glass", "TRIM": "Trim",
              "FABRIC": "Fabric & leather", "TYRE": "Tyre", "NATURAL": "Wood & stone",
              "BRAKE": "Brake", "GLOW": "Glow", "IMAGE": "Image"}

# The four quick finishes (Light card, old saves, tests): always keep the colour.
QUICK = {"GLOSS": "TRIM_GLOSS", "METAL": "METAL_POLISHED", "GLASS": "GLASS_CLEAR",
         "MATTE": "TRIM_MATTE"}
FINISH_PRESETS = {k: dict(PRESETS[v], label=k.title()) for k, v in QUICK.items()}


def presets_in(category):
    return {k: p for k, p in PRESETS.items() if p["category"] == category}


def type_inputs(type_key):
    return {i[0]: i for i in TYPES[type_key]["inputs"]}


# --- Node groups ------------------------------------------------------------------------

class _Builder:
    """Small helper to wire a group: values or sockets go into inputs."""

    def __init__(self, ng):
        self.ng, self.nodes, self.links = ng, ng.nodes, ng.links
        self.gin = self.nodes.new("NodeGroupInput")
        self.gout = self.nodes.new("NodeGroupOutput")
        self.skipped = []

    def inp(self, name):
        return self.gin.outputs[name]

    def feed(self, sock, value):
        if hasattr(value, "is_output"):
            self.links.new(value, sock)
        else:
            sock.default_value = value

    def node(self, idname, inputs=None, **props):
        n = self.nodes.new(idname)
        for k, v in props.items():
            setattr(n, k, v)
        for name, value in (inputs or {}).items():
            sock = n.inputs.get(name) if isinstance(name, str) else n.inputs[name]
            if sock is None:
                self.skipped.append("%s.%s" % (idname, name))
                continue
            self.feed(sock, value)
        return n

    def math(self, op, a, b=None, clamp=False):
        n = self.node("ShaderNodeMath", operation=op, use_clamp=clamp)
        self.feed(n.inputs[0], a)
        if b is not None:
            self.feed(n.inputs[1], b)
        return n.outputs[0]

    def vmath(self, op, a, b=None, scale=None):
        n = self.node("ShaderNodeVectorMath", operation=op)
        self.feed(n.inputs[0], a)
        if b is not None:
            self.feed(n.inputs[1], b)
        if scale is not None:
            self.feed(n.inputs[3], scale)
        return n.outputs[0]

    def mix(self, fac, a, b):
        n = self.node("ShaderNodeMix", data_type="RGBA")
        by_id = {s.identifier: s for s in n.inputs}        # names repeat ("A", "B"); ids don't
        self.feed(by_id["Factor_Float"], fac)
        self.feed(by_id["A_Color"], a)
        self.feed(by_id["B_Color"], b)
        return next(s for s in n.outputs if s.identifier == "Result_Color")

    def _mapping(self):
        node = getattr(self, "_map_node", None)
        if node is None:
            node = self._map_node = self.node("ShaderNodeGroup", node_tree=mapping_group())
        return node

    def coords(self):
        """Where patterns are evaluated, in metres: world-space position (real
        units at any import scale), or the part's UVs in metres, mirrored and
        turned per object (Plinth_Mapping)."""
        return self._mapping().outputs["Vector"]

    def proj_normal(self):
        """The normal three-sided (triplanar) projections blend by: the surface's,
        or straight up in UV mode so they read the UV plane only."""
        return self._mapping().outputs["Normal"]

    def bump(self, height, strength, distance=0.01, normal=None):
        n = self.node("ShaderNodeBump", {"Strength": strength, "Distance": distance})
        self.feed(n.inputs["Height"], height)
        if normal is not None:
            self.feed(n.inputs["Normal"], normal)
        return n.outputs["Normal"]

    def bsdf(self, inputs):
        n = self.node("ShaderNodeBsdfPrincipled")
        for name, value in inputs.items():
            sock = n.inputs.get(name)
            if sock is None:
                self.skipped.append("Principled." + name)   # older Blender: e.g. Thin Wall
                continue
            self.feed(sock, value)
        self.links.new(n.outputs["BSDF"], self.gout.inputs["Surface"])
        return n


def _lerp(b, lo, hi, f):
    """Socket/float f in 0..1 mapped to lo..hi."""
    return b.math("ADD", b.math("MULTIPLY", f, hi - lo), lo)


def _build_paint(b):
    i = b.inp
    co = b.coords()
    facing = b.node("ShaderNodeLayerWeight", {"Blend": 0.5}).outputs["Facing"]
    flip = b.math("MULTIPLY", facing, i("Flip amount"))
    base = b.mix(flip, i("Base colour"), i("Flip colour"))
    # Flakes: one random colour per Voronoi cell, used as tint mask and as a
    # tilted normal per flake (the sparkle).
    vor = b.node("ShaderNodeTexVoronoi", {"Scale": _lerp(b, 150, 1500, i("Flake size")),
                                         "Randomness": 1.0})
    b.links.new(co, vor.inputs["Vector"])
    rnd = b.node("ShaderNodeSeparateColor")
    b.links.new(vor.outputs["Color"], rnd.inputs[0])
    tint = b.math("MULTIPLY", rnd.outputs[0], b.math("MULTIPLY", i("Metallic flake"), 0.6))
    colour = b.mix(tint, base, i("Flake colour"))
    tilt = b.vmath("SCALE", b.vmath("SUBTRACT", vor.outputs["Color"], (0.5, 0.5, 0.5)),
                   scale=b.math("MULTIPLY", i("Flake sparkle"), b.math("MULTIPLY", i("Metallic flake"), 0.8)))
    geo = b.node("ShaderNodeNewGeometry").outputs["Normal"]
    flake_normal = b.vmath("NORMALIZE", b.vmath("ADD", geo, tilt))
    # Orange peel: a soft noise on the clearcoat only.
    peel = b.node("ShaderNodeTexNoise", {"Scale": _lerp(b, 20, 300, i("Orange peel scale")),
                                        "Detail": 2.0})
    b.links.new(co, peel.inputs["Vector"])
    coat_normal = b.bump(peel.outputs["Fac"], b.math("MULTIPLY", i("Orange peel"), 0.15), 0.002)
    b.bsdf({"Base Color": colour, "Metallic": b.math("MULTIPLY", i("Metallic flake"), 0.9),
            "Roughness": i("Base roughness"), "Normal": flake_normal,
            "Coat Weight": i("Clearcoat"), "Coat Roughness": i("Clearcoat roughness"),
            "Coat Normal": coat_normal,
            "Thin Film Thickness": b.math("MULTIPLY", i("Pearl"), 450.0), "Thin Film IOR": 1.5})


def _build_metal(b):
    i = b.inp
    rot = b.node("ShaderNodeCombineXYZ")
    b.feed(rot.inputs["Z"], b.math("MULTIPLY", i("Brush direction"), math.pi))
    scl = b.node("ShaderNodeCombineXYZ")
    s = _lerp(b, 20, 200, i("Brush scale"))
    b.feed(scl.inputs["X"], s)
    b.feed(scl.inputs["Y"], b.math("MULTIPLY", s, 0.01))     # stretched: lines, not dots
    b.feed(scl.inputs["Z"], s)
    mp = b.node("ShaderNodeMapping")
    b.links.new(b.coords(), mp.inputs["Vector"])
    b.links.new(rot.outputs[0], mp.inputs["Rotation"])
    b.links.new(scl.outputs[0], mp.inputs["Scale"])
    noise = b.node("ShaderNodeTexNoise", {"Scale": 4.0, "Detail": 8.0})
    b.links.new(mp.outputs[0], noise.inputs["Vector"])
    streak = b.math("MULTIPLY", b.math("SUBTRACT", noise.outputs["Fac"], 0.5), i("Brushed"))
    # Cast grain (0.9): the fine sandy pitting of cast alloy, in real size (~0.4 mm).
    grain = b.node("ShaderNodeTexNoise", {"Scale": 2500.0, "Detail": 2.0, "Roughness": 0.5})
    b.links.new(b.coords(), grain.inputs["Vector"])
    cast = b.math("MULTIPLY", b.math("SUBTRACT", grain.outputs["Fac"], 0.5), i("Cast grain"))
    rough = b.math("ADD", b.math("ADD", i("Roughness"), b.math("MULTIPLY", streak, 0.15)),
                   b.math("ADD", b.math("MULTIPLY", cast, 0.3), b.math("MULTIPLY", i("Cast grain"), 0.1)),
                   clamp=True)
    height = b.math("ADD", b.math("MULTIPLY", noise.outputs["Fac"], i("Brushed")),
                    b.math("MULTIPLY", grain.outputs["Fac"], i("Cast grain")))
    b.bsdf({"Base Color": i("Colour"), "Metallic": 1.0, "Roughness": rough,
            "Specular Tint": i("Edge tint"),
            "Anisotropic": b.math("MULTIPLY", i("Brushed"), 0.8),
            "Anisotropic Rotation": i("Brush direction"),
            "Normal": b.bump(height, b.math("ADD", b.math("MULTIPLY", i("Brushed"), 0.25),
                                            b.math("MULTIPLY", i("Cast grain"), 0.3)), 0.001),
            "Coat Weight": i("Clearcoat"), "Coat Roughness": i("Clearcoat roughness")})


UDIN_IMPERFECTIONS = "Plinth_UDIN_dep_imperfections"


def _build_glass(b):
    """Glass on the UDIN Windshield, one branch per engine.

    pane    = Fresnel mix of Transparent(Tint) and Glossy(Roughness + UDIN
              imperfections): a thin sheet seen straight through, no offset.
    glass   = the UDIN Windshield group (Glass BSDF + imperfection gloss).
    Surface (Cycles): open shells are panes (a Glass BSDF on a single surface
              refracts as if the glass never ended); closed parts are real
              glass, with see-through shadows so the cabin isn't black.
    Eevee:    pane, unless the part refracts in Eevee (solid or frosted):
              Eevee traces one refraction layer, so refracting panes hid the
              glass behind them and magnified what they showed.
    Volume:   absorption for solid glass only (a pane's colour is its Tint)."""
    i = b.inp
    udin = b.node("ShaderNodeGroup", node_tree=_udin_group(TYPES["UDIN_WINDSHIELD"]))
    for sock, name in ((1, "Tint"), (2, "Roughness"), (3, "IOR"), (5, "Imperfections"),
                       (6, "Wear scale"), (7, "Smudges"), (8, "Scratches"), (9, "Stains")):
        b.links.new(i(name), udin.inputs[sock])
    wear = b.node("ShaderNodeGroup", node_tree=bpy.data.node_groups[UDIN_IMPERFECTIONS])
    for sock, name in ((0, "Imperfections"), (1, "Wear scale"), (3, "Smudges"), (5, "Scratches"),
                       (7, "Stains")):
        b.links.new(i(name), wear.inputs[sock])

    fresnel = b.node("ShaderNodeFresnel")
    b.links.new(i("IOR"), fresnel.inputs["IOR"])
    reflect = b.math("MULTIPLY", fresnel.outputs[0], b.math("MULTIPLY", i("Reflection"), 2.0), clamp=True)
    gloss = b.node("ShaderNodeBsdfGlossy", {"Color": (1.0, 1.0, 1.0, 1.0)}, distribution="GGX")
    b.feed(gloss.inputs["Roughness"], b.math("ADD", i("Roughness"), wear.outputs[0], clamp=True))
    see = b.node("ShaderNodeBsdfTransparent")
    b.links.new(i("Tint"), see.inputs["Color"])
    pane = b.node("ShaderNodeMixShader")
    b.links.new(reflect, pane.inputs[0])
    b.links.new(see.outputs[0], pane.inputs[1])
    b.links.new(gloss.outputs[0], pane.inputs[2])

    path = b.node("ShaderNodeLightPath")
    lit = b.node("ShaderNodeMixShader")                 # closed glass: light goes through in shadows
    b.links.new(path.outputs["Is Shadow Ray"], lit.inputs[0])
    b.links.new(udin.outputs[0], lit.inputs[1])
    b.links.new(see.outputs[0], lit.inputs[2])
    cycles = b.node("ShaderNodeMixShader")
    b.links.new(i("Open shell"), cycles.inputs[0])
    b.links.new(lit.outputs[0], cycles.inputs[1])
    b.links.new(pane.outputs[0], cycles.inputs[2])
    b.links.new(cycles.outputs[0], b.gout.inputs["Surface"])

    eevee = b.node("ShaderNodeMixShader")
    b.links.new(i("Eevee refraction"), eevee.inputs[0])
    b.links.new(pane.outputs[0], eevee.inputs[1])
    b.links.new(lit.outputs[0], eevee.inputs[2])     # Eevee evaluates Is Shadow Ray too
    b.links.new(eevee.outputs[0], b.gout.inputs["Eevee"])

    vol = b.node("ShaderNodeVolumeAbsorption")
    b.links.new(i("Absorption colour"), vol.inputs["Color"])
    solid = b.math("SUBTRACT", 1.0, i("Thin pane"))
    b.feed(vol.inputs["Density"], b.math("MULTIPLY", i("Absorption"), solid))
    b.links.new(vol.outputs[0], b.gout.inputs["Volume"])


def _build_plastic(b):
    i = b.inp
    tex = b.node("ShaderNodeTexNoise", {"Scale": _lerp(b, 50, 800, i("Texture scale")), "Detail": 6.0,
                                        "Roughness": 0.7})
    b.links.new(b.coords(), tex.inputs["Vector"])
    soft = i("Soft touch")                       # 0.9: rubberised coating: velvety, low specular
    b.bsdf({"Base Color": i("Colour"), "Metallic": 0.0, "Roughness": i("Roughness"),
            "Specular IOR Level": b.math("SUBTRACT", 0.5, b.math("MULTIPLY", soft, 0.25)),
            "Sheen Weight": b.math("MULTIPLY", soft, 0.5), "Sheen Roughness": 0.4,
            "Normal": b.bump(tex.outputs["Fac"], b.math("MULTIPLY", i("Texture depth"), 0.4), 0.002),
            "Coat Weight": i("Clearcoat"), "Coat Roughness": 0.03})


def _build_rubber(b):
    """Tyre rubber: near-black with low, rubbery reflectance; fine moulded
    micro-texture; satin sheen; grey dust bloom that settles in crevices
    (ambient occlusion, works in Eevee and Cycles) and in patches; and a shine
    control from dull to freshly dressed."""
    i = b.inp
    p = b.coords()
    micro = b.node("ShaderNodeTexNoise", {"Scale": 900.0, "Detail": 8.0, "Roughness": 0.6})
    b.links.new(p, micro.inputs["Vector"])
    patch = b.node("ShaderNodeTexNoise", {"Detail": 5.0, "Roughness": 0.55})
    b.links.new(p, patch.inputs["Vector"])
    b.feed(patch.inputs["Scale"], b.math("DIVIDE", 100.0, i("Dust size (cm)")))
    ao = b.node("ShaderNodeAmbientOcclusion", {"Distance": 0.04}, samples=16)
    cavity = b.math("SUBTRACT", 1.0, ao.outputs["AO"])
    patches = b.math("MULTIPLY", b.math("SUBTRACT", patch.outputs["Fac"], 0.45), 2.5)
    dust = b.math("MULTIPLY", b.math("ADD", patches, b.math("MULTIPLY", cavity, 1.5)), i("Dust"),
                  clamp=True)
    shine = i("Tyre shine")
    rough = b.math("ADD", b.math("SUBTRACT", i("Roughness"), b.math("MULTIPLY", shine, 0.4)),
                   b.math("ADD", b.math("MULTIPLY", b.math("SUBTRACT", micro.outputs["Fac"], 0.5), 0.2),
                          b.math("MULTIPLY", dust, 0.3)), clamp=True)
    b.bsdf({"Base Color": b.mix(dust, i("Colour"), i("Dust colour")), "Metallic": 0.0,
            "Roughness": rough,
            "Specular IOR Level": b.math("ADD", 0.3, b.math("MULTIPLY", shine, 0.4)),
            "Sheen Weight": b.math("ADD", i("Sheen"), b.math("MULTIPLY", dust, 0.4), clamp=True),
            "Sheen Tint": i("Dust colour"), "Sheen Roughness": 0.5,
            "Coat Weight": b.math("MULTIPLY", shine, 0.5), "Coat Roughness": 0.15,
            "Normal": b.bump(micro.outputs["Fac"], b.math("MULTIPLY", i("Micro texture"), 0.15), 0.0005)})


def _twill(b, a, c, s):
    """2x2 twill on a plane: (cell mask 0/1 = which way the tow runs, tow height)."""
    u, v = b.math("MULTIPLY", a, s), b.math("MULTIPLY", c, s)
    cu, cv = b.math("FLOOR", u), b.math("FLOOR", v)
    fu, fv = b.math("SUBTRACT", u, cu), b.math("SUBTRACT", v, cv)
    mask = b.math("LESS_THAN", b.math("FLOORED_MODULO", b.math("ADD", cu, cv), 4.0), 2.0)
    tow_a = b.math("SINE", b.math("MULTIPLY", fv, math.pi))     # bundle along u
    tow_b = b.math("SINE", b.math("MULTIPLY", fu, math.pi))     # bundle along v
    height = b.math("ADD", tow_b, b.math("MULTIPLY", mask, b.math("SUBTRACT", tow_a, tow_b)))
    return mask, height


def _build_carbon(b):
    """Carbon fibre: a real-size 2x2 twill, projected from three sides so it
    doesn't smear on curved panels, with the angle-dependent flip of the weave
    built in (Eevee ignores anisotropy, so the flip can't rely on it)."""
    i = b.inp
    sep = b.node("ShaderNodeSeparateXYZ")
    b.links.new(b.coords(), sep.inputs[0])
    x, y, z = sep.outputs["X"], sep.outputs["Y"], sep.outputs["Z"]
    size = b.math("MAXIMUM", b.math("MULTIPLY", i("Weave size (cm)"), 0.01), 0.0005)   # metres
    s = b.math("DIVIDE", 1.0, size)
    n = b.node("ShaderNodeSeparateXYZ")
    b.links.new(b.proj_normal(), n.inputs[0])
    w = [b.math("POWER", b.math("ABSOLUTE", n.outputs[k]), 4.0) for k in ("X", "Y", "Z")]
    total = b.math("ADD", b.math("ADD", w[0], w[1]), w[2])
    w = [b.math("DIVIDE", wk, total) for wk in w]
    planes = [_twill(b, y, z, s), _twill(b, x, z, s), _twill(b, x, y, s)]   # seen from X, Y, Z

    def blend(k):
        acc = b.math("MULTIPLY", planes[0][k], w[0])
        for plane, wk in zip(planes[1:], w[1:]):
            acc = b.math("ADD", acc, b.math("MULTIPLY", plane[k], wk))
        return acc

    mask, height = blend(0), blend(1)
    facing = b.node("ShaderNodeLayerWeight", {"Blend": 0.3}).outputs["Facing"]
    flip = b.math("ADD", mask, b.math("MULTIPLY", b.math("MULTIPLY", facing, 0.6),
                                      b.math("SUBTRACT", 1.0, b.math("MULTIPLY", mask, 2.0))))
    contrast = i("Weave contrast")
    bright = b.math("ADD", b.math("SUBTRACT", 1.0, contrast), b.math("MULTIPLY", contrast, flip))
    bright = b.math("MULTIPLY", bright, b.math("ADD", 0.7, b.math("MULTIPLY", height, 0.3)))
    b.bsdf({"Base Color": b.vmath("SCALE", i("Colour"), scale=bright), "Metallic": 0.0,
            "Roughness": 0.3, "Anisotropic": i("Fibre shine"),
            "Anisotropic Rotation": b.math("MULTIPLY", mask, 0.25),
            "Normal": b.bump(height, 0.2, b.math("MULTIPLY", size, 0.1)),
            "Coat Weight": i("Clearcoat"), "Coat Roughness": i("Clearcoat roughness")})


def _triplanar(b, fn, s):
    """A flat pattern fn(b, a, c, s) -> (sockets...) projected from the three
    world axes and blended by the surface normal, so it doesn't smear on curved
    parts. Returns the blended sockets."""
    sep = b.node("ShaderNodeSeparateXYZ")
    b.links.new(b.coords(), sep.inputs[0])
    x, y, z = sep.outputs["X"], sep.outputs["Y"], sep.outputs["Z"]
    n = b.node("ShaderNodeSeparateXYZ")
    b.links.new(b.proj_normal(), n.inputs[0])
    w = [b.math("POWER", b.math("ABSOLUTE", n.outputs[k]), 4.0) for k in ("X", "Y", "Z")]
    total = b.math("ADD", b.math("ADD", w[0], w[1]), w[2])
    w = [b.math("DIVIDE", wk, total) for wk in w]
    planes = [fn(b, y, z, s), fn(b, x, z, s), fn(b, x, y, s)]
    out = []
    for k in range(len(planes[0])):
        acc = b.math("MULTIPLY", planes[0][k], w[0])
        for plane, wk in zip(planes[1:], w[1:]):
            acc = b.math("ADD", acc, b.math("MULTIPLY", plane[k], wk))
        out.append(acc)
    return out


def _cell(b, a, c, s):
    """Cell coordinates of a square grid: (whole u, whole v, fraction u, fraction v)."""
    u, v = b.math("MULTIPLY", a, s), b.math("MULTIPLY", c, s)
    cu, cv = b.math("FLOOR", u), b.math("FLOOR", v)
    return cu, cv, b.math("SUBTRACT", u, cu), b.math("SUBTRACT", v, cv)


def _holes(b, a, c, s):
    """Round holes on a square grid: (distance from the nearest hole centre, 0..0.7)."""
    _cu, _cv, fu, fv = _cell(b, a, c, s)
    du, dv = b.math("SUBTRACT", fu, 0.5), b.math("SUBTRACT", fv, 0.5)
    return (b.math("SQRT", b.math("ADD", b.math("MULTIPLY", du, du), b.math("MULTIPLY", dv, dv))),)


def _build_leather(b):
    i = b.inp
    vor = b.node("ShaderNodeTexVoronoi", {"Randomness": 1.0})
    b.links.new(b.coords(), vor.inputs["Vector"])
    b.feed(vor.inputs["Scale"], _lerp(b, 40, 400, i("Grain scale")))
    # Perforation (0.9): round holes on a real-size grid (spacing in mm).
    s = b.math("DIVIDE", 1000.0, b.math("MAXIMUM", i("Hole spacing (mm)"), 0.5))
    d = _triplanar(b, _holes, s)[0]
    hole = b.node("ShaderNodeMapRange", {"From Min": 0.2, "From Max": 0.14, "To Min": 0.0, "To Max": 1.0},
                  interpolation_type="SMOOTHSTEP")
    b.feed(hole.inputs["Value"], d)
    hole = b.math("MULTIPLY", hole.outputs["Result"], i("Perforation"))
    height = b.math("SUBTRACT", b.math("MULTIPLY", vor.outputs["Distance"],
                                       b.math("MULTIPLY", i("Grain depth"), 0.5)),
                    b.math("MULTIPLY", hole, 0.6))
    b.bsdf({"Base Color": b.vmath("SCALE", i("Colour"), scale=b.math("SUBTRACT", 1.0,
                                                                     b.math("MULTIPLY", hole, 0.85))),
            "Metallic": 0.0,
            "Roughness": b.math("ADD", i("Roughness"), b.math("MULTIPLY", hole, 0.3), clamp=True),
            "Sheen Weight": i("Sheen"), "Sheen Roughness": 0.5,
            "Normal": b.bump(height, 1.0, 0.002)})


def _cloth(b, a, c, s):
    """Threads on a plane: (plain weave height, knit height, distance from the
    cell centre for open mesh)."""
    cu, cv, fu, fv = _cell(b, a, c, s)
    over = b.math("FLOORED_MODULO", b.math("ADD", cu, cv), 2.0)          # which thread is on top
    warp = b.math("SINE", b.math("MULTIPLY", fv, math.pi))
    weft = b.math("SINE", b.math("MULTIPLY", fu, math.pi))
    weave = b.math("ADD", warp, b.math("MULTIPLY", over, b.math("SUBTRACT", weft, warp)))
    # Knit: columns of V-shaped loops along v.
    off = b.math("ABSOLUTE", b.math("SUBTRACT", fu, 0.5))
    loop = b.math("FRACT", b.math("ADD", b.math("MULTIPLY", b.math("MULTIPLY", c, s), 1.5), off))
    knit = b.math("MULTIPLY", b.math("SINE", b.math("MULTIPLY", loop, math.pi)), weft)
    centre = b.math("MAXIMUM", off, b.math("ABSOLUTE", b.math("SUBTRACT", fv, 0.5)))
    return weave, knit, centre


def _band(b, value, centre):
    """1 at value == centre, falling to 0 one step away (picks a Pattern)."""
    return b.math("SUBTRACT", 1.0, b.math("ABSOLUTE", b.math("SUBTRACT", value, centre)), clamp=True)


def _build_fabric(b):
    """Cloth: a woven, knitted or open-mesh construction (or none: a brushed
    pile) in real thread sizes, sheen for the soft rim light of fibres, and a
    nap of lighter and darker patches along the brushing direction."""
    i = b.inp
    size = b.math("MAXIMUM", b.math("MULTIPLY", i("Thread size (cm)"), 0.01), 0.0001)    # metres
    s = b.math("DIVIDE", 1.0, size)
    weave, knit, centre = _triplanar(b, _cloth, s)
    p = i("Pattern")
    woven, knitted, mesh = _band(b, p, 1.0), _band(b, p, 2.0), _band(b, p, 3.0)
    threads = b.math("ADD", woven, b.math("ADD", knitted, mesh), clamp=True)
    height = b.math("ADD", b.math("MULTIPLY", b.math("ADD", woven, mesh), weave),
                    b.math("MULTIPLY", knitted, knit))
    gap = b.math("LESS_THAN", centre, b.math("MULTIPLY", i("Openness"), 0.45))
    alpha = b.math("SUBTRACT", 1.0, b.math("MULTIPLY", gap, mesh), clamp=True)
    # Nap: patches stretched along the brushing direction.
    rot = b.node("ShaderNodeCombineXYZ")
    b.feed(rot.inputs["Z"], b.math("MULTIPLY", i("Nap direction"), math.pi))
    mp = b.node("ShaderNodeMapping")
    b.links.new(b.coords(), mp.inputs["Vector"])
    b.links.new(rot.outputs[0], mp.inputs["Rotation"])
    mp.inputs["Scale"].default_value = (8.0, 40.0, 40.0)
    nap = b.node("ShaderNodeTexNoise", {"Scale": 1.0, "Detail": 4.0, "Roughness": 0.6})
    b.links.new(mp.outputs[0], nap.inputs["Vector"])
    napv = b.math("MULTIPLY", b.math("SUBTRACT", nap.outputs["Fac"], 0.5), b.math("MULTIPLY", i("Nap"), 0.8))
    fibre = b.node("ShaderNodeTexNoise", {"Scale": 4000.0, "Detail": 2.0, "Roughness": 0.5})
    b.links.new(b.coords(), fibre.inputs["Vector"])
    shade = b.math("ADD", b.math("ADD", 1.0, napv),
                   b.math("MULTIPLY", threads, b.math("SUBTRACT", b.math("MULTIPLY", height, 0.35), 0.25)))
    fuzz = i("Fuzz")
    b.bsdf({"Base Color": b.vmath("SCALE", i("Colour"), scale=shade), "Metallic": 0.0,
            "Roughness": i("Roughness"),
            "Specular IOR Level": b.math("SUBTRACT", 0.5, b.math("MULTIPLY", fuzz, 0.3)),
            "Sheen Weight": fuzz, "Sheen Tint": i("Sheen colour"),
            "Sheen Roughness": b.math("SUBTRACT", 0.6, b.math("MULTIPLY", i("Nap"), 0.3)),
            "Alpha": alpha,
            "Normal": b.bump(b.math("ADD", b.math("MULTIPLY", height, threads),
                                    b.math("MULTIPLY", fibre.outputs["Fac"], 0.15)),
                             b.math("ADD", b.math("MULTIPLY", i("Weave depth"), threads), 0.05),
                             b.math("MULTIPLY", size, 0.15))})


def _build_wood(b):
    """Wood: growth rings (rings around the grain axis, warped by Figure) and
    streaks along it, both world space in real sizes; pores are fine dark
    needles along the grain, open (bump, rougher) unless lacquered over."""
    i = b.inp
    rot = b.node("ShaderNodeCombineXYZ")
    b.feed(rot.inputs["Z"], b.math("MULTIPLY", i("Grain direction"), math.pi))
    mp = b.node("ShaderNodeMapping")
    b.links.new(b.coords(), mp.inputs["Vector"])
    b.links.new(rot.outputs[0], mp.inputs["Rotation"])
    mp.inputs["Location"].default_value = (0.0, 0.55, 0.4)  # the tree's axis well outside the part:
    grain = mp.outputs[0]                                   # flat-sawn lines, not a bullseye; along X
    size = b.math("MAXIMUM", i("Grain size (cm)"), 0.05)
    wave = b.node("ShaderNodeTexWave", {"Detail": 3.0, "Detail Scale": 1.5, "Detail Roughness": 0.6},
                  wave_type="RINGS", rings_direction="X")
    b.links.new(grain, wave.inputs["Vector"])
    b.feed(wave.inputs["Scale"], b.math("DIVIDE", 2.0 * math.pi / 20.0 * 100.0, size))  # ring period = size
    b.feed(wave.inputs["Distortion"], b.math("MULTIPLY", i("Figure"), 15.0))
    late = b.math("POWER", wave.outputs["Fac"], 4.0)               # the dark latewood is the thin part
    stretch = b.node("ShaderNodeCombineXYZ")
    k = b.math("DIVIDE", 100.0, size)
    b.feed(stretch.inputs["X"], b.math("MULTIPLY", k, 0.1))
    b.feed(stretch.inputs["Y"], b.math("MULTIPLY", k, 2.0))
    b.feed(stretch.inputs["Z"], b.math("MULTIPLY", k, 2.0))
    streak = b.node("ShaderNodeTexNoise", {"Scale": 1.0, "Detail": 6.0, "Roughness": 0.6})
    b.links.new(b.vmath("MULTIPLY", grain, stretch.outputs[0]), streak.inputs["Vector"])
    tone = b.math("ADD", b.math("MULTIPLY", late, 0.6),
                  b.math("MULTIPLY", b.math("SUBTRACT", streak.outputs["Fac"], 0.35), 0.7), clamp=True)
    wood = b.mix(tone, i("Light colour"), i("Dark colour"))
    pore_map = b.node("ShaderNodeCombineXYZ")
    pore_map.inputs["X"].default_value, pore_map.inputs["Y"].default_value = 250.0, 3500.0
    pore_map.inputs["Z"].default_value = 3500.0
    pore_noise = b.node("ShaderNodeTexNoise", {"Scale": 1.0, "Detail": 1.0, "Roughness": 0.5})
    b.links.new(b.vmath("MULTIPLY", grain, pore_map.outputs[0]), pore_noise.inputs["Vector"])
    pore = b.node("ShaderNodeMapRange", {"From Min": 0.6, "From Max": 0.72}, interpolation_type="SMOOTHSTEP")
    b.feed(pore.inputs["Value"], pore_noise.outputs["Fac"])
    pore = b.math("MULTIPLY", pore.outputs["Result"], i("Pores"))
    b.bsdf({"Base Color": b.vmath("SCALE", wood, scale=b.math("SUBTRACT", 1.0, b.math("MULTIPLY", pore, 0.55))),
            "Metallic": 0.0,
            "Roughness": b.math("ADD", i("Roughness"), b.math("MULTIPLY", pore, 0.25), clamp=True),
            "Normal": b.bump(b.math("SUBTRACT", b.math("MULTIPLY", late, 0.1), pore), 0.3, 0.0005),
            "Coat Weight": i("Clearcoat"), "Coat Roughness": i("Clearcoat roughness")})


def _build_stone(b):
    """Stone and ceramic: two colours mottled at a real size, optional veins
    (marble) and speckle (stoneware, terrazzo-like concrete, card), small pits
    (air bubbles in concrete), and a glaze coat for ceramic and polish."""
    i = b.inp
    p = b.coords()
    mottle = b.node("ShaderNodeTexNoise", {"Detail": 6.0, "Roughness": 0.6})
    b.links.new(p, mottle.inputs["Vector"])
    b.feed(mottle.inputs["Scale"], b.math("DIVIDE", 100.0, b.math("MAXIMUM", i("Mottle size (cm)"), 0.05)))
    soft = b.node("ShaderNodeMapRange", {"From Min": 0.35, "From Max": 0.65}, interpolation_type="SMOOTHSTEP")
    b.feed(soft.inputs["Value"], mottle.outputs["Fac"])
    fac = b.math("MULTIPLY", soft.outputs["Result"], i("Mottle"))
    veins = b.node("ShaderNodeTexWave", {"Distortion": 14.0, "Detail": 10.0, "Detail Scale": 1.2,
                                         "Detail Roughness": 0.65}, wave_type="BANDS")
    b.links.new(p, veins.inputs["Vector"])
    b.feed(veins.inputs["Scale"], b.math("DIVIDE", 60.0, b.math("MAXIMUM", i("Mottle size (cm)"), 0.05)))
    vein = b.node("ShaderNodeMapRange", {"From Min": 0.965, "From Max": 1.0}, interpolation_type="SMOOTHSTEP")
    b.feed(vein.inputs["Value"], veins.outputs["Fac"])
    fac = b.math("MAXIMUM", fac, b.math("MULTIPLY", vein.outputs["Result"], i("Veins")))
    speck_n = b.node("ShaderNodeTexNoise", {"Scale": 1500.0, "Detail": 0.0})
    b.links.new(p, speck_n.inputs["Vector"])
    speck = b.math("MULTIPLY", b.math("GREATER_THAN", speck_n.outputs["Fac"], 0.66), i("Speckle"))
    pits_v = b.node("ShaderNodeTexVoronoi", {"Scale": 300.0, "Randomness": 1.0})
    b.links.new(p, pits_v.inputs["Vector"])
    pit = b.node("ShaderNodeMapRange", {"From Min": 0.16, "From Max": 0.06}, interpolation_type="SMOOTHSTEP")
    b.feed(pit.inputs["Value"], pits_v.outputs["Distance"])
    pit = b.math("MULTIPLY", pit.outputs["Result"], i("Pits"))
    colour = b.mix(fac, i("Colour"), i("Second colour"))
    shade = b.math("SUBTRACT", 1.0, b.math("ADD", b.math("MULTIPLY", speck, 0.45), b.math("MULTIPLY", pit, 0.3)))
    rough = b.math("ADD", i("Roughness"),
                   b.math("ADD", b.math("MULTIPLY", b.math("SUBTRACT", mottle.outputs["Fac"], 0.5),
                                        b.math("MULTIPLY", i("Mottle"), 0.15)),
                          b.math("MULTIPLY", pit, 0.2)), clamp=True)
    b.bsdf({"Base Color": b.vmath("SCALE", colour, scale=shade), "Metallic": 0.0, "Roughness": rough,
            "Normal": b.bump(b.math("SUBTRACT", b.math("MULTIPLY", mottle.outputs["Fac"], 0.05), pit), 0.4,
                             0.002),
            "Coat Weight": i("Glaze"), "Coat Roughness": i("Glaze roughness")})


def _build_glow(b):
    """Emission over an ordinary surface. LED dots: a world-space grid of
    round emitters (real size in cm); Edge fade: dimmer at grazing angles, as
    a tube or a lens seen side-on."""
    i = b.inp
    grid = b.node("ShaderNodeTexVoronoi", {"Randomness": 0.0})
    b.links.new(b.coords(), grid.inputs["Vector"])
    b.feed(grid.inputs["Scale"], b.math("DIVIDE", 100.0, i("Dot size (cm)")))
    off = b.math("GREATER_THAN", grid.outputs["Distance"], 0.32)        # 1 between the dots
    mask = b.math("SUBTRACT", 1.0, b.math("MULTIPLY", i("LED dots"), off))
    facing = b.node("ShaderNodeLayerWeight", {"Blend": 0.35}).outputs["Facing"]
    edge = b.math("SUBTRACT", 1.0, b.math("MULTIPLY", i("Edge fade"), facing), clamp=True)
    b.bsdf({"Base Color": i("Surface colour"), "Metallic": 0.0, "Roughness": i("Roughness"),
            "Coat Weight": i("Lens"), "Coat Roughness": 0.02,
            "Emission Color": i("Glow colour"),
            "Emission Strength": b.math("MULTIPLY", i("Brightness"), b.math("MULTIPLY", mask, edge))})


def _build_tyre(b):
    """Tread on the running surface, sidewall on the sides, both in the tyre's
    own coordinates (TYRE_ATTRIBUTE: x around 0..1, y across -1..1, z from the
    rim 0 to the tread 1). Tread: circumferential grooves, lateral grooves
    (angled into a V by Direction, staggered between ribs) and sipes, as
    bump; worn tread is shallower and scuffed lighter. Sidewall: fine
    concentric ribs near the rim and a dressing shine. Dust settles in the
    grooves and crevices (ambient occlusion)."""
    i = b.inp
    attr = b.node("ShaderNodeAttribute", attribute_type="GEOMETRY", attribute_name=TYRE_ATTRIBUTE)
    sep = b.node("ShaderNodeSeparateXYZ")
    b.links.new(attr.outputs["Vector"], sep.inputs[0])
    u, v, r = sep.outputs["X"], sep.outputs["Y"], sep.outputs["Z"]
    tread = b.node("ShaderNodeMapRange", {"From Min": 0.8, "From Max": 0.9},
                   interpolation_type="SMOOTHSTEP")
    b.feed(tread.inputs["Value"], r)
    tread = tread.outputs["Result"]
    av = b.math("ABSOLUTE", v)
    gw = b.math("ADD", 0.04, b.math("MULTIPLY", i("Groove width"), 0.3))      # fraction of a block
    # Circumferential grooves between ribs (none at the shoulders).
    k = b.math("ROUND", i("Grooves"))
    rc = b.math("MULTIPLY", b.math("ADD", b.math("MULTIPLY", v, 0.5), 0.5), b.math("ADD", k, 1.0))
    near = b.math("ABSOLUTE", b.math("SUBTRACT", rc, b.math("ROUND", rc)))
    circ = b.math("MULTIPLY", b.math("LESS_THAN", near, b.math("MULTIPLY", gw, 0.5)),
                  b.math("MULTIPLY", b.math("GREATER_THAN", k, 0.5), b.math("LESS_THAN", av, 0.9)))
    # Lateral grooves: N around, V-angled by Direction, staggered rib to rib.
    n = b.math("ROUND", i("Blocks around"))
    rib = b.math("FLOOR", rc)
    stagger = b.math("MULTIPLY", b.math("FLOORED_MODULO", rib, 2.0), 0.5)
    uu = b.math("ADD", b.math("ADD", b.math("MULTIPLY", u, n), b.math("MULTIPLY", av, b.math(
        "MULTIPLY", i("Direction"), 2.0))), stagger)
    lat = b.math("MULTIPLY", b.math("LESS_THAN", b.math("FRACT", uu), gw), b.math("GREATER_THAN", n, 0.5))
    sipe = b.math("MULTIPLY", b.math("LESS_THAN", b.math("FRACT", b.math("MULTIPLY", uu, 4.0)), 0.06),
                  b.math("MULTIPLY", i("Sipes"), b.math("GREATER_THAN", n, 0.5)))
    groove = b.math("MAXIMUM", b.math("MAXIMUM", circ, lat), b.math("MULTIPLY", sipe, 0.5))
    tread_h = b.math("SUBTRACT", 1.0, groove)
    # Sidewall: fine rings in a band near the rim.
    band = b.math("MULTIPLY", b.math("GREATER_THAN", r, 0.12), b.math("LESS_THAN", r, 0.4))
    rings = b.math("MULTIPLY", b.math("SINE", b.math("MULTIPLY", r, 600.0)),
                   b.math("MULTIPLY", band, i("Sidewall ribs")))
    side_h = b.math("MULTIPLY", rings, 0.5)
    height = b.math("ADD", side_h, b.math("MULTIPLY", tread, b.math("SUBTRACT", tread_h, side_h)))
    wear = i("Wear")
    depth = b.math("MULTIPLY", i("Tread depth"), b.math("SUBTRACT", 1.0, b.math("MULTIPLY", wear, 0.8)))
    strength = b.math("ADD", 0.05, b.math("MULTIPLY", tread, b.math("MULTIPLY", depth, 0.9)))
    ao = b.node("ShaderNodeAmbientOcclusion", {"Distance": 0.03}, samples=16)
    cavity = b.math("SUBTRACT", 1.0, ao.outputs["AO"])
    dust = b.math("MULTIPLY", b.math("MULTIPLY", cavity, 1.5), i("Dust"), clamp=True)
    # Groove floors read darker than the block tops; worn tops scuff lighter.
    floor = b.math("MULTIPLY", b.math("MULTIPLY", groove, tread), b.math("MINIMUM", depth, 1.0))
    scuff = b.math("MULTIPLY", tread, b.math("MULTIPLY", tread_h, b.math("ADD", 0.15, wear)))
    shade = b.math("SUBTRACT", b.math("ADD", 1.0, b.math("MULTIPLY", scuff, 0.8)),
                   b.math("MULTIPLY", floor, 0.55))
    rubber = b.vmath("SCALE", i("Colour"), scale=shade)
    # Whitewall (0.9): a white band out from the rim, as wide as Whitewall says.
    ww = b.math("MULTIPLY", b.math("GREATER_THAN", r, 0.2),
                b.math("LESS_THAN", r, b.math("ADD", 0.2, b.math("MULTIPLY", i("Whitewall"), 0.5))))
    ww = b.math("MULTIPLY", ww, b.math("MULTIPLY", b.math("SUBTRACT", 1.0, tread),
                                       b.math("GREATER_THAN", i("Whitewall"), 0.01)))
    rubber = b.mix(ww, rubber, i("Whitewall colour"))
    shine = b.math("MULTIPLY", i("Sidewall shine"), b.math("SUBTRACT", 1.0, tread))
    rough = b.math("ADD", b.math("SUBTRACT", i("Roughness"), b.math("MULTIPLY", shine, 0.4)),
                   b.math("MULTIPLY", dust, 0.2), clamp=True)
    b.bsdf({"Base Color": b.mix(dust, rubber, i("Dust colour")), "Metallic": 0.0, "Roughness": rough,
            "Specular IOR Level": b.math("ADD", 0.3, b.math("MULTIPLY", shine, 0.4)),
            "Sheen Weight": b.math("ADD", 0.1, b.math("MULTIPLY", dust, 0.4), clamp=True),
            "Sheen Tint": i("Dust colour"), "Sheen Roughness": 0.5,
            "Coat Weight": b.math("MULTIPLY", shine, 0.5), "Coat Roughness": 0.15,
            "Normal": b.bump(height, strength, 0.008)})


def _build_disc(b):
    """A brake disc in its own coordinates (DISC_ATTRIBUTE: x around 0..1, y
    across the friction ring's thickness -1..1 (the hat may reach past it), z
    out from the centre 0..1 of the radius). Inside Hat size is the hat; outside
    it the friction ring: rows of drilled holes (staggered into a spiral) and
    curved slots on its faces, vent gaps between the plates on its edge, fine
    turning rings, heat tint across the ring, flash rust and ceramic speckle."""
    i = b.inp
    attr = b.node("ShaderNodeAttribute", attribute_type="GEOMETRY", attribute_name=DISC_ATTRIBUTE)
    sep = b.node("ShaderNodeSeparateXYZ")
    b.links.new(attr.outputs["Vector"], sep.inputs[0])
    u, v, z = sep.outputs["X"], sep.outputs["Y"], sep.outputs["Z"]
    av = b.math("ABSOLUTE", v)
    hat = i("Hat size")
    ring = b.node("ShaderNodeMapRange", interpolation_type="SMOOTHSTEP")
    b.feed(ring.inputs["Value"], z)
    b.feed(ring.inputs["From Min"], hat)
    b.feed(ring.inputs["From Max"], b.math("ADD", hat, 0.01))
    ring = ring.outputs["Result"]                                     # 1 on the friction ring
    span = b.math("MAXIMUM", b.math("SUBTRACT", 1.0, hat), 0.02)      # ring width (radius units)
    t = b.math("DIVIDE", b.math("SUBTRACT", z, hat), span, clamp=True)   # 0 inner .. 1 outer edge
    face = b.math("MULTIPLY", ring, b.math("GREATER_THAN", av, 0.85))
    edge = b.math("MULTIPLY", b.math("GREATER_THAN", z, 0.985), b.math("LESS_THAN", av, 0.95))
    circ = b.math("MULTIPLY", z, 2 * math.pi)                         # circumference here
    # Drilled holes: rows across the middle of the ring, each row turned a
    # little further round (the spiral real discs use).
    rows = b.math("ROUND", i("Drilled holes"))
    n = b.math("ROUND", i("Holes around"))
    tt = b.math("DIVIDE", b.math("SUBTRACT", t, 0.12), 0.76)
    row = b.math("MINIMUM", b.math("MAXIMUM", b.math("FLOOR", b.math("MULTIPLY", tt, rows)), 0.0),
                 b.math("SUBTRACT", rows, 1.0))
    centre = b.math("DIVIDE", b.math("ADD", row, 0.5), b.math("MAXIMUM", rows, 1.0))
    su = b.math("ADD", b.math("MULTIPLY", u, n), b.math("DIVIDE", row, b.math("MAXIMUM", rows, 1.0)))
    du = b.math("MULTIPLY", b.math("SUBTRACT", b.math("FRACT", su), 0.5), b.math("DIVIDE", circ, n))
    pitch = b.math("DIVIDE", b.math("MULTIPLY", span, 0.76), b.math("MAXIMUM", rows, 1.0))
    dr = b.math("MULTIPLY", b.math("SUBTRACT", tt, centre), b.math("MULTIPLY", span, 0.76))
    d = b.math("SQRT", b.math("ADD", b.math("MULTIPLY", du, du), b.math("MULTIPLY", dr, dr)))
    hr = b.math("MULTIPLY", b.math("MINIMUM", pitch, b.math("DIVIDE", circ, n)),
                b.math("ADD", 0.1, b.math("MULTIPLY", i("Hole size"), 0.3)))
    inside = b.math("MULTIPLY", b.math("GREATER_THAN", tt, 0.0), b.math("LESS_THAN", tt, 1.0))
    hole = b.math("MULTIPLY", b.math("MULTIPLY", b.math("LESS_THAN", d, hr), inside),
                  b.math("MULTIPLY", face, b.math("GREATER_THAN", rows, 0.5)))
    # Slots: thin curved grooves sweeping across the ring.
    m = b.math("ROUND", i("Slots"))
    ss = b.math("FRACT", b.math("ADD", b.math("MULTIPLY", u, m), b.math("MULTIPLY", t, 0.45)))
    sd = b.math("MULTIPLY", b.math("ABSOLUTE", b.math("SUBTRACT", ss, 0.5)),
                b.math("DIVIDE", circ, b.math("MAXIMUM", m, 1.0)))
    slot = b.math("MULTIPLY", b.math("LESS_THAN", sd, 0.009),
                  b.math("MULTIPLY", b.math("MULTIPLY", b.math("GREATER_THAN", t, 0.1),
                                            b.math("LESS_THAN", t, 0.9)),
                         b.math("MULTIPLY", face, b.math("GREATER_THAN", m, 0.5))))
    slot = b.math("MULTIPLY", slot, b.math("SUBTRACT", 1.0, hole))
    # Vents: gaps between the two plates, seen on the edge.
    k = b.math("ROUND", i("Vents"))
    gaps = b.math("LESS_THAN", b.math("FRACT", b.math("MULTIPLY", u, k)), 0.55)
    vent = b.math("MULTIPLY", b.math("MULTIPLY", gaps, b.math("LESS_THAN", av, 0.45)),
                  b.math("MULTIPLY", edge, b.math("GREATER_THAN", k, 0.5)))
    cut = b.math("MAXIMUM", hole, vent)
    # Turning rings on the ring's faces.
    turned = b.math("MULTIPLY", b.math("SINE", b.math("MULTIPLY", z, 2400.0)),
                    b.math("MULTIPLY", i("Machining"), face))
    # The disc's own space for noise: x, y in radii, across a little.
    ang = b.math("MULTIPLY", u, 2 * math.pi)
    local = b.node("ShaderNodeCombineXYZ")
    b.feed(local.inputs["X"], b.math("MULTIPLY", z, b.math("COSINE", ang)))
    b.feed(local.inputs["Y"], b.math("MULTIPLY", z, b.math("SINE", ang)))
    b.feed(local.inputs["Z"], b.math("MULTIPLY", v, 0.05))
    blotch = b.node("ShaderNodeTexNoise", {"Scale": 6.0, "Detail": 8.0, "Roughness": 0.6})
    b.links.new(local.outputs[0], blotch.inputs["Vector"])
    blotch = blotch.outputs["Fac"]
    rust = b.node("ShaderNodeMapRange")
    b.feed(rust.inputs["Value"], blotch)
    b.feed(rust.inputs["From Min"], b.math("SUBTRACT", 0.7, b.math("MULTIPLY", i("Rust"), 0.25)))
    b.feed(rust.inputs["From Max"], b.math("SUBTRACT", 0.76, b.math("MULTIPLY", i("Rust"), 0.25)))
    rust = b.math("MULTIPLY", rust.outputs["Result"], b.math("GREATER_THAN", i("Rust"), 0.001))
    rust = b.math("MULTIPLY", rust, b.math("SUBTRACT", 1.0, cut))
    fleck = b.node("ShaderNodeTexNoise", {"Scale": 220.0, "Detail": 2.0})
    b.links.new(local.outputs[0], fleck.inputs["Vector"])
    speck = b.math("MULTIPLY", b.math("GREATER_THAN", fleck.outputs["Fac"], 0.6), i("Speckle"))
    # Colour: iron (or ceramic) on the ring, the hat's own colour inside it,
    # heat tint straw-gold near the hat and blue over the rest, rust and holes on top.
    iron = b.mix(b.math("MULTIPLY", speck, 0.6), i("Colour"), (0.42, 0.42, 0.44, 1))
    blued = b.node("ShaderNodeMapRange", {"From Min": 0.0, "From Max": 0.35}, interpolation_type="SMOOTHSTEP")
    b.feed(blued.inputs["Value"], t)
    heat = b.mix(blued.outputs["Result"], (0.42, 0.28, 0.08, 1), (0.10, 0.12, 0.32, 1))
    tint = b.math("MULTIPLY", b.math("MULTIPLY", i("Heat tint"), ring),
                  b.math("ADD", 0.35, b.math("MULTIPLY", blotch, 0.65)))
    iron = b.mix(b.math("MULTIPLY", tint, 0.75), iron, heat)
    base = b.mix(ring, i("Hat colour"), iron)
    base = b.mix(rust, base, (0.16, 0.06, 0.025, 1))
    base = b.mix(b.math("MAXIMUM", cut, b.math("MULTIPLY", slot, 0.6)), base, (0.015, 0.015, 0.016, 1))
    ceramic = b.math("MULTIPLY", i("Speckle"), ring)
    metal = b.math("MULTIPLY", b.math("SUBTRACT", 1.0, b.math("MAXIMUM", rust, cut)),
                   b.math("SUBTRACT", 1.0, b.math("MULTIPLY", ceramic, 0.85)))
    metal = b.math("SUBTRACT", metal, b.math("MULTIPLY", b.math("SUBTRACT", 1.0, ring), 0.4))
    rough = b.math("ADD", i("Roughness"), b.math("MULTIPLY", b.math("MAXIMUM", rust, cut), 0.6), clamp=True)
    rough = b.math("ADD", rough, b.math("MULTIPLY", turned, 0.05), clamp=True)
    height = b.math("SUBTRACT", b.math("ADD", 1.0, b.math("MULTIPLY", turned, 0.04)),
                    b.math("ADD", cut, b.math("MULTIPLY", slot, 0.6)))
    b.bsdf({"Base Color": base, "Metallic": b.math("MAXIMUM", metal, 0.0), "Roughness": rough,
            "Normal": b.bump(height, 0.6, 0.004)})


def _build_image(b):
    """The image (wired in from the material's image node) over a backing
    colour where it's transparent, tinted and brightened, optionally lit."""
    i = b.inp
    picture = b.vmath("MULTIPLY", i("Image colour"), i("Tint"))
    shown = b.vmath("SCALE", picture, scale=i("Brightness"))
    b.bsdf({"Base Color": b.mix(i("Image alpha"), i("Backing colour"), shown),
            "Metallic": i("Metallic"), "Roughness": i("Roughness"),
            "Coat Weight": i("Clearcoat"), "Coat Roughness": 0.03,
            "Emission Color": picture,
            "Emission Strength": b.math("MULTIPLY", i("Glow"), i("Image alpha"))})


def _build_textures(b):
    """A PBR texture set: colour darkened by occlusion, roughness and metal
    scaled, a tangent-space normal map then bump from the height map, glow
    and opacity. The maps come in on the Linked inputs."""
    i = b.inp
    ao = b.math("ADD", b.math("SUBTRACT", 1.0, i("Occlusion")),
                b.math("MULTIPLY", i("Occlusion"), i("Map occlusion")))
    nmap = b.node("ShaderNodeNormalMap", space="TANGENT")
    b.feed(nmap.inputs["Color"], i("Map normal"))
    b.feed(nmap.inputs["Strength"], i("Normal strength"))
    normal = b.bump(i("Map height"), b.math("MULTIPLY", i("Bump strength"), 0.5), 0.01, nmap.outputs["Normal"])
    b.bsdf({"Base Color": b.vmath("SCALE", i("Map colour"), scale=ao), "Metallic":
            b.math("MULTIPLY", i("Map metallic"), i("Metallic"), clamp=True),
            "Roughness": b.math("MULTIPLY", i("Map roughness"), i("Roughness"), clamp=True),
            "Normal": normal, "Alpha": b.math("MULTIPLY", i("Map alpha"), i("Opacity"), clamp=True),
            "Emission Color": i("Map glow"), "Emission Strength": i("Glow"),
            "Coat Weight": i("Clearcoat"), "Coat Roughness": 0.03})


def _build_blackout(b):
    """Light in, nothing out: base colour and specular both go to zero as
    Blackness reaches 1 (Specular IOR Level 0 turns the Fresnel highlight off)."""
    i = b.inp
    keep = b.math("SUBTRACT", 1.0, i("Blackness"), clamp=True)
    b.bsdf({"Base Color": b.vmath("SCALE", i("Colour"), scale=keep), "Metallic": 0.0,
            "Roughness": i("Roughness"),
            "Specular IOR Level": b.math("MULTIPLY", i("Reflection"), 0.5),
            "Coat Weight": 0.0, "Sheen Weight": 0.0, "Emission Strength": 0.0})


_BUILDERS = {"BLACKOUT": _build_blackout, "TEXTURES": _build_textures, "PAINT": _build_paint, "METAL": _build_metal, "GLASS": _build_glass,
             "PLASTIC": _build_plastic, "RUBBER": _build_rubber, "CARBON": _build_carbon,
             "LEATHER": _build_leather, "GLOW": _build_glow, "TYRE": _build_tyre,
             "IMAGE": _build_image, "DISC": _build_disc, "FABRIC": _build_fabric,
             "WOOD": _build_wood, "STONE": _build_stone}


def group_name(type_key):
    spec = TYPES[type_key]
    return spec.get("library") or "Plinth_" + spec["label"].replace(" ", "_")


def socket(node, type_key, name):
    """The group-node input behind a control: by index for UDIN types (their
    groups reuse names), by name for Plinth's own."""
    index = TYPES[type_key].get("sockets", {}).get(name)
    return node.inputs[index] if index is not None else node.inputs[name]


def _udin_group(spec):
    """A UDIN type's group, appended from the bundled library once. Every
    missing group comes in one load so their shared nested groups aren't
    duplicated; any ``.001`` copy that still appears is remapped and removed."""
    ng = bpy.data.node_groups.get(spec["library"])
    if ng is not None and ng.get("plinth_udin") == UDIN_VERSION:
        return ng
    if ng is not None:                                   # an older Plinth's copy: keep it for
        ng.name = ng.name + " (old)"                      # materials that use it, load the new
    wanted = sorted({t["library"] for t in TYPES.values() if t.get("library")})
    missing = [n for n in wanted if bpy.data.node_groups.get(n) is None]
    before = set(bpy.data.node_groups)
    with bpy.data.libraries.load(UDIN_LIBRARY, link=False) as (src, dst):
        dst.node_groups = [n for n in missing if n in src.node_groups]
    for new in [g for g in bpy.data.node_groups if g not in before]:
        base, _dot, suffix = new.name.rpartition(".")
        twin = bpy.data.node_groups.get(base) if suffix.isdigit() else None
        if twin is not None and twin is not new and twin.get("plinth_udin") == new.get("plinth_udin"):
            new.user_remap(twin)
            bpy.data.node_groups.remove(new)
    for other in TYPES.values():                         # browsable like Plinth's own types
        ng = bpy.data.node_groups.get(other.get("library") or "")
        if ng is not None and not ng.asset_data:
            ng.asset_mark()
            ng.asset_data.description = "Plinth %s (UDIN): %d controls" % (
                other["label"].lower(), len(other["inputs"]))
    return bpy.data.node_groups[spec["library"]]


def type_group(type_key):
    """Get the node group for a type, building it if missing or out of date.

    Returns (group, skipped). Existing inputs are never removed or reset, so
    materials already using the group keep their values."""
    spec = TYPES[type_key]
    if spec.get("library"):
        ng = _udin_group(spec)
        map_udin_groups()
        return ng, []
    name = group_name(type_key)
    ng = bpy.data.node_groups.get(name)
    if ng is not None and ng.get("plinth_version") == spec["version"] \
            and ng.get("plinth_mapping") == MAPPING_VERSION:
        return ng, list(ng.get("plinth_skipped", []))
    if ng is None:
        ng = bpy.data.node_groups.new(name, "ShaderNodeTree")
    have = {(item.name, item.in_out) for item in ng.interface.items_tree
            if getattr(item, "item_type", "SOCKET") == "SOCKET"}
    kinds = {"colour": "NodeSocketColor", "float": "NodeSocketFloat", "bool": "NodeSocketBool",
             "log": "NodeSocketFloat", "vector": "NodeSocketVector"}
    for name_, kind, default, lo, hi, _section in spec["inputs"]:
        if (name_, "INPUT") in have:
            continue
        sock = ng.interface.new_socket(name_, in_out="INPUT", socket_type=kinds[kind])
        sock.default_value = default
        if lo is not None:
            sock.min_value, sock.max_value = lo, hi
    for out in spec.get("outputs", ("Surface", "Volume")):
        if (out, "OUTPUT") not in have:
            ng.interface.new_socket(out, in_out="OUTPUT", socket_type="NodeSocketShader")
    ng.nodes.clear()                      # rebuild the insides only
    b = _Builder(ng)
    _BUILDERS[type_key](b)
    if type_key == "GLASS":
        map_udin_groups()                 # its wear comes from UDIN's nested groups
    ng["plinth_version"] = spec["version"]
    ng["plinth_mapping"] = MAPPING_VERSION
    ng["plinth_skipped"] = b.skipped
    if not ng.asset_data:
        ng.asset_mark()
    ng.asset_data.description = "Plinth %s: %d controls" % (spec["label"].lower(), len(spec["inputs"]))
    return ng, list(b.skipped)


# --- Mapping: world (triplanar) or UV, mirrored and turned per object ---------------------
# Every pattern reads its coordinates from one shared group, Plinth_Mapping.
# Its settings live on the *object* (custom properties read by Attribute nodes
# of type Object), not the material: two halves of a bonnet can share one
# carbon material and still weave in opposite directions, and parts that
# share a material deliberately (plinth_linked) keep that.
#   obj["plinth_map"]  = (mode 0 world / 1 UV, turn in degrees, UV size in metres)
#   obj["plinth_flip"] = (flip U 0/1, flip V 0/1, 0)
# Missing properties read as zeros: world projection, unturned, unflipped, which
# is what every part did before 0.13.
# World mode: Flip U mirrors across the car (X), Flip V front to back (Y), Turn
# spins about the vertical. UV mode: flips and turn act on the UV square
# (about its centre); patterns come out at UV size metres per UV unit, and the
# projection normal points straight up so triplanar patterns read the UV plane.

MAPPING_GROUP = "Plinth_Mapping"
MAPPING_VERSION = 1
MAP_ATTR = "plinth_map"
FLIP_ATTR = "plinth_flip"
MAP_MODES = ("WORLD", "UV")
NO_MAPPING = {"TYRE", "DISC", "IMAGE", "BLACKOUT"}       # own coordinates, or no pattern at all
UV_SIZE_RANGE = (0.01, 100.0)                           # metres per UV unit


def mapping_group():
    """The shared coordinate group (built once, versioned)."""
    ng = bpy.data.node_groups.get(MAPPING_GROUP)
    if ng is not None and ng.get("plinth_version") == MAPPING_VERSION:
        return ng
    if ng is None:
        ng = bpy.data.node_groups.new(MAPPING_GROUP, "ShaderNodeTree")
    have = {(i.name, i.in_out) for i in ng.interface.items_tree if getattr(i, "item_type", "SOCKET") == "SOCKET"}
    for name, kind in (("Vector", "NodeSocketVector"), ("Normal", "NodeSocketVector"),
                       ("UV", "NodeSocketVector"), ("Mode", "NodeSocketFloat"), ("Size", "NodeSocketFloat"),
                       ("World", "NodeSocketVector")):
        if (name, "OUTPUT") not in have:
            ng.interface.new_socket(name, in_out="OUTPUT", socket_type=kind)
    ng.nodes.clear()
    n, link = ng.nodes, ng.links.new
    out = n.new("NodeGroupOutput")

    def node(idname, **props):
        x = n.new(idname)
        for k, v in props.items():
            setattr(x, k, v)
        return x

    def math(op, a, b=None):
        m = node("ShaderNodeMath", operation=op)
        for sock, v in ((m.inputs[0], a), (m.inputs[1], b)):
            if v is None:
                continue
            if hasattr(v, "is_output"):
                link(v, sock)
            else:
                sock.default_value = v
        return m.outputs[0]

    amap = node("ShaderNodeAttribute", attribute_type="OBJECT", attribute_name=MAP_ATTR)
    aflip = node("ShaderNodeAttribute", attribute_type="OBJECT", attribute_name=FLIP_ATTR)
    sm, sf = node("ShaderNodeSeparateXYZ"), node("ShaderNodeSeparateXYZ")
    link(amap.outputs["Vector"], sm.inputs[0])
    link(aflip.outputs["Vector"], sf.inputs[0])
    mode = math("GREATER_THAN", sm.outputs["X"], 0.5)
    angle = math("RADIANS", sm.outputs["Y"])
    raw = sm.outputs["Z"]
    size = math("ADD", raw, math("LESS_THAN", raw, 1e-6))          # unset (0) -> 1 m
    sign_u = math("SUBTRACT", 1.0, math("MULTIPLY", math("GREATER_THAN", sf.outputs["X"], 0.5), 2.0))
    sign_v = math("SUBTRACT", 1.0, math("MULTIPLY", math("GREATER_THAN", sf.outputs["Y"], 0.5), 2.0))
    signs = node("ShaderNodeCombineXYZ")
    link(sign_u, signs.inputs["X"])
    link(sign_v, signs.inputs["Y"])
    signs.inputs["Z"].default_value = 1.0

    geo = node("ShaderNodeNewGeometry")
    world = node("ShaderNodeVectorMath", operation="MULTIPLY")
    link(geo.outputs["Position"], world.inputs[0])
    link(signs.outputs[0], world.inputs[1])
    turn_w = node("ShaderNodeVectorRotate", rotation_type="Z_AXIS")
    link(world.outputs[0], turn_w.inputs["Vector"])
    link(angle, turn_w.inputs["Angle"])

    tc = node("ShaderNodeTexCoord")
    flat = node("ShaderNodeVectorMath", operation="MULTIPLY")      # (u, v, 0)
    link(tc.outputs["UV"], flat.inputs[0])
    flat.inputs[1].default_value = (1.0, 1.0, 0.0)
    flipped = node("ShaderNodeVectorMath", operation="MULTIPLY_ADD")   # about the square's centre
    centred = node("ShaderNodeVectorMath", operation="SUBTRACT")
    link(flat.outputs[0], centred.inputs[0])
    centred.inputs[1].default_value = (0.5, 0.5, 0.0)
    link(centred.outputs[0], flipped.inputs[0])
    link(signs.outputs[0], flipped.inputs[1])
    flipped.inputs[2].default_value = (0.0, 0.0, 0.0)
    turn_uv = node("ShaderNodeVectorRotate", rotation_type="Z_AXIS")
    link(flipped.outputs[0], turn_uv.inputs["Vector"])
    link(angle, turn_uv.inputs["Angle"])
    uv = node("ShaderNodeVectorMath", operation="ADD")
    link(turn_uv.outputs[0], uv.inputs[0])
    uv.inputs[1].default_value = (0.5, 0.5, 0.0)
    metres = node("ShaderNodeVectorMath", operation="SCALE")
    link(uv.outputs[0], metres.inputs[0])
    link(size, metres.inputs["Scale"])

    vec = node("ShaderNodeMix", data_type="VECTOR")
    ids = {s.identifier: s for s in vec.inputs}
    link(mode, ids["Factor_Float"])
    link(turn_w.outputs[0], ids["A_Vector"])
    link(metres.outputs[0], ids["B_Vector"])
    nrm = node("ShaderNodeMix", data_type="VECTOR")
    ids_n = {s.identifier: s for s in nrm.inputs}
    link(mode, ids_n["Factor_Float"])
    link(geo.outputs["Normal"], ids_n["A_Vector"])
    ids_n["B_Vector"].default_value = (0.0, 0.0, 1.0)
    result = lambda m: next(s for s in m.outputs if s.identifier == "Result_Vector")
    link(result(vec), out.inputs["Vector"])
    link(result(nrm), out.inputs["Normal"])
    link(uv.outputs[0], out.inputs["UV"])
    link(mode, out.inputs["Mode"])
    link(size, out.inputs["Size"])
    link(turn_w.outputs[0], out.inputs["World"])
    ng["plinth_version"] = MAPPING_VERSION
    return ng


# UDIN's patterns read Geometry Position (build_udin.py made them world space);
# their projection normals sit in the triplanar and fishbone helpers.
_UDIN_PROJECTION = ("triplanar", "fishbone")


def map_udin_groups():
    """Route every UDIN group's pattern coordinates through Plinth_Mapping
    (once per group and MAPPING_VERSION). Shading normals are left alone."""
    group = None
    for ng in list(bpy.data.node_groups):
        if not ng.name.startswith("Plinth_UDIN") or ng.get("plinth_mapping") == MAPPING_VERSION:
            continue
        group = group or mapping_group()
        projection = any(word in ng.name for word in _UDIN_PROJECTION)
        for geo in [nd for nd in ng.nodes if nd.type == "NEW_GEOMETRY"]:
            moves = [("Position", "Vector")] + ([("Normal", "Normal")] if projection else [])
            for out_name, map_name in moves:
                targets = [lk.to_socket for lk in geo.outputs[out_name].links]
                if not targets:
                    continue
                m = ng.nodes.get("Plinth_Map") or ng.nodes.new("ShaderNodeGroup")
                m.name = m.label = "Plinth_Map"
                m.node_tree = group
                m.location = (geo.location.x, geo.location.y - 200)
                for sock in targets:
                    ng.links.new(m.outputs[map_name], sock)
        ng["plinth_mapping"] = MAPPING_VERSION


def mapping(obj):
    """The object's pattern mapping: {"mode", "turn" (deg), "size" (m per UV
    unit, 0 = not fitted yet), "flip_u", "flip_v"}. Read-only."""
    m = tuple(obj.get(MAP_ATTR, (0.0, 0.0, 0.0))) if obj is not None else (0.0, 0.0, 0.0)
    f = tuple(obj.get(FLIP_ATTR, (0.0, 0.0, 0.0))) if obj is not None else (0.0, 0.0, 0.0)
    return {"mode": MAP_MODES[1 if m[0] > 0.5 else 0], "turn": float(m[1]) % 360.0, "size": float(m[2]),
            "flip_u": f[0] > 0.5, "flip_v": f[1] > 0.5}


def has_uvs(obj):
    return obj is not None and obj.type == "MESH" and len(obj.data.uv_layers) > 0


def fit_uv_size(obj):
    """Metres per UV unit for this object: the square root of its surface area
    over its UV area (active render UV map), so a 1 cm weave stays about 1 cm
    on UVs of any scale. None without UVs or UV area."""
    import numpy as np
    if not has_uvs(obj):
        return None
    me = obj.data
    me.calc_loop_triangles()
    n = len(me.loop_triangles)
    if n == 0:
        return None
    tri_loops = np.empty(n * 3, np.int32)
    me.loop_triangles.foreach_get("loops", tri_loops)
    tri_verts = np.empty(n * 3, np.int32)
    me.loop_triangles.foreach_get("vertices", tri_verts)
    co = np.empty(len(me.vertices) * 3, np.float64)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    mw = np.array(obj.matrix_world, np.float64)
    co = co @ mw[:3, :3].T + mw[:3, 3]
    layer = next((u for u in me.uv_layers if u.active_render), me.uv_layers.active)
    uv = np.empty(len(me.loops) * 2, np.float64)
    layer.data.foreach_get("uv", uv)
    uv = uv.reshape(-1, 2)
    p = co[tri_verts].reshape(-1, 3, 3)
    q = uv[tri_loops].reshape(-1, 3, 2)
    area3 = 0.5 * np.linalg.norm(np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1).sum()
    e1, e2 = q[:, 1] - q[:, 0], q[:, 2] - q[:, 0]
    area2 = 0.5 * np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]).sum()
    if area2 <= 1e-12 or area3 <= 0:
        return None
    lo, hi = UV_SIZE_RANGE
    return float(min(hi, max(lo, (area3 / area2) ** 0.5)))


def set_mapping(obj, mode=None, turn=None, size=None, flip_u=None, flip_v=None):
    """Change the object's pattern mapping (any of the settings). Switching to
    UV fits the UV size the first time. Returns the new mapping, or None when
    UV was asked for and the object has no UVs."""
    now = mapping(obj)
    if mode == "UV" and not has_uvs(obj):
        return None
    if mode is not None:
        now["mode"] = mode
    if turn is not None:
        now["turn"] = float(turn) % 360.0
    if size is not None:
        lo, hi = UV_SIZE_RANGE
        now["size"] = min(hi, max(lo, float(size)))
    if flip_u is not None:
        now["flip_u"] = bool(flip_u)
    if flip_v is not None:
        now["flip_v"] = bool(flip_v)
    if now["mode"] == "UV" and now["size"] <= 0.0:
        now["size"] = fit_uv_size(obj) or 1.0
    obj[MAP_ATTR] = (1.0 if now["mode"] == "UV" else 0.0, now["turn"], now["size"])
    obj[FLIP_ATTR] = (1.0 if now["flip_u"] else 0.0, 1.0 if now["flip_v"] else 0.0, 0.0)
    obj.update_tag()
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None or mat.get("plinth_type") not in TYPES:
            continue
        type_group(mat["plinth_type"])                   # groups from an older Plinth read the mapping too
        if mat["plinth_type"] == "TEXTURES":
            sync_textures(mat, obj)                      # image textures follow the projection
    return now


def reset_mapping(obj):
    for key in (MAP_ATTR, FLIP_ATTR):
        obj.pop(key, None)
    obj.update_tag()


# --- Parts (object material slots) ---------------------------------------------------------

def _slot_index(obj, index):
    return obj.active_material_index if index is None else index


def part_material(obj, index=None):
    """Plinth's material in this slot (default: the active one), or None."""
    i = _slot_index(obj, index)
    if i >= len(obj.material_slots):
        return None
    mat = obj.material_slots[i].material
    return mat if mat is not None and "plinth_preset" in mat else None


surface_material = part_material


def group_node(mat):
    """The group node feeding the material's output (its current type)."""
    if mat is None or not mat.node_tree:
        return None
    nodes = mat.node_tree.nodes
    return nodes.get("Plinth_" + str(mat.get("plinth_type"))) or nodes.get(GROUP_NODE)


def _type_node(mat, type_key, group):
    """This material's group node for a type: (node, created_now)."""
    nodes = mat.node_tree.nodes
    node = nodes.get("Plinth_" + type_key)
    if node is not None:
        return node, False
    node = nodes.new("ShaderNodeGroup")
    node.name = "Plinth_" + type_key
    node.label = TYPES[type_key]["label"]
    node.node_tree = group
    node.location = (0, -300 * list(TYPES).index(type_key))
    return node, True


def _colour_of(mat):
    """The colour a part shows: our type's colour input, or a Principled base colour."""
    if mat is None:
        return None
    if mat.get("plinth_type") in TYPES:          # ours: the live socket, else the remembered colour
        spec = TYPES[mat["plinth_type"]]
        name = spec["colour"]
        node = group_node(mat)
        if name and node is not None and not spec.get("glass") and (
                name in spec.get("sockets", {}) or name in node.inputs):
            return tuple(socket(node, mat["plinth_type"], name).default_value)
    if "plinth_colour" in mat:
        return tuple(mat["plinth_colour"])
    if not mat.node_tree:
        return tuple(mat.diffuse_color)
    for node in mat.node_tree.nodes:
        if node.type == "BSDF_PRINCIPLED" and not node.inputs["Base Color"].is_linked:
            return tuple(node.inputs["Base Color"].default_value)
    return tuple(mat.diffuse_color)


def _remember_original(obj, i):
    originals = dict(obj.get(ORIGINALS, {}))
    if str(i) in originals:
        return
    slot = obj.material_slots[i]
    mat = slot.material
    originals[str(i)] = {"link": slot.link,
                         "name": mat.name if mat is not None and "plinth_preset" not in mat else ""}
    obj[ORIGINALS] = originals


def _own_material(obj, i):
    """This part's Plinth material, created or un-shared as needed."""
    slot = obj.material_slots[i]
    mat = slot.material if slot.material is not None and "plinth_preset" in slot.material else None
    if mat is not None and mat.users > 1 and not mat.get(LINKED_TAG):   # e.g. Shift+D copied it
        mat = mat.copy()
        mat.pop(LINKED_TAG, None)
    if mat is None:
        # "Original" left this part's Plinth material unused (see restore_original):
        # reuse it, whatever the object is called now.
        old = bpy.data.materials.get(obj.get(UNUSED, {}).get(str(i), ""))
        if old is not None and old.users == 0 and "plinth_preset" in old:
            mat = old
        else:
            mat = bpy.data.materials.new("Plinth_%s_%d" % (obj.name, i))
            mat["plinth_preset"] = ""
            if hasattr(mat, "use_nodes") and not mat.use_nodes:
                mat.use_nodes = True
            nodes = mat.node_tree.nodes
            nodes.clear()
            out = nodes.new("ShaderNodeOutputMaterial")
            out.location = (300, 0)
    slot.link = "OBJECT"                               # never touch a shared mesh
    slot.material = mat
    return mat


def remember_edits(mat):
    """Keep the current preset's values as the user left them, so coming back
    to that preset on this part brings their edits back."""
    key, type_key = mat.get("plinth_preset"), mat.get("plinth_type")
    node = group_node(mat)
    if key not in PRESETS or PRESETS[key]["type"] != type_key or node is None:
        return
    values = {}
    for name, kind, *_ in TYPES[type_key]["inputs"]:
        value = socket(node, type_key, name).default_value
        values[name] = tuple(value) if kind == "colour" else (bool(value) if kind == "bool" else float(value))
    edits = {k: v for k, v in mat.get(EDITS, {}).items()}
    edits[key] = values
    mat[EDITS] = edits


def remembered(mat, key):
    """The user's values for a preset on this material, or {}."""
    entry = mat.get(EDITS, {}).get(key) if mat is not None else None
    return entry.to_dict() if hasattr(entry, "to_dict") else dict(entry or {})


def apply_preset(obj, key, index=None, keep_colour=None, fresh=False):
    """Give a part (material slot) a preset. Returns skipped settings.

    A preset this part had before comes back as the user left it; ``fresh``
    (Reset) starts it from the preset's own values again."""
    preset = PRESETS[key]
    spec = TYPES[preset["type"]]
    if not obj.material_slots:
        obj.data.materials.append(None)
    i = _slot_index(obj, index)
    from . import bake
    bake.unbake(obj, i)                        # a baked part goes back to procedural first
    _remember_original(obj, i)
    current = obj.material_slots[i].material
    colour = _colour_of(current)
    if current is not None and current.get("plinth_preset"):
        remember_edits(current)
    mat = _own_material(obj, i)
    mat.pop("plinth_library", None)            # a library entry re-tags it after (matlib.apply)
    if fresh and key in mat.get(EDITS, {}):
        edits = {k: v for k, v in mat[EDITS].items() if k != key}
        mat[EDITS] = edits
    group, skipped = type_group(preset["type"])
    node, created = _type_node(mat, preset["type"], group)
    wire_outputs(mat, node)

    # A type used for the first time starts from its defaults; a type this part
    # had before keeps the user's edits (only the preset's own values change).
    values = {n: d for n, _k, d, *_ in spec["inputs"]} if created else {}
    values.update(preset["values"])
    kinds = {n: kind for n, kind, *_ in spec["inputs"]}
    values.update({n: v for n, v in remembered(mat, key).items() if n in kinds})
    if preset["type"] == "GLASS" and "Thin pane" not in preset["values"] \
            and "Thin pane" not in remembered(mat, key) and obj.type == "MESH":
        from . import eeveeprep                   # windows are panes, lenses and crystal solid
        values["Thin pane"] = eeveeprep.is_thin(obj)
    keep = preset["keep_colour"] if keep_colour is None else keep_colour
    if keep and colour is not None and spec["colour"]:
        values[spec["colour"]] = colour
    for name, value in values.items():
        if kinds.get(name) == "bool":
            value = bool(value)
        socket(node, preset["type"], name).default_value = value
    mat["plinth_preset"] = key
    mat["plinth_type"] = preset["type"]
    _sync(mat)
    if preset["type"] == "TYRE" and obj.type == "MESH":
        fit_tyre(obj, i)                         # the tread follows this tyre's shape
    elif preset["type"] == "DISC" and obj.type == "MESH":
        fit_disc(obj, i)                         # holes and slots follow this disc
    elif preset["type"] == "TEXTURES" and not mat.get(TEX_MAPS) and obj.type == "MESH":
        from . import textures                   # the maps the model came with, when we can find them
        found = textures.find_for_part(obj, i)
        if found:
            textures.assign(obj, found, i)
    from . import decals
    if decals.layers(obj, i):
        decals.rewire(obj, i)                    # decals stay on top of the new finish

    if preset["type"] not in GLASS_TYPES:
        for attr in GLASS_SETTINGS:              # back to Blender's defaults
            if hasattr(mat, attr):
                setattr(mat, attr, mat.bl_rna.properties[attr].default)
    else:
        skipped += glass_state(mat, obj)
    return skipped


def wire_outputs(mat, node):
    """Connect a type's group node to the material output(s). Types with an
    "Eevee" output (glass) get a second, Eevee-only Material Output; Blender
    prefers an engine's own output over the "All" one, so Cycles keeps using
    the main output. Other types: that Eevee output goes (an engine output
    left unconnected would render nothing)."""
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    out = next((n for n in nodes if n.type == "OUTPUT_MATERIAL" and n.name != EEVEE_OUT), None)
    if out is None:
        out = nodes.new("ShaderNodeOutputMaterial")
        out.location = (300, 0)
    if out.target != "ALL":
        out.target = "ALL"
    out.is_active_output = True
    eevee_src = node.outputs.get("Eevee")
    eevee_out = nodes.get(EEVEE_OUT)
    if eevee_src is None and eevee_out is not None:
        nodes.remove(eevee_out)
        eevee_out = None
    if eevee_src is not None and eevee_out is None:
        eevee_out = nodes.new("ShaderNodeOutputMaterial")
        eevee_out.name = eevee_out.label = EEVEE_OUT
        eevee_out.location = (out.location.x, out.location.y - 220)
    if eevee_out is not None:
        eevee_out.target = "EEVEE"
    for target, surface in ((out, node.outputs.get("Surface") or node.outputs[0]),   # UDIN: "shader"
                            (eevee_out, eevee_src)):
        if target is None:
            continue
        for name, source in (("Surface", surface), ("Volume", node.outputs.get("Volume"))):
            for link in list(target.inputs[name].links):
                links.remove(link)
            if source is not None:
                links.new(source, target.inputs[name])


def outputs(mat):
    """The material's output nodes Plinth feeds: [main] or [main, Eevee-only]."""
    if mat is None or mat.node_tree is None:
        return []
    nodes = mat.node_tree.nodes
    outs = [n for n in nodes if n.type == "OUTPUT_MATERIAL" and n.name != EEVEE_OUT]
    main = next((n for n in outs if n.is_active_output), outs[0] if outs else None)
    return [n for n in (main, nodes.get(EEVEE_OUT)) if n is not None]


def glass_state(mat, obj=None):
    """Glass: set the hidden inputs and Eevee's material settings from the
    part's shape and the user's choices. Returns skipped settings.

    Open shell (Cycles pane path) comes from the mesh; Eevee refracts only
    solid or frosted glass; a refracting pane is a zero-thickness slab (blur
    without magnification), a solid a sphere."""
    node = group_node(mat)
    if node is None or mat.get("plinth_type") != "GLASS":
        return []
    skipped = []
    thin = bool(node.inputs["Thin pane"].default_value)
    if obj is not None and obj.type == "MESH":
        from . import eeveeprep
        node.inputs["Open shell"].default_value = eeveeprep.mean_thickness(obj)[1]
    refract = (not thin) or node.inputs["Roughness"].default_value > FROSTED
    node.inputs["Eevee refraction"].default_value = refract
    # A pane is Blended: coloured, noise-free see-through (Dithered transparency
    # is stochastic and grey-only), with overlap on so glass behind glass in the
    # same object still shows. Refracting glass must be Dithered.
    want = dict(GLASS_MATERIAL, use_raytrace_refraction=refract,
                thickness_mode="SLAB" if thin else "SPHERE",
                surface_render_method="DITHERED" if refract else "BLENDED",
                show_transparent_back=not refract)
    for attr, value in want.items():
        if not hasattr(mat, attr):
            skipped.append(attr)
        elif getattr(mat, attr) != value:
            setattr(mat, attr, value)
    _pane_thickness(mat, thin)
    return skipped


def _pane_thickness(mat, thin):
    """A pane in Eevee is a single surface (thickness 0: no offset, no
    magnification) with no volume (a volume on a Blended material blacked out
    the whole Eevee frame in 5.2; a pane's colour is its Tint anyway). A
    solid uses Eevee's own thickness estimate and keeps its absorption."""
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    eevee_out = nodes.get(EEVEE_OUT)
    value = nodes.get("Plinth_Pane_Thickness")
    if eevee_out is None or "Thickness" not in eevee_out.inputs:
        return
    node = group_node(mat)
    volume = eevee_out.inputs["Volume"]
    if thin:
        for link in list(volume.links):
            links.remove(link)
    elif not volume.is_linked and node is not None and node.outputs.get("Volume") is not None:
        links.new(node.outputs["Volume"], volume)
    sock = eevee_out.inputs["Thickness"]
    if not thin:
        for link in list(sock.links):
            links.remove(link)
        return
    if value is None:
        value = nodes.new("ShaderNodeValue")
        value.name = value.label = "Plinth_Pane_Thickness"
        value.location = (eevee_out.location.x - 200, eevee_out.location.y - 150)
    value.outputs[0].default_value = 0.0
    if not sock.is_linked:
        links.new(value.outputs[0], sock)


def _sync(mat):
    """Keep solid-mode colour and the remembered product colour in step."""
    node = group_node(mat)
    spec = TYPES[mat["plinth_type"]]
    if mat["plinth_type"] == "IMAGE":
        sync_image(mat)                 # no colour of its own: the part keeps the one it had
        mat.diffuse_color = tuple(socket(node, "IMAGE", "Backing colour").default_value)
    elif mat["plinth_type"] == "TEXTURES":
        sync_textures(mat)
    elif spec["colour"] and mat["plinth_type"] != "GLASS" and not spec.get("glass"):
        col = tuple(socket(node, mat["plinth_type"], spec["colour"]).default_value)
        mat["plinth_colour"] = col
        mat.diffuse_color = col
    else:
        mat.diffuse_color = tuple(socket(node, mat["plinth_type"], "Tint").default_value[:3]) + (0.3,)


def set_thin(mat, thin):
    """Glass: a thin pane (windows) or solid glass (lenses, crystal)."""
    node = group_node(mat)
    node.inputs["Thin pane"].default_value = thin
    glass_state(mat)


def set_input(obj, name, value, index=None):
    """Change one input on a part's material (un-sharing it first)."""
    i = _slot_index(obj, index)
    mat = part_material(obj, i)
    if mat is None:
        return False
    mat = _own_material(obj, i)
    socket(group_node(mat), mat["plinth_type"], name).default_value = value
    if name == "Thin pane":
        set_thin(mat, bool(value))
    elif name == "Roughness" and mat["plinth_type"] == "GLASS":
        glass_state(mat)                         # frosted glass refracts in Eevee (the blur)
    _sync(mat)
    return True


def get_input(obj, name, index=None):
    mat = part_material(obj, index)
    node = group_node(mat)
    return socket(node, mat["plinth_type"], name).default_value if node is not None else None


def restore_original(obj, index=None):
    """Put a part back exactly as it was before Plinth."""
    i = _slot_index(obj, index)
    originals = dict(obj.get(ORIGINALS, {}))
    entry = originals.pop(str(i), None)
    if entry is None or i >= len(obj.material_slots):
        return False
    from . import bake
    bake.unbake(obj, i)
    slot = obj.material_slots[i]
    ours = slot.material if slot.material is not None and "plinth_preset" in slot.material else None
    if ours is not None:
        remember_edits(ours)                      # picking the preset again brings them back
    slot.material = None                          # clear the object-level slot
    slot.link = entry["link"]
    if entry["link"] == "OBJECT":
        slot.material = bpy.data.materials.get(entry["name"]) if entry["name"] else None
    obj[ORIGINALS] = originals
    # Plinth's material is now unused. It isn't deleted (an open editor may still
    # show it); unused data isn't saved, and re-applying a preset reuses it.
    if ours is not None:
        ours["plinth_preset"] = ""
        unused = dict(obj.get(UNUSED, {}))
        unused[str(i)] = ours.name
        obj[UNUSED] = unused
    from . import decals
    if decals.layers(obj, i):
        decals.rewire(obj, i)                    # on a Plinth copy: the original stays untouched
    return True


def make_own(obj, index=None):
    """Un-share the part's Plinth material before editing it directly (editor)."""
    i = _slot_index(obj, index)
    return _own_material(obj, i) if part_material(obj, i) is not None else None


# --- Which parts show: multi-material meshes ---------------------------------------
# Plinth edits the active slot. Imported meshes often have slots no face uses
# (or a Geometry Nodes Set Material that overrides them), and editing one of
# those changes nothing you can see. So: measure which slots the faces show,
# as Blender evaluates the mesh, and keep the active slot on one of them.

_PARTS = {}            # _parts_key(obj) -> slot indices its faces showed when last measured
_STORED = {}           # _parts_key(obj) -> slot indices of the mesh as stored (read while drawing);
                       # dropped on any geometry change and on file load (_forget_stored)


def _parts_key(obj):
    """Which object, which mesh, how many slots and faces: a measurement only
    answers for the mesh it was taken on."""
    return (obj.as_pointer(), obj.data.as_pointer(), len(obj.material_slots), len(obj.data.polygons))


def used_slots(obj, depsgraph=None):
    """Slot indices the object's faces show. With a depsgraph: measured on the
    evaluated mesh (modifiers included) and remembered; without (while
    drawing): the last measurement, or the mesh as stored. Read-only."""
    import numpy as np
    if obj is None or obj.type != "MESH":
        return set()
    key = _parts_key(obj)
    if depsgraph is None:
        # While drawing: never read every face again. The browser asks this for
        # every product in the scene on each redraw; on a 375-object car that
        # read took 2 s per frame and Blender stopped responding.
        if key in _PARTS:
            return set(_PARTS[key])
        if key in _STORED:
            return set(_STORED[key])
    me = obj.evaluated_get(depsgraph).data if depsgraph is not None else obj.data
    n = len(me.polygons)
    slots = len(obj.material_slots)
    if n == 0 or slots == 0:
        used = set()
    elif slots == 1:
        used = {0}                                   # every face shows the only slot
    else:
        idx = np.empty(n, np.int32)
        me.polygons.foreach_get("material_index", idx)
        used = {int(i) for i in np.unique(idx) if 0 <= i < slots}
    if depsgraph is not None:
        _PARTS[key] = frozenset(used)
    else:
        _STORED[key] = frozenset(used)
    return used


@bpy.app.handlers.persistent
def _forget_stored(scene=None, depsgraph=None):
    """Faces changed (edit mode, a modifier, an import) or a file loaded: the
    draw-time measurements may be stale."""
    if depsgraph is None or any(u.is_updated_geometry for u in depsgraph.updates):
        _STORED.clear()


def ensure_visible_part(obj, depsgraph):
    """If the active slot isn't on any face, make the first slot that is the
    active one (so an edit shows). Returns the active slot index."""
    used = used_slots(obj, depsgraph)
    if used and obj.active_material_index not in used:
        obj.active_material_index = min(used)
    return obj.active_material_index


def slot_of_face(obj, depsgraph, face_index):
    """The slot a face of the evaluated mesh shows (scene.ray_cast's index), or None."""
    me = obj.evaluated_get(depsgraph).data
    if not 0 <= face_index < len(me.polygons):
        return None
    i = me.polygons[face_index].material_index
    return i if i < len(obj.material_slots) else None


def part_label(obj, index):
    """A short name for a material slot, by what it originally was."""
    entry = obj.get(ORIGINALS, {}).get(str(index))
    if entry and entry.get("name"):
        return entry["name"]
    mat = obj.material_slots[index].material if index < len(obj.material_slots) else None
    if mat is not None and "plinth_preset" not in mat:
        return mat.name
    # Plinth's own material (named Plinth_<object>_<slot>) means nothing to people:
    # a one-part object is its object; a multi-part one numbers its parts.
    return obj.name if len(obj.material_slots) <= 1 else "Part %d" % (index + 1)


def current_preset(obj, index=None):
    mat = part_material(obj, index)
    return mat.get("plinth_preset") if mat is not None else None


# --- Linked parts (0.9) ----------------------------------------------------------------------
# Plinth gives every part its own material, so editing one never surprises
# another. Linking is the deliberate exception: four tyres, every window.
# A linked material is tagged, so _own_material keeps it shared.

def part_key(obj, index):
    """What a part "is" for matching: the material it had before Plinth (an
    imported "Tyre" on four wheels), else the material it wears now."""
    entry = obj.get(ORIGINALS, {}).get(str(index))
    mat = obj.material_slots[index].material if index < len(obj.material_slots) else None
    if mat is not None and mat.get(LINKED_TAG):
        return ("mat", mat.name)
    if entry and entry.get("name"):
        return ("orig", entry["name"])
    return ("mat", mat.name) if mat is not None else None


def parts_sharing(objects, obj, index=None):
    """[(object, slot)] among ``objects`` that are the same part as this one
    (part_key), this one first. Read-only."""
    i = _slot_index(obj, index)
    key = part_key(obj, i)
    out = [(obj, i)]
    if key is None:
        return out
    for o in objects:
        if o.type != "MESH":
            continue
        for j in sorted(used_slots(o)):
            if (o, j) != (obj, i) and part_key(o, j) == key:
                out.append((o, j))
    return out


def link_parts(parts):
    """Make every (object, slot) wear the first one's material, shared: an edit
    to one changes all. Returns the shared material (None if the first has none)."""
    if not parts:
        return None
    obj, i = parts[0]
    mat = obj.material_slots[i].material if i < len(obj.material_slots) else None
    if mat is None:
        return None
    if len(parts) > 1 and "plinth_preset" in mat:
        mat[LINKED_TAG] = True
    for o, j in parts[1:]:
        while len(o.material_slots) <= j:
            o.data.materials.append(None)
        _remember_original(o, j)
        current = o.material_slots[j].material
        if current is not None and current is not mat and current.get("plinth_preset"):
            remember_edits(current)
        slot = o.material_slots[j]
        slot.link = "OBJECT"
        slot.material = mat
    return mat


def linked_parts(objects, obj, index=None):
    """[(object, slot)] wearing this part's material when it's linked, else []."""
    i = _slot_index(obj, index)
    mat = obj.material_slots[i].material if i < len(obj.material_slots) else None
    if mat is None or not mat.get(LINKED_TAG):
        return []
    return [(o, j) for o in objects if o.type == "MESH"
            for j, s in enumerate(o.material_slots) if s.material is mat]


def unlink_part(obj, index=None):
    """Give this part its own copy of a linked material again."""
    i = _slot_index(obj, index)
    mat = obj.material_slots[i].material if i < len(obj.material_slots) else None
    if mat is None or not mat.get(LINKED_TAG):
        return None
    copy = mat.copy()
    copy.pop(LINKED_TAG, None)
    copy.name = "Plinth_%s_%d" % (obj.name, i)
    obj.material_slots[i].link = "OBJECT"
    obj.material_slots[i].material = copy
    if mat.users <= 1:                           # the last one sharing it
        mat.pop(LINKED_TAG, None)
    return copy


# --- Older files ---------------------------------------------------------------------------

LEGACY_TYPES = {"UDIN_WINDSHIELD": "GLASS"}     # 0.7-0.8 type -> the type that replaced it


def migrate(objects=None):
    """Move parts saved by an older Plinth onto today's types: automotive glass
    (UDIN_WINDSHIELD) becomes GLASS with the same values; GLASS v2 gets its
    Eevee branch wired. Idempotent. Returns how many parts changed."""
    changed = 0
    for obj in (bpy.data.objects if objects is None else objects):
        for i, slot in enumerate(getattr(obj, "material_slots", ())):
            mat = slot.material
            if mat is None or not mat.get("plinth_preset") or mat.node_tree is None:
                continue
            type_key = mat.get("plinth_type")
            if type_key in LEGACY_TYPES:
                node = group_node(mat)
                values = {n: (tuple(socket(node, type_key, n).default_value) if k == "colour"
                              else socket(node, type_key, n).default_value)
                          for n, k, *_ in controls(type_key)} if node is not None else {}
                key = mat["plinth_preset"] if mat["plinth_preset"] in PRESETS else "GLASS_WINDOW"
                apply_preset(obj, key, i, keep_colour=False, fresh=True)
                new = TYPES[LEGACY_TYPES[type_key]]["inputs"]
                for name, value in values.items():
                    if any(n == name for n, *_ in new):
                        set_input(obj, name, value, i)
                changed += 1
            elif type_key == "GLASS" and mat.node_tree.nodes.get(EEVEE_OUT) is None:
                group, _skipped = type_group("GLASS")   # rebuilt to v3, values kept
                node = group_node(mat)
                if node is None:
                    continue
                wire_outputs(mat, node)
                glass_state(mat, obj)
                changed += 1
    return changed


@bpy.app.handlers.persistent
def _migrate_on_load(_dummy=None):
    """Bring a file saved by an older Plinth up to date (materials, light quality)."""
    try:
        migrate()
        from . import studio
        studio.upgrade_lights()
    except Exception as exc:                      # never block a file from opening
        print("Plinth: could not update an older file:", exc)


def register():
    if _migrate_on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_migrate_on_load)
    for handlers in (bpy.app.handlers.load_post, bpy.app.handlers.depsgraph_update_post):
        if _forget_stored not in handlers:
            handlers.append(_forget_stored)


def unregister():
    for handlers, name in ((bpy.app.handlers.load_post, "_migrate_on_load"),
                           (bpy.app.handlers.load_post, "_forget_stored"),
                           (bpy.app.handlers.depsgraph_update_post, "_forget_stored")):
        for h in [h for h in handlers if getattr(h, "__name__", "") == name]:
            handlers.remove(h)
    _STORED.clear()


# --- The four quick finishes (compatible with v0.2) -------------------------------------------

def apply_finish(obj, key, index=None):
    """Quick finish (GLOSS/METAL/GLASS/MATTE) or any preset key; None = original."""
    if key is None:
        restore_original(obj, index)
        return []
    if key in QUICK:
        return apply_preset(obj, QUICK[key], index, keep_colour=True)
    return apply_preset(obj, key, index)


def current_finish(obj, index=None):
    """Quick finish key if the part uses one, else the preset key, else None."""
    preset = current_preset(obj, index)
    if not preset:
        return None
    return next((q for q, p in QUICK.items() if p == preset), preset)


def is_shiny(obj):
    """Any Plinth part on this object that wants sharp reflections (auto preset)."""
    for slot in obj.material_slots:
        mat = slot.material
        if mat is not None and mat.get("plinth_preset") in PRESETS:
            if PRESETS[mat["plinth_preset"]]["shiny"]:
                return True
    return False


def has_plinth_parts(obj):
    return any(s.material is not None and "plinth_preset" in s.material for s in obj.material_slots)


# --- Tyres: fitting the tread to the tyre -----------------------------------------------

TYRE_ATTRIBUTE = "plinth_tyre"


def fit_tyre(obj, index=None):
    """Give a part the tyre coordinates its Tyre material reads: for each
    corner, (around 0..1, across -1..1, out from the rim 0..1), per connected
    piece of the part, so four tyres in one mesh each get their own. The axle
    is the direction the piece is thinnest along. Stored on the mesh (corner
    attribute), so the tread turns with the wheel. Returns the number of
    tyres found."""
    return _fit_round(obj, index, TYRE_ATTRIBUTE, disc=False)


DISC_ATTRIBUTE = "plinth_disc"


def fit_disc(obj, index=None):
    """Give a part the disc coordinates its Brake disc material reads: for each
    corner, (around 0..1, across the friction ring -1..1, out from the centre
    0..1 of the radius), per connected piece like tyres. "Across" is measured
    on the ring's outer edge, so a hat standing proud of the ring doesn't
    squash it. Returns the number of discs found."""
    return _fit_round(obj, index, DISC_ATTRIBUTE, disc=True)


def _fit_round(obj, index, name, disc):
    import numpy as np
    me = obj.data
    i = _slot_index(obj, index)
    npoly = len(me.polygons)
    if npoly == 0:
        return 0
    mat_idx = np.empty(npoly, np.int32)
    me.polygons.foreach_get("material_index", mat_idx)
    start = np.empty(npoly, np.int32)
    total = np.empty(npoly, np.int32)
    me.polygons.foreach_get("loop_start", start)
    me.polygons.foreach_get("loop_total", total)
    loop_v = np.empty(len(me.loops), np.int32)
    me.loops.foreach_get("vertex_index", loop_v)
    co = np.empty(len(me.vertices) * 3, np.float64)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    faces = np.nonzero(mat_idx == i)[0]
    if len(faces) == 0:
        return 0
    # Connected pieces: union-find over the part's face edges.
    parent = {}

    def find(a):
        root = a
        while parent.get(root, root) != root:
            root = parent[root]
        while parent.get(a, a) != root:
            parent[a], a = root, parent[a]
        return root

    for f in faces:
        vs = loop_v[start[f]:start[f] + total[f]]
        ra = find(int(vs[0]))
        for vv in vs[1:]:
            rb = find(int(vv))
            if rb != ra:
                parent[rb] = ra
    verts = np.unique(np.concatenate([loop_v[start[f]:start[f] + total[f]] for f in faces]))
    roots = np.array([find(int(vv)) for vv in verts])
    coords = np.zeros((len(me.vertices), 3), np.float64)
    for root in np.unique(roots):
        vs = verts[roots == root]
        p = co[vs]
        c = (p.min(0) + p.max(0)) / 2.0
        d = p - c
        if len(vs) >= 3:
            _w, vec = np.linalg.eigh(np.cov(d.T))
            axis = vec[:, 0]                      # least spread: the axle
        else:
            axis = np.array([1.0, 0.0, 0.0])
        e1 = np.cross(axis, [0.0, 0.0, 1.0] if abs(axis[2]) < 0.9 else [1.0, 0.0, 0.0])
        e1 /= np.linalg.norm(e1)
        e2 = np.cross(axis, e1)
        w = d @ axis
        rho = np.hypot(d @ e1, d @ e2)
        ang = (np.arctan2(d @ e2, d @ e1) / (2 * np.pi)) % 1.0
        lo, hi = rho.min(), rho.max()
        coords[vs, 0] = ang
        if disc:                                  # across the ring, out to the radius
            rim = rho >= hi - 0.03 * max(hi, 1e-9)
            wlo, whi = w[rim].min(), w[rim].max()
            coords[vs, 1] = (w - (wlo + whi) / 2) / max((whi - wlo) / 2, 1e-9)
            coords[vs, 2] = rho / max(hi, 1e-9)
        else:
            coords[vs, 1] = w / max(np.abs(w).max(), 1e-9)
            coords[vs, 2] = (rho - lo) / max(hi - lo, 1e-9)
    attr = me.attributes.get(name)
    if attr is not None and (attr.domain != "CORNER" or attr.data_type != "FLOAT_VECTOR"):
        me.attributes.remove(attr)
        attr = None
    if attr is None:
        attr = me.attributes.new(name, "FLOAT_VECTOR", "CORNER")
    values = np.zeros(len(me.loops) * 3, np.float32)
    attr.data.foreach_get("vector", values)
    values = values.reshape(-1, 3)
    for f in faces:                               # per face, so the seam doesn't smear
        ls = np.arange(start[f], start[f] + total[f])
        uvw = coords[loop_v[ls]]
        if uvw[:, 0].max() - uvw[:, 0].min() > 0.5:
            uvw[uvw[:, 0] < 0.5, 0] += 1.0
        values[ls] = uvw
    attr.data.foreach_set("vector", values.ravel())
    me.update()
    return len(np.unique(roots))


# --- Image: the picture node and its mapping (in the material) ----------------------------

LINKED = "Linked"          # inputs wired from nodes in the material: never shown as controls
IMAGE_NODE = "Plinth_Image"


def controls(type_key):
    """A type's inputs a person can set (not the ones wired from the material)."""
    return [i for i in TYPES[type_key]["inputs"] if i[5] != LINKED]


def sync_image(mat):
    """Image type: make sure the image node, its UV mapping and the links into
    the group exist, and put the group's Size/Rotation/Offset on the mapping
    (rotation about the image's centre)."""
    import math as _m
    from mathutils import Matrix, Vector
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    group = nodes.get("Plinth_IMAGE")
    if group is None:
        return
    uv = nodes.get("Plinth_ImageUV") or nodes.new("ShaderNodeTexCoord")
    uv.name = "Plinth_ImageUV"
    mapping = nodes.get("Plinth_ImageMap") or nodes.new("ShaderNodeMapping")
    mapping.name = "Plinth_ImageMap"
    tex = nodes.get(IMAGE_NODE) or nodes.new("ShaderNodeTexImage")
    tex.name = IMAGE_NODE
    x, y = group.location
    uv.location, mapping.location, tex.location = (x - 900, y), (x - 700, y), (x - 450, y)
    for a, b_ in ((uv.outputs["UV"], mapping.inputs["Vector"]), (mapping.outputs["Vector"], tex.inputs["Vector"]),
                  (tex.outputs["Color"], group.inputs["Image colour"]),
                  (tex.outputs["Alpha"], group.inputs["Image alpha"])):
        if not any(l.from_socket == a for l in b_.links):
            links.new(a, b_)
    size = max(0.01, float(group.inputs["Size"].default_value))
    angle = _m.radians(float(group.inputs["Rotation"].default_value))
    ox, oy = float(group.inputs["Offset X"].default_value), float(group.inputs["Offset Y"].default_value)
    s = 1.0 / size
    rot = Matrix.Rotation(angle, 3, "Z")
    centre = Vector((0.5, 0.5, 0.0))
    loc = centre - rot @ Vector((0.5 * s, 0.5 * s, 0.0)) - Vector((ox, oy, 0.0)) * s
    mapping.vector_type = "POINT"
    mapping.inputs["Location"].default_value = loc
    mapping.inputs["Rotation"].default_value = (0.0, 0.0, angle)
    mapping.inputs["Scale"].default_value = (s, s, 1.0)


# --- Texture set: one image node per map (in the material) --------------------------------

TEX_MAPS = "plinth_textures"        # mat[...] = {channel: image name}
TEX_PREFIX = "Plinth_Tex_"
# channel -> (the group input it feeds, colour data?) in wiring order: a map
# of its own wins over the same value unpacked from ORM or inverted gloss.
TEX_CHANNELS = {
    "COLOUR": ("Map colour", True), "ALPHA": ("Map alpha", False), "METAL": ("Map metallic", False),
    "ROUGH": ("Map roughness", False), "GLOSS": ("Map roughness", False), "NORMAL": ("Map normal", False),
    "BUMP": ("Map height", False), "AO": ("Map occlusion", False), "GLOW": ("Map glow", True),
    "ORM": (None, False),
}


def texture_maps(mat):
    """{channel: bpy Image} on a Texture set material (missing images skipped)."""
    if mat is None:
        return {}
    out = {}
    for ch, name in dict(mat.get(TEX_MAPS, {})).items():
        img = bpy.data.images.get(name)
        if img is not None and ch in TEX_CHANNELS:
            out[ch] = img
    return out


def sync_textures(mat, obj=None):
    """Texture set: an image node per map, colour spaces, projection and the
    links into the group (rebuilt from mat[TEX_MAPS]; nodes are kept and only
    relinked). UV: flat on the part's UVs (mirrored and turned by the object's
    mapping) times Tiling. World: box projection of world position per Tile
    size. ``obj`` picks whose mapping mode decides; default: a user of mat."""
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    group = nodes.get("Plinth_TEXTURES")
    if group is None:
        return
    if obj is None:
        obj = next((o for o in bpy.data.objects if o.type == "MESH"
                    and any(s.material == mat for s in o.material_slots)), None)
    uv_mode = mapping(obj)["mode"] == "UV" if obj is not None else True
    if obj is not None and not has_uvs(obj):
        uv_mode = False
    gx, gy = group.location
    mp = nodes.get(TEX_PREFIX + "Mapping") or nodes.new("ShaderNodeGroup")
    mp.name = mp.label = TEX_PREFIX + "Mapping"
    mp.node_tree = mapping_group()
    mp.location = (gx - 1300, gy)
    vec = nodes.get(TEX_PREFIX + "Vector") or nodes.new("ShaderNodeVectorMath")
    vec.name = TEX_PREFIX + "Vector"
    vec.operation = "SCALE"
    vec.location = (gx - 1100, gy)
    for lk in list(vec.inputs[0].links):
        links.remove(lk)
    if uv_mode:
        links.new(mp.outputs["UV"], vec.inputs[0])
        vec.inputs["Scale"].default_value = float(group.inputs["Tiling"].default_value)
    else:
        links.new(mp.outputs["World"], vec.inputs[0])
        vec.inputs["Scale"].default_value = 100.0 / max(0.01, float(group.inputs["Tile size (cm)"].default_value))
    maps = texture_maps(mat)
    for sock_name, _c in TEX_CHANNELS.values():           # start clean: nothing linked from us
        if sock_name:
            for lk in list(group.inputs[sock_name].links):
                links.remove(lk)
    fed = set()
    y = gy

    def tex_node(ch):
        nonlocal y
        t = nodes.get(TEX_PREFIX + ch) or nodes.new("ShaderNodeTexImage")
        t.name = t.label = TEX_PREFIX + ch
        t.location = (gx - 800, y)
        y -= 300
        t.image = maps.get(ch)
        t.projection = "FLAT" if uv_mode else "BOX"
        t.projection_blend = 0.25
        if not any(lk.from_node == vec for lk in t.inputs["Vector"].links):
            links.new(vec.outputs[0], t.inputs["Vector"])
        if t.image is not None:
            colour = TEX_CHANNELS[ch][1]
            want = "sRGB" if colour else "Non-Color"
            try:
                if t.image.colorspace_settings.name != want:
                    t.image.colorspace_settings.name = want
            except TypeError:
                pass                                      # a colour management without it
        return t

    for ch in ("COLOUR", "ALPHA", "METAL", "ROUGH", "GLOSS", "NORMAL", "BUMP", "AO", "GLOW"):
        if ch not in maps:
            if nodes.get(TEX_PREFIX + ch) is not None:
                nodes[TEX_PREFIX + ch].image = None
            continue
        t = tex_node(ch)
        target = TEX_CHANNELS[ch][0]
        if target in fed:
            continue
        src = t.outputs["Color"]
        if ch == "GLOSS":                                  # gloss is roughness upside down
            inv = nodes.get(TEX_PREFIX + "Invert") or nodes.new("ShaderNodeInvert")
            inv.name = TEX_PREFIX + "Invert"
            inv.location = (gx - 500, t.location.y)
            links.new(src, inv.inputs["Color"])
            src = inv.outputs["Color"]
        elif ch == "NORMAL" and mat.get(TEX_MAPS + "_dx"):  # DirectX normal maps: green points down
            sep = nodes.get(TEX_PREFIX + "DXSplit") or nodes.new("ShaderNodeSeparateColor")
            sep.name = TEX_PREFIX + "DXSplit"
            inv = nodes.get(TEX_PREFIX + "DXFlip") or nodes.new("ShaderNodeMath")
            inv.name, inv.operation = TEX_PREFIX + "DXFlip", "SUBTRACT"
            inv.inputs[0].default_value = 1.0
            com = nodes.get(TEX_PREFIX + "DXJoin") or nodes.new("ShaderNodeCombineColor")
            com.name = TEX_PREFIX + "DXJoin"
            links.new(src, sep.inputs[0])
            links.new(sep.outputs[0], com.inputs[0])
            links.new(sep.outputs[1], inv.inputs[1])
            links.new(inv.outputs[0], com.inputs[1])
            links.new(sep.outputs[2], com.inputs[2])
            src = com.outputs[0]
        links.new(src, group.inputs[target])
        fed.add(target)
        if ch == "COLOUR" and "ALPHA" not in maps and maps["COLOUR"].alpha_mode != "NONE" \
                and getattr(maps["COLOUR"], "depth", 32) in (32, 64, 128):
            links.new(t.outputs["Alpha"], group.inputs["Map alpha"])
            fed.add("Map alpha")
    if "ORM" in maps:                                     # packed: R occlusion, G roughness, B metal
        t = tex_node("ORM")
        sep = nodes.get(TEX_PREFIX + "ORMSplit") or nodes.new("ShaderNodeSeparateColor")
        sep.name = TEX_PREFIX + "ORMSplit"
        sep.location = (gx - 500, t.location.y)
        links.new(t.outputs["Color"], sep.inputs[0])
        for k, target in enumerate(("Map occlusion", "Map roughness", "Map metallic")):
            if target not in fed:
                links.new(sep.outputs[k], group.inputs[target])
                fed.add(target)
    elif nodes.get(TEX_PREFIX + "ORM") is not None:
        nodes[TEX_PREFIX + "ORM"].image = None


def image_of(mat):
    node = mat.node_tree.nodes.get(IMAGE_NODE) if mat is not None and mat.node_tree else None
    return node.image if node is not None else None


def set_image(obj, image, index=None):
    """Put an image (bpy Image or a file path) on a part's Image material."""
    if isinstance(image, str):
        image = bpy.data.images.load(image, check_existing=True)
    i = _slot_index(obj, index)
    mat = part_material(obj, i)
    if mat is None or mat.get("plinth_type") != "IMAGE":
        apply_preset(obj, "IMAGE_PRINT", i)
    mat = _own_material(obj, i)
    sync_image(mat)
    mat.node_tree.nodes[IMAGE_NODE].image = image
    return image
