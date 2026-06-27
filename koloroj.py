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
"""koloroj - pick and compare 256-color terminal color swatches.

Run it bare in a terminal for the interactive app; give it codes (or pipe them
in) to print swatches and exit.

Interactive (no arguments, in a terminal):
    koloroj.py
  Two side-by-side schemes of ten slots each. Type a code (0-255) in a slot and
  see it as foreground text, a solid block, and a friendly name; a 256-color
  reference grid sits below. State is saved to ~/.config/koloroj/state.json.

  Keys:
    0-9 edit  ·  Backspace / Ctrl-U clear slot  ·  Ctrl-L clear all
    Tab / Enter move  ·  h j k l or arrows navigate
    g grid  ·  s swap A/B  ·  c copy column  ·  q quit

List / pipe (codes as arguments or on stdin):
    koloroj.py 230 196 21        # codes as arguments
    koloroj.py 16-21 226         # inclusive ranges
    echo 16-21 | koloroj.py      # read codes from stdin
  Prints one row per code: the code, foreground sample text, a color block, and
  a friendly name.
"""
import curses
import json
import locale
import os
import shutil
import subprocess
import sys

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


def code_of(text):
    if not text:
        return None
    try:
        c = int(text)
    except ValueError:
        return None
    return c if 0 <= c <= 255 else None


# --- color names -------------------------------------------------------------

