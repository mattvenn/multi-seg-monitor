`default_nettype none
//
// Reset strap, config packet and palette state.
//
// Two ways to configure the chip, and the second overrides the first:
//
// 1. The reset strap. ui_in carries stream data whenever rst_n is high, but
//    nothing drives the stream while the core is held in reset, so its low 4
//    bits are free to sample during the reset window:
//      ui_in[0]   pmod_type:  0 = Digilent PmodVGA, 1 = Tiny VGA Pmod
//      ui_in[3:1] preset:     which of palette_presets.v's 8 palettes
//    Re-sampled every cycle rst_n is low, so the *last* value before rst_n
//    rises is what sticks; the host must hold it for the whole pulse. The
//    strap stays even with a packet available because it is what makes the
//    pins safe from the first cycle out of reset: a Tiny VGA board must never
//    see uio driven, even briefly while it waits for a config packet.
//
// 2. The config packet. With stream mode off (uio[7] low) the generator draws
//    the picture and the stream port's strobes would otherwise be ignored, so
//    they carry config bytes instead -- no new pins, and the pixel stream's
//    framing and pacing are untouched. Every drop of uio[7] starts a new
//    packet:
//      byte 0     header: {4'hA, load_preset, cycle_en, 1'b0, pmod_type}
//      bytes 1-9  the curve, R then G then B, 3 bytes per channel:
//                 {knee_x, knee_y}, then {3'b0, m1}, then {3'b0, m2}
//      byte 10+   ignored
//    The magic nibble is there because this pin is live on a bare board: with
//    no host, a stray edge on the strobe must not be able to flip pmod_type
//    and start driving uio. A header without it ignores the whole packet.
//    Curve bytes go straight into pal_params as they arrive -- no shadow copy,
//    because a half-sent palette is merely a wrong colour until the rest
//    arrives, and 54 more flops are not free. tools/segments.py's
//    config_packet() is the host-side encoder.
//
// 3. The DIP switches. In generator mode ui_in carries nothing, so ui_in[6:1]
//    are read live as switches -- the demoboard's spare DIPs, with no host at
//    all:
//      ui_in[3:1]  the palette to hold in manual mode (the same bits the
//                  strap uses, so a board set up by strap alone agrees)
//      ui_in[4]    1 = hold that palette, 0 = change palette automatically
//      ui_in[5]    1 = steady ring speed, 0 = let it wander
//      ui_in[6]    1 = steady source drift, 0 = let it wander
//    All off is the default attract mode. Two things guard them. They are
//    sampled only at frame_start and only applied when two consecutive
//    samples agree, so a config byte sitting on the bus across a frame
//    boundary cannot be read as a switch setting. And dip_live, set at reset
//    and cleared by the first valid header, stops reading them entirely once
//    a host has spoken -- the RP2350 drives these pins to send a packet, and
//    whatever it leaves there afterwards is not a switch position. The last
//    applied setting stays in force after that.
//
// With cycle_en set (its reset value), the generator steps through the
// presets every 512 frames, about 8.5 s, fading through black across the
// change (the fade itself is in multi_seg_monitor.v, applied by zoneplate.v)
// -- the bring-up picture shows all of them with no host at all. Any valid
// header sets cycle_en from its own bit, so a host that loads a palette turns
// cycling off; so does manual mode on the switches, without disturbing
// cycle_en itself.
//
// A module of its own, rather than inline in multi_seg_monitor.v, so
// formal/config_port.sby can prove the protocol on it alone.
//
module config_port (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  data,         // ui_in: config bytes, and the strap in reset
    input  wire        stb,          // synchronised strobe edge, from stream_in
    input  wire        stream_mode,  // uio[7]: config bytes only while low
    input  wire        frame_start,  // sample the switches here
    input  wire        frame_wrap,   // frame_start on the frame frame_ctr wraps
    output reg         pmod_type,
    output reg  [53:0] pal_params,   // the live palette, see palette.v
    output reg  [2:0]  preset_idx,
    output reg         cycle_en,
    output wire        auto_pal,     // the palette is changing on its own
    output wire        vary_phase,   // to zoneplate.v
    output wire        vary_drift
    );

    reg [3:0] cfg_ptr;
    reg       cfg_ok;

    // The switches: dip_s is the last sample, dip_a the setting in force.
    // Both are ui_in[6:1], so bit 0 here is ui_in[1].
    reg       dip_live;
    reg [5:0] dip_s, dip_a;

    wire [2:0] dip_pal      = dip_a[2:0];
    wire       manual       = dip_a[3];
    wire       steady_phase = dip_a[4];
    wire       steady_drift = dip_a[5];

    assign vary_phase = !steady_phase;
    assign vary_drift = !steady_drift;

    wire cfg_stb    = stb && !stream_mode;
    wire cfg_magic  = data[7:4] == 4'hA;
    // Manual mode suspends the automatic change without touching cycle_en,
    // so a host that later takes over still gets the cycling it asked for.
    assign auto_pal = cycle_en && !(dip_live && manual);
    // Skipped, not deferred, if a config byte lands on the same cycle -- one
    // missed step in an 8 s cycle is invisible, a lost config byte isn't.
    wire cycle_tick = auto_pal && !stream_mode && frame_wrap && !cfg_stb;
    // Manual mode chases the switches: a level, not an edge, so it needs no
    // flop of its own and settles the cycle after the switches are applied.
    // Stood down on a config byte for the same reason as cycle_tick.
    wire dip_load   = dip_live && manual && !stream_mode && !cfg_stb &&
                      dip_pal != preset_idx;

    // One table, indexed by whichever preset is about to be loaded.
    wire [2:0]  preset_sel = !rst_n     ? data[3:1] :
                             cycle_tick ? preset_idx + 1'b1 :
                             dip_load   ? dip_pal : preset_idx;
    wire [53:0] preset_params;

    palette_presets presets (
        .sel    (preset_sel),
        .params (preset_params)
    );

    always @(posedge clk) begin
        if (!rst_n) begin
            {preset_idx, pmod_type} <= data[3:0];
            pal_params <= preset_params;
            cycle_en   <= 1'b1;
            cfg_ptr    <= 4'd0;
            cfg_ok     <= 1'b0;
            // All-off is the default attract mode, whatever is on the pins:
            // the switches only take effect once two frames have agreed.
            dip_live   <= 1'b1;
            dip_s      <= 6'd0;
            dip_a      <= 6'd0;
        end else begin
            // ui_in is pixel data in stream mode, so the switches are only
            // read in generator mode, and only until a host speaks.
            if (dip_live && !stream_mode && frame_start) begin
                dip_s <= data[6:1];
                if (data[6:1] == dip_s)
                    dip_a <= data[6:1];
            end

            if (stream_mode) begin
                cfg_ptr <= 4'd0;
            end else if (cfg_stb) begin
                if (cfg_ptr != 4'd10)
                    cfg_ptr <= cfg_ptr + 1'b1;
                if (cfg_ptr == 4'd0) begin
                    cfg_ok <= cfg_magic;
                    if (cfg_magic) begin
                        pmod_type <= data[0];
                        cycle_en  <= data[2];
                        dip_live  <= 1'b0;
                        if (data[3])
                            pal_params <= preset_params;
                    end
                end else if (cfg_ok) begin
                    case (cfg_ptr)
                        4'd1: pal_params[53:46] <= data;       // R knee
                        4'd2: pal_params[45:41] <= data[4:0];  // R m1
                        4'd3: pal_params[40:36] <= data[4:0];  // R m2
                        4'd4: pal_params[35:28] <= data;       // G knee
                        4'd5: pal_params[27:23] <= data[4:0];  // G m1
                        4'd6: pal_params[22:18] <= data[4:0];  // G m2
                        4'd7: pal_params[17:10] <= data;       // B knee
                        4'd8: pal_params[9:5]   <= data[4:0];  // B m1
                        4'd9: pal_params[4:0]   <= data[4:0];  // B m2
                        default: ;
                    endcase
                end
            end

            if (cycle_tick) begin
                preset_idx <= preset_idx + 1'b1;
                pal_params <= preset_params;
            end else if (dip_load) begin
                preset_idx <= dip_pal;
                pal_params <= preset_params;
            end
        end
    end

`ifdef FORMAL
    // `read_verilog -formal` (see formal/config_port.sby) defines FORMAL in
    // place of SYNTHESIS, so none of this reaches synthesis or simulation.
    //
    // The protocol as a specification, written independently of the case
    // statement above: which events may change which state, and exactly what
    // each byte does to pal_params. data/stb/stream_mode/frame_wrap are free
    // inputs, so this covers any host behaviour, not just a well-formed one.

    reg f_past_valid = 1'b0;
    always @(posedge clk)
        f_past_valid <= 1'b1;

    // Registers power up undefined (CLAUDE.md: never assume initialised
    // memory), so claims about cfg_ptr's range only hold once a real reset
    // has happened.
    reg f_reset_done = 1'b0;
    always @(posedge clk)
        if (!rst_n)
            f_reset_done <= 1'b1;

    // The documented layout, from the packet's point of view: curve byte n
    // (1-9) is channel (n-1)/3 (R, G, B from the top), field (n-1)%3 (knee,
    // m1, m2). Returns params with that one field replaced.
    function [53:0] f_apply;
        input [53:0] params;
        input [3:0]  n;
        input [7:0]  byte_in;
        integer base;
        begin
            base    = 36 - 18 * ((n - 1) / 3);
            f_apply = params;
            case ((n - 1) % 3)
                0: f_apply[base + 10 +: 8] = byte_in;       // {knee_x, knee_y}
                1: f_apply[base + 5  +: 5] = byte_in[4:0];  // m1
                2: f_apply[base      +: 5] = byte_in[4:0];  // m2
            endcase
        end
    endfunction

    wire f_header   = cfg_stb && cfg_ptr == 4'd0;
    wire f_valid_hd = f_header && cfg_magic;
    wire f_curve    = cfg_stb && cfg_ok && cfg_ptr >= 4'd1 && cfg_ptr <= 4'd9;

    // f_curve, f_valid_hd and cycle_tick all need cfg_stb or frame_wrap;
    // dip_load stands down on cfg_stb and cycle_tick cannot be true while
    // manual is, so at most one of the four fires in any cycle. The palette
    // assertions below lean on that.
    always @(posedge clk)
        if (f_reset_done && rst_n)
            assert (!(dip_load && (f_curve || f_header || cycle_tick)));

    always @(posedge clk) begin
        if (f_reset_done && rst_n)
            assert (cfg_ptr <= 4'd10);

        if (f_past_valid && !$past(rst_n)) begin
            // Leaving reset: the strap, and the preset it names.
            assert (pmod_type  == $past(data[0]));
            assert (preset_idx == $past(data[3:1]));
            assert ($past(preset_sel) == $past(data[3:1]));
            assert (pal_params == $past(preset_params));
            assert (cycle_en);
            assert (!cfg_ok && cfg_ptr == 4'd0);
            // The switches start out ignored, whatever is on the pins: it
            // takes two agreeing frame samples to apply any setting.
            assert (dip_live && dip_a == 6'd0 && dip_s == 6'd0);
        end

        if (f_past_valid && $past(rst_n) && rst_n) begin
            // Stream mode means pixels: nothing about the configuration
            // moves while it's on, however the strobe toggles.
            if ($past(stream_mode)) begin
                assert ($stable(pmod_type));
                assert ($stable(pal_params));
                assert ($stable(preset_idx));
                assert ($stable(cycle_en));
                assert (cfg_ptr == 4'd0);
                // ui_in is pixel data here, so the switches must not read it.
                assert ($stable(dip_live) && $stable(dip_a) && $stable(dip_s));
            end

            // dip_live is the host-takeover latch: it only ever falls, and
            // only on a valid header. Nothing else -- no strobe, no frame,
            // no pin pattern -- can hand the switches back or take them away.
            if (!$stable(dip_live)) begin
                assert ($past(f_valid_hd));
                assert (!dip_live);
            end
            if ($past(f_valid_hd))
                assert (!dip_live);

            // A setting is only applied when the sample before it agreed,
            // and only while the switches are being read at all.
            if (!$stable(dip_a))
                assert ($past(dip_live && !stream_mode && frame_start &&
                              data[6:1] == dip_s));

            // pmod_type and cycle_en change only on a header with the magic
            // nibble, and then to exactly what it says.
            if (!$stable(pmod_type) || !$stable(cycle_en))
                assert ($past(f_valid_hd));
            if ($past(f_valid_hd)) begin
                assert (pmod_type == $past(data[0]));
                assert (cycle_en  == $past(data[2]));
            end

            // A packet can only write the curve after a good header: cfg_ok
            // rises on nothing else, and a bad header drops it.
            if ($rose(cfg_ok))
                assert ($past(f_valid_hd));
            if ($past(f_header && !cfg_magic))
                assert (!cfg_ok);

            // Each curve byte replaces exactly its own field, nothing else.
            if ($past(f_curve) && !$past(cycle_tick))
                assert (pal_params == f_apply($past(pal_params), $past(cfg_ptr), $past(data)));

            // load_preset and the cycle tick both load from the table.
            if ($past(f_valid_hd && data[3]) && !$past(cycle_tick))
                assert (pal_params == $past(preset_params) &&
                        $past(preset_sel) == $past(preset_idx));
            if ($past(cycle_tick)) begin
                assert (preset_idx == $past(preset_idx) + 3'd1);
                assert (pal_params == $past(preset_params) &&
                        $past(preset_sel) == preset_idx);
            end

            // Manual mode: the switches name the preset, and loading it is
            // the only thing they may do to the palette state.
            if ($past(dip_load)) begin
                assert (preset_idx == $past(dip_pal));
                assert (pal_params == $past(preset_params) &&
                        $past(preset_sel) == preset_idx);
            end

            // ...and nothing else touches the palette.
            if (!$stable(pal_params))
                assert ($past(f_curve || (f_valid_hd && data[3]) || cycle_tick ||
                              dip_load));
            if (!$stable(preset_idx))
                assert ($past(cycle_tick || dip_load));
        end
    end

    // Reachability, so the asserts above aren't vacuous: a whole good packet
    // gets through, and a cycle tick happens.
    always @(posedge clk) begin
        if (f_reset_done && rst_n) begin
            cover (cfg_ok && cfg_ptr == 4'd10);
            cover ($past(cycle_tick));
            cover ($past(dip_load));
            cover (!dip_live);
        end
    end
`endif

endmodule
`default_nettype wire
