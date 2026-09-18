#!/usr/bin/env python3
"""
Render a frame from a .seg stream as the display would show it.

A software model of the renderer, for judging framing and brightness without a
monitor or a simulation:

    ./seg2png.py clip.seg preview.png --frame 30
    ./seg2png.py clip.seg preview.png --levels 4              # Tiny VGA Pmod
    ./seg2png.py clip.seg preview.png --palette 2 --levels 4  # Tiny VGA, palette 2

--levels 4 truncates each channel to the top 2 bits Tiny VGA mode carries,
worth looking at before assuming a clip reads well there: --palette 0 (the
default, grey) only has 4 distinguishable levels once truncated, and the
tinted palettes 1-3 (blue, green, purple) 7-9 (src/palette.v).
"""

import argparse

import png
import segments

WIDTH, HEIGHT = 800, 600


def render(frame, levels, palette=0):
    px = bytearray(WIDTH * HEIGHT * 3)
    for row in range(segments.ROWS):
        for col in range(segments.COLS):
            off = segments.digit_offset(col, row)
            intensity = segments.unpack_digit(frame[off : off + 4])
            for seg in range(8):
                code = intensity[seg]
                r, g, b = segments.PALETTES[palette][code]  # no gamma stage
                if levels == 4:
                    # Tiny VGA keeps each channel's top 2 bits -- truncate the
                    # looked-up colour, not the pre-lookup intensity index;
                    # these only coincide for palette 0's identity mapping.
                    r, g, b = (r >> 2) * 85, (g >> 2) * 85, (b >> 2) * 85
                else:
                    r, g, b = r * 17, g * 17, b * 17  # 0-15 -> 0-255, exact
                if not (r or g or b):
                    continue
                x0, x1, y0, y1 = segments.segment_pixels(col, row, seg)
                for y in range(y0, y1 + 1):
                    base = (y * WIDTH + x0) * 3
                    for i in range(0, (x1 - x0 + 1) * 3, 3):
                        px[base + i] = r
                        px[base + i + 1] = g
                        px[base + i + 2] = b
    return px


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="raw .seg stream")
    ap.add_argument("output", help="png to write")
    ap.add_argument("--frame", type=int, default=0)
    ap.add_argument(
        "--levels",
        type=int,
        default=16,
        choices=(4, 16),
        help="16 for the direct 4-bit Digilent output, 4 for Tiny VGA",
    )
    ap.add_argument(
        "--palette",
        type=int,
        default=0,
        choices=(0, 1, 2, 3),
        help="which of src/palette.v's 4 palettes (0 = today's grey)",
    )
    args = ap.parse_args()

    with open(args.input, "rb") as f:
        f.seek(args.frame * segments.FRAME_BYTES)
        frame = f.read(segments.FRAME_BYTES)
    if len(frame) < segments.FRAME_BYTES:
        raise SystemExit(f"frame {args.frame} is past the end of {args.input}")

    png.write_png(args.output, WIDTH, HEIGHT, render(frame, args.levels, args.palette))
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
