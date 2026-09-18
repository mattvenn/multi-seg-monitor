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
// gamma-dithering branch). A *multi-channel* palette is not bound by that
// proof: varying hue, not just brightness, independently across R/G/B can
// place all 16 stored levels at 16 distinct points in combined-RGB space
// without any single channel being injective on its own.
//
// Two hard rules apply to every palette here, checked against
// tools/segments.py's PALETTES table (the source of truth this case
// statement is generated from) by test/test_palette.py:
//   1. All 16 entries are pairwise distinct as combined (r, g, b) triples.
//   2. Entry 0 is (0, 0, 0) -- index 0 is used both for an explicitly-zeroed
//      segment and every non-segment background/margin pixel
//      (multi_seg_monitor.v's `visible`), so a palette that colours it would
//      tint the whole background, not just dim a segment.
//
// A third rule applies to palettes 1-3 only: they stay 16-way distinct even
// after Tiny VGA mode's 2-bit/channel truncation
// (src/tt_um_multi_seg_monitor.v keeps each channel's top 2 bits). Palette 0
// is exempt on purpose -- it is the plain grey ramp (r=g=b=idx) kept
// byte-identical to the old direct-to-DAC mapping so existing gold images
// and round-trip tests need no changes, and a single-channel-varying ramp
// truncated to 2 bits/channel inherently collapses to 4 levels: that is the
// original Tiny VGA prototype's documented behaviour (SPEC.md section 7),
// not a bug. Palettes 1-3 spend hue precisely to avoid that collapse.
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
            // palette 0: today's grey, r=g=b=idx
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
            // palette 1
            2'd1: case (idx)
                4'd0 : {r, g, b} = {4'd0, 4'd0, 4'd0};
                4'd1 : {r, g, b} = {4'd0, 4'd5, 4'd5};
                4'd2 : {r, g, b} = {4'd0, 4'd10, 4'd10};
                4'd3 : {r, g, b} = {4'd0, 4'd15, 4'd15};
                4'd4 : {r, g, b} = {4'd5, 4'd0, 4'd5};
                4'd5 : {r, g, b} = {4'd5, 4'd5, 4'd0};
                4'd6 : {r, g, b} = {4'd5, 4'd10, 4'd15};
                4'd7 : {r, g, b} = {4'd5, 4'd15, 4'd10};
                4'd8 : {r, g, b} = {4'd10, 4'd0, 4'd10};
                4'd9 : {r, g, b} = {4'd10, 4'd5, 4'd15};
                4'd10: {r, g, b} = {4'd10, 4'd10, 4'd0};
                4'd11: {r, g, b} = {4'd10, 4'd15, 4'd5};
                4'd12: {r, g, b} = {4'd15, 4'd0, 4'd15};
                4'd13: {r, g, b} = {4'd15, 4'd5, 4'd10};
                4'd14: {r, g, b} = {4'd15, 4'd10, 4'd5};
                4'd15: {r, g, b} = {4'd15, 4'd15, 4'd0};
            endcase
            // palette 2
            2'd2: case (idx)
                4'd0 : {r, g, b} = {4'd0, 4'd0, 4'd0};
                4'd1 : {r, g, b} = {4'd5, 4'd0, 4'd5};
                4'd2 : {r, g, b} = {4'd10, 4'd0, 4'd10};
                4'd3 : {r, g, b} = {4'd15, 4'd0, 4'd15};
                4'd4 : {r, g, b} = {4'd5, 4'd5, 4'd0};
                4'd5 : {r, g, b} = {4'd0, 4'd5, 4'd5};
                4'd6 : {r, g, b} = {4'd15, 4'd5, 4'd10};
                4'd7 : {r, g, b} = {4'd10, 4'd5, 4'd15};
                4'd8 : {r, g, b} = {4'd10, 4'd10, 4'd0};
                4'd9 : {r, g, b} = {4'd15, 4'd10, 4'd5};
                4'd10: {r, g, b} = {4'd0, 4'd10, 4'd10};
                4'd11: {r, g, b} = {4'd5, 4'd10, 4'd15};
                4'd12: {r, g, b} = {4'd15, 4'd15, 4'd0};
                4'd13: {r, g, b} = {4'd10, 4'd15, 4'd5};
                4'd14: {r, g, b} = {4'd5, 4'd15, 4'd10};
                4'd15: {r, g, b} = {4'd0, 4'd15, 4'd15};
            endcase
            // palette 3
            2'd3: case (idx)
                4'd0 : {r, g, b} = {4'd0, 4'd0, 4'd0};
                4'd1 : {r, g, b} = {4'd5, 4'd5, 4'd0};
                4'd2 : {r, g, b} = {4'd10, 4'd10, 4'd0};
                4'd3 : {r, g, b} = {4'd15, 4'd15, 4'd0};
                4'd4 : {r, g, b} = {4'd0, 4'd5, 4'd5};
                4'd5 : {r, g, b} = {4'd5, 4'd0, 4'd5};
                4'd6 : {r, g, b} = {4'd10, 4'd15, 4'd5};
                4'd7 : {r, g, b} = {4'd15, 4'd10, 4'd5};
                4'd8 : {r, g, b} = {4'd0, 4'd10, 4'd10};
                4'd9 : {r, g, b} = {4'd5, 4'd15, 4'd10};
                4'd10: {r, g, b} = {4'd10, 4'd0, 4'd10};
                4'd11: {r, g, b} = {4'd15, 4'd5, 4'd10};
                4'd12: {r, g, b} = {4'd0, 4'd15, 4'd15};
                4'd13: {r, g, b} = {4'd5, 4'd10, 4'd15};
                4'd14: {r, g, b} = {4'd10, 4'd5, 4'd15};
                4'd15: {r, g, b} = {4'd15, 4'd0, 4'd15};
            endcase
        endcase
    end

endmodule
`default_nettype wire
