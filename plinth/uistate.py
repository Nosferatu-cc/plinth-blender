# SPDX-License-Identifier: GPL-3.0-or-later
"""Transient state of the floating UI: which text field has focus, which menu
or edit page is open, the last tab per page, the shot bar's page.

None of this belongs in the .blend (it's per session, like a popup being
open), so it lives here instead of Scene.plinth. hud.build() only *reads* it;
actions (clicks) change it. Plain Python, reset on add-on reload.
"""

focus = {}          # {"id", "model": TextModel, "commit": fn(context, text), "rect", "since"}
menu = {}           # {"id", "anchor": (x, y), "items": [(id, label, shortcut, act, danger)]}
page = {}           # {"step": step key} while that step's edit page is open
tabs = {}           # page key ("STUDIO", "MATERIAL:PAINT", ...) -> last tab
dock = {"page": 0}  # shot bar paging when the cards don't fit
clicks = {}         # {"id", "time"}: double-click detection
views = {}          # framing.py: orbit view and each step's orbit/shot, this session
gizmo = {}          # transform.py: the selection before the gizmo took a light or shot
# The material browser (0.9): open, the sidebar section, the committed search,
# the first visible row, grid or list, and what a click applies to.
browser = {}
BROWSER_DEFAULTS = {"open": False, "section": "PAINT", "query": "", "row": 0, "view": "GRID",
                    "apply_to": "PART"}
# Colour pickers: the hue and saturation a colour had before it was dragged to
# black or grey (which have none), so dragging back brings the colour back.
# key -> (hue, saturation, the linear rgb we set); only used while the colour
# is still exactly the one we set.
hues = {}


def reset():
    for d in (focus, menu, page, tabs, clicks, views, gizmo, browser, hues):
        d.clear()
    dock.clear()
    dock["page"] = 0


def _same(a, b, tol=1e-5):
    return a is not None and b is not None and all(abs(x - y) <= tol for x, y in zip(a[:3], b[:3]))


def remembered_hue(key, rgb, h, s, v):
    """(h, s) to show for this colour: its own, or the remembered ones while it
    is the black/grey we set (black has no hue, grey no saturation)."""
    memo = hues.get(key)
    if memo is None or not _same(memo[2], rgb):
        return h, s
    if v <= 1e-4:
        return memo[0], memo[1]
    if s <= 1e-4:
        return memo[0], s
    return h, s


def remember_hue(key, h, s, rgb):
    hues[key] = (h, s, tuple(rgb[:3]))


def close_menu():
    menu.clear()


def open_page(step):
    page.clear()
    page["step"] = step
    menu.clear()


def close_page():
    page.clear()


def page_open(step):
    return page.get("step") == step


def browser_state():
    """The browser's state with defaults filled in (read-only)."""
    return {**BROWSER_DEFAULTS, **browser}


def open_browser(section=None):
    browser.update(open=True, row=0)
    if section:
        browser["section"] = section
    menu.clear()
    page.clear()


def close_browser():
    browser["open"] = False
