"""
Stage 1 tests: VGA timing, blanking, and a rendered frame.

The frame is captured to a PPM by the Verilog testbench and validated here, then
written out as a PNG so the geometry can be iterated by eye without hardware.
"""

import os
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge, Timer
from cocotb.utils import get_sim_time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import png  # noqa: E402
import segments  # noqa: E402

# 800x600 @ 60Hz, 40 MHz pixel clock --
# docs/superpowers/specs/2026-08-11-800x600-mode-design.md
CLK_PS = 25000  # 1 / 40 MHz

H_ACTIVE, H_FP, H_SYNC, H_BP = 800, 40, 128, 88
V_ACTIVE, V_FP, V_SYNC, V_BP = 600, 1, 4, 23
H_TOTAL = H_ACTIVE + H_FP + H_SYNC + H_BP  # 1056
V_TOTAL = V_ACTIVE + V_FP + V_SYNC + V_BP  # 628

# Grid geometry -- docs/superpowers/specs/2026-08-11-800x600-mode-design.md
COLS, ROWS = 64, 37
CELL_W, CELL_H = 12, 16
MARGIN_X = 16
MARGIN_Y = 4

# On PmodVGA every uo_out bit carries colour -- R in the low nibble, B in the
# high one -- and green sits on uio_out[3:0].  The old Tiny VGA mask here left
# two of those bits and all of green unchecked.  tb.v already splits the pins
# into px_r/px_g/px_b, so use those rather than masking by hand.


async def reset(dut):
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.dump_en.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 1)


async def cycles_between_falls(sig):
    await FallingEdge(sig)
    t0 = get_sim_time("ps")
    await FallingEdge(sig)
    return round((get_sim_time("ps") - t0) / CLK_PS)


async def low_cycles(sig):
    await FallingEdge(sig)
    t0 = get_sim_time("ps")
    await RisingEdge(sig)
    return round((get_sim_time("ps") - t0) / CLK_PS)


@cocotb.test()
async def test_vga_timing(dut):
    """hsync and vsync match the 800x600@60 constants."""
    cocotb.start_soon(Clock(dut.clk, CLK_PS, unit="ps").start())
    await reset(dut)

    period = await cycles_between_falls(dut.hs)
    assert period == H_TOTAL, f"hsync period {period}, expected {H_TOTAL}"

    width = await low_cycles(dut.hs)
    assert width == H_SYNC, f"hsync pulse {width}, expected {H_SYNC}"

    period = await cycles_between_falls(dut.vs)
    assert period == V_TOTAL * H_TOTAL, (
        f"vsync period {period} cycles, expected {V_TOTAL * H_TOTAL}"
    )

    width = await low_cycles(dut.vs)
    assert width == V_SYNC * H_TOTAL, (
        f"vsync pulse {width} cycles, expected {V_SYNC * H_TOTAL}"
    )

    dut._log.info(
        "timing ok: %d x %d, %.1f Hz",
        H_TOTAL,
        V_TOTAL,
        1e12 / (CLK_PS * H_TOTAL * V_TOTAL),
    )


@cocotb.test()
async def test_blanking(dut):
    """Colour outputs are held at zero outside active video."""
    cocotb.start_soon(Clock(dut.clk, CLK_PS, unit="ps").start())
    await reset(dut)

    # Sync pulse plus back porch, stopping short of the first active pixel.
    await FallingEdge(dut.hs)
    for i in range(H_SYNC + H_BP - 1):
        await RisingEdge(dut.clk)
        r, g, b = int(dut.px_r.value), int(dut.px_g.value), int(dut.px_b.value)
        assert (r, g, b) == (0, 0, 0), (
            f"colour r={r:x} g={g:x} b={b:x} {i} cycles into horizontal blanking"
        )

    dut._log.info("blanking ok")


