#!/usr/bin/env python3
#
# koloroj - pick and compare 256-color terminal color swatches.
# Copyright (C) 2026 Chas Kissick
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""koloroj (curses) - pick and compare 256-color terminal color swatches.

Two side-by-side schemes of ten slots each (20 total); on a narrow terminal
only the active scheme is shown. Type a color code (0-255) in a slot's box and
the rest of that slot instantly shows:
  - foreground dummy text ("lorem ipsum 12345") in that color
  - a solid background block in that color
  - a short friendly color name

Keys:
  0-9            edit the code in the current slot
  Backspace      delete a digit
  Tab            next slot (left-to-right, top-to-bottom; Shift-Tab reverses)
  Enter          next row, same column
  h / l          move to the left / right column
  j / k          move down / up a row  (arrow keys work too)
  g              toggle the 256-color reference grid (off by default)
  s              swap schemes A and B
  c              copy the current column's codes to the clipboard
  Ctrl-U         clear the current slot
  Ctrl-L         clear all slots
  q / Esc        quit

State is saved to ~/.config/koloroj/state.json after every edit and restored
on the next launch.
"""
import curses
import json
import locale
import os
import shutil
import subprocess

SAMPLE = "lorem ipsum 12345"
SLOT_ROWS = 10
SLOT_COLS = 2                    # two side-by-side schemes of 10 to compare
NSLOTS = SLOT_ROWS * SLOT_COLS   # 20 slots, stored column-major
COL_WIDTH = 54                   # preferred screen columns per slot column
MIN_COL_WIDTH = 16               # floor so a tiny window degrades gracefully
# below this width both columns can't fit, so only the active one is shown
TWO_COL_MIN_WIDTH = 100

STATE_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "koloroj")
STATE_FILE = os.path.join(STATE_DIR, "state.json")


BACKUP_FILE = STATE_FILE + ".bak"


def _read_slots(path):
    """Load slots from a state file, remapping a file written with a different
    rows-per-column so each scheme stays in its own column. Both columns are
    stored column-major; older files used 12 rows, so we realign to SLOT_ROWS.
    Raises on unreadable/corrupt files (the caller falls back to the backup)."""
    with open(path) as f:
        data = json.load(f)
    saved = data.get("slots", [])
    old_rows = data.get("rows")
    if not isinstance(old_rows, int) or old_rows <= 0:
        # infer from length: old files were SLOT_COLS columns of equal height
        old_rows = (len(saved) // SLOT_COLS) if len(saved) % SLOT_COLS == 0 \
            else len(saved)
    slots = [""] * NSLOTS
    for col in range(SLOT_COLS):
        for row in range(min(SLOT_ROWS, old_rows)):
            oi = col * old_rows + row
            if oi < len(saved) and isinstance(saved[oi], str):
                slots[col * SLOT_ROWS + row] = saved[oi]
    return slots


def load_slots():
    # Prefer the main file; if it's missing or corrupt, fall back to the
    # last-known-good backup so an accidental wipe or crash can't erase history.
    try:
        return _read_slots(STATE_FILE)
    except (OSError, ValueError):
        pass
    try:
        return _read_slots(BACKUP_FILE)
    except (OSError, ValueError):
        return [""] * NSLOTS


def _atomic_write(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, path)


def save_slots(slots, active):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        payload = {"slots": slots, "active": active, "rows": SLOT_ROWS}
        _atomic_write(STATE_FILE, payload)
        # Only refresh the backup when there's something to protect, so the
        # backup always holds your most recent non-empty palette.
        if any(s for s in slots):
            _atomic_write(BACKUP_FILE, payload)
    except OSError:
        pass


# clipboard tools to try, in order: Wayland, then X11 (xclip / xsel)
_CLIPBOARD_CMDS = [
    ["wl-copy"],
    ["xclip", "-selection", "clipboard"],
    ["xsel", "--clipboard", "--input"],
]


def copy_to_clipboard(text):
    """Send text to the system clipboard. Returns the tool name used, or None
    if no clipboard utility is available / the copy failed."""
    for cmd in _CLIPBOARD_CMDS:
        if shutil.which(cmd[0]) is None:
            continue
        try:
            subprocess.run(cmd, input=text.encode(), check=True,
                           timeout=5, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            return cmd[0]
        except (OSError, subprocess.SubprocessError):
            continue
    return None


class Pairs:
    """Lazily allocate curses color pairs, reusing by (fg, bg)."""

    def __init__(self):
        self._map = {}
        self._next = 1
        # curses.color_pair() packs the pair number into 8 bits, so numbers
        # above 255 wrap to a different (wrong) pair. Cap here so we hand back
        # the default pair instead of a corrupt one. The UI only ever needs one
        # (color, -1) pair per color (255 of them), so it never reaches this.
        self._max = min(255, curses.COLOR_PAIRS - 1)

    def get(self, fg, bg):
        key = (fg, bg)
        if key in self._map:
            return self._map[key]
        if self._next > self._max:
            return 0
        pid = self._next
        curses.init_pair(pid, fg, bg)
        self._map[key] = pid
        self._next += 1
        return pid


def color_attr(pairs, code):
    """curses attribute that paints in color `code` (as foreground on the
    default background). Color 0 is black, which equals the default background,
    so it needs no pair — we render it as the default (blank)."""
    if code <= 0:
        return curses.A_NORMAL
    return curses.color_pair(pairs.get(code, -1))


def draw_swatch(stdscr, y, x, n, code, pairs):
    """Draw an n-wide solid swatch of color `code` using '█' foreground glyphs,
    so it needs only the one (code, -1) pair. Color 0 (black) is drawn as blank
    space, which shows the default (black) background."""
    if n <= 0:
        return
    glyph = ("█" if code > 0 else " ") * n
    try:
        stdscr.addstr(y, x, glyph, color_attr(pairs, code))
    except curses.error:
        pass


def code_of(text):
    if not text:
        return None
    try:
        c = int(text)
    except ValueError:
        return None
    return c if 0 <= c <= 255 else None


# --- friendly color naming ---------------------------------------------------

# RGB for the 16 system colors (standard xterm palette).
_SYSTEM_RGB = [
    (0, 0, 0), (128, 0, 0), (0, 128, 0), (128, 128, 0),
    (0, 0, 128), (128, 0, 128), (0, 128, 128), (192, 192, 192),
    (128, 128, 128), (255, 0, 0), (0, 255, 0), (255, 255, 0),
    (0, 0, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
]
_CUBE_LEVELS = (0, 95, 135, 175, 215, 255)

# Curated reference points; each row's name is the nearest one of these.
_NAMED = [
    ("black", 0, 0, 0), ("dark grey", 64, 64, 64), ("grey", 128, 128, 128),
    ("silver", 192, 192, 192), ("white", 255, 255, 255),
    ("maroon", 128, 0, 0), ("dark red", 160, 0, 0), ("red", 255, 0, 0),
    ("salmon", 250, 128, 114), ("pink", 255, 160, 180),
    ("hot pink", 255, 105, 180), ("brown", 140, 70, 20),
    ("orange", 255, 140, 0), ("amber", 255, 180, 40), ("gold", 255, 215, 0),
    ("tan", 210, 180, 140), ("beige", 235, 225, 190), ("cream", 255, 250, 205),
    ("yellow", 255, 255, 0), ("pale yellow", 250, 250, 170),
    ("olive", 128, 128, 0), ("lime", 160, 255, 0),
    ("green", 0, 170, 0), ("dark green", 0, 100, 0),
    ("mint", 150, 255, 190), ("teal", 0, 128, 128), ("cyan", 0, 255, 255),
    ("sky blue", 135, 206, 235), ("blue", 0, 0, 255), ("navy", 0, 0, 128),
    ("royal blue", 65, 105, 225), ("indigo", 75, 0, 130),
    ("purple", 128, 0, 128), ("violet", 150, 80, 220),
    ("lavender", 200, 180, 240), ("magenta", 255, 0, 255),
]


def code_to_rgb(code):
    if code < 16:
        return _SYSTEM_RGB[code]
    if code < 232:
        c = code - 16
        return (_CUBE_LEVELS[c // 36],
                _CUBE_LEVELS[(c // 6) % 6],
                _CUBE_LEVELS[c % 6])
    v = 8 + (code - 232) * 10  # grayscale ramp
    return (v, v, v)


def name_of(code):
    r, g, b = code_to_rgb(code)
    best, bestd = "", None
    for name, nr, ng, nb in _NAMED:
        d = (r - nr) ** 2 + (g - ng) ** 2 + (b - nb) ** 2
        if bestd is None or d < bestd:
            best, bestd = name, d
    return best


def slot_rc(idx):
    """(row, col) for a storage index. Column-major: col 0 = slots 0..11,
    col 1 = slots 12..23, so an old 12-slot save loads as the left column."""
    col, row = divmod(idx, SLOT_ROWS)
    return row, col


def slot_idx(row, col):
    return col * SLOT_ROWS + row


def col_width(w):
    """Width of one slot column, sized to the terminal so both columns fit."""
    return max(MIN_COL_WIDTH, min(COL_WIDTH, w // SLOT_COLS))


def draw_slot(stdscr, slots, idx, top, x0, colw, is_active, pairs):
    h, w = stdscr.getmaxyx()
    row = slot_rc(idx)[0]
    y = top + row
    if y >= h - 1 or x0 >= w:
        return
    # everything in this slot must stay left of `right` (column edge or screen
    # edge, whichever comes first) so nothing bleeds into the other column or
    # off-screen — the latter is what was dropping the bg block.
    right = min(x0 + colw, w)

    def put(x, s, attr=curses.A_NORMAL):
        if x >= right or not s:
            return
        s = s[:right - x]
        try:
            stdscr.addstr(y, x, s, attr)
        except curses.error:
            pass

    text = slots[idx]
    marker = ">" if is_active else " "
    # input box, fixed 3-wide, digits left-aligned so the cursor can sit
    # right after them instead of stranded on the closing bracket.
    put(x0, marker + " ")
    put(x0 + 2, f"[{text:<3}]", curses.A_REVERSE if is_active else curses.A_NORMAL)

    code = code_of(text)
    if code is not None:
        sx = x0 + 9
        put(sx, SAMPLE, color_attr(pairs, code))
        bx = sx + len(SAMPLE) + 2
        # solid colour block, drawn as '█' glyphs so it shares the slot's one
        # (code, -1) pair instead of needing a separate background pair
        draw_swatch(stdscr, y, bx, min(8, right - bx), code, pairs)
        put(bx + 8 + 2, name_of(code))
    elif text:
        put(x0 + 9, "(0-255 only)", curses.A_DIM)


def draw(stdscr, slots, active, pairs, show_grid, status=""):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    stdscr.addstr(0, 0, "koloroj - two color schemes side by side", curses.A_BOLD)
    stdscr.addstr(1, 0,
                  "Tab/Enter move  -  h/j/k/l nav  -  g grid  -  s swap  -  c copy  -  "
                  "Ctrl-U clear  -  Ctrl-L all  -  q quits")
    if status:
        try:
            stdscr.addstr(2, 0, status[:w - 1], curses.A_BOLD)
        except curses.error:
            pass

    # On a narrow terminal both columns can't fit, so show only the active one.
    # `visible` maps each on-screen slot column to its logical scheme column.
    active_col = slot_rc(active)[1]
    if w < TWO_COL_MIN_WIDTH:
        visible = [active_col]
        colw = w
    else:
        visible = list(range(SLOT_COLS))
        colw = col_width(w)

    # column headers, highlighting whichever column the cursor is in
    for screen_i, c in enumerate(visible):
        x0 = screen_i * colw
        label = f"scheme {chr(ord('A') + c)}"
        if len(visible) == 1:
            label += "   (h/l switches scheme)"
        attr = curses.A_BOLD | (curses.A_UNDERLINE if c == active_col else 0)
        if x0 < w:
            try:
                stdscr.addstr(3, x0, label, attr)
            except curses.error:
                pass

    top = 4
    for screen_i, c in enumerate(visible):
        x0 = screen_i * colw
        for row in range(SLOT_ROWS):
            idx = slot_idx(row, c)
            draw_slot(stdscr, slots, idx, top, x0, colw, idx == active, pairs)

    # 256-color reference grid, 16 per row. Each cell is the code number butted
    # directly against its own '█' swatch ("NNN███"), with the gap that
    # separates cells falling to the LEFT of the number — so the number always
    # reads with its own swatch, never the neighbour's (which a 3-digit code
    # would otherwise touch). Swatches share the slots' (code, -1) pairs, so the
    # whole UI stays within the 255-pair limit. The swatch widens to use the
    # terminal, falling back to 2 cells on a narrow window.
    grid_top = top + SLOT_ROWS + 1
    num_w = 3
    swatch_w = max(2, min(8, w // 16 - (num_w + 1)))
    cell_w = num_w + swatch_w + 1  # trailing column separates this cell's swatch
    if show_grid and grid_top < h - 1:                       # from the next number
        try:
            stdscr.addstr(grid_top, 0, "256-color grid (code + swatch):",
                          curses.A_BOLD)
        except curses.error:
            pass
        for code in range(256):
            gy = grid_top + 1 + code // 16
            gx = (code % 16) * cell_w
            if gy >= h or gx + cell_w > w:
                continue
            try:
                stdscr.addstr(gy, gx, f"{code:>{num_w}}")
            except curses.error:
                pass
            draw_swatch(stdscr, gy, gx + num_w, swatch_w, code, pairs)
    elif not show_grid and grid_top < h - 1:
        try:
            stdscr.addstr(grid_top, 0, "press g for the 256-color grid",
                          curses.A_DIM)
        except curses.error:
            pass

    # park the cursor in the active input box, right after the typed digits.
    # box: x0+2 = "[", left-aligned digits at x0+3..x0+5, "]" at x0+6.
    cur_y = top + slot_rc(active)[0]
    cur_x0 = visible.index(active_col) * colw
    cur_x = cur_x0 + 3 + min(len(slots[active]), 3)
    try:
        stdscr.move(cur_y, min(cur_x, w - 1))
    except curses.error:
        pass
    stdscr.refresh()


def run(stdscr):
    curses.curs_set(1)
    curses.start_color()
    curses.use_default_colors()
    stdscr.keypad(True)

    if curses.COLORS < 256:
        stdscr.addstr(0, 0,
                      "Terminal reports <256 colors; set TERM=xterm-256color. "
                      "Press a key.")
        stdscr.getch()

    pairs = Pairs()
    slots = load_slots()
    active = 0
    show_grid = True
    status = ""

    while True:
        draw(stdscr, slots, active, pairs, show_grid, status)
        status = ""  # status is shown for one frame, then cleared
        ch = stdscr.getch()

        row, col = slot_rc(active)
        if ch in (ord("q"), 27):  # q or Esc
            save_slots(slots, active)
            return
        elif ch == ord("g"):  # toggle the reference grid
            show_grid = not show_grid
        elif ch == ord("s"):  # swap schemes A and B
            slots = slots[SLOT_ROWS:] + slots[:SLOT_ROWS]
        elif ch == ord("c"):  # copy current column's codes to the clipboard
            codes = [slots[slot_idx(r, col)] for r in range(SLOT_ROWS)]
            codes = [v for v in codes if v]
            scheme = chr(ord("A") + col)
            if not codes:
                status = f"scheme {scheme} is empty - nothing to copy"
            else:
                tool = copy_to_clipboard(" ".join(codes))
                if tool:
                    status = f"copied scheme {scheme} ({len(codes)} codes) to clipboard"
                else:
                    status = "no clipboard tool found (install wl-clipboard, xclip, or xsel)"
        elif ch == ord("\t"):  # Tab: right, then next row's left
            if col < SLOT_COLS - 1:
                active = slot_idx(row, col + 1)
            else:
                active = slot_idx((row + 1) % SLOT_ROWS, 0)
        elif ch in (curses.KEY_ENTER, 10, 13):  # Enter: next row, same column
            active = slot_idx((row + 1) % SLOT_ROWS, col)
        elif ch == curses.KEY_BTAB:  # Shift-Tab: reverse of Tab
            if col > 0:
                active = slot_idx(row, col - 1)
            else:
                active = slot_idx((row - 1) % SLOT_ROWS, SLOT_COLS - 1)
        elif ch in (curses.KEY_LEFT, ord("h")):  # left column
            active = slot_idx(row, 0)
        elif ch in (curses.KEY_RIGHT, ord("l")):  # right column
            active = slot_idx(row, SLOT_COLS - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):  # next row, same column
            active = slot_idx((row + 1) % SLOT_ROWS, col)
        elif ch in (curses.KEY_UP, ord("k")):  # previous row, same column
            active = slot_idx((row - 1) % SLOT_ROWS, col)
        elif ch in (curses.KEY_BACKSPACE, 127, 8):
            slots[active] = slots[active][:-1]
        elif ch == 21:  # Ctrl-U
            slots[active] = ""
        elif ch == 12:  # Ctrl-L
            slots = [""] * NSLOTS
        elif ord("0") <= ch <= ord("9"):
            if len(slots[active]) < 3:
                slots[active] += chr(ch)

        # persist after every keystroke so state survives even an abrupt close
        save_slots(slots, active)


def main():
    # needed so curses can render the wide '█' block glyph used for swatches
    locale.setlocale(locale.LC_ALL, "")
    curses.wrapper(run)


if __name__ == "__main__":
    main()
