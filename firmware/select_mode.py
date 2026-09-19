"""
Choose the reset-time Pmod/palette strap, then reset the ASIC so it latches.

src/config_port.v samples ui_in[3:0] every cycle rst_n is low and freezes
it once rst_n rises -- pmod_type (ui_in[0]) picks Digilent PmodVGA (0) or
Tiny VGA (1), ui_in[3:1] picks one of the chip's 8 built-in palette presets
(tools/palette_builder/presets.json).

seg_player.py and gen_mode.py each inline their own copy of set_strap()'s
logic rather than importing it from here: `mpremote run <file>` execs a
single file with no access to a sibling module, so a cross-file import
breaks that workflow (confirmed the hard way -- ImportError on real
hardware). This file exists only as a standalone way to poke the strap on
its own, e.g. to check it latches without also starting the player.

The physical reset button is NOT a reliable way to select a mode: it leaves
ui_in[3:0] at whatever it happens to be, not a deliberate choice. Only a
firmware-driven reset (here, or seg_player.py/gen_mode.py's own copy)
guarantees the strap value.
"""

import time

from machine import Pin

DATA_BASE = 17  # ui_in[0..7] -> GPIO17..24, see firmware/seg_player.py
PIXEL_HZ = 40_000_000  # required VGA pixel clock, 800x600@60


def set_strap(tt, pmod_type=0, palette=0):
    """
    Drive ui_in[3:0] and pulse the project reset so the chip latches the
    strap. `tt` must already have its clock running
    (tt.clock_project_PWM(...)) -- the strap register only samples on
    posedge clk, so the clock has to be running before and through the
    reset pulse, not just after it.
    """
    if pmod_type not in (0, 1):
        raise ValueError("pmod_type must be 0 (Digilent PmodVGA) or 1 (Tiny VGA)")
    if not 0 <= palette <= 7:
        raise ValueError("palette must be 0-7")

    strap = (palette << 1) | pmod_type
    # Keep these pins actively driven, not released back to input, for the
    # whole call: the strap register re-samples every cycle rst_n is low, so
    # an early release risks the strap floating before reset_project(False)
    # actually raises rst_n. The pins stay in this configuration after
    # returning (MicroPython Pin objects don't need to stay referenced for
    # the GPIO state to hold), which is fine: once rst_n rises the chip never
    # reads them as a strap again, only as ordinary stream data.
    for i in range(4):
        Pin(DATA_BASE + i, Pin.OUT, value=(strap >> i) & 1)

    tt.reset_project(True)
    time.sleep_ms(1)  # comfortably more than one 40 MHz clock edge
    tt.reset_project(False)

    print(
        f"strap latched: pmod_type={pmod_type} "
        f"({'Tiny VGA' if pmod_type else 'Digilent PmodVGA'}), palette={palette}"
    )


def main(pmod_type=0, palette=0):
    """
    Stand-alone convenience for checking the strap on its own, without
    starting the player or generator:

        import select_mode
        select_mode.main(pmod_type=1, palette=2)   # Tiny VGA, palette 2

    seg_player.main() and gen_mode.main() inline this same logic themselves
    (see module docstring), so this is for manual poking only.
    """
    from ttboard.demoboard import DemoBoard
    import ttboard.fpga.fabricfoxv2 as fpgaloader

    tt = DemoBoard.get()
    tt.pins.safe_bidir()
    fpgaloader.spi_transferPIO("/bitstreams/tt_um_multi_seg_monitor.bin")
    pwm = tt.clock_project_PWM(PIXEL_HZ)
    set_strap(tt, pmod_type, palette)
    print(f"pixel clock {pwm.freq()} Hz")


if __name__ == "__main__":
    main()
