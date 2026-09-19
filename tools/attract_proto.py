#!/usr/bin/env python3
"""
Prototype attract-mode effects for the internal generator, before any RTL.

Each effect is f(col, row, seg, frame) -> 4-bit level, in integer maths only and
with the bit widths the hardware would use, so a winner can be carried into
Verilog without changing how it looks. Nothing here is bit-exact yet -- that
comes once an effect is chosen and this becomes its reference model.

    ./attract_proto.py ripples                    # GIF + still in ./attract_out
    ./attract_proto.py plasma --palette 2 --frames 240
    ./attract_proto.py ca --stride 4 --frames 300 # long timescales, sped up
    ./attract_proto.py zoneplate --per-digit      # one level per digit, not per segment
    ./attract_proto.py all

Coordinates are screen pixels relative to the grid's top-left: a segment is
sampled at its centre, so there are 6 distinct x positions per digit (f/e, a/g/d,
b/c, DP) -- 18944 "pixels" rather than 2368. In RTL those are col*12 plus a
per-segment constant, i.e. a shift-and-add, not a multiply. --per-digit samples
every segment at the digit centre instead, for comparison.

GIFs play at 50 fps (the fastest frame time browsers honour, 20 ms), so they run
at 0.83x real speed with --stride 1.
"""

import argparse
import os
import subprocess

from PIL import Image

import segments

WIDTH, HEIGHT = 800, 600
COLS, ROWS = segments.COLS, segments.ROWS
GRID_W, GRID_H = segments.GRID_W, segments.GRID_H  # 768 x 592

