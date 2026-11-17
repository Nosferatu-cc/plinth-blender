# SPDX-License-Identifier: GPL-3.0-or-later
"""Plinth motion: a tiny tween engine for the overlay.

The overlay redraws on events only. Animations work by keeping a timer alive
*only while something is moving*: start a tween, ensure the redraw pump is
running, and read values in the draw pass. When nothing is moving the timer is
removed, so idle cost is zero.

The engine itself is pure Python (no bpy) and fully unit-tested. The Blender
glue is the small RedrawPump class at the bottom.
"""

import time


# --- Curves -------------------------------------------------------------------

class CubicBezier:
    """CSS-style cubic-bezier(x1, y1, x2, y2) easing. Endpoints are (0,0), (1,1)."""

    def __init__(self, x1, y1, x2, y2):
        self.cx = 3.0 * x1
        self.bx = 3.0 * (x2 - x1) - self.cx
        self.ax = 1.0 - self.cx - self.bx
        self.cy = 3.0 * y1
        self.by = 3.0 * (y2 - y1) - self.cy
        self.ay = 1.0 - self.cy - self.by

    def _x(self, s):
        return ((self.ax * s + self.bx) * s + self.cx) * s

    def _y(self, s):
        return ((self.ay * s + self.by) * s + self.cy) * s

    def _dx(self, s):
        return (3.0 * self.ax * s + 2.0 * self.bx) * s + self.cx

    def _solve(self, x):
        s = x
        for _ in range(8):                      # Newton
            err = self._x(s) - x
            if abs(err) < 1e-6:
                return s
            d = self._dx(s)
            if abs(d) < 1e-6:
                break
            s -= err / d
        lo, hi, s = 0.0, 1.0, x                  # bisection fallback
        for _ in range(40):
            v = self._x(s)
            if abs(v - x) < 1e-6:
                return s
            if v < x:
                lo = s
            else:
                hi = s
            s = (lo + hi) / 2.0
        return s

    def __call__(self, t):
        if t <= 0.0:
            return 0.0
        if t >= 1.0:
            return 1.0
        return self._y(self._solve(t))


def LINEAR(t):
    return min(1.0, max(0.0, t))


EASE_OUT = CubicBezier(0.215, 0.61, 0.355, 1.0)
OVERSHOOT = CubicBezier(0.34, 1.56, 0.64, 1.0)

# Motion tokens (seconds). Mirror docs/design/REDESIGN.md §7 and tokens.json.
DURATION = {
    "press_down": 0.080,
    "press_up": 0.160,
    "fast": 0.120,      # hover, colour fades
    "exit": 0.100,      # anything disappearing
    "base": 0.180,      # segmented slides, swatch rings
    "switch": 0.200,
    "page": 0.220,      # card page flip, card height, button->progress morph
    "menu_in": 0.160,
    "pop": 0.240,       # tick / done pop
}


# --- Engine -------------------------------------------------------------------

def _lerp(a, b, t):
    if isinstance(a, (tuple, list)):
        return tuple(x + (y - x) * t for x, y in zip(a, b))
    return a + (b - a) * t


