#!/usr/bin/env python3
#
# koloroj - preview 256-color terminal color swatches.
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
"""koloroj - preview 256-color terminal color swatches.

Type one or more color codes (0-255) and see, one per row:
  - the code
  - foreground dummy text in that color
  - a solid background block in that color

Usage:
  ./koloroj.py 230 196 21          # codes as arguments
  ./koloroj.py                     # interactive prompt
  ./koloroj.py 16-21 226           # ranges work too (a-b inclusive)
"""
import sys

SAMPLE = "lorem ipsum 12345"


def fg(code):
    return f"\033[38;5;{code}m"


def bg(code):
    return f"\033[48;5;{code}m"


RESET = "\033[0m"


def parse(tokens):
    """Turn args/words into a flat list of ints, supporting a-b ranges."""
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


def show(codes):
    if not codes:
        return
    width = max(len(str(c)) for c in codes)
    for c in codes:
        label = f"{c:>{width}}"
        text = f"{fg(c)}{SAMPLE}{RESET}"
        block = f"{bg(c)}        {RESET}"
        print(f"{label}  {text}  {block}")


def main():
    args = sys.argv[1:]
    if args:
        show(parse(args))
        return
    print("Enter color codes (0-255), space/comma separated. Ranges like 16-21 work.")
    print("Blank line or Ctrl-D to quit.")
    try:
        while True:
            line = input("colors> ")
            if not line.strip():
                break
            show(parse(line.replace(",", " ").split()))
    except EOFError:
        print()


if __name__ == "__main__":
    main()