@cocotb.test()
async def test_render_frame(dut):
    """Capture a frame, check the geometry, and write a PNG."""
    cocotb.start_soon(Clock(dut.clk, CLK_PS, unit="ps").start())
    await reset(dut)

    # Let the generator get ahead of the raster before capturing.
    await ClockCycles(dut.clk, 2 * H_TOTAL * V_TOTAL)
    dut.dump_en.value = 1
    await ClockCycles(dut.clk, 3 * H_TOTAL * V_TOTAL)

    width, height, px = read_ppm("frame.ppm")
    assert (width, height) == (H_ACTIVE, V_ACTIVE)

    def lit(x, y):
        return px[(y * width + x) * 3] != 0

    # Margins: 64 columns of 12 pixels leaves 16 blank either side, 37 rows of 16
    # leaves 4 blank top and bottom.  Reporting the first/last lit row or column
    # rather than the first offending pixel makes a misaligned capture obvious at
    # a glance.
    cols_lit = [x for x in range(width) if any(lit(x, y) for y in range(0, height, 3))]
    assert cols_lit, "nothing rendered at all"
    assert cols_lit[0] >= MARGIN_X, (
        f"content starts at x={cols_lit[0]}, left margin should be {MARGIN_X} wide"
    )
    assert cols_lit[-1] < MARGIN_X + COLS * CELL_W, (
        f"content ends at x={cols_lit[-1]}, expected < {MARGIN_X + COLS * CELL_W}"
    )

    rows_lit = [y for y in range(height) if any(lit(x, y) for x in range(0, width, 3))]
    assert rows_lit, "nothing rendered at all"
    assert rows_lit[0] >= MARGIN_Y, (
        f"content starts at y={rows_lit[0]}, top margin should be {MARGIN_Y} wide"
    )
    assert rows_lit[-1] < MARGIN_Y + ROWS * CELL_H, (
        f"content ends at y={rows_lit[-1]}, expected < {MARGIN_Y + ROWS * CELL_H}"
    )

    # Cell corners are where an x zone and a y zone both miss, so nothing selects
    # a segment and they must stay dark.
    for row in range(0, ROWS, 3):
        for col in range(0, COLS, 5):
            x0 = MARGIN_X + col * CELL_W
            y0 = MARGIN_Y + row * CELL_H
            for dx, dy in ((0, 0), (1, 1), (0, 15), (1, 14)):
                assert not lit(x0 + dx, y0 + dy), (
                    f"corner lit in cell ({col}, {row}) at offset ({dx}, {dy})"
                )

    # Every digit row carries data: row 0 is built during vertical blanking and
    # rows 1..36 during the row before, so a blank row means the generator or the
    # buffer swap is out of step.
    for row in range(ROWS):
        band = sum(
            1
            for y in range(MARGIN_Y + row * CELL_H, MARGIN_Y + (row + 1) * CELL_H)
            for x in range(MARGIN_X, MARGIN_X + COLS * CELL_W, 3)
            if lit(x, y)
        )
        assert band > 0, f"digit row {row} is entirely blank"

    total = sum(1 for y in range(height) for x in range(width) if lit(x, y))
    frac = total / (width * height)
    assert 0.05 < frac < 0.60, f"lit fraction {frac:.3f} is implausible"

    png.write_png("frame.png", width, height, px)
    dut._log.info("frame ok: %.1f%% of pixels lit, wrote frame.png", frac * 100)

    check_gold(dut, "generator.png", width, height, px)


# --------------------------------------------------------------------------
# Stream port
#
# uio[6] is the strobe, uio[7] selects streamed data over the internal generator.
# They moved up from uio[0:1] when the prototype output became PmodVGA, which
# needs the low six bits of uio for the green nibble and the syncs.
# --------------------------------------------------------------------------
UIO_MODE = 1 << 7
UIO_STB = 1 << 6

UIO_IDLE = UIO_MODE
UIO_STROBE = UIO_MODE | UIO_STB

# One digit row is 16 scanlines and 256 bytes, so a byte every 66 pixel clocks
# tracks the raster exactly.  The division is exact, which is what lets the host
# free-run instead of resynchronising every row.
BYTE_PERIOD = CELL_H * H_TOTAL // segments.ROW_BYTES


async def push_byte(dut, value):
    dut.ui_in.value = value
    await ClockCycles(dut.clk, 2)
    dut.uio_in.value = UIO_STROBE
    await ClockCycles(dut.clk, 4)
    dut.uio_in.value = UIO_IDLE
    await ClockCycles(dut.clk, BYTE_PERIOD - 6)


