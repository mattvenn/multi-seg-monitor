`default_nettype none
//
// Line buffer -- one digit row, double buffered.
//
// Byte wide, single access per cycle, one cycle read latency.  This is the
// narrowest common denominator across the IHP/gf180/sky130 macros and the iCE40
// EBR (SPEC.md section 8.1), so the same RTL hardens for FPGA and silicon.
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
    output reg  [7:0]    rdata
    );

    reg [7:0] mem [0:(1<<AW)-1];

    always @(posedge clk) begin
        if (we)
            mem[waddr] <= wdata;
        if (re)
            rdata <= mem[raddr];
    end

endmodule
`default_nettype wire
