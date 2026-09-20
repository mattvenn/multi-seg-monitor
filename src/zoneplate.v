`default_nettype none
//
// Zone plate -- the internal generator's picture.
//
// Two points wander the screen on triangle-wave Lissajous paths, and every
// segment's brightness is the sum of its squared distances to them, scaled and
// folded into a triangle wave, minus a phase that runs with time: rings that
// tighten outward and flow. tools/attract_proto.py's zoneplate_at() is the
// bit-exact model (its defaults: ring_shift 13, drift 4, two sources) and
// test_multi_seg.py compares a captured frame against it.
//
// Neither the phase nor the path position is a product of the frame number any
// more: each is an accumulator, so the *speeds* can vary without the picture
// ever jumping. See "Slow variation" below. `fade` scales every level on its
// way out, which is how the palette change hides itself in black.
//
// Nothing here is stored per pixel: each segment's level is a pure function of
// its position and the frame number, so the chip still holds no framebuffer.
// The generator asks for one line-buffer byte -- two segments -- at a time and
// writes it when `ready` rises; a byte takes ~12 cycles against the ~110 a
// digit row allows per byte, so the row is still built well before it is
// drawn.
//
// Cost is the squarer. It is shared by all four squares a sample needs, one
// per cycle, which is also why the source points are worked out once per
// frame into registers rather than per sample: four Lissajous points
// computed in parallel were ~7k um^2 of adders on their own.
//
module zoneplate (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        frame_start,  // work out this frame's source points
    // frame_ctr as it reads *during* the frame_start edge, i.e. before that
    // edge advances it. Only the top bits are used, and only to shape the two
    // rate waves below -- the picture itself comes from ring_ph and t_src.
    /* verilator lint_off UNUSEDSIGNAL */
    input  wire [13:0] frame,
    /* verilator lint_on UNUSEDSIGNAL */
    input  wire        vary_phase,   // let the ring speed wander
    input  wire        vary_drift,   // let the source speed wander
    input  wire [3:0]  fade,         // 15 = full brightness, 0 = black
    input  wire        abort,        // the generator restarted: drop the byte
    input  wire        want,         // a byte is wanted at (col, row, byte_idx)
    input  wire        take,         // ...and was written this cycle
    input  wire [5:0]  col,
    input  wire [5:0]  row,
    input  wire [1:0]  byte_idx,
    output reg         ready,
    output wire [7:0]  data          // {segment 2*byte_idx+1, segment 2*byte_idx}
    );

    // ------------------------------------------------------------------
    // Slow variation: only speeds change, never positions
    //
    // Matt's one rule for the attract mode is that nothing may jump. So the
    // two things that move -- the ring phase and the source points' time --
    // are accumulators rather than products of the frame number, and what
    // varies is the amount added each frame. A rate change then bends the
    // motion; it cannot displace it, whatever the rate does, including going
    // through zero and negative.
    //
    // Each rate is a triangle wave off the high bits of the frame counter, so
    // it creeps one step every few hundred frames. attract_proto.chip_rates()
    // is the model.
    //
    // ring_ph, 9 bits: 7 integer (all the fold keeps) + 2 fraction, so the
    // rate is in quarter fold-steps. 4 is the old fixed speed of one step per
    // frame; varying runs -3..12 -- slightly backwards, through stopped, to
    // 3x -- changing every 256 frames, a 2.3 minute round trip.
    //
    // t_src, 17 bits: 14 integer (what the source paths take, in place of the
    // frame number) + 3 fraction, so the rate is in eighths. 8 is the old
    // speed; varying runs 4..11, 0.5x to 1.4x, changing every 1024 frames
    // over 4.5 minutes. Held to positive rates: reversing the drift as well
    // as the rings made the picture look like it was being scrubbed.
    // ------------------------------------------------------------------
    reg  [8:0]  ring_ph;
    reg  [16:0] t_src;

    // tri(frame[12:8], 5) and tri(frame[13:10], 4): the top bit of each field
    // is the fold, so the wave is an XOR, not an adder (attract_proto.tri()).
    wire [3:0]  tri_ph  = frame[12] ? ~frame[11:8]  : frame[11:8];
    wire [2:0]  tri_t   = frame[13] ? ~frame[12:10] : frame[12:10];
    // -3 arrives as a 9-bit two's complement addend, which wraps ring_ph the
    // right way round without a subtracter or a sign bit anywhere else.
    wire [8:0]  rate_ph = vary_phase ? {5'b0, tri_ph} - 9'd3 : 9'd4;
    wire [16:0] rate_t  = vary_drift ? {13'b0, tri_t} + 17'd4 : 17'd8;

    always @(posedge clk) begin
        if (!rst_n) begin
            ring_ph <= 9'd0;
            t_src   <= 17'd0;
        end else if (frame_start) begin
            // `frame` still reads the pre-advance count on this edge, which is
            // what attract_proto.chip_step() is given.
            ring_ph <= ring_ph + rate_ph;
            t_src   <= t_src + rate_t;
        end
    end

    // ------------------------------------------------------------------
    // Source points, once per frame
    //
    // attract_proto._sources() at drift 4: phases are t * {24,16,16,20} / 16
    // (the last two offset by 600 and 1400 so the two points don't move in
    // step), folded into a 0..2047 triangle, then scaled by
    // * 768 >> 11, which is * 3 >> 3, and * 592 >> 11, which is * 37 >> 7.
    // Only bits [15:4] of each product matter -- the triangle wraps at 4096
    // -- so the products are 16 bits, as shift-and-adds rather than
    // multipliers.
    //
    // 768 x 592 is not this grid: the grid is 53 * 15 by 27 * 22, 795 x 594.
    // The points are deliberately left roaming the smaller range, because
    // 3 >> 3 and 37 >> 7 are two adders where 795 and 594 are multipliers on
    // the path that already carries the longest arithmetic in the design, and
    // sources that stop 27 pixels short of two edges is not something anyone
    // can see in a field of rings. attract_proto.SRC_W/SRC_H hold the same
    // pair, so the model stays bit-exact.
    //
    // t is t_src's integer part. With rate_t held at 8 it counts frames, which
    // is exactly what this computed before the accumulator went in.
    // ------------------------------------------------------------------
    reg  [9:0]  pt [0:3];   // ax, ay, bx, by
    reg  [2:0]  pi;         // point being worked out; 4 = done

    wire [13:0] t = t_src[16:3];
    wire [15:0] f24 = {t[12:0], 3'b0} + {t[11:0], 4'b0};
    wire [15:0] f16 = {t[11:0], 4'b0};
    wire [15:0] f20 = {t[13:0], 2'b0} + {t[11:0], 4'b0};
    // verilator lint_off UNUSEDSIGNAL
    wire [15:0] prod = (pi[1:0] == 2'd0) ? f24 : (pi[1:0] == 2'd3) ? f20 : f16;
    wire [11:0] ph   = prod[15:4] + (pi[1:0] == 2'd1 ? 12'd600 :
                                     pi[1:0] == 2'd2 ? 12'd1400 : 12'd0);
    wire [10:0] tr   = ph[11] ? ~ph[10:0] : ph[10:0];
    wire [13:0] sx   = tr * 3;
    wire [16:0] sy   = tr * 37;
    // verilator lint_on UNUSEDSIGNAL
    wire [9:0]  new_pt = pi[0] ? sy[16:7] : sx[12:3];

    // ------------------------------------------------------------------
    // Segment centres
    //
    // The centre of each segment rectangle (tools/segments.py SEGMENTS),
    // rounded down, relative to its cell: a (6,1) b (10,5) c (10,13)
    // d (6,17) e (1,13) f (1,5) g (6,9). oy needs 5 bits now that the cell is
    // 22 tall, and both multiplies are shift-and-adds: col * 15 is
    // col * 16 - col, row * 22 is row * 16 + row * 4 + row * 2.
    // ------------------------------------------------------------------
    reg        half;        // 0: the byte's low segment, 1: its high one
    wire [2:0] seg = {byte_idx, half};
    reg  [3:0] ox;
    reg  [4:0] oy;
    always @* begin
        case (seg)
            3'd0:    {ox, oy} = {4'd6,  5'd1};
            3'd1:    {ox, oy} = {4'd10, 5'd5};
            3'd2:    {ox, oy} = {4'd10, 5'd13};
            3'd3:    {ox, oy} = {4'd6,  5'd17};
            3'd4:    {ox, oy} = {4'd1,  5'd13};
            3'd5:    {ox, oy} = {4'd1,  5'd5};
            default: {ox, oy} = {4'd6,  5'd9};  // g; DP (7) is never sampled
        endcase
    end
    wire [9:0] sx_px = {col, 4'b0} - {4'b0, col} + {6'b0, ox};
    wire [9:0] sy_px = {row, 4'b0} + {2'b0, row, 2'b0} + {3'b0, row, 1'b0}
                       + {5'b0, oy};

    // ------------------------------------------------------------------
    // One sample: |dx|^2 + |dy|^2 for each point, a square a cycle
    //
    //   st 1: dif = x - ax
    //   st 2: mag = |dif|,  dif = y - ay
    //   st 3: acc = mag^2,  mag = |dif|,  dif = x - bx
    //   st 4: pa  = (acc + mag^2) >> 11,  mag = |dif|,  dif = y - by
    //   st 5: acc = mag^2,  mag = |dif|
    //   st 6: level = fold(pa + (acc + mag^2) >> 11 - ring_ph)
    //
    // Three registers deep -- difference, magnitude, square -- because on the
    // iCE40 the segment position (col * 12 + offset), the subtraction and the
    // negate are three carry chains, and in one cycle with the square they
    // made 22 MHz (30 with the square in a DSP). Overlapped like this it costs
    // one cycle a sample, not three. Each sum is shifted before the two are
    // added, as the model does -- the floors are per point, not of the total.
    // Only 7 bits of the phase survive the fold, so pa keeps 7.
    // ------------------------------------------------------------------
    reg  [2:0]  st;
    reg  [10:0] dif;
    reg  [9:0]  mag;
    reg  [19:0] acc;
    reg  [6:0]  pa;
    reg  [3:0]  lo, hi;

    wire        use_b = st >= 3'd3;
    wire        use_y = st == 3'd2 || st == 3'd4;
    wire [9:0]  coord = use_y ? sy_px : sx_px;
    wire [9:0]  p     = pt[{use_b, use_y}];
    wire [10:0] new_dif = {1'b0, coord} - {1'b0, p};
    // verilator lint_off UNUSEDSIGNAL
    wire [10:0] ndif  = -dif;
    wire [9:0]  amag  = dif[10] ? ndif[9:0] : dif[9:0];
    wire [19:0] sq    = mag * mag;
    wire [20:0] sum   = {1'b0, acc} + {1'b0, sq};
    wire [6:0]  v     = pa + sum[17:11] - ring_ph[8:2];
    // verilator lint_on UNUSEDSIGNAL
    wire [3:0]  level = v[6] ? ~v[5:2] : v[5:2];  // attract_proto.tri(v, 7) >> 2

    // attract_proto.apply_fade(): exact at both ends -- fade 15 leaves every
    // level alone, fade 0 is black -- for one 4x5 multiply per level.
    //
    // It scales the byte on its way *out* rather than the level on its way
    // in, which costs a second multiplier and is worth it. In front of lo/hi
    // it lands at the end of the longest chain in the design -- the DSP's
    // square, the 21-bit sum, the fold -- and cost 5 MHz on the iCE40
    // (28.3 against 33.6 on main). Here it starts from a flop and ends at
    // the line buffer's write port, which is nearly empty.
    // verilator lint_off UNUSEDSIGNAL
    wire [4:0]  gain    = {1'b0, fade} + 5'd1;   // 1..16
    wire [8:0]  fade_lo = {5'b0, lo} * {4'b0, gain};
    wire [8:0]  fade_hi = {5'b0, hi} * {4'b0, gain};
    // verilator lint_on UNUSEDSIGNAL

    assign data = {fade_hi[7:4], fade_lo[7:4]};

    // dif and mag load every cycle, with no enable: the sample states run one
    // a cycle without stalling, so free-running they hold exactly what the
    // table above says, and garbage the rest of the time that nothing reads.
    // An enable would hang the whole FSM priority chain -- frame_start, the
    // row-start abort -- off the DSP's input register on the iCE40.
    always @(posedge clk) begin
        dif <= new_dif;
        mag <= amag;
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            pi    <= 3'd4;
            st    <= 0;
            ready <= 1'b0;
        end else if (frame_start) begin
            // frame advances on this edge, so the points start next cycle.
            pi    <= 3'd0;
            st    <= 0;
            ready <= 1'b0;
        end else if (pi != 3'd4) begin
            pt[pi[1:0]] <= new_pt;
            pi <= pi + 1'b1;
        end else if (abort) begin
            st    <= 0;
            ready <= 1'b0;
        end else begin
            if (take)
                ready <= 1'b0;
            case (st)
                3'd0: if (want && !ready && !take) begin
                    half <= 1'b0;
                    st   <= 3'd1;
                end
                3'd1: st <= 3'd2;
                3'd2: st <= 3'd3;
                3'd3: begin acc <= sq; st <= 3'd4; end
                3'd4: begin pa <= sum[17:11]; st <= 3'd5; end
                3'd5: begin acc <= sq; st <= 3'd6; end
                3'd6: begin
                    if (!half) begin
                        lo   <= level;
                        half <= 1'b1;
                        // Segment 7 is the decimal point, which the zone
                        // plate leaves dark: no need to sample it.
                        if (byte_idx == 2'd3) begin
                            hi <= 4'd0; ready <= 1'b1; st <= 3'd0;
                        end else begin
                            st <= 3'd1;
                        end
                    end else begin
                        hi <= level; ready <= 1'b1; st <= 3'd0;
                    end
                end
                default: st <= 0;
            endcase
        end
    end

endmodule
`default_nettype wire
