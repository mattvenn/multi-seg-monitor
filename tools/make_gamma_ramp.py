#!/usr/bin/env python3
"""
Generate a full 0-15 brightness ramp .seg frame.

Neither existing generator is a calibration pattern: the internal hardware
generator only ever lights odd intensities (multi_seg_monitor.v's
`gen_int = gen_col[3:0] | 4'h1`), and make_diag_pattern.py only uses 0 or 15.
This lights every digit's segments at intensity (col % 16), so all 16 stored
indices -- and whichever of them the gamma table keeps distinct -- appear
side by side, repeating every 16 of the 64 columns.
"""
import argparse
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import segments  # noqa: E402


def make_frame():
    data = bytearray()
    for row in range(segments.ROWS):
        for col in range(segments.COLS):
            data += segments.pack_digit([col % 16] * 8)
    return bytes(data)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("output")
    ap.add_argument("--frames", type=int, default=1, help="repeat the frame N times")
    args = ap.parse_args()

    frame = make_frame()
    assert len(frame) == segments.FRAME_BYTES
    with open(args.output, "wb") as f:
        for _ in range(args.frames):
            f.write(frame)
    print(f"wrote {args.frames} frame(s), {len(frame) * args.frames} bytes", file=sys.stderr)


if __name__ == "__main__":
    main()
