`default_nettype none
//
// GENERATED from tools/palette_builder/presets.json by
// `tools/palette_builder/palette_builder.py --write-rtl` -- edit the JSON
// (or use the builder), not this file. tools/test_palettes.py fails if the
// two drift apart.
//
// The chip's 8 built-in palettes as curve parameters for src/palette.v:
// {R, G, B}, each {knee_x[3:0], knee_y[3:0], m1[4:0], m2[4:0]}, slopes in
// eighths. A constant table like this costs a few gates per preset, not
// flops -- only the live palette in multi_seg_monitor.v is stored.
//
module palette_presets (
    input  wire [2:0]  sel,
    output reg  [53:0] params
    );

    always @* begin
        case (sel)
            // 0: grey  r(8, 8, 15, 15)  g(8, 8, 15, 15)  b(8, 8, 15, 15)
            3'd0: params = {4'd8, 4'd8, 5'd8, 5'd8, 4'd8, 4'd8, 5'd8, 5'd8, 4'd8, 4'd8, 5'd8, 5'd8};
            // 1: blue  r(8, 8, 15, 13)  g(10, 4, 15, 12)  b(3, 8, 9, 15)
            3'd1: params = {4'd8, 4'd8, 5'd8, 5'd6, 4'd10, 4'd4, 5'd3, 5'd13, 4'd3, 4'd8, 5'd21, 5'd9};
            // 2: green  r(8, 0, 15, 12)  g(0, 0, 4, 7)  b(1, 0, 15, 12)
            3'd2: params = {4'd8, 4'd0, 5'd0, 5'd14, 4'd0, 4'd0, 5'd0, 5'd14, 4'd1, 4'd0, 5'd0, 5'd7};
            // 3: purple  r(4, 10, 15, 13)  g(9, 5, 15, 12)  b(6, 9, 15, 12)
            3'd3: params = {4'd4, 4'd10, 5'd20, 5'd2, 4'd9, 4'd5, 5'd4, 5'd9, 4'd6, 4'd9, 5'd12, 5'd3};
            // 4: amber  r(1, 0, 8, 15)  g(1, 0, 15, 14)  b(10, 0, 15, 5)
            3'd4: params = {4'd1, 4'd0, 5'd0, 5'd17, 4'd1, 4'd0, 5'd0, 5'd8, 4'd10, 4'd0, 5'd0, 5'd8};
            // 5: red  r(0, 0, 8, 15)  g(8, 0, 15, 14)  b(8, 0, 15, 10)
            3'd5: params = {4'd0, 4'd0, 5'd0, 5'd15, 4'd8, 4'd0, 5'd0, 5'd16, 4'd8, 4'd0, 5'd0, 5'd11};
            // 6: cyan  r(3, 0, 15, 7)  g(8, 8, 10, 11)  b(0, 0, 12, 15)
            3'd6: params = {4'd3, 4'd0, 5'd0, 5'd5, 4'd8, 4'd8, 5'd8, 5'd12, 4'd0, 4'd0, 5'd0, 5'd10};
            // 7: fire  r(0, 0, 11, 15)  g(5, 0, 6, 2)  b(11, 0, 15, 11)
            3'd7: params = {4'd0, 4'd0, 5'd0, 5'd11, 4'd5, 4'd0, 5'd0, 5'd16, 4'd11, 4'd0, 5'd0, 5'd22};
        endcase
    end

endmodule
`default_nettype wire
