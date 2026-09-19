# Formal properties, checked with SymbiYosys (immediate assertions embedded
# in src/*.v behind `ifdef FORMAL guards -- see the comments at each guard
# for what's being proven and why.  Requires oss-cad-suite's `sby` on PATH.

SBY = sby -f

.PHONY: all stream_in config_port vga_counter_bounds clean

all: stream_in config_port vga_counter_bounds

stream_in:
	$(SBY) stream_in.sby

config_port:
	$(SBY) config_port.sby

vga_counter_bounds:
	$(SBY) vga_counter_bounds.sby

clean:
	rm -rf stream_in config_port_prove config_port_cover vga_counter_bounds
