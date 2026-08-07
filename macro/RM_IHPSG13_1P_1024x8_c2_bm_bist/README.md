# RM_IHPSG13_1P_1024x8_c2_bm_bist

IHP SG13G2 foundry-provided 1024x8 single-port SRAM macro with BIST, used as the
line buffer for the ASIC build. See SPEC.md section 8.2.

## Source

- **Repository**: https://github.com/IHP-GmbH/IHP-Open-PDK
- **Commit**: `7c124b7324778fbc2261aa8529ba04388eb3339e`
  ("SRAM cells layout: fixed PG pins Metal1.txt and Metal4.txt layers (#239)")
- **Path**: `ihp-sg13g2/libs.ref/sg13g2_sram/`

Pinned to the same commit as `tt_um_urish_sram_test`, which taped out on ttihp0p2
with this macro. The PG pin layer fix in that commit is what the custom PDN config
in `src/pdn_cfg.tcl` depends on.

## Contents

Physical and timing views, referenced from `src/config.json`:

| File | Used for |
|---|---|
| `.gds` | Final layout merge |
| `.lef` | Abstract view for place and route |
| `*_typ_1p20V_25C.lib` | Nominal corner timing |
| `*_fast_1p32V_m55C.lib` | Min corner timing |
| `*_slow_1p08V_125C.lib` | Max corner timing |
| `.cdl` | SPICE netlist for LVS |

Two more files from the same commit live outside this directory:

- `test/models/` — the behavioural Verilog models, for simulation. Vendored so an
  RTL run against the macro needs no PDK checkout.
- `src/RM_IHPSG13_1P_1024x8_c2_bm_bist.v` — **not** from the PDK. A hand-written
  ports-only blackbox stub, because yosys cannot parse the real model's `specify`
  block. See the comment at the top of that file.

## License

These files are part of IHP-Open-PDK and are licensed under the Apache License 2.0.
See the [IHP-Open-PDK repository](https://github.com/IHP-GmbH/IHP-Open-PDK) for details.
