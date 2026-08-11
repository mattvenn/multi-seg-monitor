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
    parameter hpixels = blackH + activeHvideo;  // Total horizontal pixels.
    parameter vlines = blackV + activeVvideo;   // Total lines.

    // Registers for storing the horizontal & vertical counters. hc is 11 bits
    // because hpixels can exceed the 10-bit range that used to be enough when
    // this only ever ran 640x480 (832 total) -- e.g. 800x600's 1056 total needs
    // it. vc stays 10 bits; every mode built so far keeps vlines under 1024.
    reg [10:0] hc;
    reg [9:0]  vc;

    // Counting pixels.
    always @(posedge px_clk)
    begin
        if(reset) begin
            hc <= 0;
            vc <= 0;
        end else begin
            // Keep counting until the end of the line.
            if (hc < hpixels - 1)
                hc <= hc + 1;
            else
            // When we hit the end of the line, reset the horizontal
            // counter and increment the vertical counter.
            // If vertical counter is at the end of the frame, then
            // reset that one too.
            begin
                hc <= 0;
                if (vc < vlines - 1)
                vc <= vc + 1;
            else
               vc <= 0;
            end
        end
     end

    // Generate sync pulses (active low) and active video.
    assign hsync = (hc >= hfp && hc < hfp + hpulse) ? 0:1;
    assign vsync = (vc >= vfp && vc < vfp + vpulse) ? 0:1;
    assign activevideo = (hc >= blackH && vc >= blackV) ? 1:0;

    // Generate color.
    always @(posedge px_clk)
    begin
        if(reset) begin
            x_px <= 0;
            y_px <= 0;
        end else begin
            x_px <= hc - blackH;
            y_px <= vc - blackV;
        end
     end
 endmodule
`default_nettype wire
