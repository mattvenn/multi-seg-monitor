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

from machine import Pin

MODE = 32  # uio[7] -- 1 selects streamed data, 0 the internal generator
PIXEL_HZ = 40_000_000  # required VGA pixel clock, 800x600@60


def main():
    from ttboard.demoboard import DemoBoard
    import ttboard.fpga.fabricfoxv2 as fpgaloader

    tt = DemoBoard.get()
    tt.pins.safe_bidir()
    fpgaloader.spi_transferPIO("/bitstreams/tt_um_multi_seg_monitor.bin")

    pwm = tt.clock_project_PWM(PIXEL_HZ)
    Pin(MODE, Pin.OUT, value=0)
    print(f"generator mode, pixel clock {pwm.freq()} Hz")


if __name__ == "__main__":
    main()
