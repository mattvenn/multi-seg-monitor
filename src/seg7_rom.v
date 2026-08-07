`default_nettype none
//
// Hex to 7 segment decode.  Only the internal generator needs this -- streamed
// data carries arbitrary segment patterns and never passes through here.
//
// Bit order matches the nibble order of a digit word: a=0, b=1, c=2, d=3, e=4,
// f=5, g=6 (DP is bit 7 of the mask and is supplied by the caller).
//
module seg7_rom (
    input  wire [3:0] value,
    output reg  [6:0] segs
    );

    always @* begin
        case (value)
            4'h0: segs = 7'h3f;
            4'h1: segs = 7'h06;
            4'h2: segs = 7'h5b;
            4'h3: segs = 7'h4f;
            4'h4: segs = 7'h66;
            4'h5: segs = 7'h6d;
            4'h6: segs = 7'h7d;
            4'h7: segs = 7'h07;
            4'h8: segs = 7'h7f;
            4'h9: segs = 7'h6f;
            4'ha: segs = 7'h77;
            4'hb: segs = 7'h7c;
            4'hc: segs = 7'h39;
            4'hd: segs = 7'h5e;
            4'he: segs = 7'h79;
            4'hf: segs = 7'h71;
        endcase
    end

endmodule
`default_nettype wire
