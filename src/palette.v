`default_nettype none
//
// Colour palette: 4 bit stored intensity + 2 bit select -> 4 bit R/G/B.
//
// There used to be a single-channel gamma LUT here (src/gamma.v, removed): the
// PmodVGA output is a hard 4-bit DAC, 16 codes in and 16 codes out, and any
// monotonic curve reshaping 16 values into 16 values is forced by pigeonhole
// into being the identity -- a non-trivial curve can only ever collide two
// stored indices onto the same code, which is what made indices 14 and 15
// genuinely indistinguishable on hardware (dithering_investigation.md on the
// gamma-dithering branch). The tables below are generated from
// tools/segments.py's PALETTES (designed in tools/palette_builder), which is
// the source of truth; test_palette_matches_python_table in
// test/test_multi_seg.py fails if this file drifts from it, and
// tools/test_palettes.py checks the rules on the Python side:
//   1. Entry 0 is (0, 0, 0) -- index 0 is used both for an explicitly-zeroed
//      segment and every non-segment background/margin pixel
//      (multi_seg_monitor.v's `visible`), so a palette that colours it would
//      tint the whole background, not just dim a segment.
//   2. Brightness never decreases as the index rises.
//   3. Entries 1-15 are pairwise distinct.
//
// Palette 0 is the plain grey ramp kept byte-identical to the old
// direct-to-DAC mapping, so existing gold images need no changes. Palettes
// 1-3 are tinted ramps whose entry 1 is black on purpose, so stored level 1
// looks the same as off (15 distinct levels, not 16).
//
// Tiny VGA mode keeps only each channel's top 2 bits
// (src/tt_um_multi_seg_monitor.v), and a smooth single-hue ramp cannot stay
// distinct through that: 4 levels for grey, 7-9 for the tinted palettes.
//
// Synthesised as logic on both ASIC and FPGA rather than living in memory --
// keeping it out of a RAM removes one more way the two platforms could
// diverge (SPEC.md section 2.1), same reasoning the removed gamma.v used.
//
module palette (
    input  wire [1:0] sel,
    input  wire [3:0] idx,
    output reg  [3:0] r,
    output reg  [3:0] g,
    output reg  [3:0] b
    );

    always @* begin
        case (sel)
            // palette 0: grey, r=g=b=idx
            2'd0: case (idx)
                4'd0 : {r, g, b} = {4'd0, 4'd0, 4'd0};
                4'd1 : {r, g, b} = {4'd1, 4'd1, 4'd1};
                4'd2 : {r, g, b} = {4'd2, 4'd2, 4'd2};
                4'd3 : {r, g, b} = {4'd3, 4'd3, 4'd3};
                4'd4 : {r, g, b} = {4'd4, 4'd4, 4'd4};
                4'd5 : {r, g, b} = {4'd5, 4'd5, 4'd5};
                4'd6 : {r, g, b} = {4'd6, 4'd6, 4'd6};
                4'd7 : {r, g, b} = {4'd7, 4'd7, 4'd7};
                4'd8 : {r, g, b} = {4'd8, 4'd8, 4'd8};
                4'd9 : {r, g, b} = {4'd9, 4'd9, 4'd9};
                4'd10: {r, g, b} = {4'd10, 4'd10, 4'd10};
                4'd11: {r, g, b} = {4'd11, 4'd11, 4'd11};
                4'd12: {r, g, b} = {4'd12, 4'd12, 4'd12};
                4'd13: {r, g, b} = {4'd13, 4'd13, 4'd13};
                4'd14: {r, g, b} = {4'd14, 4'd14, 4'd14};
                4'd15: {r, g, b} = {4'd15, 4'd15, 4'd15};
            endcase
            // palette 1: blue
            2'd1: case (idx)
                4'd0 : {r, g, b} = {4'd0, 4'd0, 4'd0};
                4'd1 : {r, g, b} = {4'd0, 4'd0, 4'd0};
                4'd2 : {r, g, b} = {4'd0, 4'd1, 4'd2};
                4'd3 : {r, g, b} = {4'd0, 4'd2, 4'd4};
                4'd4 : {r, g, b} = {4'd0, 4'd3, 4'd6};
                4'd5 : {r, g, b} = {4'd0, 4'd4, 4'd9};
                4'd6 : {r, g, b} = {4'd0, 4'd5, 4'd11};
                4'd7 : {r, g, b} = {4'd0, 4'd6, 4'd13};
                4'd8 : {r, g, b} = {4'd0, 4'd8, 4'd15};
                4'd9 : {r, g, b} = {4'd2, 4'd9, 4'd15};
                4'd10: {r, g, b} = {4'd4, 4'd10, 4'd15};
                4'd11: {r, g, b} = {4'd6, 4'd11, 4'd15};
                4'd12: {r, g, b} = {4'd9, 4'd12, 4'd15};
                4'd13: {r, g, b} = {4'd11, 4'd13, 4'd15};
                4'd14: {r, g, b} = {4'd13, 4'd14, 4'd15};
                4'd15: {r, g, b} = {4'd15, 4'd15, 4'd15};
            endcase
            // palette 2: green
            2'd2: case (idx)
                4'd0 : {r, g, b} = {4'd0, 4'd0, 4'd0};
                4'd1 : {r, g, b} = {4'd0, 4'd0, 4'd0};
                4'd2 : {r, g, b} = {4'd0, 4'd2, 4'd1};
                4'd3 : {r, g, b} = {4'd0, 4'd4, 4'd3};
                4'd4 : {r, g, b} = {4'd0, 4'd6, 4'd4};
                4'd5 : {r, g, b} = {4'd0, 4'd8, 4'd5};
                4'd6 : {r, g, b} = {4'd0, 4'd10, 4'd7};
                4'd7 : {r, g, b} = {4'd0, 4'd12, 4'd8};
                4'd8 : {r, g, b} = {4'd0, 4'd14, 4'd9};
                4'd9 : {r, g, b} = {4'd2, 4'd14, 4'd10};
                4'd10: {r, g, b} = {4'd4, 4'd14, 4'd11};
                4'd11: {r, g, b} = {4'd6, 4'd14, 4'd12};
                4'd12: {r, g, b} = {4'd9, 4'd15, 4'd13};
                4'd13: {r, g, b} = {4'd11, 4'd15, 4'd13};
                4'd14: {r, g, b} = {4'd13, 4'd15, 4'd14};
                4'd15: {r, g, b} = {4'd15, 4'd15, 4'd15};
            endcase
            // palette 3: purple
            2'd3: case (idx)
                4'd0 : {r, g, b} = {4'd0, 4'd0, 4'd0};
                4'd1 : {r, g, b} = {4'd0, 4'd0, 4'd0};
                4'd2 : {r, g, b} = {4'd2, 4'd0, 4'd2};
                4'd3 : {r, g, b} = {4'd4, 4'd0, 4'd4};
                4'd4 : {r, g, b} = {4'd6, 4'd0, 4'd6};
                4'd5 : {r, g, b} = {4'd9, 4'd0, 4'd9};
                4'd6 : {r, g, b} = {4'd11, 4'd0, 4'd11};
                4'd7 : {r, g, b} = {4'd13, 4'd0, 4'd13};
                4'd8 : {r, g, b} = {4'd15, 4'd0, 4'd15};
                4'd9 : {r, g, b} = {4'd15, 4'd2, 4'd15};
                4'd10: {r, g, b} = {4'd15, 4'd4, 4'd15};
                4'd11: {r, g, b} = {4'd15, 4'd6, 4'd15};
                4'd12: {r, g, b} = {4'd15, 4'd9, 4'd15};
                4'd13: {r, g, b} = {4'd15, 4'd11, 4'd15};
                4'd14: {r, g, b} = {4'd15, 4'd13, 4'd15};
                4'd15: {r, g, b} = {4'd15, 4'd15, 4'd15};
            endcase
        endcase
    end

endmodule
`default_nettype wire
