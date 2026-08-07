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

    wire [5:0] level;
    wire hsync, vsync;

    // Prototype output: Digilent PmodVGA (4 bits/channel), spanning uo_out and the
    // low 6 bits of uio -- R/B on uo_out, G/HS/VS on uio, matching how the board's
    // two 6-pin connectors land when plugged straight across both TT headers with
    // no rewiring.  The native 6 bit intensity is truncated to 4 bits and
    // replicated across R, G and B to give grey: 16 levels rather than the Tiny
    // VGA prototype's 4, still short of the native 64 the custom ladder Pmod would
    // give.  See SPEC.md section 5.
    //
    // uo_out: R0 R1 R2 R3 B0 B1 B2 B3
    // uio:    G0 G1 G2 G3 HS VS strobe mode-select
    //
    // This moves the stream strobe/mode-select pins from uio[0:1] to uio[6:7] --
    // PmodVGA leaves those two not-connected, which is what freed them up for
    // this. uio[5:0] are now outputs (video), uio[7:6] stay inputs (stream
    // control), so uio_oe is no longer all-input.
    wire [3:0] grey = level[5:2];

    assign uo_out[3:0] = grey;  // R0-R3
    assign uo_out[7:4] = grey;  // B0-B3

    assign uio_out[3:0] = grey;          // G0-G3
    assign uio_out[4]   = hsync;
    assign uio_out[5]   = vsync;
    assign uio_out[7:6] = 2'b0;          // don't-care: uio[7:6] are inputs
    assign uio_oe       = 8'b0011_1111;  // uio[5:0] out (video), uio[7:6] in (stream ctrl)

    // Stream port: a byte on ui_in, strobed by uio[6].  uio[7] selects between the
    // internal generator and streamed data.  See SPEC.md section 7.
    multi_seg_monitor core (
        .clk         (clk),
        .rst_n       (rst_n),
        .stream_data (ui_in),
        .stream_stb  (uio_in[6]),
        .stream_mode (uio_in[7]),
        .hsync       (hsync),
        .vsync       (vsync),
        .level       (level)
    );

    // verilator lint_off UNUSEDSIGNAL
    wire _unused = &{ena, 1'b0};
    // verilator lint_on UNUSEDSIGNAL

endmodule
`default_nettype wire