# Segment centres within a cell (integer, rounded down, like the RTL would).
SEG_CX = [(s[1] + s[2]) // 2 for s in segments.SEGMENTS]
SEG_CY = [(s[3] + s[4]) // 2 for s in segments.SEGMENTS]


def tri(v, bits):
    """Triangle wave: the low `bits` of v folded to `bits-1` bits (0..2^(bits-1)-1)."""
    half = 1 << (bits - 1)
    v &= (1 << bits) - 1
    return v if v < half else (1 << bits) - 1 - v


def lfsr16(v):
    """A 16-bit hash, cheap in gates: two rounds of xorshift."""
    v &= 0xFFFF
    for _ in range(2):
        v ^= (v << 7) & 0xFFFF
        v ^= v >> 9
        v ^= (v << 8) & 0xFFFF
    return v


def dist_approx(dx, dy):
    """Two-piece alpha-max-beta-min: max(hi, 7/8 hi + 1/2 lo). Within ~3% of
    Euclidean -- round rings rather than the octagons max + lo/2 draws -- for
    two shifts, two adds and a compare."""
    dx, dy = abs(dx), abs(dy)
    hi, lo = max(dx, dy), min(dx, dy)
    return max(hi, hi - (hi >> 3) + (lo >> 1))


# ---------------------------------------------------------------------------
# 1. Raindrop ripples
#
# K drop slots, each on a P-frame cycle, staggered so a new drop lands every
# P/K frames. A drop's position is a hash of (slot, epoch), so there is no
# per-drop state at all -- just the frame counter. Each drop is a wave train:
# rings of wavelength WL behind a front moving at SPEED px/frame, fading with
# age and with distance behind the front.
#
# Cost: frame counter (exists), one hash, one |dx|,|dy| + max/min + add, one
# 4-bit saturating accumulator, iterated K times per sample -- serial over the
# ~40 spare cycles per byte. ~20 flops for the loop and accumulator, 0 scratch.
# ---------------------------------------------------------------------------
RIP_K, RIP_P, RIP_SPEED, RIP_WL = 6, 192, 3, 16


def ripples_frame(frame):
    drops = []
    for i in range(RIP_K):
        t = frame + i * (RIP_P // RIP_K)
        age = t % RIP_P
        h = lfsr16((t // RIP_P) * RIP_K + i + 1)
        x0 = (h & 0xFF) * GRID_W >> 8
        y0 = ((h >> 8) & 0xFF) * GRID_H >> 8
        drops.append((x0, y0, age))

    def f(x, y):
        acc = 0
        for x0, y0, age in drops:
            front = age * RIP_SPEED
            behind = front - dist_approx(x - x0, y - y0)
            # Only the region inside the front is disturbed, and only the
            # last few wavelengths of it -- the pond is calm behind.
            if 0 <= behind < 4 * RIP_WL:
                amp = tri(behind, 5)  # 0..15, one ring per 32 px
                # Fade over the last half of a drop's life, and linearly behind
                # the front -- both as a shift-and-subtract, no multiplier:
                # the lead ring is full brightness, the trailing ones decay.
                amp -= (behind >> 3)  # up to 7 dimmer 64 px back
                if age > RIP_P // 2:
                    amp -= (age - RIP_P // 2) >> 3  # up to 12 dimmer at death
                acc += max(amp, 0)
        return min(acc, 15)

    return f


# ---------------------------------------------------------------------------
# 2. Interference / zone plate
#
# Two sources wander on Lissajous paths. Each contributes a phase of
# (dx^2 + dy^2) >> k, which is the Fresnel zone plate: rings that tighten
# outward. Note the sum of the two is algebraically a single zone plate centred
# on the sources' midpoint (|p-a|^2 + |p-b|^2 = 2|p-m|^2 + const), so this is
# not true interference -- that is `interference_slow` below, which sums
# distances instead of squared distances.
#
# Cost: squares built incrementally along the scan ((x+1)^2 = x^2 + 2x + 1), so
# per source two ~20-bit accumulators plus an adder -- or reuse one adder
# serially. Source positions are triangle waves of the frame counter. ~60 flops
# if the squares are held per source; halve that by recomputing serially.
# ---------------------------------------------------------------------------
ZP_SHIFT = 9


def zoneplate_frame(frame):
    ax = tri(frame * 3, 11) * GRID_W >> 10
    ay = tri(frame * 2 + 300, 11) * GRID_H >> 10
    bx = tri(frame * 2 + 700, 11) * GRID_W >> 10
    by = tri(frame * 5 + 100, 11) * GRID_H >> 10
    t = frame * 3

    def f(x, y):
        pa = ((x - ax) ** 2 + (y - ay) ** 2) >> ZP_SHIFT
        pb = ((x - bx) ** 2 + (y - by) ** 2) >> ZP_SHIFT
        return tri(pa + pb - t, 5)

    return f


# Slow variant: rings four times as far apart in r^2 (fewer of them, so the
# corners alias less against the segment pitch),
# sources that take ~1 minute to cross the screen, and the ring phase carried
# with 2 fractional bits so it creeps a quarter of a brightness step per frame
# instead of jumping. Same hardware as above plus 2 bits of phase.
ZPS_SHIFT = 11


def zoneplate_slow_frame(frame):
    ax = tri(frame * 3 >> 1, 12) * GRID_W >> 11
    ay = tri(frame + 600, 12) * GRID_H >> 11
    bx = tri(frame + 1400, 12) * GRID_W >> 11
    by = tri(frame * 5 >> 2, 12) * GRID_H >> 11

    def f(x, y):
        pa = ((x - ax) ** 2 + (y - ay) ** 2) >> (ZPS_SHIFT - 2)
        pb = ((x - bx) ** 2 + (y - by) ** 2) >> (ZPS_SHIFT - 2)
        return tri(pa + pb - frame, 7) >> 2

    return f


# True two-source interference: sum of *distances*, not squared distances, so
# rings stay evenly spaced (no aliasing anywhere on screen) and the pair forms
# the ellipse-and-hyperbola pattern of two stones dropped in a pond. Distance
# via dist_approx, so no multiplier and no squarer at all -- cheaper than the
# zone plate. Slow sources and 2 fractional phase bits, as above.
def interference_slow_frame(frame):
    ax = tri(frame * 3 >> 1, 12) * GRID_W >> 11
    ay = tri(frame + 600, 12) * GRID_H >> 11
    bx = tri(frame + 1400, 12) * GRID_W >> 11
    by = tri(frame * 5 >> 2, 12) * GRID_H >> 11

    def f(x, y):
        d = dist_approx(x - ax, y - ay) + dist_approx(x - bx, y - by)
        return tri((d << 1) - frame, 7) >> 2  # a ring per 64 px of summed distance

    return f


# ---------------------------------------------------------------------------
# 4. Plasma
#
# The demoscene classic: a sum of sines of x, y, x+y and a moving radial term,
# folded to 4 bits. One 64-entry sine (a 16-entry quarter-wave table, 4 bits
# out), looked up four times per sample.
#
# Cost: 16x4 ROM, a 6-bit phase adder, a 6-bit accumulator, serial x4. The
# radial term reuses dist_approx. ~15 flops, 0 scratch.
# ---------------------------------------------------------------------------
_QSIN = [round(7.5 + 7.5 * __import__("math").sin(i * 3.14159265 / 32)) for i in range(16)]


def sin64(p):
    """4-bit sine of a 6-bit phase, from a 16-entry quarter-wave table."""
    p &= 63
    q, i = p >> 4, p & 15
    if q == 0:
        return _QSIN[i]
    if q == 1:
        return _QSIN[15 - i] if i else 15
    if q == 2:
        return 15 - _QSIN[i]
    return 15 - (_QSIN[15 - i] if i else 15)


def plasma_frame(frame):
    t = frame
    cx = GRID_W // 2 + (sin64(t >> 1) - 8) * 24
    cy = GRID_H // 2 + (sin64((t >> 1) + 16) - 8) * 18

    def f(x, y):
        s = sin64((x >> 3) + t)
        s += sin64((y >> 3) - (t >> 1) * 3)
        s += sin64(((x + y) >> 4) + 2 * t)
        s += sin64((dist_approx(x - cx, y - cy) >> 2) - t)
        # The sum of four sines clusters around 30, so one fold of 0..60
        # would sit near full brightness everywhere; folding twice spends
        # the whole 0..15 range on the part of the sum that actually moves.
        return tri(s, 5)

    return f


# Slow variant: half the spatial frequency (blobs twice the size) and time
# carried with 3 fractional bits, so each term's phase steps once every few
# frames rather than every frame. The sine table is unchanged; the 4 terms
# drift at different, non-harmonic rates so the pattern never visibly repeats.
def plasma_slow_frame(frame):
    t = frame  # phase units of 1/8 of a table step
    cx = GRID_W // 2 + (sin64(t >> 5) - 8) * 24
    cy = GRID_H // 2 + (sin64((t >> 5) + 16) - 8) * 18

    def f(x, y):
        s = sin64((x >> 4) + (t >> 3))
        s += sin64((y >> 4) - (t * 3 >> 4))
        s += sin64(((x + y) >> 5) + (t * 5 >> 4))
        s += sin64((dist_approx(x - cx, y - cy) >> 3) - (t >> 2))
        return tri(s, 5)

    return f


# ---------------------------------------------------------------------------
# 6. 1D cellular automaton, scrolling
#
# Digit row r shows generation G0 + r of an elementary CA, 64 cells wide with
# zero edges. G0 advances every CA_K frames, so the picture scrolls up. The
# rule depends on the absolute generation, so a rule change is a boundary that
# scrolls up the screen rather than a cut. A live cell shows its 3-cell
# neighbourhood (1..7) as a digit: legible, and it makes the rule visible.
# Brightness rises towards the bottom, where the newest generations appear.
#
# Cost: the top row lives in scratch (8 B) and advances every CA_K frames; each
# subsequent row is read back from the previous one (8 B, via the rule) as it is
# built -- 64 bits of state that are never in flops at once if the row is
# processed a byte at a time with 2 bits of carry. ~25 flops, 16 B scratch.
# ---------------------------------------------------------------------------
CA_K, CA_RULE_GENS = 6, 96
CA_RULES = (30, 90, 110, 150, 54, 45)


def ca_step(bits, rule):
    # Null boundaries (cells beyond the edge are 0), not wrap-around. Rule 90
    # is linear, and on a ring of 2^6 cells it is nilpotent: every state dies
    # within 64 generations and the bottom of the screen goes black. With
    # fixed-zero edges the ring is effectively 65 wide and it never dies --
    # and it saves the wrap wiring.
    out = 0
    for c in range(COLS):
        l = (bits >> (c + 1)) & 1 if c + 1 < COLS else 0
        m = (bits >> c) & 1
        r = (bits >> (c - 1)) & 1 if c > 0 else 0
        out |= ((rule >> (l << 2 | m << 1 | r)) & 1) << c
    return out


def ca_rule(gen):
    return CA_RULES[(gen // CA_RULE_GENS) % len(CA_RULES)]


class CA:
    """Holds the scrolling top row, as scratch SRAM would."""

    def __init__(self):
        self.top = 1 << (COLS // 2)  # single seed: the classic triangles
        self.g0 = 0

    def frame(self, frame):
        while self.g0 < frame // CA_K:
            self.top = ca_step(self.top, ca_rule(self.g0))
            self.g0 += 1
            # Re-seed at every rule boundary from an LFSR, so a rule that
            # dies out (90 is linear, so it can collapse) always
            # has something to work on. A dot for 90, noise for the rest.
            if self.g0 % CA_RULE_GENS == 0:
                if ca_rule(self.g0) == 90:
                    self.top |= 1 << (lfsr16(self.g0) % COLS)
                else:
                    h = self.g0
                    for k in range(4):
                        h = lfsr16(h + k)
                        self.top ^= h << (16 * k)
                    self.top &= (1 << COLS) - 1
        rows = [self.top]
        for r in range(1, ROWS):
            rows.append(ca_step(rows[-1], ca_rule(self.g0 + r - 1)))
        return rows


def ca_levels(rows):
    """Per-digit segment levels directly, rather than via a sample function."""
    out = []
    for r, bits in enumerate(rows):
        bright = 3 + r * 12 // (ROWS - 1)
        line = []
        for c in range(COLS):
            if (bits >> c) & 1:
                l = (bits >> (c + 1)) & 1 if c + 1 < COLS else 0
                rr = (bits >> (c - 1)) & 1 if c > 0 else 0
                nb = l << 2 | 2 | rr  # middle is alive: 2, 3, 6 or 7
                segs = SEG7[nb]
                line.append([bright if (segs >> s) & 1 else 0 for s in range(7)] + [0])
            else:
                line.append([0] * 8)
        out.append(line)
    return out


SEG7 = [0x3F, 0x06, 0x5B, 0x4F, 0x66, 0x6D, 0x7D, 0x07]  # src/seg7_rom.v, 0-7


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
EFFECTS = {
    "ripples": ripples_frame,
    "zoneplate": zoneplate_frame,
    "plasma": plasma_frame,
    "zoneplate_slow": zoneplate_slow_frame,
    "interference_slow": interference_slow_frame,
    "plasma_slow": plasma_slow_frame,
    "ca": None,  # per-digit, handled specially
}


def sample(f, per_digit):
    """Turn a sample function into [row][col][seg] levels."""
    out = []
    for r in range(ROWS):
        line = []
        for c in range(COLS):
            x, y = c * segments.CELL_W, r * segments.CELL_H
            if per_digit:
                v = f(x + 5, y + 7)
                line.append([v] * 7 + [0])
            else:
                line.append([f(x + SEG_CX[s], y + SEG_CY[s]) for s in range(7)] + [0])
        out.append(line)
    return out


def gif_palette(palette, levels):
    pal = []
    for r, g, b in segments.PALETTES[palette]:
        if levels == 4:  # Tiny VGA: top 2 bits of each channel
            r, g, b = (r >> 2) * 85, (g >> 2) * 85, (b >> 2) * 85
        else:
            r, g, b = r * 17, g * 17, b * 17
        pal += [r, g, b]
    return pal + [0] * (768 - len(pal))


def draw(levels, pal):
    im = Image.new("P", (WIDTH, HEIGHT), 0)
    im.putpalette(pal)
    px = im.load()
    for r in range(ROWS):
        for c in range(COLS):
            for s, v in enumerate(levels[r][c]):
                if not v:
                    continue
                x0, x1, y0, y1 = segments.segment_pixels(c, r, s)
                for y in range(y0, y1 + 1):
                    for x in range(x0, x1 + 1):
                        px[x, y] = v
    return im


def run(name, args):
    pal = gif_palette(args.palette, args.levels)
    frames = []
    ca = CA() if name == "ca" else None
    for n in range(args.frames):
        fr = args.start + n * args.stride
        if ca:
            lv = ca_levels(ca.frame(fr))
        else:
            lv = sample(EFFECTS[name](fr), args.per_digit)
        frames.append(draw(lv, pal))
    tag = f"{name}{'_digit' if args.per_digit else ''}_p{args.palette}"
    if args.levels == 4:
        tag += "_tinyvga"
    os.makedirs(args.out, exist_ok=True)
    gif = os.path.join(args.out, f"attract_{tag}.gif")
    still = os.path.join(args.out, f"attract_{tag}.png")
    if args.mp4:
        # 60 fps and real speed, which a GIF can't do; and a fraction of the size.
        mp4 = gif[:-4] + ".mp4"
        ff = subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
             "-s", f"{WIDTH}x{HEIGHT}", "-r", str(60 // args.stride or 1), "-i", "-",
             "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv444p", mp4],
            stdin=subprocess.PIPE,
        )
        for im in frames:
            ff.stdin.write(im.convert("RGB").tobytes())
        ff.stdin.close()
        ff.wait()
        print(f"wrote {mp4}")
    else:
        frames[0].save(gif, save_all=True, append_images=frames[1:], duration=20, loop=0)
        print(f"wrote {gif}")
    frames[len(frames) // 2].convert("RGB").save(still)
    print(f"wrote {still}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("effect", choices=list(EFFECTS) + ["all"])
    ap.add_argument("--frames", type=int, default=120)
    ap.add_argument("--start", type=int, default=0, help="first frame number")
    ap.add_argument("--stride", type=int, default=1, help="frames advanced per GIF frame")
    ap.add_argument("--palette", type=int, default=0, choices=range(len(segments.PALETTES)))
    ap.add_argument("--levels", type=int, default=16, choices=(4, 16))
    ap.add_argument("--per-digit", action="store_true")
    ap.add_argument("--out", default="attract_out")
    ap.add_argument("--mp4", action="store_true", help="60 fps H.264 via ffmpeg instead of a GIF")
    args = ap.parse_args()
    for name in EFFECTS if args.effect == "all" else [args.effect]:
        run(name, args)


if __name__ == "__main__":
    main()
