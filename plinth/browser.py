# SPDX-License-Identifier: GPL-3.0-or-later
"""The material browser (0.9): a floating sheet beside the Material card.

    [ search materials…            ] [Grid|List] [×]
    Favourites   3 | Paint                       12
    Recent       8 | [tile] [tile] [tile] [tile]
    In this scene 4|  ...                     (rows scroll)
    My library   5 |
    -------------  |
    Paint       12 |
    Metal        9 | Apply to: [This part | Parts like it (4) | Selected]
    ...

Like hud.py, this is data only: elements built from the scene and
uistate.browser, read-only while Blender draws. Clicks apply through
operators; the wheel, right-click and Esc are overlay.PLINTH_OT_browser's.
Rows scroll whole (no clipping in the overlay): ``row`` is the first one shown.
"""

import math

import bpy

from . import catalog, hud, materials, uistate
from .hud import El

SHEET_W, SIDE_W, PAD, GAP = 660, 160, 16, 8
TILE_W, TILE_H = 104, 84
NAV_H, NAV_MIN = 30, 22
LIST_H = 46
APPLY_TO = [("PART", "This part"), ("SHARING", "Parts like it"), ("SELECTED", "Selected")]


def is_open():
    return bool(uistate.browser.get("open"))


def query():
    """The search text: live while the field has focus, else the committed one."""
    f = uistate.focus
    if f.get("id") == "browser_search" and f.get("model") is not None:
        return f["model"].text
    return uistate.browser.get("query", "")


def scene_products(scene):
    """Meshes in the scene that aren't Plinth's (the product, all of it)."""
    col = bpy.data.collections.get("Plinth")
    own = set(col.all_objects) if col else set()
    return [o for o in scene.objects if o.type == "MESH" and o not in own]


def visible_entries(scene, st, entries=None, state=None):
    """(heading, entries) for the browser's current section or search."""
    entries = catalog.all_entries() if entries is None else entries
    q = query()
    if q.strip():
        return 'Results for "%s"' % q.strip(), catalog.search(entries, q)
    key = st["section"]
    ids = catalog.scene_ids(scene_products(scene)) if key == "SCENE" else ()
    found = catalog.section(entries, key, state, ids)
    label = dict(catalog.SECTIONS).get(key) or catalog.FAMILY_LABELS.get(key) or \
        (key[7:] if key.startswith("FOLDER:") else key)
    return label, found


def _apply(cid):
    def act(context):
        hud._op("plinth.apply_catalog", entry=cid, scope=uistate.browser_state()["apply_to"])
    return act


def _set(**values):
    def act(context):
        uistate.browser.update(values)
    return act


def _commit_query(context, text):
    uistate.browser.update(query=text, row=0)
    return True


def _rename(entry_id):
    def commit(context, text):
        from . import matlib
        return matlib.rename(entry_id, text)
    return commit


def _swatch(entry, here):
    from . import matlib
    if entry["kind"] == "saved":
        return matlib.swatch(entry["entry"])
    return hud.preset_swatch(materials.PRESETS[entry["key"]], here)


def _preview(entry, obj):
    from . import previews
    if entry["kind"] == "saved":
        return previews.wanted_entry(entry["entry"])
    return previews.wanted(obj, entry["key"])


def sheet_rect(scene, g, card_x):
    """(x, y, w, h, over_card) of the sheet, or None when there's no room."""
    s = g.scale
    right = card_x - 12 * s
    left = g.left + hud.EDGE * s
    over = False
    if right - left < 440 * s:                       # narrow viewport: take the card's place
        right, over = card_x + hud.CARD_W * s, True
    w = min(SHEET_W * s, right - left)
    top = hud._card_top(g)
    floor = hud.EDGE * s
    dock = hud.group_dock(scene, g)
    if dock:
        dx, dy, dw, dh = hud.bbox(dock)
        if dx < right and right - w < dx + dw:
            floor = dy + dh + 12 * s
    h = top - floor
    if w < 360 * s or h < 300 * s:
        return None
    return right - w, floor, w, h, over


