# SPDX-License-Identifier: GPL-3.0-or-later
"""Motion for the floating UI (REDESIGN.md §7): what is animating, and where it
is right now.

hud.py publishes *targets* (where things should be: the selected segment, the
card's height, which page is open). Every draw, ``Motion.update`` compares them
with the last frame and starts tweens for whatever changed; overlay.py reads
the animated current values while painting. Plain Python on hud elements, so it
is tested headless.

Rules: controls animate, values and the 3D scene never do (a slider value is
instant). Everything is <= 240 ms, exits are faster than entrances, and a
retarget continues from the current value.
"""

from .anim import DURATION, EASE_OUT, OVERSHOOT, Animator

SLIDE_PX = 16                 # page flip: content slides this far (px at scale 1)
PRESS_SCALE = 0.96
POP_KINDS = {"tick"}          # pop 0.5 -> 1.12 -> 1 when they turn on
SWITCH_KINDS = {"switchrow", "listrow"}


class Motion:
    def __init__(self, animator=None):
        self.a = animator or Animator()
        self._seen = {}               # key -> last target
        self._menu_ids = ()
        self._last_menu = []
        self.ghost = []               # a closing menu's elements, fading out
        self.pressed = None
        self.dragging = None

    # --- Internals ---------------------------------------------------------------

    def _track(self, key, target, duration, ease=EASE_OUT, fade=False):
        """Tween ``key`` to ``target`` when the target changes. The first time a
        key is seen it is set instantly: nothing animates in on first draw."""
        if key not in self._seen:
            self._seen[key] = target
            self.a.set(key, target)
            return False
        if self._seen[key] == target:
            return False
        self._seen[key] = target
        self.a.to(key, target, duration, ease, fade=fade)
        return True

    # --- Per frame -------------------------------------------------------------------

    def update(self, els, hover=None, dragging=None):
        """Diff this frame's targets against the last and start tweens."""
        self.dragging = dragging
        indicators = {}
        menu_now = [e for e in els if getattr(e, "layer", None) == "menu"]
        for e in els:
            if e.act is not None or e.drag is not None or e.kind in SWITCH_KINDS:
                self._track(e.id + ".hover", 1.0 if hover == e.id else 0.0,
                            DURATION["fast"], fade=True)
                self._track(e.id + ".on", 1.0 if e.on else 0.0, DURATION["fast"], fade=True)
            if e.group and e.on:
                indicators[e.group] = tuple(e.rect)
            if e.kind in SWITCH_KINDS:
                self._track(e.id + ".switch", 1.0 if e.on else 0.0, DURATION["switch"], OVERSHOOT)
            if e.kind == "palette":
                self._track(e.id + ".ring", 1.0 if e.on else 0.0, DURATION["base"])
            if e.kind in POP_KINDS:
                was = self._seen.get(e.id + ".pop")
                self._seen[e.id + ".pop"] = bool(e.on)
                if e.on and was is False:
                    self.a.keys(e.id + ".pop", [(1.12, DURATION["pop"] * 0.6, EASE_OUT),
                                                (1.0, DURATION["pop"] * 0.4, EASE_OUT)], start=0.5)
            if e.kind == "slider":
                self._track(e.id + ".grab", 1.0 if dragging == e.id else 0.0, 0.140, OVERSHOOT)
            if e.kind == "card":
                self._card(e)
            if e.kind == "progress":
                self.a.set(e.id + ".fill", e.value)          # follows the real render
        for group, rect in indicators.items():
            self._track(group + ".indicator", rect, DURATION["base"])
        self._menu(menu_now)

    def _card(self, card):
        self._track("card.rect", tuple(card.rect), DURATION["page"])
        page = (card.extra or {}).get("page", "")
        step = page.split("/")[0]
        last = self._seen.get("card.page")
        self._seen["card.page"] = page
        if last is None or last == page or last.split("/")[0] != step:
            return
        was_page, is_page = "/page" in last, "/page" in page
        if was_page != is_page:                      # front <-> page: slide + fade
            self.a.set("card.dir", 1.0 if is_page else -1.0)
            self.a.to("card.flip", 0.0, DURATION["page"], start=1.0, fade=True)
        else:                                        # another tab: fade only
            self.a.set("card.dir", 0.0)
            self.a.to("card.flip", 0.0, DURATION["fast"], start=1.0, fade=True)

    def _menu(self, menu_now):
        ids = tuple(e.id for e in menu_now)
        if menu_now:
            self._last_menu = menu_now
        if ids == self._menu_ids:
            if not menu_now and self.ghost and not self.a.moving("menu.out"):
                self.ghost = []
            return
        if menu_now and not self._menu_ids and self._seen:     # opened (not on the first frame)
            self.a.to("menu.in", 1.0, DURATION["menu_in"], start=0.0, fade=True)
            self.ghost = []
        elif not menu_now:                           # closed: fade the last frame out
            self.ghost = self._last_menu
            self.a.to("menu.out", 0.0, DURATION["exit"], start=1.0, fade=True)
        self._menu_ids = ids

    def press(self, el_id):
        """Any clickable: scale to 0.96 about its centre, then back with a nudge."""
        self.pressed = el_id
        self.a.keys(el_id + ".press", [(PRESS_SCALE, DURATION["press_down"], EASE_OUT),
                                       (1.0, DURATION["press_up"], OVERSHOOT)], start=1.0)

    # --- Reading ---------------------------------------------------------------------

    def hover(self, el_id):
        return self.a.value(el_id + ".hover", 0.0)

    def on(self, el_id, default=0.0):
        return self.a.value(el_id + ".on", default)

    def scale(self, el_id):
        return self.a.value(el_id + ".press", 1.0) * self.a.value(el_id + ".pop", 1.0)

    def indicator(self, group):
        return self.a.value(group + ".indicator", None)

    def switch(self, el_id, default=0.0):
        return self.a.value(el_id + ".switch", default)

    def ring(self, el_id, default=0.0):
        return self.a.value(el_id + ".ring", default)

    def grab(self, el_id):
        return self.a.value(el_id + ".grab", 0.0)

    def card_rect(self, rect):
        return self.a.value("card.rect", tuple(rect))

    def flip(self):
        """(x offset in px at scale 1, opacity) for the card's content."""
        f = self.a.value("card.flip", 0.0)
        return SLIDE_PX * f * self.a.value("card.dir", 0.0), 1.0 - f

    def menu_in(self):
        """(scale, opacity) of the open menu."""
        t = self.a.value("menu.in", 1.0)
        return 0.94 + 0.06 * t, t

    def menu_out(self):
        return self.a.value("menu.out", 0.0)

    def fill(self, el_id, default=0.0):
        return self.a.value(el_id + ".fill", default)

    @property
    def active(self):
        return self.a.active

