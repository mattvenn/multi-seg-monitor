`default_nettype none
//
// Multi Segment Monitor -- core
//
// Renders a 53 x 27 grid of 7 segment digits by racing the beam.  There is no
// framebuffer: only the digit row currently being drawn is resident, held in a
// double buffered line buffer while the source fills the other half (SPEC.md
// section 3).
//
// Stage 1 drives that line buffer from the internal generator, so the design
// produces a picture with no external data at all.
//
module multi_seg_monitor (
    input  wire       clk,          // 40 MHz pixel clock
    input  wire       rst_n,
    input  wire [7:0] stream_data,  // ui_in
    input  wire       stream_stb,   // uio[0], asynchronous
    input  wire       stream_mode,  // uio[1]: 0 = internal generator, 1 = stream
    output wire       pmod_type,    // reset strap or config packet, to the wrapper
    output reg        hsync,
    output reg        vsync,
    output wire [3:0] r,             // registered inside palette.v
    output wire [3:0] g,
    output wire [3:0] b
    );

    // Grid geometry, fixed at synthesis (SPEC.md section 1).
    //
    // The fat glyph: a 13x20 body in a 15x22 cell, which is
    // tools/shapes.py's digit(thick_h=4, len_h=5, thick_v=4, len_v=4,
    // gap_x=2, gap_y=2) -- run it and it prints this grid, these margins and
    // the band table the zones below implement.  SPEC.md and the numbers in
    // it predate this glyph and describe the 12x16 cell on a 64x37 grid.
    localparam CELL_W    = 15;
    localparam CELL_H    = 22;
    localparam COLS      = 53;
    localparam ROWS      = 27;
    localparam MARGIN_X  = 2;               // (800 - COLS*CELL_W) / 2
    localparam MARGIN_Y  = 3;               // (600 - ROWS*CELL_H) / 2
    localparam ROW_BYTES = COLS * 4;        // 212 bytes per digit row.  The
                                             // line buffer's wall (SPEC.md
                                             // section 3) is 64 columns:
                                             // fetch_col below is a 6 bit
                                             // field and the buffer holds four
                                             // rows at a 256 byte stride, so
                                             // 212 leaves 44 bytes of each row
                                             // buffer unused rather than
                                             // straining anything.

    wire [10:0] x_px;
    wire [9:0]  y_px;
    wire        vga_hsync, vga_vsync;

    VgaSyncGen sync_gen (
        .px_clk      (clk),
        .reset       (!rst_n),
        .hsync       (vga_hsync),
        .vsync       (vga_vsync),
        .x_px        (x_px),
        .y_px        (y_px),
        // Unused on purpose: the range checks below serve as the active flag
        // instead, in the same pipeline stage as the coordinates.
        // verilator lint_off PINCONNECTEMPTY
        .activevideo ()
        // verilator lint_on PINCONNECTEMPTY
    );

    // x_px and y_px are registered inside VgaSyncGen and underflow to large
    // values during blanking, so range checks on them serve as active video flags
    // while staying in the same pipeline stage as the coordinates themselves.
    wire cell_x   = (x_px >= MARGIN_X) && (x_px < MARGIN_X + COLS * CELL_W);
    wire cell_y   = (y_px >= MARGIN_Y) && (y_px < MARGIN_Y + ROWS * CELL_H);

    // ------------------------------------------------------------------
    // Grid coordinates
    //
    // Neither axis divides, so both are counters.  The column always was one;
    // the row used to be a slice of y_px, which only worked while CELL_H was
    // 16 -- `cy = y_rel[3:0], row = y_rel[9:4]` off a subtract by MARGIN_Y.
    // At CELL_H 22 that slice is gone and the pair below replaces it -- which
    // is also why tools/shapes.py no longer warns about a cell height that
    // isn't a power of two: with a counter here, every height costs the same.
    // ------------------------------------------------------------------
    reg [3:0] cx;
    reg [5:0] col;

    always @(posedge clk) begin
        if (!rst_n) begin
            cx  <= 0;
            col <= 0;
        end else if (x_px == MARGIN_X - 1) begin
            cx  <= 0;
            col <= 0;
        end else if (cell_x) begin
            if (cx == CELL_W - 1) begin
                cx  <= 0;
                col <= col + 1'b1;
            end else begin
                cx <= cx + 1'b1;
            end
        end
    end

    // Advanced once a line, in the first cycle of horizontal blanking.
    // VgaSyncGen advances y_px on the edge where x_px == 799 and restarts x_px
    // at 2048 - blackH = 1792, so from that cycle y_px already holds the line
    // about to start -- and the update lands a full 256 cycles before the
    // column 0 prefetch at x_px 2046 reads the y zones through slot0_seg /
    // slot1_seg, and before render_buf is used.
    //
    // Outside cell_y the pair free-runs and means nothing, exactly as the
    // underflowing y_rel did: visible is gated on cell_y, and the generator
    // hardcodes gen_buf to 0 at frame_start rather than deriving it from row.
    localparam [10:0] LINE_RESTART = 11'd1792;  // 2048 - (hfp + hpulse + hbp)

    reg [4:0] cy;
    reg [5:0] row;

    always @(posedge clk) begin
        if (!rst_n) begin
            cy  <= 0;
            row <= 0;
        end else if (x_px == LINE_RESTART) begin
            if (y_px == MARGIN_Y) begin
                cy  <= 0;
                row <= 0;
            end else if (cy == CELL_H - 1) begin
                cy  <= 0;
                row <= row + 1'b1;
            end else begin
                cy <= cy + 1'b1;
            end
        end
    end

    // ------------------------------------------------------------------
    // Segment zones
    //
    // Each segment is one AND of an x zone and a y zone -- the renderer below
    // uses both, the prefetch only the y zones.
    // ------------------------------------------------------------------
    // The digit body is 13x20 inside the 15x22 cell.  The spare two columns
    // and two rows are what stop a digit's right rail merging into its
    // neighbour's left rail, and one row's bottom bar merging into the next
    // row's top bar -- without them the grid reads as a mesh rather than as
    // digits.
    wire xz_left  = (cx < 4);                   // f, e
    wire xz_mid   = (cx >= 4)  && (cx < 9);     // a, g, d
    wire xz_right = (cx >= 9)  && (cx < 13);    // b, c; cx 13, 14 is the gap

    wire yz_top   = (cy < 4);                   // a
    wire yz_up    = (cy >= 4)  && (cy < 8);     // f, b
    wire yz_mid   = (cy >= 8)  && (cy < 12);    // g
    wire yz_low   = (cy >= 12) && (cy < 16);    // e, c
    wire yz_bot   = (cy >= 16) && (cy < 20);    // d; cy 20,21 is the gap

    // Every segment sits in exactly one y zone, and no y zone crosses more than
    // two segments, so a scanline only ever shows two of a digit's seven
    // segments. The prefetch fetches just those two, into two slots: slot 0 for
    // the segment in the left or middle x zone, slot 1 for the one in the
    // right zone. Segment numbers are the nibble order (a = 0 ... g = 6;
    // nibble 7 is reserved and never displayed), so segment k is byte k[2:1],
    // high nibble if k[0].
    reg [2:0] slot0_seg, slot1_seg;

    always @* begin
        case (1'b1)
            yz_top:  {slot0_seg, slot1_seg} = {3'd0, 3'd0};  // a, -
            yz_up:   {slot0_seg, slot1_seg} = {3'd5, 3'd1};  // f, b
            yz_mid:  {slot0_seg, slot1_seg} = {3'd6, 3'd0};  // g, -
            yz_low:  {slot0_seg, slot1_seg} = {3'd4, 3'd2};  // e, c
            // d, and slot 1 reads the reserved nibble: nothing sits at cx 9..12
            // down here, so it is fetched (the prefetch timing doesn't change)
            // and never shown.
            yz_bot:  {slot0_seg, slot1_seg} = {3'd3, 3'd7};
            default: {slot0_seg, slot1_seg} = {3'd0, 3'd0};  // gap rows
        endcase
    end

    // ------------------------------------------------------------------
    // Line buffer and prefetch
    //
    // The renderer reads the half indexed by the current digit row while the
    // generator fills the other.  A digit is 4 bytes, but a scanline only shows
    // two of its nibbles (the slots above), so the next digit's two are
    // fetched a byte each over the first two pixels of the current one.
    // Column 0 of each row is fetched in the last two cycles of horizontal
    // blanking.
    //
    // Blanking rather than the first two pixels of the line, which is where
    // column 0's fetch used to sit.  The fetch is two cycles deep -- the line
    // buffer's registered read, then fetch_en_d -- so next_digit is only
    // complete two pixels after the second fetch, and cur_digit loads it at
    // x_px == MARGIN_X - 1.  That silently required MARGIN_X >= 4.  It held
    // against a 16 pixel left margin and does not against this glyph's 2, so
    // the fetch moved to where every other spare cycle already lives.
    //
    // Fetching all 4 bytes instead is what this used to do: 64 flops of
    // digit registers rather than 16 plus the byte-lane and 8-way nibble
    // muxes around them, about 3.7k um^2 synthesised on IHP, and twice the
    // reads for no pixel that could ever use them.
    // ------------------------------------------------------------------
    // Four row buffers, not two.  The 1 kB macro was already being bought for
    // 512 B, and the spare capacity buys timing freedom instead: the host may run
    // between one and three rows ahead rather than being pinned to exactly one.
    // That is the difference between a host that has to track every row boundary
    // and one that can free-run at a fixed rate (SPEC.md section 4.3).
    wire [1:0] render_buf = row[1:0];

    // x_px counts up through blanking and wraps 2047 -> 0, so &x_px[10:1] is
    // exactly the two cycles before the first visible pixel, and x_px[0] still
    // picks slot 0 then slot 1 across them.
    wire       fetch_en   = cell_x ? (cx < 2)     : (&x_px[10:1]);
    // col + 1 reaches COLS on the last digit of a row, which reads bytes
    // 212..215 of the row buffer: never written, never displayed.  On the
    // 64 column grid this wrapped the 6 bit field to 0 instead.
    wire [5:0] fetch_col  = cell_x ? (col + 1'b1) : 6'd0;
    wire       fetch_slot = cell_x ? cx[0]        : x_px[0];
    wire [2:0] fetch_seg  = fetch_slot ? slot1_seg : slot0_seg;
    wire [1:0] fetch_byte = fetch_seg[2:1];

    reg fetch_en_d;
    reg fetch_slot_d;
    reg fetch_hi_d;

    always @(posedge clk) begin
        fetch_en_d   <= fetch_en;
        fetch_slot_d <= fetch_slot;
        fetch_hi_d   <= fetch_seg[0];
    end

    // Reads take 2 of every 15 cycles to prefetch the next digit, so writes have
    // the other 13 -- about 34 MB/s against the 365 kB/s a source actually needs.
    // Reads always win, which is what keeps the one-access-per-cycle guarantee
    // the memory wrapper depends on (SPEC.md section 8.1).
    wire lb_re    = fetch_en;
    wire wr_grant = !lb_re;

    wire [7:0] lb_rdata;
    reg [7:0] next_digit;  // {slot 1, slot 0}
    reg [7:0] cur_digit;

    always @(posedge clk) begin
        if (!rst_n) begin
            next_digit <= 8'b0;
            cur_digit  <= 8'b0;
        end else begin
            if (fetch_en_d)
                next_digit[{fetch_slot_d, 2'b00} +: 4] <= fetch_hi_d ? lb_rdata[7:4]
                                                                     : lb_rdata[3:0];
            if (x_px == MARGIN_X - 1 || (cell_x && cx == CELL_W - 1))
                cur_digit <= next_digit;
        end
    end

    // ------------------------------------------------------------------
    // Internal generator
    //
    // Draws a moving zone plate (zoneplate.v) into every one of the 1431
    // digits, with no external data: the picture a bare board shows, and the
    // silicon bring-up safety net. It replaced a scrolling hex test pattern;
    // the zone plate still sweeps all 15 lit codes across the screen, and the
    // reserved nibble is always 0 in this mode.
    //
    // Row N+1 is built while row N is on screen, into the buffer half that is not
    // being read. Each byte waits for the zone plate to compute it (~12 cycles)
    // and is then written in whatever cycle the renderer isn't reading, so a
    // row's 212 bytes are placed long before that row is needed.
    // ------------------------------------------------------------------
    reg [5:0]  gen_row;
    reg [7:0]  gen_ptr;
    reg [1:0]  gen_buf;
    reg        gen_busy;
    // 14 bits: the zone plate's source paths wrap at 2^16 of t * {24, 16,
    // 20}, so the whole picture repeats every 2^14 frames (4.5 minutes) and a
    // wider counter would change nothing. The low 10 bits drive the palette
    // cycle and its fade, and the top bits shape the zone plate's two rate
    // waves (see src/zoneplate.v).
    reg [13:0] frame_ctr;
    reg        vsync_d;

    wire frame_start = vsync_d && !vga_vsync;

    // From config_port: the live DIP switches, and whether the palette is
    // changing on its own (see below).
    wire        auto_pal;
    wire        vary_phase, vary_drift;

    // Fade through black around the automatic palette change, so a change is
    // a dip rather than a cut. Purely combinational off the frame counter --
    // 64 frames down, 64 up, either side of the wrap, full brightness for the
    // 896 in between -- so it costs no state of its own. It is applied to the
    // generator's picture only, and only while the palette is changing
    // itself: a host that has taken the palette over never sees a dip.
    //
    // Four frames a level, not two: a 16-step fade at 60 Hz is short enough
    // to read as a staircase rather than a dip, and the whole point is that
    // the change is not noticed happening.
    wire [9:0] fade_k   = frame_ctr[9:0];
    wire [3:0] fade_lvl = fade_k < 10'd64   ? fade_k[5:2] :
                          fade_k >= 10'd960 ? 4'd15 - fade_k[5:2] : 4'd15;

    // Registered, not wired straight into the zone plate. The level changes
    // once a frame, but as a wire it put frame_ctr's two comparators in
    // front of the fade multiplier on the way to the line buffer's write
    // port, and that was the critical path (27.5 MHz on the iCE40 against
    // 33.6 on main). Four flops buy it back.
    //
    // It follows frame_ctr a cycle late, which no byte can see: the first
    // one of a frame is written at least sixteen cycles after frame_start,
    // while the zone plate works out the frame's source points.
    reg [3:0] gen_fade;
    always @(posedge clk) begin
        if (!rst_n)
            gen_fade <= 4'd15;
        else
            gen_fade <= auto_pal ? fade_lvl : 4'd15;
    end

    // The palette change is registered a cycle off frame_start rather than
    // taken from it directly. frame_start is the far end of the longest
    // combinational path on the chip -- y_px through VgaSyncGen's vsync
    // comparator, a 10-bit carry chain -- and config_port answers it with
    // the preset table and a 54-bit palette load, which put the two together
    // at 26.8 MHz on the iCE40 against 33.6 on main. One flop splits them,
    // and the palette arriving a cycle into vertical blanking is 512 frames
    // early for anything that could see it.
    reg frame_wrap;
    reg frame_start_d;
    always @(posedge clk) begin
        if (!rst_n) begin
            frame_wrap    <= 1'b0;
            frame_start_d <= 1'b0;
        end else begin
            // frame_ctr advances on frame_start, so it still reads the old
            // count here: this is the last frame of the 512, not the first.
            frame_wrap    <= frame_start && frame_ctr[9:0] == 10'h3FF;
            // Likewise the switch sampling. It is a debounce over whole
            // frames, so a cycle either way means nothing to it, and keeping
            // it off frame_start keeps that net's fanout down.
            frame_start_d <= frame_start;
        end
    end
    wire row_start   = cell_y && cy == 0 && x_px == 0 && row < ROWS - 1;
    wire zp_ready;
    wire gen_grant   = wr_grant && !stream_mode && gen_busy && zp_ready;

    always @(posedge clk) begin
        if (!rst_n) begin
            gen_row   <= 0;
            gen_ptr   <= 0;
            gen_buf   <= 2'd0;
            gen_busy  <= 0;
            // 64, not 0: gen_fade below holds the first 64 frames of every
            // 1024 at a fade-in, and power-up must not land in one. The
            // bring-up picture would come up dim, and the gold and
            // gate-level captures -- taken a handful of frames after reset,
            // and checking the lit fraction and that all 16 DAC codes appear
            // -- would be measuring a fade rather than the picture.
            frame_ctr <= 14'd64;
            vsync_d   <= 1'b1;
        end else begin
            vsync_d <= vga_vsync;

            if (frame_start) begin
                // Frame start: row 0 has to be ready before the first active
                // line, so it is built during vertical blanking.
                gen_row   <= 0;
                gen_buf   <= 2'd0;
                gen_ptr   <= 0;
                gen_busy  <= 1'b1;
                frame_ctr <= frame_ctr + 1'b1;
            end else if (row_start) begin
                // Start of digit row N: build row N+1 into the other buffer.
                gen_row  <= row + 1'b1;
                gen_buf  <= row[1:0] + 2'd1;
                gen_ptr  <= 0;
                gen_busy <= 1'b1;
            end else if (gen_grant) begin
                if (gen_ptr == ROW_BYTES - 1)
                    gen_busy <= 1'b0;
                gen_ptr <= gen_ptr + 1'b1;
            end
        end
    end

    wire [7:0] gen_data;

    // gen_ptr and gen_row hold still while a byte is computed: gen_ptr only
    // moves on a grant, which waits for zp_ready. A row or frame restart
    // moves them anyway, so it drops whatever byte was in progress.
    zoneplate zp (
        .clk         (clk),
        .rst_n       (rst_n),
        .frame_start (frame_start),
        .frame       (frame_ctr),
        .vary_phase  (vary_phase),
        .vary_drift  (vary_drift),
        .fade        (gen_fade),
        .abort       (row_start),
        .want        (gen_busy && !stream_mode),
        .take        (gen_grant),
        .col         (gen_ptr[7:2]),
        .row         (gen_row),
        .byte_idx    (gen_ptr[1:0]),
        .ready       (zp_ready),
        .data        (gen_data)
    );

    // ------------------------------------------------------------------
    // Stream port and write arbitration
    // ------------------------------------------------------------------
    wire       str_req;
    wire       str_stb;
    wire [9:0] str_waddr;
    wire [7:0] str_wdata;
    wire       str_grant = wr_grant && stream_mode && str_req;

    stream_in #(
        .ROW_BYTES (ROW_BYTES),
        .ROWS      (ROWS)
    ) stream (
        .clk         (clk),
        .rst_n       (rst_n),
        .data        (stream_data),
        .strobe      (stream_stb),
        .frame_start (frame_start),
        .enable      (stream_mode),
        .stb         (str_stb),
        .req         (str_req),
        .grant       (str_grant),
        .waddr       (str_waddr),
        .wdata       (str_wdata)
    );

    // ------------------------------------------------------------------
    // Reset strap, config packet and palette state -- see config_port.v.
    // ------------------------------------------------------------------
    wire [53:0] pal_params;
    // verilator lint_off UNUSEDSIGNAL
    wire [2:0]  preset_idx;  // only read by the tests, via the hierarchy
    wire        cycle_en;    // likewise
    // verilator lint_on UNUSEDSIGNAL
    config_port cfg (
        .clk         (clk),
        .rst_n       (rst_n),
        .data        (stream_data),
        .stb         (str_stb),
        .stream_mode (stream_mode),
        .frame_start (frame_start_d),
        .frame_wrap  (frame_wrap),
        .pmod_type   (pmod_type),
        .pal_params  (pal_params),
        .preset_idx  (preset_idx),
        .cycle_en    (cycle_en),
        .auto_pal    (auto_pal),
        .vary_phase  (vary_phase),
        .vary_drift  (vary_drift)
    );

    wire       lb_we    = stream_mode ? str_grant : gen_grant;
    wire [9:0] lb_waddr = stream_mode ? str_waddr : {gen_buf, gen_ptr};
    wire [7:0] lb_wdata = stream_mode ? str_wdata : gen_data;

    line_buffer #(.AW(10)) lb (
        .clk   (clk),
        .we    (lb_we),
        .waddr (lb_waddr),
        .wdata (lb_wdata),
        .re    (lb_re),
        .raddr ({render_buf, fetch_col, fetch_byte}),
        .rdata (lb_rdata)
    );

    // ------------------------------------------------------------------
    // Segment renderer
    //
    // Each segment is one AND of an x zone and a y zone, so the whole digit costs
    // a handful of constant comparisons rather than a bitmap lookup.  Zones that
    // meet at a corner select nothing, which is what leaves the cell corners
    // blank.  Layout is SPEC.md section 1.1.  The y zone already picked which
    // two segments were fetched, so the x zone only has to pick the slot.
    // ------------------------------------------------------------------
    wire seg_hit = (xz_mid   & yz_top) |   // a
                   (xz_right & yz_up ) |   // b
                   (xz_right & yz_low) |   // c
                   (xz_mid   & yz_bot) |   // d
                   (xz_left  & yz_low) |   // e
                   (xz_left  & yz_up ) |   // f
                   (xz_mid   & yz_mid);    // g

    wire [3:0] seg_int = xz_right ? cur_digit[7:4] : cur_digit[3:0];
    wire       visible = seg_hit && cell_x && cell_y;

    // Colour comes from a selectable palette rather than a single grey value
    // -- see palette.v for why this doesn't repeat the pigeonhole collision
    // that killed the old single-channel gamma LUT, and for the invariants
    // every palette must hold (index 0 -> black, all 16 indices distinct).
    // This module has no notion that two physical Pmods exist; the wrapper
    // (tt_um_multi_seg_monitor.v) decides how many bits of r/g/b actually
    // reach a pin.
    //
    // Three pipeline flops between the segment decode and the pins, all for
    // timing. Before the palette was a loadable curve its ROM output went
    // straight to the pins, a path nextpnr never times; the curve's
    // multiplier made that path matter. On the iCE40 decode plus curve came
    // to well over the 25 ns cycle, so the index is registered here and the
    // curve is split in two inside palette.v, which also registers its
    // output. On the ASIC the same flops take the multiplier off the pad
    // path and stop the DAC pins glitching while it settles.
    //
    // The palette input used to be one cycle behind the raw syncs; these add
    // three more, so the syncs get four cycles to match. VgaSyncGen now
    // decodes its syncs from the registered coordinates, which is already one
    // of those cycles, so three flops here make up the rest. Every pin moves
    // by the same cycles, so the picture doesn't move at all.
    reg [3:0] pal_idx;

    always @(posedge clk)
        pal_idx <= visible ? seg_int : 4'h0;

    palette pal (
        .clk    (clk),
        .params (pal_params),
        .idx    (pal_idx),
        .r      (r),
        .g      (g),
        .b      (b)
    );

    reg [1:0] hsync_p, vsync_p;

    always @(posedge clk) begin
        hsync_p <= {hsync_p[0], vga_hsync};
        vsync_p <= {vsync_p[0], vga_vsync};
        hsync   <= hsync_p[1];
        vsync   <= vsync_p[1];
    end

`ifdef FORMAL
    // `read_verilog -formal` (see formal/) defines FORMAL in place of
    // SYNTHESIS. Each property below is compiled in only by the one
    // formal/*.sby run that -D's its own guard macro, so the three proofs
    // stay independent of each other despite living in one module. Each
    // .sby has to list every file the core instantiates, not just the ones
    // its property touches: lb_exclusivity and zone_exclusivity silently
    // stopped building when palette.v and config_port.v went in without
    // being added to them. FORMAL_BUF has no .sby (buf_isolation.sby, in
    // git history, only ever documented the failure described below).

