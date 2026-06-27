# koloroj

A terminal tool for picking and comparing 256-color terminal color swatches.

Type a color code (0–255) into a slot and instantly see what it actually looks
like: the color as **foreground text** (`lorem ipsum 12345`), as a **solid
block**, and a short **friendly name** (e.g. `230 → cream`, `196 → red`). Build
up two schemes side by side — **A** and **B** — and compare them row for row, so
you can audit a palette before committing it to a config.

A built-in **256-color reference grid** shows every code next to its swatch, and
a column of codes can be copied straight to the clipboard for pasting into your
`.vimrc`, shell theme, or wherever the colors are headed.

## Features

- Two schemes of 10 slots each, edited live with instant preview
- Foreground text, solid block, and friendly name per color
- 256-color reference grid with code + swatch (scales to terminal width)
- Swap schemes (`s`), copy a column's codes to the clipboard (`c`)
- State persists between runs, with an automatic backup
- Adapts to the window: shows a single column on narrow terminals

## Run

Run it bare in a terminal for the interactive app:

```sh
python3 koloroj.py
```

Or give it codes (or pipe them in) to print swatches and exit — handy in
scripts and pipes:

```sh
python3 koloroj.py 230 196 21      # codes as arguments
python3 koloroj.py 16-21 226       # inclusive ranges
echo 16-21 | python3 koloroj.py    # codes on stdin
```

Requires Python 3 with the standard-library `curses` module and a 256-color
terminal (`TERM=xterm-256color`). Copying a column to the clipboard uses
`wl-copy`, `xclip`, or `xsel` if one is installed.

## Keys

- `0–9` edit · `Backspace` / `Ctrl-U` clear slot · `Ctrl-L` clear all
- `Tab` / `Enter` move · `h` `j` `k` `l` or arrows navigate
- `g` toggle grid · `s` swap A/B · `c` copy column · `q` quit

## License

[GPL-3.0](LICENSE).
