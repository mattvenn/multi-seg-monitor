"""
Stage 1 tests: VGA timing, blanking, and a rendered frame.

The frame is captured to a PPM by the Verilog testbench and validated here, then
written out as a PNG so the geometry can be iterated by eye without hardware.
"""

import os
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge
from cocotb.utils import get_sim_time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import png  # noqa: E402
import segments  # noqa: E402

# 640x480 @ 72Hz, 31.5 MHz pixel clock -- SPEC.md section 6
CLK_PS = 31746  # 1 / 31.5 MHz

H_ACTIVE, H_FP, H_SYNC, H_BP = 640, 24, 40, 128
V_ACTIVE, V_FP, V_SYNC, V_BP = 480, 9, 3, 28
H_TOTAL = H_ACTIVE + H_FP + H_SYNC + H_BP  # 832
V_TOTAL = V_ACTIVE + V_FP + V_SYNC + V_BP  # 520

# Grid geometry -- SPEC.md section 1
COLS, ROWS = 52, 30
CELL_W, CELL_H = 12, 16
MARGIN_X = 8

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
    """hsync and vsync match the 640x480@72 constants."""
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

    # Margins: 52 columns of 12 pixels leaves 8 blank either side.  Reporting the
    # first and last lit column rather than the first offending pixel makes a
    # misaligned capture obvious at a glance.
    cols_lit = [x for x in range(width) if any(lit(x, y) for y in range(0, height, 3))]
    assert cols_lit, "nothing rendered at all"
    assert cols_lit[0] >= MARGIN_X, (
        f"content starts at x={cols_lit[0]}, left margin should be {MARGIN_X} wide"
    )
    assert cols_lit[-1] < MARGIN_X + COLS * CELL_W, (
        f"content ends at x={cols_lit[-1]}, expected < {MARGIN_X + COLS * CELL_W}"
    )

    # Cell corners are where an x zone and a y zone both miss, so nothing selects
    # a segment and they must stay dark.
    for row in range(0, ROWS, 3):
        for col in range(0, COLS, 5):
            x0 = MARGIN_X + col * CELL_W
            y0 = row * CELL_H
            for dx, dy in ((0, 0), (1, 1), (0, 15), (1, 14)):
                assert not lit(x0 + dx, y0 + dy), (
                    f"corner lit in cell ({col}, {row}) at offset ({dx}, {dy})"
                )

    # Every digit row carries data: row 0 is built during vertical blanking and
    # rows 1..29 during the row before, so a blank row means the generator or the
    # buffer swap is out of step.
    for row in range(ROWS):
        band = sum(
            1
            for y in range(row * CELL_H, (row + 1) * CELL_H)
            for x in range(MARGIN_X, MARGIN_X + COLS * CELL_W, 3)
            if lit(x, y)
        )
        assert band > 0, f"digit row {row} is entirely blank"

    total = sum(1 for y in range(height) for x in range(width) if lit(x, y))
    frac = total / (width * height)
    assert 0.05 < frac < 0.60, f"lit fraction {frac:.3f} is implausible"

    png.write_png("frame.png", width, height, px)
    dut._log.info("frame ok: %.1f%% of pixels lit, wrote frame.png", frac * 100)


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

# One digit row is 16 scanlines and 208 bytes, so a byte every 64 pixel clocks
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


async def host_stream(dut, frame, frames):
    """
    Emulates the RP2350 pacing described in SPEC.md section 4.3.

    Restart at vsync, then push bytes at a fixed rate. The vertical blanking
    interval gives a head start of about two rows, and from there the byte rate
    and the raster advance together, so the host stays between one and three rows
    ahead for the whole frame without ever looking at hsync.

    Bounded rather than free-running: a coroutine still parked on a trigger when
    cocotb tears the simulation down segfaults Icarus.
    """
    for _ in range(frames):
        await FallingEdge(dut.vs)
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
