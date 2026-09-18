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
// test advance a whole frame in a single ClockCycles await instead of 663168
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

    // Standalone palette instance for test_palette.py, separate from the one
    // inside multi_seg_monitor.v: that instance's sel/idx are wires driven by
    // real rendering logic, not freely settable from cocotb, so the palette
    // distinctness/RTL-vs-Python checks need ports a testbench can actually
    // drive. Parallel to the real design, touches nothing in it.
    reg  [1:0] dbg_pal_sel;
    reg  [3:0] dbg_pal_idx;
    wire [3:0] dbg_pal_r, dbg_pal_g, dbg_pal_b;

    // RTL-only: the gate-level netlist is flattened to one module, so there is
    // no `palette` to instantiate under GL_TEST, and test_palette_matches_
    // python_table skips itself there (it checks the RTL table, not gates).
`ifndef GL_TEST
    palette dbg_pal (
        .sel (dbg_pal_sel),
        .idx (dbg_pal_idx),
        .r   (dbg_pal_r),
        .g   (dbg_pal_g),
        .b   (dbg_pal_b)
    );
`endif

    // Pixel capture, muxed on the reset-time strap (src/tt_um_multi_seg_monitor.v).
    // Digilent PmodVGA (4 bits/channel) spans uo_out and uio; Tiny VGA
    // (2 bits/channel) is uo_out only with a different bit order -- see that
    // file for both. The testbench latches ui_in[0] itself, the same way the
    // chip does (resampled every cycle rst_n is low), rather than reaching
    // into user_project for the strap: the gate-level netlist has no
    // `pmod_type` net to reach, and this way the Tiny VGA gold image runs
    // under GL_TEST too, still depending only on pins.
    reg capture_tiny = 1'b0;
    always @(posedge clk)
        if (!rst_n)
            capture_tiny <= ui_in[0];

    wire hs = capture_tiny ? uo_out[7] : uio_out[4];
    wire vs = capture_tiny ? uo_out[3] : uio_out[5];

    // Digilent: a full 4-bit nibble per channel (16 levels), scaled by 17 to
    // fill a byte. Tiny VGA: only 2 bits per channel (4 levels) live at
    // uo_out[0]/[4] (R), [1]/[5] (G), [2]/[6] (B) -- see
    // src/tt_um_multi_seg_monitor.v for the bit order -- so px_* holds just
    // that 2-bit code and the scale below is 85, not 17, to still fill a
    // byte (3*85 == 255).
    wire [3:0] px_r = capture_tiny ? {2'b0, uo_out[0], uo_out[4]} : uo_out[3:0];
    wire [3:0] px_g = capture_tiny ? {2'b0, uo_out[1], uo_out[5]} : uio_out[3:0];
    wire [3:0] px_b = capture_tiny ? {2'b0, uo_out[2], uo_out[6]} : uo_out[7:4];
    wire [7:0] px_scale = capture_tiny ? 8'd85 : 8'd17;

    // Pixel position measured from the sync edges, so the capture depends only
    // on what leaves the chip.  800x600@60: from the falling edge of hsync comes
    // 128 sync + 88 back porch = 216 before active video; from the falling edge
    // of vsync comes 4 sync + 23 back porch = 27 lines.
    //
    // H_LEAD is one less than that because detecting the edge costs this
    // testbench a cycle: tb_hc reaches 0 one cycle after hsync actually falls.
    // The vertical count is free of that because it advances on hsync edges,
    // which carry the same latency on both sides of the comparison.
    localparam H_LEAD = 216 - 1;
    localparam V_LEAD = 27;

    reg [10:0] tb_hc;
    reg [10:0] tb_vc;
    reg        hsync_d, vsync_d;
    // vsync falls partway through a line, and that line's own hsync edge follows
    // it.  Without this the first line would be counted twice.
    reg        vsync_seen;

    wire px_active = (tb_hc >= H_LEAD) && (tb_hc < H_LEAD + 800) &&
                     (tb_vc >= V_LEAD) && (tb_vc < V_LEAD + 600);

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
                $fwrite(ppm, "P3\n800 600\n255\n");
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
            $fwrite(ppm, "%0d %0d %0d\n", px_r * px_scale, px_g * px_scale, px_b * px_scale);
    end

    // SPEC.md section 8.1: the line buffer is single port on silicon, so `we`
    // and `re` must never be high in the same cycle.  The inferred array used
    // for simulation and FPGA does both happily, which is exactly the way an
    // FPGA can pass where an ASIC would fail -- the IHP macro reads MEN+WEN+REN
    // as write-through and would put wdata at raddr, silently corrupting a byte
    // on its way to the screen.  Check it on every edge rather than trusting the
    // arbitration upstream to stay correct.
    //
    // Reaches into the hierarchy, so it cannot survive synthesis: the gate level
    // netlist has no `core.lb`.  The property is about the RTL's arbitration
    // anyway, and if it holds there it holds in the netlist.
`ifndef GL_TEST
    always @(posedge clk) begin
        if (rst_n && user_project.core.lb.we && user_project.core.lb.re) begin
            $display("ERROR: line buffer read and write in the same cycle at %0t", $time);
            $fatal(1);
        end
    end
`endif

endmodule
`default_nettype wire
