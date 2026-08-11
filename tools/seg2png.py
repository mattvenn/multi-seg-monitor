#!/usr/bin/env python3
"""
Render a frame from a .seg stream as the display would show it.

A software model of the renderer, for judging framing and gamma without a
monitor or a simulation:

    ./seg2png.py clip.seg preview.png --frame 30
    ./seg2png.py clip.seg preview.png --levels 4    # as the Tiny VGA prototype

--levels 4 truncates to the 2 bits per channel the prototype pmod carries, which
is worth looking at before assuming a clip will survive the FPGA bring-up: 4
levels is a long way from 64.
"""

import argparse

import png
import segments

WIDTH, HEIGHT = 800, 600


def render(frame, levels):
    px = bytearray(WIDTH * HEIGHT * 3)
    for row in range(segments.ROWS):
        for col in range(segments.COLS):
            off = segments.digit_offset(col, row)
            intensity = segments.unpack_digit(frame[off : off + 4])
            for seg in range(8):
                value = segments.GAMMA[intensity[seg]]
                if levels == 4:
                    # The prototype keeps the top 2 bits, so rescale to match.
                    value = (value >> 4) * 85
                else:
                    value = value * 255 // 63
                if not value:
                    continue
                x0, x1, y0, y1 = segments.segment_pixels(col, row, seg)
                for y in range(y0, y1 + 1):
                    base = (y * WIDTH + x0) * 3
                    for i in range(0, (x1 - x0 + 1) * 3, 3):
                        px[base + i] = value
                        px[base + i + 1] = value
                        px[base + i + 2] = value
    return px


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="raw .seg stream")
    ap.add_argument("output", help="png to write")
    ap.add_argument("--frame", type=int, default=0)
    ap.add_argument(
        "--levels",
        type=int,
        default=64,
        choices=(4, 64),
        help="64 for the custom pmod, 4 for the Tiny VGA prototype",
    )
    args = ap.parse_args()

    with open(args.input, "rb") as f:
        f.seek(args.frame * segments.FRAME_BYTES)
        frame = f.read(segments.FRAME_BYTES)
    if len(frame) < segments.FRAME_BYTES:
        raise SystemExit(f"frame {args.frame} is past the end of {args.input}")

    png.write_png(args.output, WIDTH, HEIGHT, render(frame, args.levels))
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
