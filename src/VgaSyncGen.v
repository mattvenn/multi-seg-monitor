`default_nettype none
//////////////////////////////////////////////////////////////////////////////////
// Company: Ridotech
// Engineer: Juan Manuel Rico
// 
// Create Date:    09:34:23 30/09/2017 
// Module Name:    vga_controller
// Description:    Basic control for 800x600@60Hz VGA signal.
//
// Dependencies: 
//
// Revision: 
// Revision 0.01 - File Created for Roland Coeurjoly (RCoeurjoly) in 640x480@85Hz.
// Revision 0.02 - Change for 640x480@60Hz.
// Revision 0.03 - Solved some mistakes.
// Revision 0.04 - Change for 640x480@72Hz and output signals 'activevideo'
//                 and 'px_clk'.
//
// Additional Comments: 
//
//////////////////////////////////////////////////////////////////////////////////
module VgaSyncGen (
            input wire       px_clk,        // Input clock: 40MHz
            input wire       reset,         // reset
            output wire       hsync,        // Horizontal sync out
            output wire       vsync,        // Vertical sync out
            output reg [10:0] x_px,         // X position for actual pixel.
            output reg [9:0]  y_px,         // Y position for actual pixel.
            output wire       activevideo
         );

    /*
    VESA DMT 800x600@60 -- the same standard the previous 640x480@72 mode used.
    PIXEL_CLK   =   40000
    H_DISP      =   800
    V_DISP      =   600
    H_FPORCH    =   40
    H_SYNC      =   128
    H_BPORCH    =   88
    V_FPORCH    =   1
    V_SYNC      =   4
    V_BPORCH    =   23
    */

    // Video structure constants.
    parameter activeHvideo = 800;               // Width of visible pixels.
    parameter activeVvideo =  600;              // Height of visible lines.
    parameter hfp = 40;                         // Horizontal front porch length.
    parameter hpulse = 128;                     // Hsync pulse length.
    parameter hbp = 88;                         // Horizontal back porch length.
    parameter vfp = 1;                          // Vertical front porch length.
    parameter vpulse = 4;                       // Vsync pulse length.
    parameter vbp = 23;                         // Vertical back porch length.
    parameter blackH = hfp + hpulse + hbp;      // Hide pixels in one line.
    parameter blackV = vfp + vpulse + vbp;      // Hide lines in one frame.

    // No separate hc/vc counters: x_px and y_px are the counters. They start
    // at -blackH/-blackV and count up through the blanking interval to 0 at the
    // first visible pixel/line, so they read as screen coordinates directly
    // and underflow to large values during blanking, which is what the core's
    // range checks rely on. This used to be hc/vc plus a registered copy of
    // each, less blackH/blackV -- 21 flops (about 1.3k um^2 synthesised on
    // IHP) spent holding the same count twice.
    //
    // The registered copy also ran one cycle behind the counter the syncs were
    // decoded from. The syncs are now decoded from the coordinates, so they
    // come out one cycle later than they used to; multi_seg_monitor.v's sync
    // pipeline is one flop shorter to match, and every pin still moves on the
    // same cycle as before.
    //
    // 11 bits for x because a 1056 pixel line (blackH + activeHvideo) won't
    // fit in 10; y stays 10 bits (628 lines).
    localparam integer xStartInt = 2048 - blackH;
    localparam integer yStartInt = 1024 - blackV;
    localparam [10:0] xStart = xStartInt[10:0];  // -blackH, 11 bits
    localparam [9:0]  yStart = yStartInt[9:0];   // -blackV, 10 bits

    always @(posedge px_clk)
    begin
        if (reset) begin
            x_px <= xStart;
            y_px <= yStart;
        end else if (x_px == activeHvideo - 1) begin
            // End of the line: back to the start of horizontal blanking, and
            // on to the next line (or back to the top of the frame).
            x_px <= xStart;
            if (y_px == activeVvideo - 1)
                y_px <= yStart;
            else
                y_px <= y_px + 1;
        end else begin
            x_px <= x_px + 1;
        end
    end

    // Generate sync pulses (active low) and active video. Front porch, then
    // sync, then back porch, all counted from the start of blanking.
    assign hsync = (x_px >= xStart + hfp && x_px < xStart + hfp + hpulse) ? 0:1;
    assign vsync = (y_px >= yStart + vfp && y_px < yStart + vfp + vpulse) ? 0:1;
    assign activevideo = (x_px < activeHvideo && y_px < activeVvideo) ? 1:0;

`ifdef FORMAL
    // `read_verilog -formal` (see formal/) defines FORMAL in place of
    // SYNTHESIS, so none of this reaches synthesis or ordinary simulation.

    // x_px/y_px are undefined pre-reset like any other register here, so the
    // bound only has to hold once a real reset has actually happened --
    // k-induction otherwise explores states that just power up already
    // out of range, which reset never claimed to rule out.
    reg f_reset_done = 1'b0;
    always @(posedge px_clk)
        if (reset)
            f_reset_done <= 1'b1;

    // The wraparound compares (x_px, y_px == last visible pixel/line) are the
    // only thing standing between this counter and walking off the end of the
    // line/frame -- exactly the kind of off-by-one that changed silently
    // between the 640x480 and 800x600 modes (resolution_discussion.md
    // section 11). k-induction, not full BMC replay: the invariant only
    // needs one step to re-establish itself, not a full frame of history.
    always @(posedge px_clk)
        if (f_reset_done) begin
            assert (x_px < activeHvideo || x_px >= xStart);
            assert (y_px < activeVvideo || y_px >= yStart);
        end
`endif

 endmodule
`default_nettype wire
