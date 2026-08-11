#!/usr/bin/env python3
"""
Convert video to a segment intensity stream for the Multi Segment Monitor.

Each of the 18944 segments gets its own 4 bit brightness, averaged from the
source pixels its rectangle covers -- the same idea as the fragment shader in
the Sea of Segments installation, and the reason per-segment brightness is in
the design at all.  Sampling one pixel per digit instead would throw away most
of the resolution.

Output is raw frames of 9472 bytes, ready to copy to the demoboard.

    ./video2seg.py clip.mp4 clip.seg --fps 24

Averaging happens in linear light.  Video luma is gamma encoded, and the chip's
gamma LUT expects a linear intensity index (SPEC.md section 2.1), so both ends
have to be undone and redone or midtones come out wrong.
"""

import argparse
import subprocess
import sys

import numpy as np

import segments


def decode(path, fps, gamma):
    """Yield frames of linear luminance, shaped (GRID_H, GRID_W), range 0..1."""
    cmd = [
        "ffmpeg",
        "-v", "error",
        "-i", path,
        "-vf", (
            f"scale={segments.GRID_W}:{segments.GRID_H}"
            ":force_original_aspect_ratio=increase,"
            f"crop={segments.GRID_W}:{segments.GRID_H}"
        ),
        "-r", str(fps),
        "-f", "rawvideo",
        "-pix_fmt", "gray",
        "-",
    ]
    frame_bytes = segments.GRID_W * segments.GRID_H
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    try:
        while True:
            raw = proc.stdout.read(frame_bytes)
            if len(raw) < frame_bytes:
                break
            luma = np.frombuffer(raw, dtype=np.uint8).reshape(
                segments.GRID_H, segments.GRID_W
            )
            yield (luma / 255.0) ** gamma
    finally:
        proc.stdout.close()
        proc.wait()


def frame_to_segments(linear):
    """
    Linear luminance -> intensity array shaped (8, ROWS, COLS).

    Every cell has the same layout, so reshaping to (row, cy, col, cx) turns
    "average the pixels under this segment" into one slice and mean per segment
    rather than 18944 separate lookups.
    """
    cells = linear.reshape(
        segments.ROWS, segments.CELL_H, segments.COLS, segments.CELL_W
    )
    out = np.zeros((8, segments.ROWS, segments.COLS), dtype=np.uint8)
    for i, (_, x0, x1, y0, y1) in enumerate(segments.SEGMENTS):
        mean = cells[:, y0 : y1 + 1, :, x0 : x1 + 1].mean(axis=(1, 3))
        out[i] = np.clip(np.rint(mean * 15), 0, 15).astype(np.uint8)
    return out


def pack(intensity):
    """(8, ROWS, COLS) intensities -> FRAME_BYTES, low nibble first."""
    packed = np.zeros((segments.ROWS, segments.COLS, 4), dtype=np.uint8)
    for k in range(4):
        packed[:, :, k] = (intensity[2 * k + 1] << 4) | intensity[2 * k]
    return packed.tobytes()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="any video ffmpeg can read")
    ap.add_argument("output", help="raw segment stream, 9472 bytes per frame")
    ap.add_argument("--fps", type=float, default=24.0)
    ap.add_argument(
        "--gamma",
        type=float,
        default=2.2,
        help="decode exponent for the source; lower lifts the midtones",
    )
    ap.add_argument("--max-frames", type=int, default=0, help="0 for no limit")
    args = ap.parse_args()

    count = 0
    with open(args.output, "wb") as out:
        for linear in decode(args.input, args.fps, args.gamma):
            data = pack(frame_to_segments(linear))
            assert len(data) == segments.FRAME_BYTES
            out.write(data)
            count += 1
            if args.max_frames and count >= args.max_frames:
                break

    size = count * segments.FRAME_BYTES
    print(
        f"{count} frames, {size} bytes ({size / 1024:.0f} kB), "
        f"{count / args.fps:.1f} s at {args.fps:g} fps",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
