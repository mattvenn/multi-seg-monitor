SIM ?= icarus
TOPLEVEL_LANG ?= verilog
SRC_DIR = $(PWD)/../src

# The macro's behavioural models are vendored in test/models rather than taken
# from $PDK_ROOT, so an RTL run against the macro needs no PDK installed.  They
# came from the same IHP-Open-PDK commit as the physical views -- see
# macro/RM_IHPSG13_1P_1024x8_c2_bm_bist/README.md.
MODEL_DIR = $(PWD)/models

ifneq ($(GATES),yes)

# RTL simulation.
SIM_BUILD = sim_build/rtl
VERILOG_SOURCES += $(SRC_DIR)/tt_um_multi_seg_monitor.v
VERILOG_SOURCES += $(SRC_DIR)/multi_seg_monitor.v
VERILOG_SOURCES += $(SRC_DIR)/VgaSyncGen.v
VERILOG_SOURCES += $(SRC_DIR)/line_buffer.v
VERILOG_SOURCES += $(SRC_DIR)/seg7_rom.v
VERILOG_SOURCES += $(SRC_DIR)/gamma.v
VERILOG_SOURCES += $(SRC_DIR)/stream_in.v

# Swap the inferred array for the IHP macro, which is what the ASIC build
# hardens (src/config.json defines IHP_SRAM):
#
#     make IHP_SRAM=1
#
ifdef IHP_SRAM
VERILOG_SOURCES += $(MODEL_DIR)/RM_IHPSG13_1P_1024x8_c2_bm_bist.v
VERILOG_SOURCES += $(MODEL_DIR)/RM_IHPSG13_1P_core_behavioral_bm_bist.v
COMPILE_ARGS += -DIHP_SRAM -DFUNCTIONAL
endif

else

# Gate level simulation.  The netlist is copied in by the GDS action workflow.
#
# FUNCTIONAL is not optional for the macro.  Without it the model takes its
# timing path, where the internal _DELAY signals are driven only as $setuphold
# negative-check outputs -- Icarus leaves them undriven and the memory sees X on
# every pin.
SIM_BUILD = sim_build/gl
COMPILE_ARGS += -DGL_TEST
COMPILE_ARGS += -DFUNCTIONAL
COMPILE_ARGS += -DSIM
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13g2/libs.ref/sg13g2_io/verilog/sg13g2_io.v
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13g2/libs.ref/sg13g2_stdcell/verilog/sg13g2_stdcell.v
VERILOG_SOURCES += $(MODEL_DIR)/RM_IHPSG13_1P_1024x8_c2_bm_bist.v
VERILOG_SOURCES += $(MODEL_DIR)/RM_IHPSG13_1P_core_behavioral_bm_bist.v
VERILOG_SOURCES += $(PWD)/gate_level_netlist.v

endif

VERILOG_SOURCES += $(PWD)/tb.v

COMPILE_ARGS += -g2012

TOPLEVEL = tb
COCOTB_TEST_MODULES = test_multi_seg

include $(shell cocotb-config --makefiles)/Makefile.sim

# Vsync-to-first-byte delay sweep.  Ten full frame captures, so it is kept out of
# the default run -- about ten minutes against the suite's two and a half, and CI
# runs the suite twice (inferred array and IHP macro).
#
#     make delay-sweep
#
# Writes frame_delay_0100us.png .. frame_delay_1000us.png.  results.xml is
# removed first because it is what Makefile.sim uses to decide the run is already
# up to date, and nothing here changes the sources it depends on.
# The filter values carry their own double quotes because Makefile.sim pastes
# COCOTB_TEST_FILTER into the recipe unquoted, so a regex containing | would
# otherwise be read by the shell as a pipeline.
delay-sweep: testcard.seg
	rm -f results.xml
	DELAY_SWEEP=1 COCOTB_TEST_FILTER='"test_stream_delay_.*"' $(MAKE)

# Rewrite the gold images after an intended change to the picture.  Only the
# three deterministic captures have gold; look at what this produces before
# committing it, because nothing else will.
gold: testcard.seg
	rm -f results.xml
	GOLD_UPDATE=1 DELAY_SWEEP=1 \
	    COCOTB_TEST_FILTER='"test_render_frame|test_stream_frame|test_stream_delay_0000us"' \
	    $(MAKE)

# testcard.seg is committed, so this only fires if it has been deleted.  The
# sweep must not depend on the local ffmpeg: a different build rounding one
# pixel differently would break the gold comparison for no real reason.
testcard.seg:
	../tools/testcard.sh

clean::
	rm -rf frame.ppm frame.png sim_build results.xml tb.vcd
	rm -f frame_delay_*.png *_diff.png testcard.png