async def host_stream(dut, frame, frames, delay_us=0):
    """
    Emulates the RP2350 pacing described in SPEC.md section 4.3.

    Restart at vsync, then push bytes at a fixed rate. The vertical blanking
    interval gives a head start of about two rows, and from there the byte rate
    and the raster advance together, so the host stays between one and three rows
    ahead for the whole frame without ever looking at hsync.

    `delay_us` models the real host's latency between seeing vsync and its first
    byte leaving -- on the demoboard, the vsync interrupt handler restarting the
    DMA. It is spent once per frame and comes straight off the head start; the
    byte rate afterwards is unchanged, because the DMA is paced by the PIO and
    does not speed up to catch up.

    Bounded rather than free-running: a coroutine still parked on a trigger when
    cocotb tears the simulation down segfaults Icarus.
    """
    for _ in range(frames):
        await FallingEdge(dut.vs)
        if delay_us:
            await Timer(delay_us, unit="us")
        for byte in frame:
            await push_byte(dut, byte)


def make_test_frame():
    """Every segment a different intensity, varying across the grid."""
    data = bytearray()
    for row in range(segments.ROWS):
        for col in range(segments.COLS):
            data += segments.pack_digit([(col + row + s) & 0xF for s in range(8)])
    return bytes(data)


@cocotb.test()
async def test_stream_frame(dut):
    """Push a frame in over the stream port and read it back off the screen."""
    cocotb.start_soon(Clock(dut.clk, CLK_PS, unit="ps").start())
    await reset(dut)
    dut.uio_in.value = UIO_IDLE

    frame = make_test_frame()
    assert len(frame) == segments.FRAME_BYTES

    cocotb.start_soon(host_stream(dut, frame, frames=7))

    # Let the host get into step, then capture.
    await ClockCycles(dut.clk, 2 * H_TOTAL * V_TOTAL)
    dut.dump_en.value = 1
    await ClockCycles(dut.clk, 3 * H_TOTAL * V_TOTAL)

    width, height, px = read_ppm("frame.ppm")

    def level_at(x, y):
        # PmodVGA carries 4 bits per channel and the testbench scales them by 17
        # to fill a byte, so dividing recovers the nibble the design put out.
        return px[(y * width + x) * 3] // 17

    bad = []
    for row in range(segments.ROWS):
        for col in range(segments.COLS):
            off = segments.digit_offset(col, row)
            sent = segments.unpack_digit(frame[off : off + 4])
            for seg in range(8):
                x, y = segments.segment_centre(col, row, seg)
                # The design emits grey = level[5:2], so the 6 bit gamma entry
                # loses its bottom two bits on the way to the pmod.
                want = segments.GAMMA[sent[seg]] >> 2
                got = level_at(x, y)
                if got != want:
                    bad.append((col, row, segments.SEGMENTS[seg][0], sent[seg], want, got))

    assert not bad, (
        f"{len(bad)} of {segments.ROWS * segments.COLS * 8} segments wrong, "
        f"first few: {bad[:5]}"
    )

    png.write_png("frame_stream.png", width, height, px)
    dut._log.info(
        "stream ok: %d segments round-tripped, wrote frame_stream.png",
        segments.ROWS * segments.COLS * 8,
    )

    check_gold(dut, "stream.png", width, height, px)


# --------------------------------------------------------------------------
# Vsync-to-first-byte delay sweep
#
# Reproduces, in simulation, the tearing seen when streaming to the FPGA
# breakout at 640x480.  There the host's vsync handler took 580-600 us to
# restart the DMA against only 819 us of vertical blanking, so the head start
# it is supposed to build up was nearly gone before the first byte moved
# (commit ce2388c). 800x600's head start is tighter still (713 us, see below),
# which is exactly why this sweep is worth re-running at the new timing rather
# than assumed safe by analogy.
#
# What that does to the picture: the renderer re-fetches the whole 256 byte row
# on every one of its 16 scanlines, sweeping it in 1056 clocks, while the host
# fills that same row over 16896.  Once the lead is smaller than a row the
# renderer overtakes the host partway across, so the left of each digit row is
# the byte the host just wrote and the right is whatever was in that buffer
# before -- which, four buffers deep, is digit row R-4 of the previous frame.
# A still image tears just as visibly as a moving one for that reason: the stale
# bytes are not the same row again, they are a row from elsewhere in the picture.
#
# Off by default -- set DELAY_SWEEP.  Each case captures a full frame, so the ten
# of them cost roughly ten minutes.
# --------------------------------------------------------------------------