# The authoritative xterm 256-color names, from the community-standard table at
# https://jonasjacek.github.io/colors/ (the xterm palette naming), lowercased
# and spaced, each followed by its hex value.
_NAMES = [
    "black #000000", "maroon #800000", "green #008000", "olive #808000", "navy #000080",
    "purple #800080", "teal #008080", "silver #c0c0c0", "grey #808080", "red #ff0000",
    "lime #00ff00", "yellow #ffff00", "blue #0000ff", "fuchsia #ff00ff", "aqua #00ffff",
    "white #ffffff", "grey 0 #000000", "navy blue #00005f", "dark blue #000087",
    "blue 3 #0000af", "blue 3 #0000d7", "blue 1 #0000ff", "dark green #005f00",
    "deep sky blue 4 #005f5f", "deep sky blue 4 #005f87", "deep sky blue 4 #005faf",
    "dodger blue 3 #005fd7", "dodger blue 2 #005fff", "green 4 #008700",
    "spring green 4 #00875f", "turquoise 4 #008787", "deep sky blue 3 #0087af",
    "deep sky blue 3 #0087d7", "dodger blue 1 #0087ff", "green 3 #00af00",
    "spring green 3 #00af5f", "dark cyan #00af87", "light sea green #00afaf",
    "deep sky blue 2 #00afd7", "deep sky blue 1 #00afff", "green 3 #00d700",
    "spring green 3 #00d75f", "spring green 2 #00d787", "cyan 3 #00d7af",
    "dark turquoise #00d7d7", "turquoise 2 #00d7ff", "green 1 #00ff00",
    "spring green 2 #00ff5f", "spring green 1 #00ff87", "medium spring green #00ffaf",
    "cyan 2 #00ffd7", "cyan 1 #00ffff", "dark red #5f0000", "deep pink 4 #5f005f",
    "purple 4 #5f0087", "purple 4 #5f00af", "purple 3 #5f00d7", "blue violet #5f00ff",
    "orange 4 #5f5f00", "grey 37 #5f5f5f", "medium purple 4 #5f5f87", "slate blue 3 #5f5faf",
    "slate blue 3 #5f5fd7", "royal blue 1 #5f5fff", "chartreuse 4 #5f8700",
    "dark sea green 4 #5f875f", "pale turquoise 4 #5f8787", "steel blue #5f87af",
    "steel blue 3 #5f87d7", "cornflower blue #5f87ff", "chartreuse 3 #5faf00",
    "dark sea green 4 #5faf5f", "cadet blue #5faf87", "cadet blue #5fafaf",
    "sky blue 3 #5fafd7", "steel blue 1 #5fafff", "chartreuse 3 #5fd700",
    "pale green 3 #5fd75f", "sea green 3 #5fd787", "aquamarine 3 #5fd7af",
    "medium turquoise #5fd7d7", "steel blue 1 #5fd7ff", "chartreuse 2 #5fff00",
    "sea green 2 #5fff5f", "sea green 1 #5fff87", "sea green 1 #5fffaf",
    "aquamarine 1 #5fffd7", "dark slate gray 2 #5fffff", "dark red #870000",
    "deep pink 4 #87005f", "dark magenta #870087", "dark magenta #8700af",
    "dark violet #8700d7", "purple #8700ff", "orange 4 #875f00", "light pink 4 #875f5f",
    "plum 4 #875f87", "medium purple 3 #875faf", "medium purple 3 #875fd7",
    "slate blue 1 #875fff", "yellow 4 #878700", "wheat 4 #87875f", "grey 53 #878787",
    "light slate grey #8787af", "medium purple #8787d7", "light slate blue #8787ff",
    "yellow 4 #87af00", "dark olive green 3 #87af5f", "dark sea green #87af87",
    "light sky blue 3 #87afaf", "light sky blue 3 #87afd7", "sky blue 2 #87afff",
    "chartreuse 2 #87d700", "dark olive green 3 #87d75f", "pale green 3 #87d787",
    "dark sea green 3 #87d7af", "dark slate gray 3 #87d7d7", "sky blue 1 #87d7ff",
    "chartreuse 1 #87ff00", "light green #87ff5f", "light green #87ff87",
    "pale green 1 #87ffaf", "aquamarine 1 #87ffd7", "dark slate gray 1 #87ffff",
    "red 3 #af0000", "deep pink 4 #af005f", "medium violet red #af0087", "magenta 3 #af00af",
    "dark violet #af00d7", "purple #af00ff", "dark orange 3 #af5f00", "indian red #af5f5f",
    "hot pink 3 #af5f87", "medium orchid 3 #af5faf", "medium orchid #af5fd7",
    "medium purple 2 #af5fff", "dark goldenrod #af8700", "light salmon 3 #af875f",
    "rosy brown #af8787", "grey 63 #af87af", "medium purple 2 #af87d7",
    "medium purple 1 #af87ff", "gold 3 #afaf00", "dark khaki #afaf5f",
    "navajo white 3 #afaf87", "grey 69 #afafaf", "light steel blue 3 #afafd7",
    "light steel blue #afafff", "yellow 3 #afd700", "dark olive green 3 #afd75f",
    "dark sea green 3 #afd787", "dark sea green 2 #afd7af", "light cyan 3 #afd7d7",
    "light sky blue 1 #afd7ff", "green yellow #afff00", "dark olive green 2 #afff5f",
    "pale green 1 #afff87", "dark sea green 2 #afffaf", "dark sea green 1 #afffd7",
    "pale turquoise 1 #afffff", "red 3 #d70000", "deep pink 3 #d7005f", "deep pink 3 #d70087",
    "magenta 3 #d700af", "magenta 3 #d700d7", "magenta 2 #d700ff", "dark orange 3 #d75f00",
    "indian red #d75f5f", "hot pink 3 #d75f87", "hot pink 2 #d75faf", "orchid #d75fd7",
    "medium orchid 1 #d75fff", "orange 3 #d78700", "light salmon 3 #d7875f",
    "light pink 3 #d78787", "pink 3 #d787af", "plum 3 #d787d7", "violet #d787ff",
    "gold 3 #d7af00", "light goldenrod 3 #d7af5f", "tan #d7af87", "misty rose 3 #d7afaf",
    "thistle 3 #d7afd7", "plum 2 #d7afff", "yellow 3 #d7d700", "khaki 3 #d7d75f",
    "light goldenrod 2 #d7d787", "light yellow 3 #d7d7af", "grey 84 #d7d7d7",
    "light steel blue 1 #d7d7ff", "yellow 2 #d7ff00", "dark olive green 1 #d7ff5f",
    "dark olive green 1 #d7ff87", "dark sea green 1 #d7ffaf", "honeydew 2 #d7ffd7",
    "light cyan 1 #d7ffff", "red 1 #ff0000", "deep pink 2 #ff005f", "deep pink 1 #ff0087",
    "deep pink 1 #ff00af", "magenta 2 #ff00d7", "magenta 1 #ff00ff", "orange red 1 #ff5f00",
    "indian red 1 #ff5f5f", "indian red 1 #ff5f87", "hot pink #ff5faf", "hot pink #ff5fd7",
    "medium orchid 1 #ff5fff", "dark orange #ff8700", "salmon 1 #ff875f",
    "light coral #ff8787", "pale violet red 1 #ff87af", "orchid 2 #ff87d7", "orchid 1 #ff87ff",
    "orange 1 #ffaf00", "sandy brown #ffaf5f", "light salmon 1 #ffaf87",
    "light pink 1 #ffafaf", "pink 1 #ffafd7", "plum 1 #ffafff", "gold 1 #ffd700",
    "light goldenrod 2 #ffd75f", "light goldenrod 2 #ffd787", "navajo white 1 #ffd7af",
    "misty rose 1 #ffd7d7", "thistle 1 #ffd7ff", "yellow 1 #ffff00",
    "light goldenrod 1 #ffff5f", "khaki 1 #ffff87", "wheat 1 #ffffaf", "cornsilk 1 #ffffd7",
    "grey 100 #ffffff", "grey 3 #080808", "grey 7 #121212", "grey 11 #1c1c1c",
    "grey 15 #262626", "grey 19 #303030", "grey 23 #3a3a3a", "grey 27 #444444",
    "grey 30 #4e4e4e", "grey 35 #585858", "grey 39 #626262", "grey 42 #6c6c6c",
    "grey 46 #767676", "grey 50 #808080", "grey 54 #8a8a8a", "grey 58 #949494",
    "grey 62 #9e9e9e", "grey 66 #a8a8a8", "grey 70 #b2b2b2", "grey 74 #bcbcbc",
    "grey 78 #c6c6c6", "grey 82 #d0d0d0", "grey 85 #dadada", "grey 89 #e4e4e4",
    "grey 93 #eeeeee",
]


