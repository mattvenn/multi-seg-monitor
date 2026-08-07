`default_nettype none
`timescale 1ns / 1ps

//
// Testbench wrapper.
//
// As well as instantiating the design, this captures a frame to a PPM so the
// geometry can be checked -- by assertion and by eye -- without hardware.  The
// capture is driven entirely from the output pins rather than from internal
// signals, so what lands in the file is what the pmod would see.
//
// Writing the file from Verilog rather than from cocotb matters: it lets the
// test advance a whole frame in a single ClockCycles await instead of 432640
// Python callbacks.
//
module tb ();

    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb.vcd");
            $dumpvars(0, tb);
        end
    end

    reg        clk;
    reg        rst_n;
    reg        ena;
    reg [7:0]  ui_in;
    reg [7:0]  uio_in;
    wire [7:0] uo_out;
    wire [7:0] uio_out;
    wire [7:0] uio_oe;

    // Raised from cocotb to request a capture of the next complete frame.
    reg        dump_en;

    tt_um_multi_seg_monitor user_project (
        .ui_in   (ui_in),
        .uo_out  (uo_out),
        .uio_in  (uio_in),
        .uio_out (uio_out),
        .uio_oe  (uio_oe),
        .ena     (ena),
        .clk     (clk),
        .rst_n   (rst_n)
    );

    // Tiny VGA pinout
    wire       hs   = uo_out[7];
    wire       vs   = uo_out[3];
    wire [1:0] px_r = {uo_out[0], uo_out[4]};
    wire [1:0] px_g = {uo_out[1], uo_out[5]};
    wire [1:0] px_b = {uo_out[2], uo_out[6]};

    // Pixel position measured from the sync edges, so the capture depends only
    // on what leaves the chip.  640x480@72: from the falling edge of hsync comes
    // 40 sync + 128 back porch = 168 before active video; from the falling edge
    // of vsync comes 3 sync + 28 back porch = 31 lines.
    //
    // H_LEAD is one less than that because detecting the edge costs this
    // testbench a cycle: tb_hc reaches 0 one cycle after hsync actually falls.
    // The vertical count is free of that because it advances on hsync edges,
    // which carry the same latency on both sides of the comparison.
    localparam H_LEAD = 168 - 1;
    localparam V_LEAD = 31;

    reg [10:0] tb_hc;
    reg [10:0] tb_vc;
    reg        hsync_d, vsync_d;
    // vsync falls partway through a line, and that line's own hsync edge follows
    // it.  Without this the first line would be counted twice.
    reg        vsync_seen;

    wire px_active = (tb_hc >= H_LEAD) && (tb_hc < H_LEAD + 640) &&
                     (tb_vc >= V_LEAD) && (tb_vc < V_LEAD + 480);

    integer ppm;
    reg     dumping;
    reg     dump_done;

    initial begin
        tb_hc      = 0;
        tb_vc      = 0;
        hsync_d    = 1'b1;
        vsync_d    = 1'b1;
        vsync_seen = 1'b0;
        dump_en   = 1'b0;
        dumping   = 1'b0;
        dump_done = 1'b0;
        ppm       = 0;
    end

    always @(posedge clk) begin
        hsync_d <= hs;
        vsync_d <= vs;

        if (hsync_d && !hs)
            tb_hc <= 0;
        else
            tb_hc <= tb_hc + 1'b1;

        if (vsync_d && !vs) begin
            tb_vc      <= 0;
            vsync_seen <= 1'b1;
        end else if (hsync_d && !hs) begin
            if (vsync_seen)
                vsync_seen <= 1'b0;
            else
                tb_vc <= tb_vc + 1'b1;
        end

        // Dropping dump_en arms the next capture, so more than one test can ask
        // for a frame in the same simulation.  The reset task does this, which is
        // why no test needs to lower it by hand.
        if (!dump_en)
            dump_done <= 1'b0;

        if (vsync_d && !vs) begin
            if (dump_en && !dumping && !dump_done) begin
                ppm = $fopen("frame.ppm", "w");
                $fwrite(ppm, "P3\n640 480\n255\n");
                dumping <= 1'b1;
            end else if (dumping) begin
                $fclose(ppm);
                ppm       = 0;
                dumping   <= 1'b0;
                dump_done <= 1'b1;
            end
        end

        // `ppm` is cleared with a blocking assignment while `dumping` clears with
        // a non-blocking one, so on the closing cycle dumping still reads high
        // against an already closed handle.  Test the handle, not just the flag.
        if (dumping && ppm != 0 && px_active)
            $fwrite(ppm, "%0d %0d %0d\n", px_r * 85, px_g * 85, px_b * 85);
    end

endmodule
`default_nettype wire
