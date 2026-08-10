`default_nettype none
//
// Gamma LUT: 4 bit stored intensity -> 6 bit output level.
//
// Perception is logarithmic, so 16 linear steps band visibly at the low end.
// Only the top 4 bits (level[5:2] in tt_um_multi_seg_monitor.v) ever reach the
// panel, so each entry is round((idx/15) ** (1/2.2) * 15) scaled back up by 4 --
// not round((idx/15) ** (1/2.2) * 63) rounded independently at 6-bit precision,
// which double-rounds on its way through the truncation and silently loses
// extra levels on top of what the curve's own compression at the bright end
// already costs (resolution_discussion.md section 2.1). The bottom two bits are
// therefore always zero today; they exist for a possible future 6-bit path
// (resolution_discussion.md section 6.2) where they would stop being free.
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
            4'd1:  level = 6'd16;
            4'd2:  level = 6'd24;
            4'd3:  level = 6'd28;
            4'd4:  level = 6'd32;
            4'd5:  level = 6'd36;
            4'd6:  level = 6'd40;
            4'd7:  level = 6'd44;
            4'd8:  level = 6'd44;
            4'd9:  level = 6'd48;
            4'd10: level = 6'd48;
            4'd11: level = 6'd52;
            4'd12: level = 6'd56;
            4'd13: level = 6'd56;
            4'd14: level = 6'd60;
            4'd15: level = 6'd60;
        endcase
    end

endmodule
`default_nettype wire
