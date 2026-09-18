#!/usr/bin/env python3
"""
Interactive palette builder for the Multi Segment Monitor.

    ./palette_builder.py

Edit the 16 entries of a palette (4 bit stored intensity -> 4 bit R/G/B, as in
src/palette.v) and see the result on a frame of the generator or of a droplet
clip, as the 12-bit PmodVGA (4 bits/channel) or the 6-bit Tiny VGA Pmod
(2 bits/channel) would show it.

Deliberately standalone and read-only towards the rest of the repo: it imports
tools/segments.py for the geometry and the four existing palettes, reads .seg
clips and test/gold/generator.png, and writes only into its own palettes/ and
exports/ directories.  It never edits src/palette.v or segments.PALETTES --
Export produces text for a person to paste.

The render is a software model, the same maths as seg2png.py (which
test_palette_builder.py cross-checks it against), not a simulation of the RTL.
The 6-bit path truncates the *looked-up colour* to its top 2 bits, because that
is where src/tt_um_multi_seg_monitor.v does it -- so the same palette edits
show up differently on the two Pmods, which is the point of the toggle.
"""

import json
import os
import re
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))  # tools/, for segments and png

import png  # noqa: E402
import segments  # noqa: E402

WIDTH, HEIGHT = 800, 600
GENERATOR_PNG = REPO / "test" / "gold" / "generator.png"
PALETTE_DIR = HERE / "palettes"
EXPORT_DIR = HERE / "exports"


# --------------------------------------------------------------------------
# Frames -> index images.  An index image holds the stored intensity (0-15) of
# every screen pixel, with 0 for background/margin, so a palette edit is just
# a 16-entry lookup over it and stays fast enough to drag a slider.
# --------------------------------------------------------------------------

_SEG_MASK = None  # flat pixel indices covered by any segment
_SEG_NIBBLE = None  # which nibble of the frame each of those pixels reads


def _segment_tables():
    global _SEG_MASK, _SEG_NIBBLE
    if _SEG_MASK is None:
        seg_map = np.full((HEIGHT, WIDTH), -1, dtype=np.int32)
        for row in range(segments.ROWS):
            for col in range(segments.COLS):
                for seg in range(8):
                    x0, x1, y0, y1 = segments.segment_pixels(col, row, seg)
                    # nibble n of a frame is digit (n // 8), segment (n % 8):
                    # low nibble first within a byte, 4 bytes per digit.
                    seg_map[y0 : y1 + 1, x0 : x1 + 1] = (row * segments.COLS + col) * 8 + seg
        flat = seg_map.ravel()
        _SEG_MASK = np.flatnonzero(flat >= 0)
        _SEG_NIBBLE = flat[_SEG_MASK]
    return _SEG_MASK, _SEG_NIBBLE


def index_image_from_frame(frame):
    """One .seg frame (FRAME_BYTES) -> (600, 800) uint8 of stored intensities."""
    mask, nibble_of = _segment_tables()
    data = np.frombuffer(frame, dtype=np.uint8)
    nibbles = np.empty(len(data) * 2, dtype=np.uint8)
    nibbles[0::2] = data & 0xF
    nibbles[1::2] = data >> 4
    idx = np.zeros(HEIGHT * WIDTH, dtype=np.uint8)
    idx[mask] = nibbles[nibble_of]
    return idx.reshape(HEIGHT, WIDTH)


