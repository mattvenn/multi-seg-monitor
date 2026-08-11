`default_nettype none
//
// Multi Segment Monitor -- core
//
// Renders a 64 x 37 grid of 7 segment digits by racing the beam.  There is no
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
    output reg        hsync,
    output reg        vsync,
    output wire [5:0] level         // native 6 bit intensity
    );

    // Grid geometry, fixed at synthesis (SPEC.md section 1).
    localparam CELL_W    = 12;
    localparam CELL_H    = 16;
    localparam COLS      = 64;
    localparam ROWS      = 37;
    localparam MARGIN_X  = 16;              // (800 - COLS*CELL_W) / 2
    localparam MARGIN_Y  = 4;               // (600 - ROWS*CELL_H) / 2
    localparam ROW_BYTES = COLS * 4;        // 256 bytes per digit row -- the
                                             // line buffer's wall (SPEC.md
                                             // section 3): fetch_col below is
                                             // a 6 bit field, so 64 is the most
                                             // this design can ever address.

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
        .activevideo ()
    );

    // x_px and y_px are registered inside VgaSyncGen and underflow to large
    // values during blanking, so range checks on them serve as active video flags
    // while staying in the same pipeline stage as the coordinates themselves.
    wire h_active = (x_px < 800);
    wire cell_x   = (x_px >= MARGIN_X) && (x_px < MARGIN_X + COLS * CELL_W);
    wire cell_y   = (y_px >= MARGIN_Y) && (y_px < MARGIN_Y + ROWS * CELL_H);

    // ------------------------------------------------------------------
    // Grid coordinates
    //
    // 800 does not divide by 12, so the column is tracked with a counter rather
    // than a divider.  The row still doesn't need one: CELL_H is a power of two,
    // so cy/row fall out of a slice once y_px is offset by MARGIN_Y -- unlike
    // the old 640x480 mode, 600 doesn't divide evenly by 16, so that offset is
    // no longer zero and can't be skipped.
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

    wire [9:0] y_rel = y_px - MARGIN_Y;  // underflows harmlessly outside cell_y
    wire [3:0] cy    = y_rel[3:0];
    wire [5:0] row   = y_rel[9:4];

    // ------------------------------------------------------------------
    // Line buffer and prefetch
    //
    // The renderer reads the half indexed by the current digit row while the
    // generator fills the other.  A digit is 4 bytes and a cell is 12 pixels
    // wide, so the next digit is fetched a byte at a time over the first four
    // pixels of the current one.  Column 0 of each row is fetched during the left
    // margin.
    // ------------------------------------------------------------------
    // Four row buffers, not two.  The 1 kB macro was already being bought for
    // 512 B, and the spare capacity buys timing freedom instead: the host may run
    // between one and three rows ahead rather than being pinned to exactly one.
    // That is the difference between a host that has to track every row boundary
    // and one that can free-run at a fixed rate (SPEC.md section 4.3).
    wire [1:0] render_buf = row[1:0];

    wire       fetch_en   = cell_x ? (cx < 4)     : (x_px < 4);
    wire [5:0] fetch_col  = cell_x ? (col + 1'b1) : 6'd0;
    wire [1:0] fetch_byte = cell_x ? cx[1:0]      : x_px[1:0];

    reg       fetch_en_d;
    reg [1:0] fetch_byte_d;

    always @(posedge clk) begin
        fetch_en_d   <= fetch_en;
        fetch_byte_d <= fetch_byte;
    end

    // Reads take 4 of every 12 cycles to prefetch the next digit, so writes have
    // the other 8 -- about 26.7 MB/s against the 606 kB/s a source actually needs.
    // Reads always win, which is what keeps the one-access-per-cycle guarantee
    // the memory wrapper depends on (SPEC.md section 8.1).
    wire lb_re    = fetch_en;
    wire wr_grant = !lb_re;

    wire [7:0] lb_rdata;
    reg [31:0] next_digit;
    reg [31:0] cur_digit;

    always @(posedge clk) begin
        if (!rst_n) begin
            next_digit <= 32'b0;
            cur_digit  <= 32'b0;
        end else begin
            if (fetch_en_d)
                next_digit[{fetch_byte_d, 3'b000} +: 8] <= lb_rdata;
            if (x_px == MARGIN_X - 1 || (cell_x && cx == CELL_W - 1))
                cur_digit <= next_digit;
        end
    end

    // ------------------------------------------------------------------
    // Internal generator
    //
    // Fills every one of the 2368 digits with a scrolling diagonal of hex values
    // and a brightness band that varies across the row, so a single glance at the
    // screen exercises all 16 patterns, all 8 segments, the whole grid and the
    // gamma LUT.
    //
    // Row N+1 is built while row N is on screen, into the buffer half that is not
    // being read.  Writes take whatever cycles the renderer is not using, so a
    // row's 256 bytes are placed long before that row is needed.
    // ------------------------------------------------------------------
    reg [5:0] gen_row;
    reg [7:0] gen_ptr;
    reg [1:0] gen_buf;
    reg       gen_busy;
    reg [7:0] frame_ctr;
    reg       vsync_d;

    wire frame_start = vsync_d && !vga_vsync;
    wire gen_grant   = wr_grant && !stream_mode && gen_busy;

    always @(posedge clk) begin
        if (!rst_n) begin
            gen_row   <= 0;
            gen_ptr   <= 0;
            gen_buf   <= 2'd0;
            gen_busy  <= 0;
            frame_ctr <= 0;
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
            end else if (cell_y && cy == 0 && x_px == 0 && row < ROWS - 1) begin
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

    wire [5:0] gen_col  = gen_ptr[7:2];
    wire [1:0] gen_byte = gen_ptr[1:0];
    wire [3:0] gen_val  = gen_col[3:0] + gen_row[3:0] + frame_ctr[7:4];
    wire [6:0] gen_segs;

    seg7_rom rom (
        .value (gen_val),
        .segs  (gen_segs)
    );

    // Decimal point on every eighth digit, so segment 7 is exercised too.
    wire [7:0] gen_mask = {gen_col[2:0] == 3'b000, gen_segs};
    // Brightness bands across the row.  Never zero, so no digit vanishes.
    wire [3:0] gen_int  = gen_col[3:0] | 4'h1;

    wire [2:0] seg_lo = {gen_byte, 1'b0};
    wire [2:0] seg_hi = {gen_byte, 1'b1};

    wire [7:0] gen_data = {gen_mask[seg_hi] ? gen_int : 4'h0,
                           gen_mask[seg_lo] ? gen_int : 4'h0};

    // ------------------------------------------------------------------
    // Stream port and write arbitration
    // ------------------------------------------------------------------
    wire       str_req;
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
        .req         (str_req),
        .grant       (str_grant),
        .waddr       (str_waddr),
        .wdata       (str_wdata)
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
    // blank.  Layout is SPEC.md section 1.1.
    // ------------------------------------------------------------------
    // The digit body is 10x14 inside the 12x16 cell.  The spare column and the
    // spare two rows are what stop a digit's right rail merging into its
    // neighbour's left rail, and one row's bottom bar merging into the next
    // row's top bar -- without them the grid reads as a mesh rather than as
    // digits.  The decimal point lives in the spare column, which is where a
    // real display puts it.
    wire xz_left  = (cx < 2);                   // f, e
    wire xz_mid   = (cx >= 2)  && (cx < 8);     // a, g, d
    wire xz_right = (cx >= 8)  && (cx < 10);    // b, c
    wire xz_dp    = (cx == 10);                 // DP, cx == 11 is the gap

    wire yz_top   = (cy < 2);                   // a
    wire yz_up    = (cy >= 2)  && (cy < 6);     // f, b
    wire yz_mid   = (cy >= 6)  && (cy < 8);     // g
    wire yz_low   = (cy >= 8)  && (cy < 12);    // e, c
    wire yz_bot   = (cy >= 12) && (cy < 14);    // d, DP; cy 14,15 is the gap

    reg [2:0] seg_idx;
    reg       seg_hit;

    always @* begin
        seg_hit = 1'b1;
        case (1'b1)
            xz_mid   & yz_top: seg_idx = 3'd0;   // a
            xz_right & yz_up : seg_idx = 3'd1;   // b
            xz_right & yz_low: seg_idx = 3'd2;   // c
            xz_mid   & yz_bot: seg_idx = 3'd3;   // d
            xz_left  & yz_low: seg_idx = 3'd4;   // e
            xz_left  & yz_up : seg_idx = 3'd5;   // f
            xz_mid   & yz_mid: seg_idx = 3'd6;   // g
            xz_dp    & yz_bot: seg_idx = 3'd7;   // DP
            default: begin
                seg_idx = 3'd0;
                seg_hit = 1'b0;
            end
        endcase
    end

    wire [3:0] seg_int   = cur_digit[{seg_idx, 2'b00} +: 4];
    wire       visible   = seg_hit && cell_x && cell_y;
    wire [3:0] gamma_idx = visible ? seg_int : 4'h0;

    gamma gamma_lut (
        .idx   (gamma_idx),
        .level (level)
    );

    // level is combinational from the x_px pipeline stage, one cycle behind the
    // raw sync outputs, so delay the syncs to match.
    always @(posedge clk) begin
        hsync <= vga_hsync;
        vsync <= vga_vsync;
    end

endmodule
`default_nettype wire