# Vsync falling to the first active line: 4 sync + 23 back porch lines.  This is
# the entire head start, and the delay comes straight off it.
V_LEAD_CLOCKS = (V_SYNC + V_BP) * H_TOTAL  # 28512, 713 us at 40 MHz

# A frame's 9472 bytes at 66 clocks each is 625152 of the 663168 clocks in a
# frame, so beyond ~950 us of delay the push no longer fits between two vsyncs
# and the tail is cut off by the pointer reset instead -- a different failure.
# The sweep stops at 900 us, just inside that.
#
# 0 is the control: the same card, the same path, no delay.  Without it there is
# no way to tell tearing from the card simply being hard to read as digits.
DELAYS_US = [0] + list(range(100, 901, 100))

TESTCARD = os.path.join(os.path.dirname(__file__), "testcard.seg")


def load_testcard():
    """Frame 0 of the test card -- see tools/testcard.sh."""
    try:
        with open(TESTCARD, "rb") as f:
            frame = f.read(segments.FRAME_BYTES)
    except FileNotFoundError:
        raise FileNotFoundError(
            f"{TESTCARD} is missing -- run tools/testcard.sh to generate it"
        ) from None
    assert len(frame) == segments.FRAME_BYTES, (
        f"{TESTCARD} holds {len(frame)} bytes, expected {segments.FRAME_BYTES}"
    )
    return frame


def analyse(px, width, frame):
    """
    Compare every segment against what was streamed.

    Returns (wrong, stale, first_bad) where `stale` counts the wrong segments
    whose value is what digit row R-4 holds -- the signature of the renderer
    reading a buffer the host has not refilled yet -- and `first_bad` maps each
    digit row to the leftmost column that disagrees, which is where the renderer
    caught up with the host on that row.
    """

    def want(col, row, seg):
        off = segments.digit_offset(col, row)
        # The design emits grey = level[5:2], so the 6 bit gamma entry loses its
        # bottom two bits on the way to the pmod.
        return segments.GAMMA[segments.unpack_digit(frame[off : off + 4])[seg]] >> 2

    wrong = stale = 0
    first_bad = {}
    for row in range(segments.ROWS):
        for col in range(segments.COLS):
            for seg in range(8):
                x, y = segments.segment_centre(col, row, seg)
                got = px[(y * width + x) * 3] // 17
                if got == want(col, row, seg):
                    continue
                wrong += 1
                if row >= 4 and got == want(col, row - 4, seg):
                    stale += 1
                if row not in first_bad:
                    first_bad[row] = col
    return wrong, stale, first_bad


async def run_delay_case(dut, delay_us):
    cocotb.start_soon(Clock(dut.clk, CLK_PS, unit="ps").start())
    await reset(dut)
    dut.uio_in.value = UIO_IDLE

    frame = load_testcard()
    cocotb.start_soon(host_stream(dut, frame, frames=7, delay_us=delay_us))

    # Let the host get into step, then capture.
    await ClockCycles(dut.clk, 2 * H_TOTAL * V_TOTAL)
    dut.dump_en.value = 1
    await ClockCycles(dut.clk, 3 * H_TOTAL * V_TOTAL)

    width, height, px = read_ppm("frame.ppm")
    wrong, stale, first_bad = analyse(px, width, frame)

    name = f"frame_delay_{delay_us:04d}us.png"
    png.write_png(name, width, height, px)

    lead = (V_LEAD_CLOCKS - delay_us * 40) / BYTE_PERIOD
    total = segments.ROWS * segments.COLS * 8
    dut._log.info(
        "delay %d us: lead %.0f bytes (%.2f rows), %d/%d segments wrong, "
        "%d of them stale from row-4, wrote %s",
        delay_us,
        lead,
        lead / segments.ROW_BYTES,
        wrong,
        total,
        stale,
        name,
    )
    if first_bad:
        dut._log.info(
            "first bad column by digit row: %s",
            ", ".join(f"{r}:{c}" for r, c in sorted(first_bad.items())),
        )
    else:
        dut._log.info("no corruption at this delay")

    # The control is the only case with a right answer to hold it to.
    if delay_us == 0:
        check_gold(dut, "delay_0000us.png", width, height, px)