def name_of(code):
    return _NAMES[code] if 0 <= code < len(_NAMES) else ""


# --- list / pipe mode (raw ANSI, no curses) ----------------------------------

_ANSI_RESET = "\033[0m"


def _ansi_fg(code):
    return f"\033[38;5;{code}m"


def _ansi_bg(code):
    return f"\033[48;5;{code}m"


def parse_codes(tokens):
    """Turn args/words into a flat list of color codes, supporting a-b ranges."""
    codes = []
    for tok in tokens:
        tok = tok.strip().rstrip(",")
        if not tok:
            continue
        if "-" in tok:
            a, b = tok.split("-", 1)
            try:
                a, b = int(a), int(b)
            except ValueError:
                print(f"skipping invalid range: {tok!r}", file=sys.stderr)
                continue
            step = 1 if b >= a else -1
            codes.extend(range(a, b + step, step))
        else:
            try:
                codes.append(int(tok))
            except ValueError:
                print(f"skipping invalid code: {tok!r}", file=sys.stderr)
    return [c for c in codes if 0 <= c <= 255]


def list_swatches(codes):
    """Print one row per code: code, fg sample text, color block, friendly name."""
    if not codes:
        return
    width = max(len(str(c)) for c in codes)
    for c in codes:
        label = f"{c:>{width}}"
        text = f"{_ansi_fg(c)}{SAMPLE}{_ANSI_RESET}"
        block = f"{_ansi_bg(c)}        {_ANSI_RESET}"
        print(f"{label}  {text}  {block}  {name_of(c)}")


# --- interactive curses app --------------------------------------------------


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


def slot_rc(idx):
    """(row, col) for a storage index. Column-major: col 0 = slots 0..9,
    col 1 = slots 10..19, so an old single-column save loads as the left one."""
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


def run_tui():
    # needed so curses can render the wide '█' block glyph used for swatches
    locale.setlocale(locale.LC_ALL, "")
    curses.wrapper(run)


def main():
    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help"):
        print(__doc__)
        return
    if args:                                # codes (and ranges) as arguments
        list_swatches(parse_codes(args))
        return
    if not sys.stdin.isatty():              # codes piped in on stdin
        list_swatches(parse_codes(sys.stdin.read().replace(",", " ").split()))
        return
    if sys.stdout.isatty():                 # bare, in a real terminal: full app
        run_tui()
        return
    print(__doc__)                          # nothing to show interactively


if __name__ == "__main__":
    main()
