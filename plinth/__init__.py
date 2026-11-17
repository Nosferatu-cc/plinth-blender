# SPDX-License-Identifier: GPL-3.0-or-later
"""Plinth: a minimal product-photography studio for Blender."""

import sys

# Reinstalling or reloading inside a running Blender re-runs this file, but
# Python would hand back the *old* submodules still cached in sys.modules
# (e.g. an old props without STEPS). Drop them so every import below is fresh.
for _name in [n for n in sys.modules if n.startswith(__name__ + ".")]:
    del sys.modules[_name]

from . import props, materials, content, operators, overlay, outlines, nav, previews, ui  # noqa: E402

_modules = (props, materials, content, operators, overlay, outlines, nav, previews, ui)


def register():
    for m in _modules:
        m.register()


def unregister():
    for m in reversed(_modules):
        m.unregister()
