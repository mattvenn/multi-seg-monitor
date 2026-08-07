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

    assign uio_out = 8'b0;
    assign uio_oe  = 8'b0;

    wire [5:0] level;
    wire hsync, vsync;

    // Prototype output: Tiny VGA pmod, https://github.com/mole99/tiny-vga
    // The native 6 bit intensity is truncated to the pmod's 2 bits per channel and
    // replicated across R, G and B to give grey.  This gives 4 levels rather than
    // 64 -- enough to prove geometry and streaming, but not the fading that
    // per-segment brightness exists for.  See SPEC.md section 5.
    wire [1:0] grey = level[5:4];

    assign uo_out[0] = grey[1];  // R1
    assign uo_out[1] = grey[1];  // G1
    assign uo_out[2] = grey[1];  // B1
    assign uo_out[3] = vsync;
    assign uo_out[4] = grey[0];  // R0
    assign uo_out[5] = grey[0];  // G0
    assign uo_out[6] = grey[0];  // B0
    assign uo_out[7] = hsync;

    // Stream port: a byte on ui_in, strobed by uio[0].  uio[1] selects between the
    // internal generator and streamed data.  See SPEC.md section 7.
    multi_seg_monitor core (
        .clk         (clk),
        .rst_n       (rst_n),
        .stream_data (ui_in),
        .stream_stb  (uio_in[0]),
        .stream_mode (uio_in[1]),
        .hsync       (hsync),
        .vsync       (vsync),
        .level       (level)
    );

    // verilator lint_off UNUSEDSIGNAL
    wire _unused = &{ena, uio_in[7:2], 1'b0};
    // verilator lint_on UNUSEDSIGNAL

endmodule
`default_nettype wire
