`default_nettype none
//
// Stream input port.
//
// The host puts a byte on ui_in and pulses the strobe.  Bytes fill the line
// buffer in order: byte n of the frame is digit n/4, nibble pair n%4.  The
// pointer resets on vsync, so the link is self-synchronising -- a lost or extra
// byte costs one frame and then corrects itself, rather than corrupting the
// display until someone rewrites it.
//
// Only the low two bits of the row counter reach the address, because the buffer
// holds four rows.  Staying in step is the host's job, but four buffers make that
// easy: it needs to run between one and three rows ahead of the raster, which a
// fixed byte rate restarted each frame achieves without tracking rows at all.
// See SPEC.md section 4.3.
//
module stream_in #(
    parameter ROW_BYTES = 208,
    parameter ROWS      = 30
    )
    (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] data,         // ui_in
    input  wire       strobe,       // asynchronous, from the host
    input  wire       frame_start,  // vsync edge
    output reg        req,          // a byte is waiting to be written
    input  wire       grant,
    output wire [9:0] waddr,
    output wire [7:0] wdata
    );

    // The strobe crosses from the host's clock domain.  Two flops to synchronise
    // and a third to find the edge.  At roughly 64 pixel clocks per strobe there
    // is no shortage of time for this.
    reg [2:0] sync;
    always @(posedge clk)
        sync <= {sync[1:0], strobe};

    wire strobe_rise = sync[1] && !sync[2];

    reg [7:0] hold;
    reg [4:0] s_row;
    reg [7:0] s_byte;

    always @(posedge clk) begin
        if (!rst_n || frame_start) begin
            s_row  <= 0;
            s_byte <= 0;
            req    <= 1'b0;
        end else begin
            if (grant) begin
                req <= 1'b0;
                if (s_byte == ROW_BYTES - 1) begin
                    s_byte <= 0;
                    s_row  <= (s_row == ROWS - 1) ? 5'd0 : s_row + 1'b1;
                end else begin
                    s_byte <= s_byte + 1'b1;
                end
            end
            // A strobe arriving in the same cycle as a grant is fine and must not
            // drop the byte: wdata still carries the old hold value for this
            // cycle's write, and req stays set for the new one.
            if (strobe_rise) begin
                hold <= data;
                req  <= 1'b1;
            end
        end
    end

    assign waddr = {s_row[1:0], s_byte};
    assign wdata = hold;

endmodule
`default_nettype wire
