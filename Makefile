# FPGA build for the TT ICE40UP5K breakout board.
#
# This mirrors what `tt_fpga.py harden` does, so it can be run without the
# tt-support-tools python environment.  Deploying to the demoboard still needs
# the real tool:
#
#     python tt_fpga.py --project-dir . configure --upload
#
TT_TOOLS ?= $(HOME)/asic/tt-support-tools

TOP      = tt_um_multi_seg_monitor
FREQ     = 31.5
PCF      = $(TT_TOOLS)/fpga/tt_fpga_fabricfoxv2.pcf
WRAPPER  = $(TT_TOOLS)/fpga/tt_fpga_top.v

SOURCES  = src/tt_um_multi_seg_monitor.v \
           src/multi_seg_monitor.v \
           src/VgaSyncGen.v \
           src/line_buffer.v \
           src/seg7_rom.v \
           src/gamma.v \
           src/stream_in.v

BUILD    = build

.PHONY: bitstream test clean

bitstream: $(BUILD)/$(TOP).bin

# The wrapper ties uio_in/uio_out/uio_oe to real bidirectional pins and names the
# user module, so it has to be generated per project.
src/_tt_fpga_top.v: $(WRAPPER)
	sed 's/__tt_um_placeholder/$(TOP)/' $< > $@

$(BUILD)/$(TOP).json: src/_tt_fpga_top.v $(SOURCES)
	@mkdir -p $(BUILD)
	yosys -l $(BUILD)/01-synth.log -DSYNTH \
	    -p "read_verilog -sv src/_tt_fpga_top.v $(SOURCES); \
	        synth_ice40 -top tt_fpga_top -json $@"

$(BUILD)/$(TOP).asc: $(BUILD)/$(TOP).json
	nextpnr-ice40 -l $(BUILD)/02-nextpnr.log --pcf-allow-unconstrained \
	    --seed 0 --freq $(FREQ) --package sg48 --up5k \
	    --pcf $(PCF) --json $< --asc $@

$(BUILD)/$(TOP).bin: $(BUILD)/$(TOP).asc
	icepack $< $@

test:
	$(MAKE) -C test
	cd tools && python3 test_video2seg.py

clean:
	rm -rf $(BUILD) src/_tt_fpga_top.v
	$(MAKE) -C test clean
