`default_nettype none
//
// Stream input port.
//
// The host puts a byte on ui_in and pulses the strobe.  Bytes fill the line
// buffer in order: byte n of the frame is digit n/4, nibble pair n%4.  The
// pointer resets on vsync, so the link is self-synchronising -- a lost or extra
// byte costs one frame and then corrects itself, rather than corrupting the
// display until someone rewrites it.
//
// Only the low two bits of the row counter reach the address, because the buffer
// holds four rows.  Staying in step is the host's job, but four buffers make that
// easy: it needs to run between one and three rows ahead of the raster, which a
// fixed byte rate restarted each frame achieves without tracking rows at all.
// See SPEC.md section 4.3.
//
module stream_in #(
    parameter ROW_BYTES = 208,
    parameter ROWS      = 30
    )
    (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] data,         // ui_in
    input  wire       strobe,       // asynchronous, from the host
    input  wire       frame_start,  // vsync edge
    output reg        req,          // a byte is waiting to be written
    input  wire       grant,
    output wire [9:0] waddr,
    output wire [7:0] wdata
    );

    // The strobe crosses from the host's clock domain.  Two flops to synchronise
    // and a third to find the edge.  At roughly 64 pixel clocks per strobe there
    // is no shortage of time for this.
    reg [2:0] sync;
    always @(posedge clk)
        sync <= {sync[1:0], strobe};

    wire strobe_rise = sync[1] && !sync[2];

    reg [7:0] hold;
    reg [5:0] s_row;  // 6 bits: ROWS-1 (36 for this design's 37 rows) doesn't
                       // fit in 5, which silently made the wrap-at-ROWS
                       // comparison below dead code (formal caught this).
    reg [7:0] s_byte;

    always @(posedge clk) begin
        if (!rst_n || frame_start) begin
            s_row  <= 0;
            s_byte <= 0;
            req    <= 1'b0;
        end else begin
            if (grant) begin
                req <= 1'b0;
                // verilator lint_off WIDTHEXPAND
                // ROW_BYTES is an unsized parameter (32-bit by Verilog default)
                // compared against 8-bit s_byte; values always fit (<=256), so
                // this is a width-checker nitpick, not a real truncation risk.
                if (s_byte == ROW_BYTES - 1) begin
                // verilator lint_on WIDTHEXPAND
                    s_byte <= 0;
                    s_row  <= (s_row == ROWS - 1) ? 6'd0 : s_row + 1'b1;
                end else begin
                    s_byte <= s_byte + 1'b1;
                end
            end
            // A strobe arriving in the same cycle as a grant is fine and must not
            // drop the byte: wdata still carries the old hold value for this
            // cycle's write, and req stays set for the new one.
            if (strobe_rise) begin
                hold <= data;
                req  <= 1'b1;
            end
        end
    end

    assign waddr = {s_row[1:0], s_byte};
    assign wdata = hold;

`ifdef FORMAL
    // `read_verilog -formal` (see formal/) defines FORMAL in place of
    // SYNTHESIS, so none of this reaches synthesis or ordinary simulation.

    // $past() is free/uninitialised at the very first checked cycle -- no
    // real clock edge has happened yet for it to remember -- so every
    // $past-based assertion below is gated on this, or BMC can "prove" a
    // violation out of that non-history instead of a real one.
    reg f_past_valid = 1'b0;
    always @(posedge clk)
        f_past_valid <= 1'b1;

    // s_row's full 6 bit range includes values (37-63) that are only ever
    // reachable if the register happens to power up there and rst_n is
    // never actually asserted -- a real precondition (CLAUDE.md: "Contents
    // are undefined at power-up... nothing may depend on initialisation"),
    // not a corner case to prove around. The bound below only has to hold
    // once a real reset has happened at least once.
    reg f_reset_done = 1'b0;
    always @(posedge clk)
        if (!rst_n)
            f_reset_done <= 1'b1;

    // A pending request is only ever cleared by a grant or a new frame --
    // never withdrawn on its own.  If a future edit let req drop by itself,
    // the host's byte would vanish with nothing to signal it happened.
    always @(posedge clk)
        if (f_past_valid && rst_n && $past(rst_n) && $past(req) && !$past(grant) && !$past(frame_start))
            assert (req);

    // Both halves of waddr must stay inside the row this instance is
    // configured for, or the wrap logic (s_byte == ROW_BYTES-1,
    // s_row == ROWS-1) has an off-by-one.
    always @(posedge clk)
        if (rst_n) begin
            assert (s_byte < ROW_BYTES);
            if (f_reset_done)
                assert (s_row < ROWS);
        end

    // Data integrity, under the documented host discipline (CLAUDE.md
    // "Pacing is the load-bearing invariant"): the host paces at 66 pixel
    // clocks per byte, far slower than the arbiter's near-every-other-cycle
    // grants, so it never strobes a new byte before the previous one is
    // granted. That discipline isn't derivable from this module alone, so
    // it's an assume rather than something proved -- under it, the byte
    // that reaches wdata is always the one most recently strobed, never a
    // stale or silently dropped one.
    // Purely formal bookkeeping, not a real register, so it may (and must)
    // start at a known value rather than the power-up-undefined discipline
    // real hardware regs follow: otherwise BMC's first checked cycle can
    // pick shadow_valid true with a shadow value that never corresponded to
    // any real hold, a false failure rather than a real one.
    reg [7:0] shadow;
    reg       shadow_valid = 1'b0;

    always @(posedge clk) begin
        if (!rst_n || frame_start) begin
            shadow_valid <= 1'b0;
        end else begin
            assume (!(strobe_rise && req));
            // Mirrors the real req/hold block's statement order exactly:
            // on the documented overlap cycle (grant and strobe_rise
            // together), Verilog resolves simultaneous nonblocking writes
            // to the same reg by last-in-program-order, so shadow_valid
            // must clear-then-set in the same order req does, or this
            // ghost model disagrees with the real register on that cycle.
            if (grant) begin
                if (f_reset_done && rst_n && shadow_valid)
                    assert (wdata == shadow);
                shadow_valid <= 1'b0;
            end
            if (strobe_rise) begin
                shadow       <= data;
                shadow_valid <= 1'b1;
            end
        end
    end

    // shadow/shadow_valid are built to track hold/req exactly, but that's
    // only implied by construction, not stated anywhere -- k-induction
    // can't take it on faith, so without saying it explicitly it's free to
    // start its induction window from a state where they've already
    // silently diverged (shadow_valid true with a shadow that was never
    // really hold), only to have that surface later at the assert above.
    // Stating it turns it into a fact induction can carry forward itself.
    always @(posedge clk)
        if (f_reset_done) begin
            assert (shadow_valid == req);
            assert (!shadow_valid || (shadow == hold));
        end
`endif

endmodule
`default_nettype wire