def group_browser(scene, g, products, card_x):
    """The sheet's elements, or [] when closed or there's no room."""
    if not is_open():
        return []
    geo = sheet_rect(scene, g, card_x)
    if geo is None:
        return []
    x, y0, w, h, _over = geo
    s = g.scale
    st = uistate.browser_state()
    state = catalog.load_state()
    entries = catalog.all_entries()
    obj = products[0] if products else None
    current = catalog.entry_for_part(obj) if obj is not None and obj.material_slots else None
    top = y0 + h
    els = [El("sheet", "glass", (x, y0, w, h), block=True, extra={"radius": 22 * s})]

    # Header: search, grid/list, close.
    hy = top - PAD * s - 36 * s
    close = 36 * s
    seg_w = 112 * s
    els.append(El("browser_close", "closebtn", (x + w - PAD * s - close, hy, close, close), "×",
                  act=lambda c: uistate.close_browser()))
    sx = x + w - PAD * s - close - 8 * s - seg_w
    els.append(El("browser_viewbg", "well", (sx, hy, seg_w, 36 * s), extra={"radius": 12 * s}, block=True))
    for i, (key, label) in enumerate((("GRID", "Grid"), ("LIST", "List"))):
        els.append(El("view_" + key, "segcell", (sx + 4 * s + i * (seg_w - 8 * s) / 2, hy + 4 * s,
                                                 (seg_w - 8 * s) / 2, 28 * s), label,
                      on=st["view"] == key, act=_set(view=key, row=0), group="seg:browserview"))
    q = query()
    els.append(El("browser_search", "searchbox", (x + PAD * s, hy, sx - 8 * s - x - PAD * s, 36 * s),
                  q, shown="Search materials", edit=(uistate.browser.get("query", ""), _commit_query),
                  block=True))                     # no act: a click focuses the field

    # Sidebar: sections, then families (with counts).
    body_top = hy - 14 * s
    footer_h = 46 * s
    nav = _nav_items(scene, st, entries, state)
    room = body_top - (y0 + PAD * s)
    nh = max(NAV_MIN * s, min(NAV_H * s, (room - 9 * s) / max(1, len(nav))))
    ny = body_top
    for item in nav:
        if item is None:
            ny -= 9 * s
            els.append(El("nav_rule_%d" % len(els), "hline", (x + PAD * s, ny + 4 * s, SIDE_W * s, 1 * s)))
            continue
        nid, label, count, on, act, indent = item
        ny -= nh
        els.append(El("nav_" + nid, "navitem", (x + PAD * s + indent * s, ny, SIDE_W * s - indent * s,
                                                nh - 2 * s), label, shown=str(count) if count else "",
                      on=on, act=act))

    # Content: heading, tiles or rows, scrollbar, footer.
    cx = x + (PAD + SIDE_W + 14) * s
    cw = x + w - PAD * s - cx - 10 * s                 # room for the scrollbar
    heading, found = visible_entries(scene, st, entries, state)
    els.append(El("browser_heading", "label", (cx, body_top - 16 * s, cw, 16 * s), heading,
                  shown="%d" % len(found)))
    area_top = body_top - 28 * s
    area_bottom = y0 + PAD * s + footer_h + 10 * s
    here = _here_colour(obj)
    if st["view"] == "LIST":
        per_row, ih = 1, LIST_H * s
    else:
        per_row = max(2, int((cw + GAP * s) // ((TILE_W + GAP) * s)))
        ih = TILE_H * s
    rows_total = math.ceil(len(found) / per_row)
    rows_shown = max(1, int((area_top - area_bottom + GAP * s) // (ih + GAP * s)))
    first = max(0, min(st["row"], rows_total - rows_shown))
    tw = (cw - GAP * s * (per_row - 1)) / per_row
    fav = set(state.get("favourites", []))
    for n, entry in enumerate(found[first * per_row:(first + rows_shown) * per_row]):
        r, c = divmod(n, per_row)
        ry = area_top - (r + 1) * ih - r * GAP * s
        rect = (cx + c * (tw + GAP * s), ry, tw, ih)
        cid = entry["id"]
        edit = (entry["label"], _rename(entry["key"])) if entry["kind"] == "saved" else None
        kind = "catrow" if st["view"] == "LIST" else "presettile"
        els.append(El("item_" + cid, kind, rect, entry["label"], on=cid == current, act=_apply(cid),
                      extra=_swatch(entry, here), shown=_preview(entry, obj), edit=edit,
                      value=1.0 if cid in fav else 0.0, layer="sheet",
                      hit=rect))
        if kind == "catrow":
            els[-1].extra = {"rgb": _swatch(entry, here), "meta": entry["description"],
                             "family": catalog.FAMILY_LABELS.get(entry["family"], "")}
    if not found:
        els.append(El("browser_empty", "note", (cx, area_top - 40 * s, cw, 32 * s), "",
                      extra=hud._wrap(_empty_text(st, q), cw, hud.HELP * s, g)))
    if rows_total > rows_shown:
        track = (x + w - PAD * s - 6 * s, area_bottom, 6 * s, area_top - area_bottom)
        span = rows_total - rows_shown
        els.append(El("browser_scroll", "scrollbar", track, value=first / span,
                      extra={"frac": rows_shown / rows_total}, track=track,
                      drag=lambda c, f, span=span: uistate.browser.update(row=round((1 - f) * span)),
                      hit=(track[0] - 6 * s, track[1], track[2] + 12 * s, track[3])))
    els += _apply_to_row(scene, st, obj, products, cx, y0 + PAD * s, cw + 10 * s, g)
    for e in els:
        if e.layer is None:
            e.layer = "sheet"
    return els


def _here_colour(obj):
    mat = materials.part_material(obj) if obj is not None and obj.material_slots else None
    if mat is not None and mat.get("plinth_type") in materials.TYPES:
        name = materials.TYPES[mat["plinth_type"]]["colour"]
        if name:
            return tuple(materials.get_input(obj, name))
    return (0.5, 0.5, 0.5, 1)


def _empty_text(st, q):
    if q.strip():
        return 'Nothing matches "%s". Try another word (finish, colour, family).' % q.strip()
    return {"FAV": "No favourites yet. Right-click a material and choose Favourite.",
            "RECENT": "Materials you use show up here.",
            "SCENE": "Nothing on the product yet.",
            "MINE": "Nothing saved yet. Save a part's material (Save on the card) to use it "
                    "anywhere."}.get(st["section"], "Nothing here yet.")


def _nav_items(scene, st, entries, state):
    """[(id, label, count, on, act, indent) or None for a divider]."""
    from . import matlib
    sec = st["section"] if not query().strip() else None
    out = []
    counts = catalog.counts(entries)
    n = {"FAV": len(catalog.section(entries, "FAV", state)),
         "RECENT": len(catalog.section(entries, "RECENT", state)),
         "SCENE": len(catalog.scene_ids(scene_products(scene))),
         "MINE": sum(1 for e in entries if e["source"] == "mine")}
    for key, label in catalog.SECTIONS:
        out.append((key, label, n[key], sec == key, _set(section=key, query="", row=0), 0))
        if key == "MINE" and (sec == "MINE" or (sec or "").startswith("FOLDER:")):
            for name in matlib.folders():
                fid = "FOLDER:" + name
                k = sum(1 for e in entries if e["source"] == "mine" and e["folder"] == name)
                out.append((fid, name, k, sec == fid, _set(section=fid, query="", row=0), 14))
    out.append(None)
    for key, label, _desc in catalog.FAMILIES:
        if counts.get(key):
            out.append((key, label, counts[key], sec == key, _set(section=key, query="", row=0), 0))
    return out


def _apply_to_row(scene, st, obj, products, x, y, w, g):
    s = g.scale
    sharing = len(materials.parts_sharing(scene_products(scene), obj)) if obj is not None \
        and obj.material_slots else 1
    selected = len(products)
    labels = {"PART": "This part", "SHARING": "Parts like it (%d)" % sharing,
              "SELECTED": "Selected (%d)" % selected}
    usable = {"PART": True, "SHARING": sharing > 1, "SELECTED": selected > 1}
    mode = st["apply_to"] if usable.get(st["apply_to"]) else "PART"
    h = 38 * s
    lw = g.measure("Apply to", hud.SMALL * s) + 12 * s
    els = [El("applyto_head", "hint", (x, y, lw, h), "Apply to")]
    bx = x + lw
    bw = w - lw
    els.append(El("applyto_bg", "well", (bx, y, bw, h), extra={"radius": 12 * s}, block=True))
    cw = (bw - 8 * s) / len(APPLY_TO)
    for i, (key, _label) in enumerate(APPLY_TO):
        els.append(El("applyto_" + key, "segcell", (bx + 4 * s + i * cw, y + 4 * s, cw, h - 8 * s),
                      labels[key], on=mode == key, act=_set(apply_to=key) if usable[key] else None,
                      dim=not usable[key], group="seg:applyto"))
    return els


# --- Menus ------------------------------------------------------------------------------

def catalog_menu_items(scene, els, m):
    """Right-click on a tile: apply, favourite, and (your library) rename,
    duplicate, update, move to a folder, delete."""
    from . import matlib, overlay
    cid = m.get("target", "")
    kind, _sep, key = cid.partition(":")
    tile = next((e for e in els if e.id == "item_" + cid), None)
    fav = catalog.is_favourite(cid)
    items = [("apply", "Apply to this part", "", lambda c: hud._op("plinth.apply_catalog", entry=cid,
                                                                    scope="PART"), False, None),
             ("apply_like", "Apply to parts like it", "",
              lambda c: hud._op("plinth.apply_catalog", entry=cid, scope="SHARING"), False, None),
             ("favourite", "Remove from favourites" if fav else "Favourite", "",
              lambda c: catalog.toggle_favourite(cid), False, ("F", False))]
    if kind == "mine" and key in matlib.entries():
        def rename(c):
            if tile is not None:
                overlay.start_edit(c, tile)

        def new_folder(c):
            if tile is not None:
                overlay.start_edit(c, El(tile.id, tile.kind, tile.rect, edit=(
                    "", lambda cc, text: matlib.move(key, text))))
        items += [("rename", "Rename", "F2", rename, False, ("F2", False)),
                  ("duplicate", "Duplicate", "⇧D", lambda c: matlib.duplicate(key), False, ("D", True)),
                  ("update", "Update from this part", "", lambda c: hud._op("plinth.update_saved_material",
                                                                             entry=key), False, None)]
        for name in matlib.folders()[:5]:
            items.append(("folder_" + name, "Move to " + hud._short(name, 14), "",
                          (lambda c, name=name: matlib.move(key, name)), False, None))
        items.append(("newfolder", "Move to new folder…", "", new_folder, False, None))
        if matlib.entries()[key].get("folder"):
            items.append(("unfile", "Take out of folder", "", lambda c: matlib.move(key, ""), False, None))
        items.append(("delete", "Delete", "⌫", lambda c: hud._op("plinth.remove_saved_material", entry=key),
                      True, ("BACK_SPACE", False)))
    return items


def pins_menu_items(scene, els, m):
    """Material ⋯ › Card sliders: tick the controls the card shows (up to four)."""
    type_key = m.get("target", "")
    if type_key not in materials.TYPES:
        return []
    shown = catalog.pinned(type_key)
    out = []
    for name, kind, *_ in materials.controls(type_key):
        if kind in ("colour", "bool"):
            continue

        def toggle(c, name=name):
            catalog.toggle_pin(type_key, name)
            hud.open_menu(c, "pins", type_key, "card_more")     # stay open: tick several
        out.append(("pin_" + hud._slug(name), name, "✓" if name in shown else "", toggle, False, None))
    return out[:12]


hud.MENU_BUILDERS.update({"catalog": catalog_menu_items, "pins": pins_menu_items})
