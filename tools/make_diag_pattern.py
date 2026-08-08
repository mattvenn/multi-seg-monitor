#!/usr/bin/env python3
"""
Generate a structured diagnostic .seg frame -- not video content, but a
pattern designed to make wiring/timing corruption obvious at a glance rather
than subtle.

Two things are encoded independently in the same frame:

- Row identity: only ONE segment is lit per row, chosen by (row % 4). Since
  the line buffer holds exactly 4 physical row-slots, this makes stale data
  from the wrong slot (e.g. leftover content from 4 rows back) show up as the
  WRONG segment lighting up, rather than a brightness difference that could
  get lost in the 4-level Tiny VGA truncation.
- Column identity: even columns are fully lit ("brightest" reading of
  whichever segment the row selected), odd columns are fully dark. Any
  corruption tied to byte position within a row -- e.g. only the later
  (right-hand) bytes of a row being wrong -- breaks the alternating stripe
  rhythm at a specific, countable column.
"""
import argparse
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import segments  # noqa: E402

ROW_SEGMENTS = ["a", "b", "g", "d"]  # top, upper-right, middle, bottom


def make_frame():
    data = bytearray()
    for row in range(segments.ROWS):
        seg_name = ROW_SEGMENTS[row % 4]
        seg_idx = [s[0] for s in segments.SEGMENTS].index(seg_name)
        for col in range(segments.COLS):
            intensities = [0] * 8
            if col % 2 == 0:
                intensities[seg_idx] = 15
            data += segments.pack_digit(intensities)
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
