SIM ?= icarus
TOPLEVEL_LANG ?= verilog
SRC_DIR = $(PWD)/../src

VERILOG_SOURCES += $(SRC_DIR)/tt_um_multi_seg_monitor.v
VERILOG_SOURCES += $(SRC_DIR)/multi_seg_monitor.v
VERILOG_SOURCES += $(SRC_DIR)/VgaSyncGen.v
VERILOG_SOURCES += $(SRC_DIR)/line_buffer.v
VERILOG_SOURCES += $(SRC_DIR)/seg7_rom.v
VERILOG_SOURCES += $(SRC_DIR)/gamma.v
VERILOG_SOURCES += $(PWD)/tb.v

COMPILE_ARGS += -g2012

TOPLEVEL = tb
COCOTB_TEST_MODULES = test_multi_seg

include $(shell cocotb-config --makefiles)/Makefile.sim

clean::
	rm -rf frame.ppm frame.png sim_build results.xml tb.vcd
