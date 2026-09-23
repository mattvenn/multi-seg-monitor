"""
24-hour digital clock for the Tiny Tapeout demoboard.

The chip has no framebuffer (see firmware/seg_player.py's docstring for the
pacing that forces on every streamer), so this draws the clock itself: the
64x37 grid of small 7-segment digits is used as a "pixel" canvas, and whole
digit cells are turned on or off to build big blocky numerals out of many
small digits. There is no RTC on this board, so the wall-clock reference has
to come from the host PC at launch time (`main()`'s
`start_h/start_m/start_s`); from there the RP2350 free-runs off its own
millisecond ticks and wraps at 24h.

Shading doesn't stop at "whole cell lit or dark", though: a lit cell's 7 real
segments (a-g -- an actual 7-segment digit, the small kind, not the big-font
shape) are each sampled independently at their own sub-cell position
(`_SEG_XY8`) on one diagonal linear gradient in real screen space
(`_gradient_intensity`, `GRADIENT_DX`/`GRADIENT_DY`), computed as fixed-point
integers rather than floats -- see the comment on `_gradient_intensity` for
why that matters here. The gradient's reach is set to span the whole display
exactly once (`_HALF_PERIOD`), so it always reads as a single uniform sweep
across every digit at once, never several repeats of the same ramp, and it
scrolls slowly over time (`GRADIENT_MS_PER_UNIT`). The colour comes from
whichever palette curve is loaded (defaults to preset 3, purple -- see
"Choosing the Pmod and palette" in README.md); the gradient just walks each
segment's intensity code up and down that curve.

`import rp2`/`machine.Pin` are guarded so this file can also be run under a
plain desktop `python3` -- see the bottom of the file -- to preview the font
and centering without any hardware attached.
"""

import time

try:
    import rp2
    from machine import Pin
except ImportError:  # desktop preview, not MicroPython -- see __main__ below
    rp2 = None
    Pin = None

# Edit these to change what the chip comes up as; main()'s arguments still
# override them for a one-off call.
PMOD_TYPE = 0  # 0 = Digilent PmodVGA, 1 = Tiny VGA
PALETTE = 3  # 0-7, one of the chip's built-in presets -- 3 is purple
START_H, START_M, START_S = 0, 0, 0  # only used by a bare `mpremote run`

# Demoboard GPIO map -- identical to firmware/seg_player.py, not imported
# because `mpremote run` execs one file with no access to a sibling module.
DATA_BASE = 17  # ui_in[0..7]  -> GPIO17..24
STROBE = 31  # uio[6]       -> stream strobe
MODE = 32  # uio[7]       -> 1 selects streamed data
VSYNC_DIGILENT = 30  # uio[5]   -> Digilent PmodVGA vsync
VSYNC_TINYVGA = 36  # uo_out[3] -> Tiny VGA vsync

# Display constants -- must match
# docs/superpowers/specs/2026-08-11-800x600-mode-design.md
COLS, ROWS = 64, 37
CELL_W, CELL_H = 12, 16
H_TOTAL = 1056
BYTES_PER_DIGIT = 4
ROW_BYTES = COLS * BYTES_PER_DIGIT  # 256
FRAME_BYTES = ROWS * ROW_BYTES  # 9472

CLOCKS_PER_BYTE = CELL_H * H_TOTAL // ROW_BYTES  # 66, exactly
PIXEL_HZ = 40_000_000  # required VGA pixel clock, 800x600@60

PIO0_TXF0 = 0x50200010  # PIO0 TX FIFO 0


# ---------------------------------------------------------------------------
# Font: which whole digit cells light up to build one big numeral, not the
# real per-cell segment geometry (that's tools/segments.py's SEGMENTS, a
# different, smaller thing). Each glyph is DIGIT_H rows tall; segment rects
# are derived from DIGIT_T/DIGIT_H rather than hand-picked, so changing the
# stroke thickness or height doesn't mean re-deriving every rectangle by hand.
#
# DIGIT_H=15 must leave an even margin against ROWS=37 to stay centred
# ((37-15)/2 = 11 exactly) -- pick another odd DIGIT_H if this changes.
DIGIT_T = 3  # stroke thickness, in whole digit-cells
DIGIT_H = 15  # glyph height, in whole digit-cells
DIGIT_W = 2 * DIGIT_T + 1  # two vertical strokes plus a 1-cell gap between them
COLON_W = DIGIT_T + 1  # a T-wide dot plus a 1-cell spacer, matching the digits' gap

