"""
Bring the FPGA up in internal-generator mode, no streaming.

Note what this does to the DIP switches. In generator mode the chip reads
ui_in[6:1] as live switches (src/config_port.v), and this script drives
ui_in[3:0] as outputs to set the reset strap and then leaves them driven. So
the strapped palette is also the manual-mode palette, which is consistent, but
ui_in[6:4] are left floating unless the demoboard's switches are set: whatever
they settle at chooses manual-vs-auto palette and the two variation modes.
Sending a config packet -- `curve=` or `cycle=False` below -- clears the chip's
dip_live latch and freezes the switches at whatever was last applied, which is
usually what you want when driving the board from the RP2350.

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

# Edit these to change what the chip comes up as; main()'s arguments still
# override them for a one-off call.
PMOD_TYPE = 0  # 0 = Digilent PmodVGA, 1 = Tiny VGA
PALETTE = 3  # 0-7, one of the chip's built-in presets

MODE = 32  # uio[7] -- 1 selects streamed data, 0 the internal generator
STROBE = 31  # uio[6] -- stream strobe, carries config bytes while MODE is low
DATA_BASE = 17  # ui_in[0..7] -> GPIO17..24, see firmware/seg_player.py
PIXEL_HZ = 40_000_000  # required VGA pixel clock, 800x600@60


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


def main(pmod_type=PMOD_TYPE, palette=PALETTE, cycle=True, curve=None):
    """
    `pmod_type`/`palette` pick the reset-time strap: pmod_type 0=Digilent
    PmodVGA, 1=Tiny VGA; palette 0-7 selects one of the chip's built-in
    presets (tools/palette_builder/presets.json) to start from.

    `cycle` (the chip's own default) steps through all 8 presets every 1024
    frames, about 17 s, fading through black across each change. `curve`
    loads a custom palette instead, as three (x1, y1, x2, y2) point pairs for
    R, G, B -- palette_builder's Export prints a ready-made call; it always
    switches cycling off, because the next cycle step would overwrite it.
    Both go in as a config packet, see src/config_port.v.

    Either one also ends the chip's reading of the ui_in DIP switches for
    good -- see the note at the top of this file.
    """
    from ttboard.demoboard import DemoBoard
    import ttboard.fpga.fabricfoxv2 as fpgaloader

    tt = DemoBoard.get()
    tt.pins.safe_bidir()
    fpgaloader.spi_transferPIO("/bitstreams/tt_um_multi_seg_monitor.bin")

    pwm = tt.clock_project_PWM(PIXEL_HZ)

    # The strap register (src/config_port.v) only samples ui_in[3:0] while
    # rst_n is low, so this has to come after the clock
    # starts and pulse reset itself -- `mpremote run` execs one file with no
    # access to a sibling module, so this is repeated from seg_player.py
    # rather than imported.
    strap = (palette << 1) | pmod_type
    for i in range(4):
        Pin(DATA_BASE + i, Pin.OUT, value=(strap >> i) & 1)
    tt.reset_project(True)
    time.sleep_ms(1)  # comfortably more than one 40 MHz clock edge
    tt.reset_project(False)

    # Cycling is on out of reset, so only a change from that needs a packet.
    if curve is not None or not cycle:
        send_config(config_packet(curve, pmod_type, cycle and curve is None))

    Pin(MODE, Pin.OUT, value=0)
    print(
        f"generator mode, pmod_type={pmod_type} palette={palette} "
        f"cycle={cycle and curve is None} curve={curve}, "
        f"pixel clock {pwm.freq()} Hz"
    )


if __name__ == "__main__":
    main()
