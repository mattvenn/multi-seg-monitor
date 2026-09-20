"""
Segment video player for the Tiny Tapeout demoboard.

Streams a .seg file (produced by tools/video2seg.py) to the Multi Segment
Monitor over the byte-wide push port.

The chip has no framebuffer, so every displayed frame has to be pushed in full,
60.3 times a second, whatever the video's own frame rate is.  That is 571 kB/s,
which is why the transfer is PIO plus DMA rather than anything touching Python
per byte.

Pacing is the interesting part and it comes out almost free.  One digit row is
16 scanlines and 256 bytes, so a byte every 66 pixel clocks tracks the raster
exactly -- the division is exact, no remainder to accumulate.  Vertical blanking
gives the host a head start of a bit under two rows, and from there the byte
rate and the raster advance in step.  The chip's write pointer resets on vsync
and so does the DMA, so the two cannot drift apart by more than a frame.

That is why the line buffer holds four rows rather than two: at a lead of ~2
rows the host is always writing a buffer the renderer is not reading, and no
row-by-row handshaking is needed.

That head start is the whole budget, and it is not large: vsync falling to the
first active line is 27 lines, 28512 pixel clocks, 713 us (800x600@60, was 819
us at 640x480@72).  Whatever the host spends before its first byte comes
straight off it.  A soft IRQ (the default for Pin.irq) doesn't run in the
interrupt itself -- it waits for micropython.schedule() to reach a bytecode
boundary, which service()'s uninterruptible flash read routinely delays past
that budget, so the vsync handler measured 500-600 us before its first
register write and tore the picture at the old, tighter clock. hard=True below
dispatches from the interrupt itself and cuts that to ~80 us, comfortably
inside budget.
"""

import time

import rp2
from machine import Pin

# Edit these to change what the chip comes up as; main()'s arguments still
# override them for a one-off call.
PMOD_TYPE = 1  # 0 = Digilent PmodVGA, 1 = Tiny VGA
PALETTE = 3  # 0-7, one of the chip's built-in presets

# Demoboard GPIO map, from the tt-demo-pcb README.  ui_in is contiguous on
# GPIO17-24, which is what lets the whole byte leave in a single PIO `out`.
#
# strobe/mode-select sit on uio[6:7] in both Pmod modes -- Tiny VGA touches
# no uio pin at all, so there's nothing to relocate them for (see
# src/tt_um_multi_seg_monitor.v). vsync does move, though: uio[5] on
# Digilent PmodVGA, uo_out[3] on Tiny VGA -- _on_vsync's IRQ pin has to
# follow pmod_type or it listens on a pin the chip isn't driving in that
# mode, which is exactly what desynced the DMA and broke streaming under
# Tiny VGA before this was fixed.
DATA_BASE = 17  # ui_in[0..7]  -> GPIO17..24
STROBE = 31  # uio[6]       -> stream strobe
MODE = 32  # uio[7]       -> 1 selects streamed data
VSYNC_DIGILENT = 30  # uio[5]   -> Digilent PmodVGA vsync
VSYNC_TINYVGA = 36  # uo_out[3] -> Tiny VGA vsync

# Display constants -- must match
# docs/superpowers/specs/2026-08-11-800x600-mode-design.md
COLS, ROWS = 64, 37
CELL_H = 16
H_TOTAL = 1056
ROW_BYTES = COLS * 4  # 256
FRAME_BYTES = ROWS * ROW_BYTES  # 9472

CLOCKS_PER_BYTE = CELL_H * H_TOTAL // ROW_BYTES  # 66, exactly
PIXEL_HZ = 40_000_000  # required VGA pixel clock, 800x600@60

PIO0_TXF0 = 0x50200010  # PIO0 TX FIFO 0


# Config packet (src/config_port.v). A copy of tools/segments.py's
# curve_params() + config_packet(), inlined because `mpremote run` execs one
# file with no access to tools/ -- tools/test_palettes.py checks this copy
# against the original for every legal curve, so it can't drift silently.
# `curve` is three (x1, y1, x2, y2) point pairs, R then G then B, as
# presets.json and tools/palette_builder write them.
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
    """
    Strobe `packet` in with MODE low, where strobes carry config rather than
    pixels. Bit-banged with Pin rather than PIO: it's ten bytes, once, at
    startup, and it has to happen before Player hands the data and strobe
    pins to the PIO. Each step is 10 us, ~400 pixel clocks -- far more than
    the 3 the chip's strobe synchroniser needs.
    """
    mode = Pin(MODE, Pin.OUT, value=1)  # a fall of MODE starts a new packet
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


@rp2.asm_pio(
    out_init=(rp2.PIO.OUT_LOW,) * 8,
    sideset_init=rp2.PIO.OUT_LOW,
    autopull=True,
    pull_thresh=8,
    out_shiftdir=rp2.PIO.SHIFT_RIGHT,
    fifo_join=rp2.PIO.JOIN_TX,
)
def push_bytes():
    # Data goes out with the strobe low, then the strobe rises: one full PIO
    # cycle of setup before the chip latches.  Stalling on an empty FIFO holds
    # the strobe low, so running dry cannot fake a transfer.
    out(pins, 8).side(0)
    nop().side(1)


