# SPDX-License-Identifier: GPL-3.0-or-later
"""The overlay text field's editing model (REDESIGN.md §5), plain Python.

Blender's draw API can't make text widgets, so the overlay draws its own field
and a short-lived modal operator feeds it key events. Everything about editing
(caret, selection, keys) lives here so it is tested without bpy.
"""

# Keys that edit or move; anything else with a printable ``unicode`` inserts it.
CONTROL_KEYS = {"BACK_SPACE", "DEL", "LEFT_ARROW", "RIGHT_ARROW", "HOME", "END",
                "RET", "NUMPAD_ENTER", "ESC", "TAB"}
# Event types that type a character on a Latin keyboard; if one arrives with an
# empty ``unicode`` an input method is composing (CJK IME), which modal
# operators don't receive reliably: fall back to Blender's native field.
_TYPING = (set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
           | {"ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE",
              "SPACE", "MINUS", "PLUS", "PERIOD", "COMMA", "SLASH", "SEMI_COLON", "QUOTE",
              "LEFT_BRACKET", "RIGHT_BRACKET", "BACK_SLASH", "EQUAL", "ACCENT_GRAVE"})
MAX_LENGTH = 64


class TextModel:
    """Text with a caret and an optional selection (``anchor`` .. ``caret``)."""

    def __init__(self, text="", select_all=True, max_length=MAX_LENGTH):
        self.text = text[:max_length]
        self.max_length = max_length
        self.caret = len(self.text)
        self.anchor = 0 if select_all else self.caret
        self.original = self.text

    # --- Selection -------------------------------------------------------------------

    @property
    def selection(self):
        """(start, end) of the selected text, or None."""
        if self.anchor == self.caret:
            return None
        return min(self.anchor, self.caret), max(self.anchor, self.caret)

    def _delete_selection(self):
        sel = self.selection
        if sel is None:
            return False
        a, b = sel
        self.text = self.text[:a] + self.text[b:]
        self.caret = self.anchor = a
        return True

    def select_all(self):
        self.anchor, self.caret = 0, len(self.text)

    # --- Editing ---------------------------------------------------------------------

    def insert(self, chars):
        chars = "".join(c for c in chars if c.isprintable() and c not in "\r\n\t")
        self._delete_selection()
        room = self.max_length - len(self.text)
        chars = chars[:max(0, room)]
        self.text = self.text[:self.caret] + chars + self.text[self.caret:]
        self.caret = self.anchor = self.caret + len(chars)

    def paste(self, clipboard):
        self.insert((clipboard or "").replace("\r\n", " ").replace("\n", " "))

    def backspace(self):
        if not self._delete_selection() and self.caret > 0:
            self.text = self.text[:self.caret - 1] + self.text[self.caret:]
            self.caret = self.anchor = self.caret - 1

    def delete(self):
        if not self._delete_selection() and self.caret < len(self.text):
            self.text = self.text[:self.caret] + self.text[self.caret + 1:]

    def move(self, to, extend=False):
        """Put the caret at ``to``; collapse the selection unless ``extend``."""
        self.caret = max(0, min(len(self.text), to))
        if not extend:
            self.anchor = self.caret

    def left(self, extend=False):
        sel = self.selection
        self.move(sel[0] if sel and not extend else self.caret - 1, extend)

    def right(self, extend=False):
        sel = self.selection
        self.move(sel[1] if sel and not extend else self.caret + 1, extend)

    def home(self, extend=False):
        self.move(0, extend)

    def end(self, extend=False):
        self.move(len(self.text), extend)


def handle_key(model, type_, unicode="", ctrl=False, shift=False, clipboard=""):
    """Apply one key press. Returns "commit", "cancel", "native" (IME: use
    Blender's own field) or None (keep editing)."""
    if type_ in {"RET", "NUMPAD_ENTER"}:
        return "commit"
    if type_ == "ESC":
        return "cancel"
    if ctrl and type_ == "A":
        model.select_all()
    elif ctrl and type_ == "V":
        model.paste(clipboard)
    elif ctrl and type_ == "C":
        pass                                      # copying is the caller's (clipboard write)
    elif type_ == "BACK_SPACE":
        model.backspace()
    elif type_ == "DEL":
        model.delete()
    elif type_ == "LEFT_ARROW":
        model.home(shift) if ctrl else model.left(shift)
    elif type_ == "RIGHT_ARROW":
        model.end(shift) if ctrl else model.right(shift)
    elif type_ == "HOME":
        model.home(shift)
    elif type_ == "END":
        model.end(shift)
    elif unicode and unicode.isprintable() and not ctrl:
        model.insert(unicode)
    elif type_ in _TYPING and not ctrl:
        return "native"
    return None


def parse_number(text):
    """First number in ``text`` ("486 W" -> 486.0, "f/2.8" -> 2.8), or None."""
    import re
    m = re.search(r"[-+]?\d*[.,]?\d+(?:[eE][-+]?\d+)?", text or "")
    return float(m.group(0).replace(",", ".")) if m else None


def parse_hex(text):
    """'#E6E3DC', 'e6e3dc' or 'fff' -> (r, g, b) in 0..1 sRGB, or None."""
    t = (text or "").strip().lstrip("#")
    if len(t) == 3:
        t = "".join(c * 2 for c in t)
    if len(t) != 6:
        return None
    try:
        return tuple(int(t[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return None
