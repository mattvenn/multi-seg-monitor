// Empty blackbox stub for the IHP foundry SRAM macro, for synthesis only.
//
// The PDK's own model cannot be used here: yosys cannot parse its `specify`
// block ("syntax error, unexpected ','" on the $setuphold calls, which use the
// empty-argument form), in any read_verilog mode.  Defining FUNCTIONAL gets it
// past the parser but then hands yosys a behavioural 1024x8 array to synthesise,
// which is the opposite of the point.
//
// So synthesis gets ports and nothing else, and the timing comes from the
// liberty files declared in src/config.json.  The real behavioural models live
// in test/models/ for simulation.  This mirrors tt_um_urish_sram_test, which
// taped out on ttihp0p2 with the same split.
//
// Ports must stay in step with the macro: 1024 x 8, single port, plus BIST.
//
module RM_IHPSG13_1P_1024x8_c2_bm_bist (
    input A_CLK,
    input A_MEN,
    input A_WEN,
    input A_REN,
    input [9:0] A_ADDR,
    input [7:0] A_DIN,
    input A_DLY,
    output [7:0] A_DOUT,
    input [7:0] A_BM,
    input A_BIST_CLK,
    input A_BIST_EN,
    input A_BIST_MEN,
    input A_BIST_WEN,
    input A_BIST_REN,
    input [9:0] A_BIST_ADDR,
    input [7:0] A_BIST_DIN,
    input [7:0] A_BIST_BM
);

endmodule