_g0 = (DIGIT_H - DIGIT_T) // 2  # middle bar's first row
_g1 = _g0 + DIGIT_T - 1  # middle bar's last row

_SEG_RECTS = {
    "a": (0, DIGIT_T - 1, 0, DIGIT_W - 1),  # top
    "d": (DIGIT_H - DIGIT_T, DIGIT_H - 1, 0, DIGIT_W - 1),  # bottom
    "g": (_g0, _g1, 0, DIGIT_W - 1),  # middle
    # Verticals run from the outer edge through the middle bar rather than
    # stopping short of it, so the strokes read as one solid piece instead of
    # a gap at every crossing.
    "f": (0, _g1, 0, DIGIT_T - 1),  # upper left
    "b": (0, _g1, DIGIT_W - DIGIT_T, DIGIT_W - 1),  # upper right
    "e": (_g0, DIGIT_H - 1, 0, DIGIT_T - 1),  # lower left
    "c": (_g0, DIGIT_H - 1, DIGIT_W - DIGIT_T, DIGIT_W - 1),  # lower right
}

_DIGIT_SEGS = {
    "0": "abcdef",
    "1": "bc",
    "2": "abged",
    "3": "abgcd",
    "4": "fgbc",
    "5": "afgcd",
    "6": "afgecd",
    "7": "abc",
    "8": "abcdefg",
    "9": "abcdfg",
}


def _glyph(segs):
    """Which whole cells are part of the big numeral -- on/off only. Once a
    cell is on, all 7 of ITS OWN real segments (a-g, see SEG_OFFSET below)
    get lit; the gradient is what gives them individually different
    brightness, not this grid."""
    grid = [[False] * DIGIT_W for _ in range(DIGIT_H)]
    for name in segs:
        r0, r1, c0, c1 = _SEG_RECTS[name]
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                grid[r][c] = True
    return grid


FONT = {ch: _glyph(segs) for ch, segs in _DIGIT_SEGS.items()}

# Two T-tall dots, one just above the middle bar's band and one just below
# it -- echoes where a colon sits against a real 7-segment digit.
_colon = [[False] * COLON_W for _ in range(DIGIT_H)]
for _r in range(_g0 - DIGIT_T, _g0):
    for _c in range(DIGIT_T):
        _colon[_r][_c] = True
for _r in range(_g1 + 1, _g1 + 1 + DIGIT_T):
    for _c in range(DIGIT_T):
        _colon[_r][_c] = True
FONT[":"] = _colon


def _char_width(ch):
    return COLON_W if ch == ":" else DIGIT_W


_ZERO_FRAME = bytes(FRAME_BYTES)

# ---------------------------------------------------------------------------
# Shading: one diagonal linear gradient, in real screen space, sweeping
# uniformly across the whole display -- not stepped per digit or per
# character. What makes it land on individual segments rather than whole
# digit-cells is that each of a lit cell's 7 *real* segments (a-g, the same
# small rectangles as tools/segments.py's SEGMENTS, not the big-font "which
# whole cells are on" grid above) is sampled at its own sub-cell position, so
# two segments a few pixels apart can end up at meaningfully different points
# on the ramp even inside the same lit cell.
#
# SEG_XY8 is each segment's rectangle centre, in eighths of a cell (0..8 in
# each axis, integer) -- e.g. "a" sits near the cell's top edge, horizontally
# centred. Fixed-point rather than the exact fraction: this only has to look
# smooth, not match tools/segments.py to the pixel, and every value from here
# down staying a plain int (not a float) is load-bearing -- see below.
_SEG_XY8 = {
    "a": (3, 0),
    "b": (6, 2),
    "c": (6, 5),
    "d": (3, 6),
    "e": (0, 5),
    "f": (0, 2),
    "g": (3, 3),
}
CELL_UNIT = 8  # sub-cell resolution the gradient is sampled at, in eighths

# Direction of the sweep, in (grid-columns, grid-rows) -- not (1, 0) or
# (0, 1), so it cuts across at an angle rather than reading as strictly
# horizontal or vertical bands.
GRADIENT_DX, GRADIENT_DY = 1, 2