class Animator:
    """Keyed tweens. `to()` retargets from the *current* value, so interrupting
    an animation (clicking again mid-slide) never jumps.

    A tween is a chain of segments (start, end, t0, duration, ease); `keys()`
    builds multi-step ones (press: down then up; pop: 0.5 -> 1.12 -> 1).
    With ``reduce_motion`` every tween is instant, except opacity fades
    (``fade=True``), which keep a short 120 ms cross-fade."""

    FADE = 0.120

    def __init__(self, clock=time.monotonic, reduce_motion=False):
        self.clock = clock
        self.reduce_motion = reduce_motion
        self._tweens = {}   # key -> [(start, end, t0, duration, ease), ...]
        self._values = {}   # key -> settled value

    def _duration(self, duration, fade):
        if not self.reduce_motion:
            return duration
        return min(duration, self.FADE) if fade else 0.0

    def to(self, key, target, duration, ease=EASE_OUT, start=None, fade=False):
        if start is None:
            start = self.value(key, default=target)
        self.keys(key, [(target, duration, ease)], start=start, fade=fade)

    def keys(self, key, frames, start=None, fade=False):
        """Run through ``frames`` = [(value, duration, ease), ...] in order."""
        now = self.clock()
        if start is None:
            start = self.value(key, default=frames[0][0])
        chain, t0, prev = [], now, start
        for target, duration, ease in frames:
            duration = self._duration(duration, fade)
            if duration > 0 and prev != target:
                chain.append((prev, target, t0, duration, ease))
                t0 += duration
            prev = target
        if chain:
            self._tweens[key] = chain
        else:
            self._tweens.pop(key, None)
        self._values[key] = prev

    def set(self, key, value):
        """Jump with no animation (values that must never lag, e.g. slider values)."""
        self._tweens.pop(key, None)
        self._values[key] = value

    def value(self, key, default=0.0):
        chain = self._tweens.get(key)
        if chain is None:
            return self._values.get(key, default)
        now = self.clock()
        for start, end, t0, dur, ease in chain:
            if now < t0 + dur:
                return _lerp(start, end, ease(max(0.0, (now - t0) / dur)))
        del self._tweens[key]
        return self._values[key]

    def moving(self, key):
        """True while ``key`` has an unfinished tween."""
        chain = self._tweens.get(key)
        if not chain:
            return False
        _s, _e, t0, dur, _ease = chain[-1]
        return self.clock() < t0 + dur

    @property
    def active(self):
        now = self.clock()
        return any(now < c[-1][2] + c[-1][3] for c in self._tweens.values())

    def tweening(self):
        """Keys with an unfinished tween (tests, debugging)."""
        return [k for k in self._tweens if self.moving(k)]

    def clear(self):
        self._tweens.clear()
        self._values.clear()


# --- Blender glue -------------------------------------------------------------

class RedrawPump:
    """Redraws the viewports at ~60 fps only while something moves.

    Plinth has no always-running modal operator (it blocks autosave and dies on
    file load), so the pump is a ``bpy.app.timers`` function rather than a
    window timer: `kick()` after starting a tween registers it if it isn't
    running; it redraws every tick and unregisters itself once
    ``animator.active`` is False and nothing ``hold``s it (a focused text
    field's caret, a running Shoot). Idle cost is zero.

    ``register`` and ``redraw`` are injectable so the pump is testable without
    Blender."""

    FPS = 60

    def __init__(self, animator, register=None, redraw=None):
        self.animator = animator
        self._register = register
        self._redraw = redraw
        self.running = False
        self.holds = set()

    def kick(self, context=None):
        if not self.running and (self.animator.active or self.holds):
            self.running = True
            (self._register or _timers_register)(self._tick, first_interval=1.0 / self.FPS)
        (self._redraw or _redraw_all)()

    def hold(self, reason):
        self.holds.add(reason)
        self.kick()

    def release(self, reason):
        self.holds.discard(reason)

    def _tick(self):
        try:
            (self._redraw or _redraw_all)()
        except Exception:
            pass
        if self.animator.active or self.holds:
            return 1.0 / self.FPS
        self.running = False
        return None

    def shutdown(self):
        """Unregister (add-on unregister/reload): no timer outlives the module."""
        self.holds.clear()
        if self.running and self._register is None:
            import bpy
            if bpy.app.timers.is_registered(self._tick):
                bpy.app.timers.unregister(self._tick)
        self.running = False


def _timers_register(fn, first_interval):
    import bpy
    bpy.app.timers.register(fn, first_interval=first_interval)


def _redraw_all():
    import bpy
    wm = bpy.context.window_manager
    for win in (wm.windows if wm else ()):
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
