`default_nettype none
//
// Colour palette: 4 bit stored intensity + 54 bits of curve parameters ->
// 4 bit R/G/B.
//
// Each channel is two straight lines: from (0, 0) to a knee, then on at a
// second slope until it clips at 15 --
//
//     c = idx < knee_x ? m1*idx : knee_y + m2*(idx - knee_x),  clipped to 15
//
// with m1/m2 in eighths (5 bits, 0-3.875) and rounded half up. The clip is
// load-bearing: it gives the tinted ramps their flat top with no third
// segment. tools/segments.py's curve_value() is the bit-exact model and
// test_multi_seg.py checks this module against it.
//
// Why a curve and not the 16-entry table this module used to be: the table
// was ROM, and making it loadable costs 16x12 = 192 flops, about 40% of the
// die's free area at 2x2 tiles. The curve's parameters are 54 flops, and the
// arithmetic is one 4x5 multiplier per channel -- the operands are muxed
// before it, so the two segments share it. The knee (movable in x and y) is
// what lets a palette bend for basic per-monitor gamma shaping; a single
// start+slope ramp can only darken, not brighten, the low end.
//
// There used to be a single-channel gamma LUT here (src/gamma.v, removed): the
// PmodVGA output is a hard 4-bit DAC, 16 codes in and 16 codes out, and any
// monotonic curve reshaping 16 values into 16 values is forced by pigeonhole
// into being the identity -- a non-trivial curve can only ever collide two
// stored indices onto the same code, which is what made indices 14 and 15
// genuinely indistinguishable on hardware (dithering_investigation.md on the
// gamma-dithering branch). A bent curve here collides levels in exactly the
// same way; the knee picks *where* the collisions land, it can't avoid them.
// That is why the palette rules (tools/segments.py, tools/test_palettes.py)
// are checked in Python against every preset rather than trusted:
//   1. Entry 0 is (0, 0, 0) -- index 0 is used both for an explicitly-zeroed
//      segment and every non-segment background/margin pixel
//      (multi_seg_monitor.v's `visible`), so a palette that colours it would
//      tint the whole background, not just dim a segment. Forced below as
//      well, because a config packet from the host is never checked.
//   2. Brightness never decreases as the index rises.
//   3. Entries 1-15 are pairwise distinct.
//
// Tiny VGA mode keeps only each channel's top 2 bits
// (src/tt_um_multi_seg_monitor.v), and a smooth single-hue ramp cannot stay
// distinct through that: 4 levels for grey, 7-10 for the tinted presets.
//
module palette (
    input  wire        clk,
    input  wire [53:0] params,  // {R, G, B}, each {knee_x, knee_y, m1, m2}
    input  wire [3:0]  idx,
    output wire [3:0]  r,       // registered, 2 cycles after idx
    output wire [3:0]  g,
    output wire [3:0]  b
    );

    palette_curve cr (.clk(clk), .params(params[53:36]), .idx(idx), .c(r));
    palette_curve cg (.clk(clk), .params(params[35:18]), .idx(idx), .c(g));
    palette_curve cb (.clk(clk), .params(params[17:0]),  .idx(idx), .c(b));

endmodule

// Two pipeline stages, for the FPGA: in one cycle the curve alone -- knee
// compare, segment mux, a 4x5 multiply in LUTs, add, clip -- came to ~32 ns
// on the iCE40, against 25. The cut is after the segment choice because
// that is where the fewest bits cross: 6 per channel (dx, which segment,
// index-is-zero). The slopes and knee_y are read again after the cut rather
// than carried across it; they come from pal_params, which only changes on
// a config write, so the stage they're read in doesn't matter.
module palette_curve (
    input  wire        clk,
    input  wire [17:0] params,  // {knee_x[3:0], knee_y[3:0], m1[4:0], m2[4:0]}
    input  wire [3:0]  idx,
    output reg  [3:0]  c
    );

    wire [3:0] knee_x = params[17:14];
    wire [3:0] knee_y = params[13:10];
    wire [4:0] m1     = params[9:5];
    wire [4:0] m2     = params[4:0];

    // Stage 1: which segment, and how far along it.
    reg       low, zero;
    reg [3:0] dx;

    always @(posedge clk) begin
        low  <= idx < knee_x;
        dx   <= (idx < knee_x) ? idx : idx - knee_x;
        zero <= idx == 4'd0;
    end

    // Stage 2: the segment's line, then the clip. The +4 rounding is folded
    // into the base, so the product needs one add after it rather than two.
    wire [4:0] m    = low ? m1   : m2;
    wire [6:0] base = low ? 7'd4 : {knee_y, 3'b100};

    // A named 9-bit wire, not dx*m inline in the sum: inside a concatenation
    // the product would be self-determined, i.e. only 5 bits wide.
    wire [8:0] prod = dx * m;

    // 15*31 + 15*8 + 4 = 589 fits in 10 bits.
    wire [9:0] sum = {1'b0, prod} + {3'b0, base};

    always @(posedge clk)
        c <= zero         ? 4'd0  :  // rule 1, see header
             (|sum[9:7])  ? 4'd15 :  // clip
                            sum[6:3];

endmodule
`default_nettype wire