# Each segment's own fixed contribution to the (DX, DY) projection, in
# CELL_UNIT-ths, precomputed once and in pack_digit's a-g order -- the hot
# loop in render() below then only has to add a cell's own base position.
_SEG_POS = tuple(
    sx * GRADIENT_DX + sy * GRADIENT_DY for sx, sy in (_SEG_XY8[n] for n in "abcdefg")
)

_INTENSITY_MIN, _INTENSITY_MAX = 4, 15

# One full sweep's *reach* (dark to bright and back to dark) is set to just
# cover the whole grid along (DX, DY), so at any instant there's exactly one
# smooth ramp across the entire display, not several repeats of it -- pick it
# up in the middle of a digit and it still looks the same as picking it up in
# the middle of any other. A triangle wave (not a sawtooth) is what avoids a
# hard seam where the ramp would otherwise snap from bright back to dark.
_HALF_PERIOD = (COLS * CELL_UNIT * GRADIENT_DX + ROWS * CELL_UNIT * GRADIENT_DY) // 2
GRADIENT_PERIOD = 2 * _HALF_PERIOD
GRADIENT_MS_PER_UNIT = 90  # ms per CELL_UNIT-th -- full sweep in about 100 s
_GRADIENT_PERIOD_MS = GRADIENT_PERIOD * GRADIENT_MS_PER_UNIT


def _gradient_intensity(pos, phase):
    # Subtracting phase (rather than adding) is what makes the ramp scroll
    # towards higher (dx, dy)-projected positions as phase grows. Integer
    # throughout -- see the note on _SEG_XY8 above: `_on_vsync` runs as a
    # hard IRQ (see ClockPlayer), and MicroPython floats are heap objects
    # while small ints aren't, so a float here means an allocation per
    # segment per lit cell, thousands of them a redraw. That pushes the GC
    # to collect far more often, and a hard IRQ landing mid-collection is
    # exactly the kind of thing that showed up here as an intermittently
    # torn frame -- switching this to fixed-point integers is the fix.
    x = (pos - phase) % GRADIENT_PERIOD
    tri = x if x <= _HALF_PERIOD else GRADIENT_PERIOD - x
    span = _INTENSITY_MAX - _INTENSITY_MIN
    return _INTENSITY_MIN + (tri * span) // _HALF_PERIOD


def render(h, m, s, phase=0, buf=None):
    """
    Draw "HH:MM:SS" into `buf` (or a fresh bytearray), centered on the 64x37
    grid. Every lit cell's 7 real segments are shaded independently, each at
    its own point on one diagonal gradient that scrolls with `phase` (see
    `_gradient_intensity`). Pure function -- no hardware imports -- so it
    also backs the desktop ASCII preview at the bottom of this file.
    """
    if buf is None:
        buf = bytearray(FRAME_BYTES)
    buf[:] = _ZERO_FRAME

    text = "%02d:%02d:%02d" % (h, m, s)
    total_w = sum(_char_width(ch) for ch in text) + (len(text) - 1)
    x0 = (COLS - total_w) // 2
    y0 = (ROWS - DIGIT_H) // 2  # (37-15)//2 = 11 exactly, both margins equal

    x = x0
    for ch in text:
        glyph = FONT[ch]
        w = _char_width(ch)
        for r in range(DIGIT_H):
            row = glyph[r]
            grow = y0 + r
            row_off = grow * ROW_BYTES
            base_row = grow * CELL_UNIT * GRADIENT_DY
            for ci in range(w):
                if not row[ci]:
                    continue
                gcol = x + ci
                base = gcol * CELL_UNIT * GRADIENT_DX + base_row
                a = _gradient_intensity(base + _SEG_POS[0], phase)
                b = _gradient_intensity(base + _SEG_POS[1], phase)
                c = _gradient_intensity(base + _SEG_POS[2], phase)
                d = _gradient_intensity(base + _SEG_POS[3], phase)
                e = _gradient_intensity(base + _SEG_POS[4], phase)
                f = _gradient_intensity(base + _SEG_POS[5], phase)
                g = _gradient_intensity(base + _SEG_POS[6], phase)
                off = row_off + gcol * BYTES_PER_DIGIT
                buf[off] = (b << 4) | a
                buf[off + 1] = (d << 4) | c
                buf[off + 2] = (f << 4) | e
                buf[off + 3] = g  # DP nibble stays 0 -- not part of the big-font shape
        x += w + 1
    return buf