class Player:
    def __init__(self, path, video_fps, pixel_hz, pmod_type=0):
        self.file = open(path, "rb")
        self.file.seek(0, 2)
        self.frames = self.file.tell() // FRAME_BYTES
        if not self.frames:
            raise ValueError(f"{path} holds less than one frame")
        self.file.seek(0)

        # Two buffers: the DMA replays one while the next video frame is read
        # into the other.  A frame is 9472 bytes, so this costs ~19 kB of the
        # RP2350's 520 kB.
        self.buffers = [bytearray(FRAME_BYTES), bytearray(FRAME_BYTES)]
        self.current = 0
        self.index = 0
        self._read_into(self.buffers[0])

        # How many display frames each video frame is held for.
        self.repeat = max(1, round(60.3 / video_fps))
        self.shown = 0

        Pin(MODE, Pin.OUT, value=1)

        # Two instructions per byte, one byte per CLOCKS_PER_BYTE pixel clocks,
        # ratioed off the pixel clock actually achieved by clock_project_PWM --
        # not off a fixed assumed divider. clock_project_PWM can retune the
        # RP2350's own system clock to whatever divides most cleanly to the
        # target (ttboard/demoboard.py _get_best_rp2040_freq), so the real
        # divisor from sysclk to pixel clock isn't guaranteed in advance.
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

    def _read_into(self, buf):
        got = self.file.readinto(buf)
        if got != FRAME_BYTES:  # wrap at end of file
            self.file.seek(0)
            self.file.readinto(buf)
            self.index = 0
        else:
            self.index += 1

    def _on_vsync(self, _pin):
        # The chip resets its write pointer on this same edge, so restarting the
        # DMA here is what keeps the two ends aligned. Runs from a hard IRQ --
        # see the module docstring -- and dma.config() has measured fine there,
        # so there's no need for the extra complexity of poking DMA registers
        # directly.
        self.dma.active(0)
        self.dma.config(
            read=self.buffers[self.current],
            write=PIO0_TXF0,
            count=FRAME_BYTES,
            ctrl=self.dma_ctrl,
            trigger=True,
        )
        self.shown += 1

    def service(self):
        """Call often from the main loop; decodes ahead of the DMA."""
        if self.shown >= self.repeat:
            self.shown = 0
            nxt = 1 - self.current
            self._read_into(self.buffers[nxt])
            self.current = nxt

    def stop(self):
        self.dma.active(0)
        self.sm.active(0)
        self.file.close()


def main(
    path="video.seg",
    video_fps=24.0,
    pmod_type=PMOD_TYPE,
    palette=PALETTE,
    curve=None,
):
    """
    `pmod_type`/`palette` pick the reset-time strap: pmod_type 0=Digilent
    PmodVGA, 1=Tiny VGA; palette 0-7 selects one of the chip's built-in
    presets (tools/palette_builder/presets.json), applied in either mode.

    `curve` loads a custom palette over the top instead, as three
    (x1, y1, x2, y2) point pairs for R, G, B -- palette_builder's Export
    prints a ready-made call. It is sent as a config packet after reset and
    before streaming starts. See src/config_port.v for both.
    """
    from ttboard.demoboard import DemoBoard
    import ttboard.fpga.fabricfoxv2 as fpgaloader

    tt = DemoBoard.get()
    # tt.shuttle.tt_um_multi_seg_monitor.enable() depends on the demoboard's
    # FPGA-carrier auto-detect (reads a dedicated GPIO), which is unreliable on
    # this board -- it falls back to the ASIC shuttle mux and the name lookup
    # fails. Push the bitstream directly instead; this is all .enable() does
    # for an FPGA target.
    tt.pins.safe_bidir()
    fpgaloader.spi_transferPIO("/bitstreams/tt_um_multi_seg_monitor.bin")

    pwm = tt.clock_project_PWM(PIXEL_HZ)
    pixel_hz = pwm.freq()  # actually achieved, may differ from PIXEL_HZ

    # The strap register (src/config_port.v) only samples ui_in[3:0] while
    # rst_n is low, so this has to come after the clock
    # starts and pulse reset itself, and before the Player below starts
    # pushing bytes -- pulsing rst_n also resets the core's write pointer,
    # and doing that once a stream is already running would desync the host
    # from the raster, the same class of corruption the delay-sweep tests
    # exist to catch. `mpremote run` execs one file with no access to a
    # sibling module, so this can't live in a shared helper.
    strap = (palette << 1) | pmod_type
    for i in range(4):
        Pin(DATA_BASE + i, Pin.OUT, value=(strap >> i) & 1)
    tt.reset_project(True)
    time.sleep_ms(1)  # comfortably more than one 40 MHz clock edge
    tt.reset_project(False)

    # Config goes in while MODE is still low: Player raises it, and from then
    # on every strobe is a pixel byte.
    if curve is not None:
        send_config(config_packet(curve, pmod_type))

    player = Player(path, video_fps, pixel_hz, pmod_type)
    try:
        while True:
            player.service()
    except KeyboardInterrupt:
        player.stop()


if __name__ == "__main__":
    main()