def _register_delay_tests():
    """
    One test per delay, built in a loop.

    Separate tests rather than one test looping, so each writes its own PNG and
    a failure in one still leaves the others' images on disk.
    """
    for delay_us in DELAYS_US:
        name = f"test_stream_delay_{delay_us:04d}us"

        @cocotb.test(name=name)
        async def _test(dut, delay_us=delay_us):
            await run_delay_case(dut, delay_us)

        _test.__doc__ = f"Stream with {delay_us} us between vsync and the first byte."
        globals()[name] = _test


if os.environ.get("DELAY_SWEEP"):
    _register_delay_tests()


def read_ppm(path):
    with open(path) as f:
        assert f.readline().strip() == "P3"
        width, height = (int(v) for v in f.readline().split())
        f.readline()  # maxval
        px = [int(v) for v in f.read().split()]
    assert len(px) == width * height * 3, (
        f"got {len(px)} samples, expected {width * height * 3}"
    )
    return width, height, px


# --------------------------------------------------------------------------
# Gold images
#
# The property assertions above say the picture is plausible -- margins clear,
# corners dark, no blank row, a sane lit fraction.  They do not say it is the
# same picture as yesterday, and most of what one would want to catch here is a
# change in appearance rather than a violation of a rule: a segment one pixel
# wide, a gamma entry off by one, a digit row rendered from the wrong buffer.
# Comparing against a committed image catches all of those.
#
# Only deterministic frames are golden.  The delay sweep's corrupted captures
# are observations of a fault, not a specification of one, so pinning them would
# turn any future change in the arbitration into a failure that has to be
# rubber-stamped.  Its 0 us control is golden, because that one is supposed to be
# pixel exact and it is what makes the rest of the sweep trustworthy.
# --------------------------------------------------------------------------
GOLD_DIR = os.path.join(os.path.dirname(__file__), "gold")


def check_gold(dut, name, width, height, px):
    """
    Compare a captured frame against test/gold/<name>.

    Regenerate with `make -C test gold` after an intended change, and look at
    the result before committing it -- an image test is only worth as much as
    the eye that last approved it.
    """
    path = os.path.join(GOLD_DIR, name)

    if os.environ.get("GOLD_UPDATE"):
        os.makedirs(GOLD_DIR, exist_ok=True)
        png.write_png(path, width, height, px)
        dut._log.warning("GOLD_UPDATE set: rewrote %s -- check it by eye", path)
        return

    if not os.path.exists(path):
        raise AssertionError(
            f"{path} is missing -- create it with `make -C test gold` and check it"
        )

    gw, gh, gold = png.read_png(path)
    assert (gw, gh) == (width, height), (
        f"{name} is {gw}x{gh}, captured frame is {width}x{height}"
    )

    if px == gold:
        dut._log.info("matches gold/%s exactly", name)
        return

    bad = [i // 3 for i in range(0, len(px), 3) if px[i : i + 3] != gold[i : i + 3]]

    # Differences on their own are hard to find in an 800x600 field of digits, so
    # write a map: everything dimmed, the disagreeing pixels in red.
    out = [v // 4 for v in px]
    for p in bad:
        out[p * 3 : p * 3 + 3] = [255, 0, 0]
    diff_name = name.replace(".png", "_diff.png")
    png.write_png(diff_name, width, height, out)

    first = ", ".join(f"({p % width},{p // width})" for p in bad[:5])
    raise AssertionError(
        f"{len(bad)} of {width * height} pixels differ from gold/{name} "
        f"(first at {first}) -- see {diff_name}"
    )
