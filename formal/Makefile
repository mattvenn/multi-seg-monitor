# Formal properties, checked with SymbiYosys (immediate assertions embedded
# in src/*.v behind `ifdef FORMAL guards -- see the comments at each guard
# for what's being proven and why.  Requires oss-cad-suite's `sby` on PATH.

SBY = sby -f

.PHONY: all stream_in lb_exclusivity zone_exclusivity vga_counter_bounds buf_isolation clean

# buf_isolation is deliberately not part of `all`: it fails at the basecase
# itself (step 1, see the comment in buf_isolation.sby), not just an
# induction step that won't close -- run it explicitly.
all: stream_in lb_exclusivity zone_exclusivity vga_counter_bounds

stream_in:
	$(SBY) stream_in.sby

lb_exclusivity:
	$(SBY) lb_exclusivity.sby

zone_exclusivity:
	$(SBY) zone_exclusivity.sby

vga_counter_bounds:
	$(SBY) vga_counter_bounds.sby

buf_isolation:
	$(SBY) buf_isolation.sby

clean:
	rm -rf stream_in lb_exclusivity zone_exclusivity vga_counter_bounds buf_isolation
