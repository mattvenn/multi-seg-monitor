`default_nettype none
//
// Gamma LUT: 4 bit stored intensity -> 6 bit output level.
//
// Perception is logarithmic, so 16 linear steps band visibly at the low end.
// Each entry is round((idx/15) ** (1/2.2) * 63), true 6-bit precision --
// deliberately not rounded to 4-bit and scaled, which is what an earlier
// version of this table did to avoid a double-rounding truncation loss
// (resolution_discussion.md section 2.1). That avoided losing distinct
// *stored* levels, but bring-up on real hardware (2026-09-10) showed even
// the resulting 12 distinct 4-bit codes were hard to tell apart by eye near
// the bright end. tt_um_multi_seg_monitor.v now dithers the bottom two bits
// instead of discarding them, which only recovers anything if they carry a
// real fraction -- so they matter again, not "always zero" as before.
//
// Synthesised as logic on both ASIC and FPGA rather than living in memory --
// keeping it out of a RAM removes one more way the two platforms could diverge
// (SPEC.md section 2.1).
//
module gamma (
    input  wire [3:0] idx,
    output reg  [5:0] level
    );

    always @* begin
        case (idx)
            4'd0:  level = 6'd0;
            4'd1:  level = 6'd18;
            4'd2:  level = 6'd25;
            4'd3:  level = 6'd30;
            4'd4:  level = 6'd35;
            4'd5:  level = 6'd38;
            4'd6:  level = 6'd42;
            4'd7:  level = 6'd45;
            4'd8:  level = 6'd47;
            4'd9:  level = 6'd50;
            4'd10: level = 6'd52;
            4'd11: level = 6'd55;
            4'd12: level = 6'd57;
            4'd13: level = 6'd59;
            4'd14: level = 6'd61;
            4'd15: level = 6'd63;
        endcase
    end

endmodule
`default_nettype wire
