"""
Segment video player for the Tiny Tapeout demoboard.

Streams a .seg file (produced by tools/video2seg.py) to the Multi Segment
Monitor over the byte-wide push port.

The chip has no framebuffer, so every displayed frame has to be pushed in full,
72.8 times a second, whatever the video's own frame rate is.  That is 454 kB/s,
which is why the transfer is PIO plus DMA rather than anything touching Python
per byte.

Pacing is the interesting part and it comes out almost free.  One digit row is
16 scanlines and 208 bytes, so a byte every 64 pixel clocks tracks the raster
exactly -- the division is exact, no remainder to accumulate.  Vertical blanking
gives the host a head start of about two rows, and from there the byte rate and
the raster advance in step.  The chip's write pointer resets on vsync and so
does the DMA, so the two cannot drift apart by more than a frame.

That is why the line buffer holds four rows rather than two: at a lead of ~2
rows the host is always writing a buffer the renderer is not reading, and no
row-by-row handshaking is needed.

That head start is the whole budget, and it is not large: vsync falling to the
first active line is 31 lines, 25792 pixel clocks, 819 us.  Whatever the host
spends before its first byte comes straight off it.  The first version of this
file spent 580-600 us of it restarting the DMA from the vsync handler, leaving a
lead of ~107 bytes against the 208 a row needs -- so the renderer overtook the
host partway across every row, and everything right of column ~28 showed the
previous frame in the top half of each digit and the current one in the bottom
half.  Hence the register-level restart below.
"""

import rp2
from machine import Pin
from uctypes import addressof

# Demoboard GPIO map, from the tt-demo-pcb README.  ui_in is contiguous on
# GPIO17-24, which is what lets the whole byte leave in a single PIO `out`.
#
# PmodVGA prototype pinout: uio[5:0] carry video out to the pmod (G/HS/VS),
# which is why strobe/mode moved off uio[0:1] onto uio[6:7] -- PmodVGA's
# second connector leaves those two not-connected. vsync moved from
# uo_out[3] (Tiny VGA) to uio[5] to match. See src/tt_um_multi_seg_monitor.v.
DATA_BASE = 17  # ui_in[0..7]  -> GPIO17..24
STROBE = 31  # uio[6]       -> stream strobe
MODE = 32  # uio[7]       -> 1 selects streamed data
VSYNC = 30  # uio[5]       -> PmodVGA vsync

# Display constants -- must match SPEC.md sections 1 and 6
COLS, ROWS = 52, 30
CELL_H = 16
H_TOTAL = 832
ROW_BYTES = COLS * 4  # 208
FRAME_BYTES = ROWS * ROW_BYTES  # 6240

CLOCKS_PER_BYTE = CELL_H * H_TOTAL // ROW_BYTES  # 64, exactly
PIXEL_HZ = 31_500_000  # SPEC.md section 6: required VGA pixel clock

PIO0_TXF0 = 0x50200010  # PIO0 TX FIFO 0


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
    def __init__(self, path, video_fps, pixel_hz):
        self.file = open(path, "rb")
        self.file.seek(0, 2)
        self.frames = self.file.tell() // FRAME_BYTES
        if not self.frames:
            raise ValueError(f"{path} holds less than one frame")
        self.file.seek(0)

        # Two buffers: the DMA replays one while the next video frame is read
        # into the other.  A frame is 6240 bytes, so this costs 12 kB of the
        # RP2350's 520 kB.
        self.buffers = [bytearray(FRAME_BYTES), bytearray(FRAME_BYTES)]
        self.current = 0
        self.index = 0
        self._read_into(self.buffers[0])

        # How many display frames each video frame is held for.
        self.repeat = max(1, round(72.8 / video_fps))
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

        # Everything the vsync handler touches is resolved here, once.  It runs
        # as a hard IRQ, so it may not allocate: no keyword calls, no new
        # objects, only stores into things that already exist.
        #
        # `registers` is a 32-bit memoryview over the channel's register block:
        # 0 READ_ADDR, 1 WRITE_ADDR, 2 TRANS_COUNT, 3 CTRL_TRIG.  WRITE_ADDR is
        # set once and never moves again -- inc_write is off.
        self.regs = self.dma.registers
        self.regs[1] = PIO0_TXF0
        self.buf_addr = [addressof(b) for b in self.buffers]

        self.sm.active(1)

        # hard=True dispatches from the interrupt itself rather than deferring to
        # micropython.schedule(), which would not run until the main loop next
        # reached a bytecode boundary -- and service() reads 6240 bytes off flash
        # in one uninterruptible call.  Requires the handler above to be
        # allocation-free, which is what the precomputation buys.
        self.vsync_pin = Pin(VSYNC, Pin.IN)
        self.vsync_pin.irq(
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
        # DMA here is what keeps the two ends aligned -- and how long this takes
        # is directly how much of the 819 us head start survives.  Three register
        # writes, no method calls, nothing allocated:
        #
        #   READ_ADDR   <- top of the frame buffer; it advanced as we streamed
        #   TRANS_COUNT <- 6240; it decremented to zero
        #   CTRL_TRIG   <- the ctrl word.  Writing this register is the trigger,
        #                  so it must go last.
        #
        # Nothing is aborted first.  6240 bytes at 64 pixel clocks each is 12.68
        # of the frame's 13.73 ms, so the channel has been idle for about a
        # millisecond by the time this runs.
        regs = self.regs
        regs[0] = self.buf_addr[self.current]
        regs[2] = FRAME_BYTES
        regs[3] = self.dma_ctrl
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


def main(path="video.seg", video_fps=24.0):
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

    player = Player(path, video_fps, pixel_hz)
    try:
        while True:
            player.service()
    except KeyboardInterrupt:
        player.stop()


if __name__ == "__main__":
    main()
