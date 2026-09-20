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
    ./attract_proto.py plasma --param folds=3 --param speed=64 --mp4
    ./attract_proto.py all

plasma, zoneplate, chip and interference take tuning parameters (PARAMS below);
tools/palette_builder/palette_builder.py shows them live, with a slider each,
and prints the --param string for the setting on screen.

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

import segments

WIDTH, HEIGHT = 800, 600
COLS, ROWS = segments.COLS, segments.ROWS
GRID_W, GRID_H = segments.GRID_W, segments.GRID_H  # 768 x 592

# Segment centres within a cell (integer, rounded down, like the RTL would).
SEG_CX = [(s[1] + s[2]) // 2 for s in segments.SEGMENTS]
SEG_CY = [(s[3] + s[4]) // 2 for s in segments.SEGMENTS]


def tri(v, bits):
    """Triangle wave: the low `bits` of v folded to `bits-1` bits (0..2^(bits-1)-1).

    Written without a branch -- XOR with all-ones when the top bit is set --
    which is also exactly the hardware: `bits-1` XOR gates. It works unchanged
    on numpy arrays, which is how palette_builder evaluates a whole frame in one
    call.
    """
    half = 1 << (bits - 1)
    v = v & ((1 << bits) - 1)
    return (v ^ ((v >> (bits - 1)) * ((1 << bits) - 1))) & (half - 1)


def _max(a, b):
    return (a + b + abs(a - b)) >> 1  # exact for integers, scalar or array


def _min(a, b):
    return (a + b - abs(a - b)) >> 1



def lfsr16(v):
    """A 16-bit hash, cheap in gates: two rounds of xorshift."""
    v &= 0xFFFF
    for _ in range(2):
        v ^= (v << 7) & 0xFFFF
        v ^= v >> 9
        v ^= (v << 8) & 0xFFFF
    return v


def dist_approx(dx, dy, roundness=2):
    """Distance, at three costs. Any max-of-lines distance is a polygon: its
    contours are straight between the kinks, and at ring sizes of hundreds of
    pixels the kinks show as corners.

    1: max(hi, 7/8 hi + lo/2) -- two lines per octant, so a 16-gon whose
       second side spans ~31 degrees: reads as an octagon. Two adds.
    2: max of four tangent lines, at 0, 15, 30 and 45 degrees -- a 24-gon,
       within ~1%, and the corners are 15 degrees apart so they don't read.
       Coefficients are shift-and-add (31/32, 1/4, 7/8, 1/2, 45/64).
    3: exact, isqrt(dx^2 + dy^2): the squares the zone plate already builds
       plus a 10-step serial square root (~30 flops, one bit per cycle, well
       inside the ~40 spare cycles per byte).
    """
    dx, dy = abs(dx), abs(dy)
    if roundness >= 3:
        return _isqrt(dx * dx + dy * dy)
    hi, lo = _max(dx, dy), _min(dx, dy)
    if roundness <= 1:
        return _max(hi, hi - (hi >> 3) + (lo >> 1))
    d = _max(hi, hi - (hi >> 5) + (lo >> 2))  # 15 degrees: 0.969, 0.25
    d = _max(d, hi - (hi >> 3) + (lo >> 1))  # 30 degrees: 0.875, 0.5
    return _max(d, ((hi + lo) * 45) >> 6)  # 45 degrees: 0.703 each


def _isqrt(v):
    if hasattr(v, "shape"):
        import numpy as np

        # Exact for the < 2^22 values here: a double's sqrt of a perfect
        # square is exact, and of anything else is never rounded up past it.
        return np.floor(np.sqrt(v.astype(np.float64))).astype(np.int64)
    import math

    return math.isqrt(v)


def _lut(table, i):
    """table[i] for a scalar or a numpy index array."""
    if hasattr(i, "shape"):
        import numpy as np

        return np.asarray(table)[i]
    return table[i]



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


def ripples_frame(frame, **_):
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
# Tunable effects
#
# Every parameter is an integer standing for something cheap in hardware -- a
# shift amount, a small multiplier done as shift-and-add, a fold count -- so a
# setting that looks right carries into Verilog as constants. PARAMS lists
# (name, lo, hi, default, help); defaults are the slow, smooth versions, and the
# first, faster prototype is reachable from the sliders (noted per effect).
#
# All three sample functions are straight-line integer maths, so they take
# either Python ints or numpy arrays of x/y (see tri()).
# ---------------------------------------------------------------------------


def _levels(fine):
    """Wrap a 6-bit sample function as the 4-bit one everything else calls;
    the 6-bit one stays reachable as .fine, for dither()."""

    def f(x, y):
        return fine(x, y) >> 2

    f.fine = fine
    return f


# ---------------------------------------------------------------------------
# Temporal dither
#
# Every tunable effect computes 6 bits and shows 4, and slow motion is where
# that shows: a contour band creeps a whole brightness step at a time, which
# fast motion hides. Dithering spends the 2 dropped bits over time: a
# threshold cycles through 0-3 over 4 frames, and the level rounds up on the
# frames where the dropped bits beat it -- so over 4 frames at 60 Hz the eye
# averages 64 levels, and a band creeps in quarter steps.
#
# The threshold is offset by position (a 2x2 ordered pattern over column+seg
# and row parity) so neighbouring segments don't all round up on the same
# frame, which would read as the whole screen flickering.
#
# Cost: 2 frame-counter bits (exist), 3 parity bits (exist), a 2-bit compare
# and an increment with a clip at 15, applied as each byte goes to the line
# buffer. No flops.
#
# Tried and rejected by eye (2026-09-19, lossless 60 fps renders of all three
# effects): not wanted in the RTL. Kept only as a record of the experiment,
# alongside the earlier spatial attempt on the gamma-dithering branch.
# ---------------------------------------------------------------------------
_BAYER2 = (0, 2, 3, 1)  # 2x2 ordered: [[0, 2], [3, 1]], row-major


def dither(fine, frame, col, row, seg):
    """6-bit level -> 4-bit, rounding up on the right share of frames."""
    t = (_BAYER2[(((row & 1) << 1) | ((col + seg) & 1))] + frame) & 3
    return min(15, (fine >> 2) + ((fine & 3) > t))


def _sources(frame, drift):
    """Two points wandering on triangle-wave Lissajous paths. `drift` 4 takes
    about a minute to cross the screen; the four rates are 6:4:4:5 so the pair
    never falls into step."""
    ax = tri((frame * drift * 6) >> 4, 12) * GRID_W >> 11
    ay = tri(((frame * drift * 4) >> 4) + 600, 12) * GRID_H >> 11
    bx = tri(((frame * drift * 4) >> 4) + 1400, 12) * GRID_W >> 11
    by = tri((frame * drift * 5) >> 4, 12) * GRID_H >> 11
    return ax, ay, bx, by


# ---------------------------------------------------------------------------
# 2. Zone plate
#
# Each source contributes a phase of (dx^2 + dy^2) >> k: the Fresnel zone
# plate, rings that tighten outward. With two sources the sum is algebraically
# a *single* zone plate centred on their midpoint (|p-a|^2 + |p-b|^2 =
# 2|p-m|^2 + const), so it never shows interference -- that is `interference`.
# The phase carries 2 fractional bits, so the rings can creep by a quarter of a
# brightness step per frame.
#
# Cost: squares built incrementally along the scan ((x+1)^2 = x^2 + 2x + 1), so
# two ~20-bit accumulators and an adder per source, or one adder reused
# serially. ~30-60 flops, 0 scratch.
#
# First prototype: ring_shift 9, drift 16, phase_speed 48.
# ---------------------------------------------------------------------------
ZONEPLATE_PARAMS = [
    ("ring_shift", 8, 16, 13, "ring density: r^2 >> this; lower = more, finer rings"),
    ("drift", 0, 32, 4, "how fast the sources wander"),
    ("phase_speed", 0, 64, 4, "how fast the rings flow, in 1/16 brightness steps per frame"),
    ("sources", 1, 2, 2, "one zone plate, or two (which look like one at their midpoint)"),
]


def zoneplate_at(t, phase, fade=15, ring_shift=13, drift=4, sources=2):
    """The zone plate as the chip actually computes it: the source paths at
    time `t` and the rings at `phase`, neither of them a product of the frame
    number. src/zoneplate.v drives these from its two accumulators (t_src and
    ring_ph), which is what lets the speeds vary without the picture jumping.
    `fade` 0..15 scales every level on the way out, for the fade through
    black at a palette change."""
    ax, ay, bx, by = _sources(t, drift)
    sh = ring_shift - 2

    def fine(x, y):
        p = ((x - ax) ** 2 + (y - ay) ** 2) >> sh
        if sources == 2:
            p = p + (((x - bx) ** 2 + (y - by) ** 2) >> sh)
        return tri(p - phase, 7)

    f = _levels(fine)
    if fade == 15:
        return f

    def faded(x, y):
        return apply_fade(f(x, y), fade)

    faded.fine = fine
    return faded


def zoneplate_frame(frame, ring_shift=13, drift=4, phase_speed=4, sources=2):
    """The tunable effect, for exploring parameters: both speeds constant, so
    time and phase are still simple multiples of the frame number."""
    return zoneplate_at(frame, (frame * phase_speed) >> 2,
                        ring_shift=ring_shift, drift=drift, sources=sources)


# ---------------------------------------------------------------------------
# 2a. The chip
#
# What src/zoneplate.v and src/multi_seg_monitor.v actually do, bit for bit,
# rather than what the tunable effect above explores: the zone plate at fixed
# parameters, driven by two accumulators whose *rates* wander, and dimmed
# around the automatic palette change. The cocotb suite checks the RTL against
# this, and palette_builder previews it as the `chip` effect.
#
# The rule the whole design follows is that nothing may jump. Only rates vary;
# ring_ph and t_src themselves are continuous, so a rate change bends the
# motion rather than displacing it.
# ---------------------------------------------------------------------------
RING_MASK = (1 << 9) - 1     # src/zoneplate.v ring_ph: 7 integer + 2 fraction
T_SRC_MASK = (1 << 17) - 1   # t_src: 14 integer + 3 fraction
FRAME_MASK = (1 << 14) - 1   # multi_seg_monitor.v frame_ctr
CHIP_RESET_FRAME = 64        # ...and its reset value, clear of the fade-in


def chip_rates(frame_ctr, vary_phase=1, vary_drift=1):
    """(rate_ph, rate_t) for the frame starting at `frame_ctr`, which is the
    counter as it reads *before* that frame_start advances it -- the RTL
    computes both from the same pre-advance value.

    rate_ph is in quarter fold-steps (4 = one step a frame, the old fixed
    speed) and runs -3..12 when varying, one step every 256 frames. rate_t is
    in eighths (8 = the old speed) and runs 4..11, one step every 1024."""
    rate_ph = tri(frame_ctr >> 8, 5) - 3 if vary_phase else 4
    rate_t = 4 + tri(frame_ctr >> 10, 4) if vary_drift else 8
    return rate_ph, rate_t


def chip_step(state, frame_ctr, vary_phase=1, vary_drift=1):
    """(ring_ph, t_src) one frame_start on, from `state` at `frame_ctr`."""
    ring_ph, t_src = state
    rate_ph, rate_t = chip_rates(frame_ctr, vary_phase, vary_drift)
    return ((ring_ph + rate_ph) & RING_MASK, (t_src + rate_t) & T_SRC_MASK)


def chip_fade(frame_ctr, auto=1):
    """The fade level 0..15 for the frame showing `frame_ctr`. The automatic
    palette change happens as frame_ctr[9:0] wraps, with 64 frames of fade
    either side of it -- four frames a level -- so the change itself lands in
    black. `auto` off -- a host holding the palette, or manual mode -- means
    no fade at all."""
    if not auto:
        return 15
    k = frame_ctr & 0x3FF
    if k < 64:
        return k >> 2
    if k >= 960:
        return (1023 - k) >> 2
    return 15


_CHIP_CACHE = {}


def chip_state(frames, vary_phase=1, vary_drift=1):
    """(frame_ctr, ring_ph, t_src) `frames` frames after reset, with the
    switches held the whole time. Simulated a frame at a time, because that
    is the only way to know where two wandering rates have got to; cached, so
    playing an effect forward costs one step a frame."""
    key = (vary_phase, vary_drift)
    trace = _CHIP_CACHE.setdefault(key, [(CHIP_RESET_FRAME, 0, 0)])
    while len(trace) <= frames:
        frame_ctr, ring_ph, t_src = trace[-1]
        ring_ph, t_src = chip_step((ring_ph, t_src), frame_ctr,
                                   vary_phase, vary_drift)
        trace.append(((frame_ctr + 1) & FRAME_MASK, ring_ph, t_src))
    return trace[frames]


def chip_preset(frame, palette=0, manual=0, **_):
    """Which of the 8 built-in palettes the chip is showing `frame` frames
    after reset.

    Manual mode holds the one the switches name. Otherwise it steps every time
    frame_ctr's low 10 bits wrap, starting from `palette` -- which is the same
    ui_in[3:1] field the reset strap uses, so a board set up by switches alone
    agrees with itself. The step happens on the frame_start that ends frame
    1023, so frame 1023 still shows the old palette and 1024 shows the new
    one: both are at fade level 0, which is the point."""
    if manual:
        return palette & 7
    return (palette + ((CHIP_RESET_FRAME + frame) >> 10)) & 7


def chip_frame(frame, palette=0, manual=0, steady_phase=0, steady_drift=0):
    """The picture the chip draws `frame` frames after reset.

    The arguments are the DIP switch bits themselves, so every one of them is
    0 on a board with every switch off -- which is the full attract mode. The
    two speed switches are therefore "steady", the inverse of the vary_phase
    and vary_drift the RTL and chip_step() carry; the inversion lives here and
    nowhere else. `palette` only picks a colour, which the caller applies via
    chip_preset(), but it is taken so a whole switch setting passes through as
    one **params."""
    del palette
    frame_ctr, ring_ph, t_src = chip_state(frame, not steady_phase, not steady_drift)
    return zoneplate_at(t_src >> 3, ring_ph >> 2, chip_fade(frame_ctr, not manual))


# The four fields of the chip's DIP switches (src/config_port.v), in the pins'
# own polarity: all zero is every switch off, which is the full attract mode.
CHIP_PARAMS = [
    ("palette", 0, 7, 0, "ui_in[3:1]: the palette to hold, or where the automatic change starts"),
    ("manual", 0, 1, 0, "ui_in[4]: 1 = hold that palette, 0 = change every 1024 frames through a fade"),
    ("steady_phase", 0, 1, 0, "ui_in[5]: 1 = fixed ring speed, 0 = let it wander"),
    ("steady_drift", 0, 1, 0, "ui_in[6]: 1 = fixed source drift, 0 = let it wander"),
]


# ---------------------------------------------------------------------------
# 3. Interference
#
# Sum (or difference) of *distances*, not squared distances, so rings stay
# evenly spaced -- nothing on screen is finer than the segment pitch -- and the
# pair makes the ellipses of two stones dropped in a pond (sum) or the
# hyperbolic nodal lines of a two-slit pattern (difference). Distance via
# dist_approx: no multiplier and no squarer, cheaper than the zone plate.
#
# Cost: two dist_approx (or one, serially), an adder, the fold. ~25 flops.
# ---------------------------------------------------------------------------
INTERFERENCE_PARAMS = [
    ("ring_spacing", 0, 7, 5, "a ring every 16 * 2^k px of summed distance"),
    ("drift", 0, 32, 3, "how fast the sources wander"),
    ("phase_speed", 0, 128, 8, "how fast the rings flow, in 1/128 brightness steps per frame (16 = the old 2)"),
    ("mode", 0, 1, 1, "0 = sum of distances (ellipses), 1 = difference (hyperbolas)"),
    ("roundness", 1, 3, 3, "distance: 1 = 2 lines (octagonal), 2 = 4 lines (24-gon), 3 = exact sqrt"),
]


def interference_frame(frame, ring_spacing=5, drift=3, phase_speed=8, mode=1, roundness=3):
    ax, ay, bx, by = _sources(frame, drift)

    def fine(x, y):
        da = dist_approx(x - ax, y - ay, roundness)
        db = dist_approx(x - bx, y - by, roundness)
        d = da - db if mode else da + db
        return tri(((d << 3) >> ring_spacing) - ((frame * phase_speed) >> 5), 7)

    return _levels(fine)


# ---------------------------------------------------------------------------
# 4. Plasma
#
# The demoscene classic: a sum of sines of x, y, x+y and a moving radial term,
# folded to 4 bits. One 256-step sine with 6-bit output (a 64-entry
# quarter-wave table), looked up four times per sample.
#
# Resolution matters more than it looks. The first version used a 64-step,
# 4-bit sine: at x >> 4 each term's phase only moved every 16 px, so every term
# was a staircase, and the diagonal term's steps crossing the others' drew
# jagged triangles all over the screen. A 256-step phase moves every 4 px at
# the same scale, and 6-bit terms keep the folded sum smooth.
#
# The shifts keep their meaning from that version (sin(x >> k) at 64 steps per
# cycle is the same wavelength as sin((x << 2) >> k) at 256). Time is
# frame * speed in 1/65536 of a cycle; the four terms run at 1, 3/2, 5/2 and 2
# times that, non-harmonic so the pattern doesn't visibly repeat.
#
# Cost: 64x6 ROM, an 8-bit phase adder, an 8-bit accumulator, serial x4. The
# radial term reuses dist_approx. ~20 flops, 0 scratch.
#
# First prototype: shifts 3/3/4/2, speed 1024 (off the slider), folds 2,
# wobble 24 (at the old
# 64-step, 4-bit resolution, so it was also jaggier).
# ---------------------------------------------------------------------------
_QSIN = [round(31.5 + 31.5 * __import__("math").sin((i + 0.5) * 3.14159265 / 128)) for i in range(64)]


def _sin256_scalar(p):
    """Quarter-wave symmetry: 64 stored entries cover the whole cycle."""
    q, i = p >> 6, p & 63
    if q == 0:
        return _QSIN[i]
    if q == 1:
        return _QSIN[63 - i]
    if q == 2:
        return 63 - _QSIN[i]
    return 63 - _QSIN[63 - i]


SIN256 = tuple(_sin256_scalar(p) for p in range(256))


def sin256(p):
    """6-bit sine (0..63) of an 8-bit phase."""
    return _lut(SIN256, p & 255)


PLASMA_PARAMS = [
    ("x_shift", 1, 7, 4, "horizontal term wavelength: 2^k * 64 px / 4; lower = tighter"),
    ("y_shift", 1, 7, 4, "vertical term wavelength, likewise"),
    ("diag_shift", 1, 7, 5, "diagonal (x + y) term wavelength"),
    ("radial_shift", 1, 7, 3, "radial term ring spacing"),
    ("speed", 0, 255, 50, "time, in 1/65536 of a cycle per frame; 50 = a cycle in ~22 s"),
    ("folds", 1, 4, 2, "folds of the summed sines into 0..15: more = more contour bands"),
    ("wobble", 0, 32, 24, "how far the radial term's centre wanders"),
]


def plasma_frame(frame, x_shift=4, y_shift=4, diag_shift=5, radial_shift=3, speed=50, folds=2, wobble=24):
    t = frame * speed
    cx = GRID_W // 2 + (((sin256(t >> 10) - 32) * wobble) >> 2)
    cy = GRID_H // 2 + (((sin256((t >> 10) + 64) - 32) * wobble * 3) >> 4)

    def fine(x, y):
        s = sin256(((x << 2) >> x_shift) + (t >> 8))
        s = s + sin256(((y << 2) >> y_shift) - ((t * 3) >> 9))
        s = s + sin256((((x + y) << 2) >> diag_shift) + ((t * 5) >> 9))
        s = s + sin256(((dist_approx(x - cx, y - cy) << 2) >> radial_shift) - (t >> 7))
        # The sum of four sines (0..252) clusters around the middle, so one
        # fold spends most of 0..15 near full brightness; two spends it on
        # the part of the sum that actually moves. Three and four draw
        # contour bands. Rescaled to 6 bits: the top 4 are the level, the
        # rest what dither() spends.
        v = tri(s, 9 - folds)
        return v >> (2 - folds) if folds <= 2 else v << (folds - 2)

    return _levels(fine)



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


SEG7 = [0x3F, 0x06, 0x5B, 0x4F, 0x66, 0x6D, 0x7D, 0x07]  # 7-segment codes for 0-7, a=bit 0


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
EFFECTS = {
    "ripples": ripples_frame,
    "zoneplate": zoneplate_frame,
    "chip": chip_frame,
    "interference": interference_frame,
    "plasma": plasma_frame,
    "ca": None,  # per-digit, handled specially
}

# The tunable ones -- what palette_builder offers, with its sliders.
PARAMS = {
    "plasma": PLASMA_PARAMS,
    "zoneplate": ZONEPLATE_PARAMS,
    "chip": CHIP_PARAMS,
    "interference": INTERFERENCE_PARAMS,
}


# ---------------------------------------------------------------------------
# Changing effect: fade to black, switch, fade back up
#
# The attract sequencer changes effect this way, so a change never cuts
# between two unrelated pictures. A counter k runs from 0 at the moment of
# the change; the old effect fades out over the first half, the new one in
# over the second, with the switch made while the screen is black.
#
# Cost: an 8-bit frame counter, a 4-bit fade level derived from it, and a
# 4x5 multiply on each level on its way to the line buffer (the same shape
# as the palette's, and one per byte serially would do). ~15 flops.
# ---------------------------------------------------------------------------
FADE_FRAMES = 180  # the whole change: 3 s at 60 fps, 1.5 s down and 1.5 s up


def fade_factor(k):
    """Fade level 0..15 (15 = full brightness) at k frames into a change.
    Frames 0..FADE_FRAMES/2-1 fade the old effect out, the rest fade the
    new one in; returns 15 once the change is over."""
    half = FADE_FRAMES // 2
    if k < half:
        return 15 - (k * 16) // half
    return min(15, ((k - half) * 16) // half)


def fade_showing_new(k):
    """Whether frame k of a change shows the new effect (the second half)."""
    return k >= FADE_FRAMES // 2


def apply_fade(level, fade):
    """Scale a 4-bit level by fade/15: exact at both ends (fade 15 leaves
    every level alone, fade 0 is black) with one small multiply."""
    return (level * (fade + 1)) >> 4


def default_params(name):
    return {p[0]: p[3] for p in PARAMS.get(name, [])}


def param_args(params):
    """The CLI spelling of a parameter set, for copying out of the builder."""
    return " ".join(f"--param {k}={v}" for k, v in params.items())


def sample(f, per_digit, dither_frame=None):
    """Turn a sample function into [row][col][seg] levels. With dither_frame
    set, a 6-bit effect is temporally dithered for that frame instead of
    truncated (see dither())."""
    if dither_frame is not None and hasattr(f, "fine"):
        fine = f.fine
        g = lambda x, y, c, r, s: dither(fine(x, y), dither_frame, c, r, s)  # noqa: E731
    else:
        g = lambda x, y, c, r, s: f(x, y)  # noqa: E731
    out = []
    for r in range(ROWS):
        line = []
        for c in range(COLS):
            x, y = c * segments.CELL_W, r * segments.CELL_H
            if per_digit:
                v = g(x + 5, y + 7, c, r, 0)
                line.append([v] * 7 + [0])
            else:
                line.append([g(x + SEG_CX[s], y + SEG_CY[s], c, r, s) for s in range(7)] + [0])
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
    from PIL import Image  # only the renderer needs it; palette_builder doesn't

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


def parse_params(name, pairs):
    params = default_params(name)
    spec = {p[0]: p for p in PARAMS.get(name, [])}
    for pair in pairs:
        key, _, val = pair.partition("=")
        if key not in spec:
            raise SystemExit(f"{name} has no parameter {key!r} (has: {', '.join(params) or 'none'})")
        v = int(val)
        if not spec[key][1] <= v <= spec[key][2]:
            raise SystemExit(f"{key}={v} is outside {spec[key][1]}..{spec[key][2]}")
        params[key] = v
    return params


def run(name, args):
    params = parse_params(name, args.param if args.effect != "all" else [])
    if params:
        print(f"{name}: {param_args(params)}")
    pal = gif_palette(args.palette, args.levels)
    frames = []
    ca = CA() if name == "ca" else None
    for n in range(args.frames):
        fr = args.start + n * args.stride
        if ca:
            lv = ca_levels(ca.frame(fr))
        else:
            lv = sample(EFFECTS[name](fr, **params), args.per_digit,
                        dither_frame=fr if args.dither else None)
        # The chip picks its own palette frame by frame, so --palette does
        # not apply to it: --param palette=N sets which one manual mode holds,
        # or where the automatic change starts. A GIF frame carries its own
        # palette, so following the change costs nothing here.
        if name == "chip":
            pal = gif_palette(chip_preset(fr, **params), args.levels)
        frames.append(draw(lv, pal))
    tag = f"{name}{'_digit' if args.per_digit else ''}{'_dither' if args.dither else ''}_p{args.palette}"
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
             "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p", mp4],
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
    ap.add_argument("--dither", action="store_true",
                    help="temporally dither the 2 bits below the 4-bit level (plasma, zoneplate, interference)")
    ap.add_argument("--out", default="attract_out")
    ap.add_argument("--mp4", action="store_true", help="60 fps H.264 via ffmpeg instead of a GIF")
    ap.add_argument("--param", action="append", default=[], metavar="NAME=VALUE",
                    help="set a tuning parameter (repeatable); see PARAMS per effect")
    args = ap.parse_args()
    for name in EFFECTS if args.effect == "all" else [args.effect]:
        run(name, args)


if __name__ == "__main__":
    main()
