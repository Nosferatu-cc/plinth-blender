# SPDX-License-Identifier: GPL-3.0-or-later
"""Help: where to ask, and the "Copy support info" text for a bug report."""

import os
import platform

REPO_URL = "https://github.com/Nosferatu-cc/plinth-blender"
DISCORD_URL = "https://discord.gg/MbMfjbNNwW"
SITE_URL = "https://nosferatu.cc"

LINKS = (("Report a bug", REPO_URL + "/issues", "URL"),
         ("Discord", DISCORD_URL, "COMMUNITY"),
         ("nosferatu.cc", SITE_URL, "WORLD"))


def addon_version():
    """The add-on's version from its manifest."""
    try:
        import tomllib
        with open(os.path.join(os.path.dirname(__file__), "blender_manifest.toml"), "rb") as f:
            return tomllib.load(f).get("version", "")
    except (OSError, ValueError, ImportError):
        return ""


def support_info(packs=None):
    """What a bug report needs, nothing personal: versions, system, packs."""
    import bpy
    lines = ["Plinth %s" % (addon_version() or "(from source)"),
             "Blender %s" % bpy.app.version_string,
             "System: %s" % platform.platform(terse=True)]
    try:
        import gpu
        lines.append("Graphics: %s" % gpu.platform.renderer_get())
    except Exception:                          # background mode has no GPU context
        pass
    if packs:
        lines.append("Content: " + ", ".join("%s %s" % (k, v.get("version", "?")) for k, v in sorted(packs.items())))
    return "\n".join(lines)