`ifdef FORMAL_WE_RE
    // we and re must never be high in the same cycle (line_buffer.v): on
    // the IHP macro that combination is write-through and would silently
    // put wdata at raddr instead of the intended write address. This is
    // true by construction (wr_grant = !lb_re), so it should hold for any
    // reachable OR unreachable state -- a useful smoke test that the
    // arbitration hasn't been refactored into something that only looks
    // equivalent.
    always @*
        assert (!(lb_we && lb_re));
`endif

`ifdef FORMAL_ZONE
    // At most one of the 7 segment zones may claim a given (cx, cy): if two
    // ever overlapped, the case(1'b1) priority-encoder would silently pick
    // one and the other segment just wouldn't render there. This is what
    // keeps the spare column/row load-bearing rather than cosmetic
    // (comment above xz_left etc.) -- formalizes it at the zone-boundary
    // level instead of only catching it pixel-by-pixel in a gold image.
    always @*
        assert ($countones({xz_mid  & yz_top, xz_right & yz_up,
                             xz_right & yz_low, xz_mid  & yz_bot,
                             xz_left & yz_low,  xz_left & yz_up,
                             xz_mid  & yz_mid}) <= 1);
`endif

`ifdef FORMAL_BUF
    // The whole point of four line-buffer rows instead of two (comment
    // above render_buf): the row a writer targets must never be the row
    // currently being displayed, or the renderer would read a half-written
    // digit. Unlike FORMAL_WE_RE/FORMAL_ZONE this isn't a same-cycle
    // combinational identity, so BMC to any practical depth is close to
    // meaningless here -- a real row transition is ~16896 cycles away from
    // reset -- and k-induction needs help to close it. It doesn't, yet:
    // documenting the attempt and exactly where it stopped rather than
    // leaving a bare unproven assert.
    //
    // The property actually splits into three sub-claims of increasing
    // difficulty:
    //
    // 1. Stream port (str_waddr[9:8] != render_buf): depends on the host's
    //    pacing discipline -- CLAUDE.md: "Staying in step is the host's
    //    job" -- which is software this chip's RTL can't see. Asserting it
    //    here would mean assuming what needs proving, so it's out of scope
    //    for a chip-only proof; test/'s delay-sweep covers it empirically
    //    instead, by observing the failure mode on real mis-paced traces.
    //
    // 2. Generator, steady state (the cy==0 && x_px==0 transition each
    //    digit row): closeable, and mostly closed here. It needs two
    //    lemmas beyond the target assert itself:
    //      - rate: wr_grant is high >=8 of every 12 cycles (wr_grant =
    //        !lb_re, lb_re = fetch_en, true for only 4 of 12), so gen_ptr
    //        gains >=2 every 3 cycles once gen_busy is set, and reaches
    //        ROW_BYTES-1 (structural bound: gen_ptr is 8 bits) within
    //        ~400 cycles.
    //      - separation: row/render_buf are derived from y_px, which
    //        VgaSyncGen only updates once per scanline (hpixels = 1056
    //        pixel clocks), so render_buf can't have moved on ~400 cycles
    //        into the fill.
    //    Formalized with a ghost "cycles since last transition" counter
    //    and a captured "render_buf at transition" value, both lemmas were
    //    individually provable by k-induction.
    //
    // 3. Generator, frame_start (row 0, built during vertical blanking,
    //    CLAUDE.md section on frame_start): NOT closed, and the reason is
    //    load-bearing enough to write down. frame_start hardcodes
    //    `gen_buf <= 2'd0` rather than `row[1:0] + 1'b1` -- because
    //    outside cell_y, y_rel underflows and row/render_buf read
    //    something other than a real row number. That garbage value isn't
    //    even stable: it visibly changes across the ~28-scanline vertical
    //    blanking period before the first active line, breaking the
    //    "separation" lemma from case 2 exactly as written (it assumed one
    //    fixed render_buf value for the whole fill). It's very likely
    //    still safe -- outside cell_y, visible = seg_hit && cell_x &&
    //    cell_y is always 0, so nothing reaches the display regardless of
    //    what render_buf/gen_buf happen to be -- but that's a different
    //    argument (gate on cell_y, or separately show the frame_start fill
    //    finishes before cell_y goes true), not an extension of case 2's.
    //
    // Net effect: the assert below fails at the basecase (step 1, not just
    // an induction step that won't close) -- confirmed by running it.
    // Case 3's gap is reachable immediately from a cold/undefined power-up
    // state (CLAUDE.md: nothing may depend on initialisation), not only
    // after the ~16896-cycle run to a real row transition. Closing this
    // means gating the assert on cell_y (or otherwise scoping case 3 out),
    // not anything about case 2, which remains individually provable as
    // described above.
    always @(posedge clk)
        if (rst_n && gen_grant)
            assert (gen_buf != render_buf);
`endif
`endif

endmodule
`default_nettype wire
