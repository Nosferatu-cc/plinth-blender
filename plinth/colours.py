# SPDX-License-Identifier: GPL-3.0-or-later
"""Colour helpers for the edit pages: palettes, sRGB <-> linear, HSV, hex.

Blender colour sockets hold scene-linear values; people pick and type sRGB
(what a hex code or a swatch means). Pages show and accept sRGB and convert at
the edge. Pure Python.
"""

import colorsys

# Palettes from the design canvas (Pages board: STUDIO_PAL, GELS, PAINT), sRGB hex.
STUDIO = [("White", "#f4f4f5"), ("Paper", "#e6e3dc"), ("Light grey", "#c4c4c6"),
          ("Mid grey", "#8e8e93"), ("Charcoal", "#3a3b41"), ("Black", "#111113"),
          ("Sand", "#cdb89a"), ("Sage", "#8a9a86"), ("Sky", "#a9c3d9"),
          ("Blush", "#e2b8b0"), ("Clay", "#b0715a"), ("Navy", "#1f2a44")]
GELS = [("Neutral", "#ffffff"), ("Half CTO", "#ffd9a8"), ("Full CTO", "#ffb86b"),
        ("Half CTB", "#cfe0ff"), ("Full CTB", "#9fc0ff"), ("Straw", "#fff0a8"),
        ("Rose", "#ff9fb8"), ("Magenta", "#e35cc6"), ("Cyan", "#5ce1f0"),
        ("Lime", "#b8f06b"), ("Amber", "#ffbf3a"), ("Red", "#ff4a3a")]
PAINT = [("Arctic white", "#eef0f2"), ("Silver", "#b8bcc2"), ("Gunmetal", "#4a4f57"),
         ("Midnight", "#15181d"), ("Racing red", "#b3121b"), ("Signal orange", "#e2621b"),
         ("Sunburst", "#e7b416"), ("British green", "#1f4a33"), ("Petrol blue", "#1d4e63"),
         ("Ultramarine", "#23409a"), ("Plum", "#4b2342"), ("Sand", "#c8b48f")]

STRIP_STEPS = 24


def to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def to_srgb(c):
    c = max(0.0, c)
    return c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def linear_rgb(srgb):
    return tuple(to_linear(c) for c in srgb[:3])


def srgb(linear):
    return tuple(min(1.0, to_srgb(c)) for c in linear[:3])


def hex_to_rgb(text):
    t = text.lstrip("#")
    return tuple(int(t[i:i + 2], 16) / 255 for i in (0, 2, 4))


def rgb_to_hex(rgb):
    return "#%02X%02X%02X" % tuple(round(max(0.0, min(1.0, c)) * 255) for c in rgb[:3])


def same(a, b, tol=1.5 / 255):
    """Two sRGB colours match (a swatch is 'on' when the colour is that swatch)."""
    return all(abs(x - y) <= tol for x, y in zip(a[:3], b[:3]))


def hsv(rgb):
    return colorsys.rgb_to_hsv(*[max(0.0, min(1.0, c)) for c in rgb[:3]])


def from_hsv(h, s, v):
    return colorsys.hsv_to_rgb(h % 1.0 if h < 1.0 else 0.999999, s, v)


def strip_colours(channel, h, s, v, n=STRIP_STEPS):
    """sRGB segment colours for a Hue / Saturation / Brightness strip."""
    out = []
    for i in range(n):
        f = (i + 0.5) / n
        if channel == "h":
            out.append(from_hsv(f, 0.85, 0.95))
        elif channel == "s":
            out.append(from_hsv(h, f, max(v, 0.35)))
        else:
            out.append(from_hsv(h, s, f))
    return out