def index_image_from_png(path):
    """A palette-0 capture (grey = intensity * 17) -> stored intensities.

    Only valid for an identity-palette capture; anything else (a coloured
    palette, a Tiny VGA capture) would silently read as the wrong intensities,
    so refuse it rather than show a plausible-looking wrong frame.
    """
    w, h, px = png.read_png(str(path))
    if (w, h) != (WIDTH, HEIGHT):
        raise ValueError(f"{path} is {w}x{h}, expected {WIDTH}x{HEIGHT}")
    rgb = np.array(px, dtype=np.uint8).reshape(HEIGHT, WIDTH, 3)
    if not ((rgb[..., 0] == rgb[..., 1]).all() and (rgb[..., 1] == rgb[..., 2]).all()):
        raise ValueError(f"{path} is not grey, so it is not a palette-0 capture")
    if (rgb[..., 0] % 17).any():
        raise ValueError(f"{path} is not a 16-level ramp (levels of 17)")
    return (rgb[..., 0] // 17).astype(np.uint8)


def list_clips(directory):
    """(path, frame_count) for every .seg in `directory` that is a whole number
    of current-geometry frames.  Clips from the 640x480 era are not, and would
    render as noise, so they are left out rather than offered."""
    out = []
    for p in sorted(Path(directory).glob("*.seg")):
        size = p.stat().st_size
        if size and size % segments.FRAME_BYTES == 0:
            out.append((str(p), size // segments.FRAME_BYTES))
    return out


def read_frame(path, n):
    with open(path, "rb") as f:
        f.seek(n * segments.FRAME_BYTES)
        frame = f.read(segments.FRAME_BYTES)
    if len(frame) < segments.FRAME_BYTES:
        raise IndexError(f"frame {n} is past the end of {path}")
    return frame


# --------------------------------------------------------------------------
# Palettes
# --------------------------------------------------------------------------


def palette_to_lut(palette, bits):
    """16 x (r, g, b) 4-bit entries -> 16 x 3 uint8 as the given Pmod shows them."""
    p = np.array(palette, dtype=np.int32)
    if bits == 12:
        return (p * 17).astype(np.uint8)
    if bits == 6:
        return ((p >> 2) * 85).astype(np.uint8)
    raise ValueError(f"bits must be 12 or 6, not {bits}")


def render(idx_image, palette, bits):
    return palette_to_lut(palette, bits)[idx_image]


def _groups(colours):
    seen = {}
    for i, c in enumerate(colours):
        seen.setdefault(tuple(c), []).append(i)
    return [g for g in seen.values() if len(g) > 1]


def check_palette(palette):
    """Warnings for the rules src/palette.v documents; [] means clean.

    Palette 0 (the grey ramp) legitimately collapses on the 6-bit Pmod -- that
    is the documented exemption -- so a 6-bit warning is a heads-up, not
    necessarily a defect.
    """
    msgs = []
    if tuple(palette[0]) != (0, 0, 0):
        msgs.append(
            "entry 0 must be black: it is also every background and margin pixel, "
            "so a colour there tints the whole screen"
        )
    full = _groups(palette)
    if full:
        msgs.append(
            "12-bit: identical colours, so these levels cannot be told apart: "
            + "; ".join(", ".join(map(str, g)) for g in full)
        )
    trunc = _groups([(r >> 2, g >> 2, b >> 2) for r, g, b in palette])
    if trunc:
        msgs.append(
            "6-bit (Tiny VGA) collapses these levels: "
            + "; ".join(", ".join(map(str, g)) for g in trunc)
        )
    return msgs


_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def gradient_palette(stops):
    """Colour stops ('#rrggbb', at least two) spread evenly over entries 1-15,
    linearly interpolated.  Entry 0 stays black.  8-bit stops are quantised to
    the palette's 4 bits."""
    if len(stops) < 2:
        raise ValueError("a gradient needs at least two colour stops")
    cols = []
    for s in stops:
        if not _HEX.match(s):
            raise ValueError(f"{s!r} is not a #rrggbb colour")
        cols.append(tuple(int(s[i : i + 2], 16) / 17 for i in (1, 3, 5)))
    pal = [(0, 0, 0)]
    for i in range(1, 16):
        pos = (i - 1) / 14 * (len(cols) - 1)
        lo = min(int(pos), len(cols) - 2)
        t = pos - lo
        pal.append(
            tuple(
                min(15, int(cols[lo][ch] * (1 - t) + cols[lo + 1][ch] * t + 0.5))
                for ch in range(3)
            )
        )
    return pal


def to_python(palette, name):
    lines = [f"{name} = ["]
    lines += [f"    ({r}, {g}, {b}),  # {i}" for i, (r, g, b) in enumerate(palette)]
    lines.append("]")
    return "\n".join(lines) + "\n"


def to_verilog(palette, n):
    """A `case (idx)` arm in src/palette.v's layout.  The header is at column 0
    and entries are indented 4 relative to it; re-indent when pasting."""
    lines = [f"// palette {n}", f"2'd{n}: case (idx)"]
    for i, (r, g, b) in enumerate(palette):
        label = f"4'd{i}".ljust(5)
        lines.append(f"    {label}: {{r, g, b}} = {{4'd{r}, 4'd{g}, 4'd{b}}};")
    lines.append("endcase")
    return "\n".join(lines) + "\n"


def to_json(palette, name):
    return json.dumps({"name": name, "entries": [list(c) for c in palette]}, indent=1)


def from_json(text):
    entries = json.loads(text).get("entries")
    if not isinstance(entries, list) or len(entries) != 16:
        raise ValueError("expected 16 entries")
    out = []
    for e in entries:
        if (
            not isinstance(e, list)
            or len(e) != 3
            or not all(isinstance(v, int) and 0 <= v <= 15 for v in e)
        ):
            raise ValueError(f"bad entry {e!r}: need [r, g, b] with each 0-15")
        out.append(tuple(e))
    return out


def safe_name(name):
    return re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_") or "custom"


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------


class App:
    def __init__(self, root):
        self.root = root
        root.title("Palette builder")
        self.palette = [tuple(c) for c in segments.PALETTES[0]]
        self.sel = 1
        self._syncing = False
        self._photo = None
        self._clip_idx = (None, None, None)  # (path, frame, index image) cache

        self.source = tk.StringVar(value="generator")
        self.bits = tk.IntVar(value=12)
        self.name = tk.StringVar(value="custom")
        self.stops = tk.StringVar(value="#0000ff #00ffff #ffffff")

        try:
            self.gen_idx = index_image_from_png(GENERATOR_PNG)
            self.gen_error = None
        except (OSError, ValueError) as e:
            self.gen_idx, self.gen_error = None, str(e)
        self.clips = list_clips(REPO)

        self._build()
        if self.gen_idx is None and self.clips:
            self.source.set("droplet")
        self.select(1)

    # -- layout --

    def _build(self):
        left = ttk.Frame(self.root, padding=8)
        left.grid(row=0, column=0, sticky="ns")
        right = ttk.Frame(self.root, padding=8)
        right.grid(row=0, column=1, sticky="nsew")
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)

        ttk.Label(left, text="Entries (click to edit)").grid(row=0, column=0, columnspan=3, sticky="w")
        self.rows, self.swatches, self.rgb_labels = [], [], []
        for i in range(16):
            row = tk.Frame(left, highlightthickness=2, highlightbackground=self.root.cget("bg"))
            row.grid(row=i + 1, column=0, sticky="ew", pady=1)
            num = tk.Label(row, text=f"{i:2d}", width=3)
            sw = tk.Label(row, width=10, height=1, relief="sunken")
            txt = tk.Label(row, width=9, anchor="w", font=("TkFixedFont", 9))
            for w in (num, sw, txt):
                w.pack(side="left")
            for w in (row, num, sw, txt):
                w.bind("<Button-1>", lambda _e, i=i: self.select(i))
            self.rows.append(row)
            self.swatches.append(sw)
            self.rgb_labels.append(txt)

        self.sliders = []
        box = ttk.LabelFrame(left, text="Selected entry", padding=4)
        box.grid(row=17, column=0, sticky="ew", pady=(8, 0))
        for ch, label in enumerate("RGB"):
            ttk.Label(box, text=label).grid(row=ch, column=0)
            s = tk.Scale(box, from_=0, to=15, orient="horizontal", length=180, command=self._on_slider)
            s.grid(row=ch, column=1)
            self.sliders.append(s)

        grad = ttk.LabelFrame(left, text="Gradient over entries 1-15", padding=4)
        grad.grid(row=18, column=0, sticky="ew", pady=(8, 0))
        ttk.Entry(grad, textvariable=self.stops, width=30).grid(row=0, column=0, columnspan=2)
        ttk.Button(grad, text="Apply", command=self._apply_gradient).grid(row=1, column=0, sticky="w")
        ttk.Label(grad, text="#rrggbb stops, spaced evenly").grid(row=1, column=1, sticky="e")

        pre = ttk.LabelFrame(left, text="Start from", padding=4)
        pre.grid(row=19, column=0, sticky="ew", pady=(8, 0))
        for n in range(4):
            ttk.Button(pre, text=f"Palette {n}", width=9, command=lambda n=n: self._load_preset(n)).grid(
                row=0, column=n
            )

        io = ttk.LabelFrame(left, text="Save / export", padding=4)
        io.grid(row=20, column=0, sticky="ew", pady=(8, 0))
        ttk.Entry(io, textvariable=self.name, width=14).grid(row=0, column=0)
        ttk.Button(io, text="Save", width=6, command=self._save).grid(row=0, column=1)
        ttk.Button(io, text="Open...", width=7, command=self._open).grid(row=0, column=2)
        ttk.Button(io, text="Export", width=7, command=self._export).grid(row=0, column=3)

        top = ttk.Frame(right)
        top.grid(row=0, column=0, sticky="w")
        ttk.Label(top, text="Source:").pack(side="left")
        for val, label in (("generator", "Generator"), ("droplet", "Droplet")):
            ttk.Radiobutton(top, text=label, value=val, variable=self.source, command=self.refresh).pack(side="left")
        ttk.Label(top, text="   Output:").pack(side="left")
        for val, label in ((12, "12-bit PmodVGA"), (6, "6-bit Tiny VGA")):
            ttk.Radiobutton(top, text=label, value=val, variable=self.bits, command=self.refresh).pack(side="left")
        ttk.Label(top, text="   (keys: s source, b bits)").pack(side="left")

        clip = ttk.Frame(right)
        clip.grid(row=1, column=0, sticky="ew", pady=4)
        self.clip_box = ttk.Combobox(
            clip, state="readonly", width=28, values=[os.path.basename(p) for p, _ in self.clips]
        )
        self.clip_box.pack(side="left")
        if self.clips:
            best = next((i for i, (p, _) in enumerate(self.clips) if "waterdrop" in p), 0)
            self.clip_box.current(best)
        self.clip_box.bind("<<ComboboxSelected>>", lambda _e: self._clip_changed())
        self.frame_scale = tk.Scale(clip, from_=0, to=0, orient="horizontal", length=420, command=lambda _v: self.refresh())
        self.frame_scale.pack(side="left", padx=8)
        self._clip_changed(refresh=False)

        self.preview = tk.Label(right, bd=1, relief="sunken")
        self.preview.grid(row=2, column=0)
        self.status = tk.Label(right, justify="left", anchor="w", wraplength=WIDTH)
        self.status.grid(row=3, column=0, sticky="ew", pady=4)

        self.root.bind("<Key>", self._on_key)

    # -- state changes --

    def _on_key(self, e):
        if isinstance(e.widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
            return
        if e.char == "s":
            self.source.set("droplet" if self.source.get() == "generator" else "generator")
            self.refresh()
        elif e.char == "b":
            self.bits.set(6 if self.bits.get() == 12 else 12)
            self.refresh()

    def _clip_changed(self, refresh=True):
        n = self.clips[self.clip_box.current()][1] if self.clips else 1
        self.frame_scale.config(to=max(n - 1, 0))
        self.frame_scale.set(min(self.frame_scale.get(), n - 1))
        if refresh:
            self.refresh()

    def select(self, i):
        self.sel = i
        self._syncing = True
        for s, v in zip(self.sliders, self.palette[i]):
            s.set(v)
            s.config(state="disabled" if i == 0 else "normal")  # entry 0 is locked to black
        self._syncing = False
        self.refresh()

    def _on_slider(self, _v):
        if self._syncing or self.sel == 0:
            return
        self.palette[self.sel] = tuple(s.get() for s in self.sliders)
        self.refresh()

    def _set_palette(self, pal):
        self.palette = [tuple(c) for c in pal]
        self.select(self.sel)

    def _load_preset(self, n):
        self._set_palette(segments.PALETTES[n])

    def _apply_gradient(self):
        try:
            self._set_palette(gradient_palette(re.split(r"[\s,]+", self.stops.get().strip())))
        except ValueError as e:
            self._say(f"gradient: {e}", bad=True)

    def _save(self):
        name = safe_name(self.name.get())
        PALETTE_DIR.mkdir(exist_ok=True)
        path = PALETTE_DIR / f"{name}.json"
        path.write_text(to_json(self.palette, name))
        self._say(f"saved {path.relative_to(REPO)}")

    def _open(self):
        PALETTE_DIR.mkdir(exist_ok=True)
        path = filedialog.askopenfilename(initialdir=PALETTE_DIR, filetypes=[("palette", "*.json")])
        if not path:
            return
        try:
            self._set_palette(from_json(Path(path).read_text()))
            self.name.set(Path(path).stem)
        except (OSError, ValueError) as e:
            self._say(f"open: {e}", bad=True)

    def _export(self):
        name = safe_name(self.name.get())
        EXPORT_DIR.mkdir(exist_ok=True)
        path = EXPORT_DIR / f"{name}.txt"
        head = [f"# palette builder export: {name}"]
        head += [f"# WARNING: {m}" for m in check_palette(self.palette)]
        body = (
            "\n".join(head)
            + "\n\n# tools/segments.py PALETTES entry:\n"
            + to_python(self.palette, name.upper())
            + "\n// src/palette.v case arm (renumber the 2'dN and comment to the slot you replace):\n"
            + to_verilog(self.palette, 0)
        )
        path.write_text(body)
        self._say(f"exported {path.relative_to(REPO)} (nothing in src/ or segments.py was touched)")

    # -- drawing --

    def _current_index_image(self):
        if self.source.get() == "generator":
            if self.gen_idx is None:
                raise ValueError(f"generator frame unavailable: {self.gen_error}")
            return self.gen_idx
        if not self.clips:
            raise ValueError("no current-geometry .seg clips found in the repo root")
        path, _ = self.clips[self.clip_box.current()]
        n = int(self.frame_scale.get())
        if self._clip_idx[:2] != (path, n):
            self._clip_idx = (path, n, index_image_from_frame(read_frame(path, n)))
        return self._clip_idx[2]

    def _say(self, msg, bad=False):
        self.status.config(text=msg, fg="#b00020" if bad else "#1b5e20")

    def refresh(self):
        lut = palette_to_lut(self.palette, self.bits.get())
        for i in range(16):
            r, g, b = (int(v) for v in lut[i])
            self.swatches[i].config(bg=f"#{r:02x}{g:02x}{b:02x}")
            self.rgb_labels[i].config(text="{:2d} {:2d} {:2d}".format(*self.palette[i]))
            self.rows[i].config(highlightbackground="#1565c0" if i == self.sel else self.root.cget("bg"))
        try:
            img = lut[self._current_index_image()]
        except (ValueError, IndexError, OSError) as e:
            self._say(str(e), bad=True)
            return
        self._photo = tk.PhotoImage(data=b"P6\n%d %d\n255\n" % (WIDTH, HEIGHT) + img.tobytes())
        self.preview.config(image=self._photo)
        warns = check_palette(self.palette)
        self._say("\n".join(warns) if warns else "All 16 entries distinct at 12-bit and 6-bit; entry 0 is black.", bool(warns))


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
