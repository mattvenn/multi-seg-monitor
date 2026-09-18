"""
Bring the FPGA up in internal-generator mode, no streaming.

`seg_player.py`'s mode pin (uio[7]) can't be flipped on its own from a bare
`machine.Pin` call in a separate mpremote session: the bitstream push
(spi_transferPIO) and pixel clock (clock_project_PWM) don't survive between
separate RP2350 script runs, only within one. Skipping them left the FPGA
completely unconfigured -- "no signal", not a mode problem.

This does the same bring-up `seg_player.main()` does, minus creating a
Player, and leaves the mode pin low instead of high.
"""

import time

from machine import Pin

MODE = 32  # uio[7] -- 1 selects streamed data, 0 the internal generator
DATA_BASE = 17  # ui_in[0..7] -> GPIO17..24, see firmware/seg_player.py
PIXEL_HZ = 40_000_000  # required VGA pixel clock, 800x600@60


def main(pmod_type=1, palette=0):
    """
    `pmod_type`/`palette` pick the reset-time strap: pmod_type 0=Digilent
    PmodVGA (default), 1=Tiny VGA; palette 0-3 selects one of
    src/palette.v's colour palettes, applied in either mode. See
    src/tt_um_multi_seg_monitor.v.
    """
    from ttboard.demoboard import DemoBoard
    import ttboard.fpga.fabricfoxv2 as fpgaloader

    tt = DemoBoard.get()
    tt.pins.safe_bidir()
    fpgaloader.spi_transferPIO("/bitstreams/tt_um_multi_seg_monitor.bin")

    pwm = tt.clock_project_PWM(PIXEL_HZ)

    # The strap register (src/tt_um_multi_seg_monitor.v) only samples
    # ui_in[2:0] while rst_n is low, so this has to come after the clock
    # starts and pulse reset itself -- `mpremote run` execs one file with no
    # access to a sibling module, so this is inlined rather than imported
    # from select_mode.py (kept only as a standalone convenience for manual
    # `mpremote run firmware/select_mode.py`-style poking).
    strap = (palette << 1) | pmod_type
    for i in range(3):
        Pin(DATA_BASE + i, Pin.OUT, value=(strap >> i) & 1)
    tt.reset_project(True)
    time.sleep_ms(1)  # comfortably more than one 40 MHz clock edge
    tt.reset_project(False)

    Pin(MODE, Pin.OUT, value=0)
    print(
        f"generator mode, pmod_type={pmod_type} palette={palette}, "
        f"pixel clock {pwm.freq()} Hz"
    )


if __name__ == "__main__":
    main()