# ---------------------------------------------------------------------------
# Config packet (src/config_port.v). A copy of tools/segments.py's
# curve_params() + config_packet(), inlined for the same reason
# firmware/seg_player.py copies it: `mpremote run` execs one file with no
# access to tools/. tools/test_palettes.py checks this copy against the
# original for every legal curve.
def config_packet(curve, pmod_type=0, cycle=False):
    out = [0xA0 | (0x04 if cycle else 0) | (0x01 if pmod_type else 0)]
    if curve is None:
        return bytes(out)
    for x1, y1, x2, y2 in curve:
        m1 = min(31, (y1 * 16 + x1) // (2 * x1)) if x1 else 0
        m2 = min(31, ((y2 - y1) * 16 + (x2 - x1)) // (2 * (x2 - x1)))
        out += [(x1 << 4) | y1, m1, m2]
    return bytes(out)


def send_config(packet):
    """Strobe `packet` in with MODE low -- see seg_player.py's copy of this
    for the timing rationale, unchanged here."""
    mode = Pin(MODE, Pin.OUT, value=1)
    strobe = Pin(STROBE, Pin.OUT, value=0)
    data = [Pin(DATA_BASE + i, Pin.OUT, value=0) for i in range(8)]
    time.sleep_us(10)
    mode.value(0)
    time.sleep_us(10)
    for byte in packet:
        for i in range(8):
            data[i].value((byte >> i) & 1)
        time.sleep_us(10)
        strobe.value(1)
        time.sleep_us(10)
        strobe.value(0)
        time.sleep_us(10)


if rp2 is not None:

    @rp2.asm_pio(
        out_init=(rp2.PIO.OUT_LOW,) * 8,
        sideset_init=rp2.PIO.OUT_LOW,
        autopull=True,
        pull_thresh=8,
        out_shiftdir=rp2.PIO.SHIFT_RIGHT,
        fifo_join=rp2.PIO.JOIN_TX,
    )
    def push_bytes():
        out(pins, 8).side(0)
        nop().side(1)


class ClockPlayer:
    """
    Streams a live clock instead of a recorded video: same PIO+DMA plumbing
    as seg_player.Player, but `service()` redraws the off-screen buffer from
    wall time instead of reading the next frame from a file, and on a fixed
    redraw cadence (REDRAW_MS) rather than only when something changed -- the
    gradient's phase is continuous, so by the time it's worth checking it has
    always moved at least a little, making a plain "did it change" test
    pointless. REDRAW_MS is independent of GRADIENT_MS_PER_UNIT: it just
    trades redraw cost against how coarsely the sweep's motion is sampled.
    """

    REDRAW_MS = 300

    def __init__(self, pixel_hz, pmod_type=0, start_h=0, start_m=0, start_s=0):
        self.start_total = (start_h * 3600 + start_m * 60 + start_s) % 86400
        self.start_ticks = time.ticks_ms()

        self.buffers = [bytearray(FRAME_BYTES), bytearray(FRAME_BYTES)]
        self.current = 0
        self.last_redraw_ticks = time.ticks_ms()
        render(*_hms(self._current_second()), phase=self._current_phase(), buf=self.buffers[0])

        Pin(MODE, Pin.OUT, value=1)

        # See seg_player.Player for why this is derived from the pixel clock
        # actually achieved rather than a fixed divider.
        pio_freq = pixel_hz // (CLOCKS_PER_BYTE // 2)
        self.sm = rp2.StateMachine(
            0,
            push_bytes,
            freq=pio_freq,
            out_base=Pin(DATA_BASE),
            sideset_base=Pin(STROBE),
        )

        self.dma = rp2.DMA()
        self.dma_ctrl = self.dma.pack_ctrl(
            size=0,  # bytes
            inc_read=True,
            inc_write=False,
            treq_sel=0,  # DREQ_PIO0_TX0: pace off the PIO FIFO
        )

        self.sm.active(1)
        vsync = VSYNC_TINYVGA if pmod_type else VSYNC_DIGILENT
        Pin(vsync, Pin.IN).irq(
            trigger=Pin.IRQ_FALLING, handler=self._on_vsync, hard=True
        )

    def _current_second(self):
        elapsed = time.ticks_diff(time.ticks_ms(), self.start_ticks) // 1000
        return (self.start_total + elapsed) % 86400

    def _current_phase(self):
        # Reduced mod the (bounded) period in ms first, so this stays a
        # small int indefinitely rather than growing with uptime.
        elapsed = time.ticks_diff(time.ticks_ms(), self.start_ticks)
        return (elapsed % _GRADIENT_PERIOD_MS) // GRADIENT_MS_PER_UNIT

    def _on_vsync(self, _pin):
        # The chip resets its write pointer on this same edge -- see
        # seg_player.Player._on_vsync, unchanged here.
        self.dma.active(0)
        self.dma.config(
            read=self.buffers[self.current],
            write=PIO0_TXF0,
            count=FRAME_BYTES,
            ctrl=self.dma_ctrl,
            trigger=True,
        )

    def service(self):
        """Call often from the main loop; redraws ahead of the DMA every
        REDRAW_MS -- see the class docstring for why that's simpler than
        change-detection here."""
        now_ticks = time.ticks_ms()
        if time.ticks_diff(now_ticks, self.last_redraw_ticks) >= self.REDRAW_MS:
            nxt = 1 - self.current
            render(*_hms(self._current_second()), phase=self._current_phase(), buf=self.buffers[nxt])
            self.current = nxt
            self.last_redraw_ticks = now_ticks

    def stop(self):
        self.dma.active(0)
        self.sm.active(0)


def _hms(total_seconds):
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    return h, m, s


def main(
    pmod_type=PMOD_TYPE,
    palette=PALETTE,
    curve=None,
    start_h=START_H,
    start_m=START_M,
    start_s=START_S,
):
    """
    `pmod_type`/`palette` pick the reset-time strap, `curve` overrides the
    palette with a custom one -- see seg_player.main()'s docstring, identical
    here. `start_h`/`start_m`/`start_s` are the wall-clock time at the moment
    this call starts streaming; there's no RTC on this board, so the host is
    expected to pass its own current time in (see README.md).
    """
    from ttboard.demoboard import DemoBoard
    import ttboard.fpga.fabricfoxv2 as fpgaloader

    tt = DemoBoard.get()
    tt.pins.safe_bidir()
    fpgaloader.spi_transferPIO("/bitstreams/tt_um_multi_seg_monitor.bin")

    pwm = tt.clock_project_PWM(PIXEL_HZ)
    pixel_hz = pwm.freq()  # actually achieved, may differ from PIXEL_HZ

    # The strap register (src/config_port.v) only samples ui_in[3:0] while
    # rst_n is low -- see seg_player.main(), identical here.
    strap = (palette << 1) | pmod_type
    for i in range(4):
        Pin(DATA_BASE + i, Pin.OUT, value=(strap >> i) & 1)
    tt.reset_project(True)
    time.sleep_ms(1)  # comfortably more than one 40 MHz clock edge
    tt.reset_project(False)

    if curve is not None:
        send_config(config_packet(curve, pmod_type))

    player = ClockPlayer(pixel_hz, pmod_type, start_h, start_m, start_s)
    try:
        while True:
            player.service()
    except KeyboardInterrupt:
        player.stop()


def _ascii_frame(buf):
    """Render a frame buffer as hex-nibble intensity text (`.` for dark) --
    desktop preview only."""
    lines = []
    for row in range(ROWS):
        row_off = row * ROW_BYTES
        chars = "".join(
            ("%x" % (buf[row_off + col * BYTES_PER_DIGIT] & 0xF))
            if buf[row_off + col * BYTES_PER_DIGIT]
            else "."
            for col in range(COLS)
        )
        lines.append(chars)
    return "\n".join(lines)


if __name__ == "__main__":
    if rp2 is not None:
        main()
    else:
        # `python3 firmware/clock_mode.py [HH:MM:SS ...]` -- previews the
        # font, centering and gradient (as hex intensity, at a few scroll
        # phases) without any hardware attached.
        import sys

        times = sys.argv[1:] or ["12:34:56"]
        for t in times:
            h, m, s = (int(x) for x in t.split(":"))
            for phase in (0, GRADIENT_PERIOD // 2):
                print(f"{t}  phase={phase}")
                print(_ascii_frame(render(h, m, s, phase=phase)))
                print()
