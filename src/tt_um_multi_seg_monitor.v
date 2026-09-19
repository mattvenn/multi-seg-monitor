`default_nettype none
module tt_um_multi_seg_monitor (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered, so you can ignore it
    input  wire       clk,      // clock
    input  wire       rst_n     // reset_n - low to reset
);

    wire [3:0] r, g, b;
    wire hsync, vsync;

    // Which Pmod the pins are laid out for. Chosen by a reset-time strap on
    // ui_in[0] and optionally changed later by a config packet; both live in
    // the core, alongside the palette settings they share a packet with --
    // see src/config_port.v. Everything below is pure pin mapping on top of it.
    wire pmod_type;

    // Digilent PmodVGA (4 bits/channel), spanning uo_out and the low 6 bits
    // of uio -- R/B on uo_out, G/HS/VS on uio, matching how the board's two
    // 6-pin connectors land when plugged straight across both TT headers
    // with no rewiring. See SPEC.md section 5.
    //
    // uo_out: R0 R1 R2 R3 B0 B1 B2 B3
    // uio:    G0 G1 G2 G3 HS VS strobe mode-select
    //
    // This moves the stream strobe/mode-select pins from uio[0:1] to uio[6:7]
    // -- PmodVGA leaves those two not-connected, which is what freed them up
    // for this. uio[5:0] are outputs (video) in this mode, uio[7:6] stay
    // inputs (stream control).
    wire [7:0] uo_digilent  = {b, r};
    wire [7:0] uio_digilent = {2'b0, vsync, hsync, g};
    wire [7:0] oe_digilent  = 8'b0011_1111;

    // Tiny Tapeout VGA Pmod (2 bits/channel, uo_out only): this chip's
    // original output mode in its very first commit, removed when the design
    // switched to PmodVGA -- reconstructed here from that history rather than
    // designed fresh. uio carries no video at all in this mode (Tiny VGA is a
    // single 8-pin connector), so strobe/mode-select stay on uio[6:7] exactly
    // as in Digilent mode -- there is no need to relocate them, and
    // firmware/seg_player.py's GPIO map does not need to fork on pmod_type.
    // uio_oe goes fully input rather than partially driving pins nothing on
    // this Pmod expects to be outputs -- a real electrical hazard on
    // hardware if left wrong, not just a simulation nicety, since a user who
    // has chosen Tiny VGA has very plausibly wired uio to something else
    // entirely on the header this Pmod doesn't use.
    //
    // Only the top 2 bits of each of the core's 4-bit channels reach a pin;
    // that costs levels -- 4 for grey, 7-9 for the tinted palettes, see
    // palette.v. Bit order re-derived from d7fee74 above,
    // not copied from a paraphrase: uo_out[0]=R1=r[3] (the channel's MSB,
    // matching that commit's grey[1] -> R1), uo_out[4]=R0=r[2], and so on
    // for G/B -- i.e. uo_out[7:0] = {hsync,B0,G0,R0,vsync,B1,G1,R1}.
    wire [7:0] uo_tinyvga  = {hsync, b[2], g[2], r[2], vsync, b[3], g[3], r[3]};
    wire [7:0] uio_tinyvga = 8'b0;
    wire [7:0] oe_tinyvga  = 8'b0;

    assign uo_out  = pmod_type ? uo_tinyvga  : uo_digilent;
    assign uio_out = pmod_type ? uio_tinyvga : uio_digilent;
    assign uio_oe  = pmod_type ? oe_tinyvga  : oe_digilent;

    // Stream port: a byte on ui_in, strobed by uio[6].  uio[7] selects between the
    // internal generator and streamed data.  See SPEC.md section 7.
    multi_seg_monitor core (
        .clk         (clk),
        .rst_n       (rst_n),
        .stream_data (ui_in),
        .stream_stb  (uio_in[6]),
        .stream_mode (uio_in[7]),
        .pmod_type   (pmod_type),
        .hsync       (hsync),
        .vsync       (vsync),
        .r           (r),
        .g           (g),
        .b           (b)
    );

    // verilator lint_off UNUSEDSIGNAL
    wire _unused = &{ena, 1'b0};
    // verilator lint_on UNUSEDSIGNAL

endmodule
`default_nettype wire
