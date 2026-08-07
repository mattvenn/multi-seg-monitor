`default_nettype none
//
// Line buffer -- four digit rows, single port, byte wide, one cycle read
// latency.  This is the narrowest common denominator across the IHP/gf180/sky130
// macros and the iCE40 EBR (SPEC.md section 8.1), so the same RTL hardens for
// FPGA and silicon.
//
// Separate read and write addresses are exposed because that is what infers an
// EBR cleanly, but the caller guarantees `we` and `re` are never both high in the
// same cycle: the renderer reads only during active cells and the source writes
// only during horizontal blanking.  That guarantee is what lets a single-port
// ASIC macro stand in here behind an address mux, and it is asserted in the
// testbench rather than left to chance.
//
// Contents are undefined at power-up on an ASIC, so nothing may depend on
// initialisation -- the buffer is fully written before it is first displayed.
//
// Two implementations sit behind this one interface.  The default infers an
// array, which maps to an iCE40 EBR and simulates anywhere.  `IHP_SRAM` swaps in
// the sg13g2 macro for the ASIC.  Nothing above this module changes between them.
//
module line_buffer #(
    parameter AW = 9
    )
    (
    input  wire          clk,
    input  wire          we,
    input  wire [AW-1:0] waddr,
    input  wire [7:0]    wdata,
    input  wire          re,
    input  wire [AW-1:0] raddr,
    output wire [7:0]    rdata
    );

`ifdef IHP_SRAM

    // RM_IHPSG13_1P_1024x8_c2_bm_bist -- 1024 x 8, one port, one cycle data
    // access, 336.46 x 146.88 um.  Every control pin is active high and sampled
    // on the rising edge, and A_DOUT is already registered, so the macro lands
    // exactly where the inferred array was with no extra pipeline stage and no
    // change to the fetch timing in multi_seg_monitor.
    //
    // 1024 words is the whole address space here, so this branch requires
    // AW = 10.  multi_seg_monitor passes it explicitly; there is no other
    // instantiation to keep in step.
    //
    // Pin-for-pin against tt_um_urish_sram_test, the same macro on ttihp0p2 and
    // the only silicon-proven use of it (SRAM.csv).  A_DLY high, A_BM all ones
    // and all eight BIST pins tied low are that design's settings, not just the
    // datasheet's recommendations.
    //
    // The one divergence is A_MEN.  That design holds it at rst_n, so the macro
    // is enabled every cycle after reset; here it rises only for an actual
    // access, which is 4 reads in every 12 cycles plus scattered writes rather
    // than 100% duty.  Legal either way -- MEN low is "No Operation" in the
    // datasheet's operation table -- and it saves array power, but it does mean
    // the enable toggles in a pattern the shuttle has not yet run.  Tie A_MEN
    // high instead if that is ever worth trading power for.
    //
    // One port, two callers, so the address is muxed with reads taking priority
    // -- which is the arbitration multi_seg_monitor already implements upstream
    // (`wr_grant = !lb_re`).  we and re are never both high, so this mux never
    // actually has to choose.  If it ever did, MEN+WEN+REN is the macro's
    // write-through mode: it would write wdata to raddr, not to waddr, and
    // return it.  That is a silent corruption of a byte the renderer is about to
    // display, which is why tb.v asserts the exclusion rather than trusting it.
    wire [9:0] addr = re ? raddr : waddr;

    RM_IHPSG13_1P_1024x8_c2_bm_bist u_sram (
        .A_CLK       (clk),
        .A_MEN       (re | we),      // memory enable: high for any access
        .A_WEN       (we),
        .A_REN       (re),
        .A_ADDR      (addr),
        .A_DIN       (wdata),
        .A_BM        (8'hff),        // write every bit; the port is one byte wide
        .A_DLY       (1'b1),         // datasheet: "recommended setting: Tie to 1"
        .A_DOUT      (rdata),

        // BIST is unused.  A_BIST_EN low leaves the functional port in control;
        // the rest are tied off rather than left dangling so no X can reach the
        // BIST mux inside the macro.
        .A_BIST_CLK  (1'b0),
        .A_BIST_EN   (1'b0),
        .A_BIST_MEN  (1'b0),
        .A_BIST_WEN  (1'b0),
        .A_BIST_REN  (1'b0),
        .A_BIST_ADDR (10'b0),
        .A_BIST_DIN  (8'b0),
        .A_BIST_BM   (8'b0)
    );

`else

    reg [7:0] mem [0:(1<<AW)-1];
    reg [7:0] rdata_q;

    always @(posedge clk) begin
        if (we)
            mem[waddr] <= wdata;
        if (re)
            rdata_q <= mem[raddr];
    end

    assign rdata = rdata_q;

`endif

endmodule
`default_nettype wire
