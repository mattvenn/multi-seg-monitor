# RM_IHPSG13_1P_1024x8_c2_bm_bist

IHP SG13G2 foundry-provided 1024x8 single-port SRAM macro with BIST, used as the
line buffer for the ASIC build. See SPEC.md section 8.2.

## Source

**Repository**: https://github.com/IHP-GmbH/IHP-Open-PDK, path
`ihp-sg13g2/libs.ref/sg13g2_sram/`.

Two different commits, deliberately:

| Files | Commit |
|---|---|
| `.gds`, `.lef`, `.cdl`, models | `7c124b7324778fbc2261aa8529ba04388eb3339e` |
| `.lib` (all three corners) | `d490cfb2e325` |

The physical views are pinned to the same commit as `tt_um_urish_sram_test`, which
taped out on ttihp0p2 with this macro — the PG pin layer fix in it
("fixed PG pins Metal1.txt and Metal4.txt layers", #239) is what the custom PDN
config in `src/pdn_cfg.tcl` depends on. The `.gds` has changed upstream since, so it
stays at the version that has actually been through a shuttle.

The liberty files could not stay there. At `7c124b7` they read:

```
max_capacitance : "6.4e-14" ;
```

but the library declares `capacitive_load_unit (1, pf)`, so that is 6.4e-14 **pF**,
not the 0.064 pF intended — the number was written in Farads. OpenROAD reads it as
zero and `repair_design` aborts the build:

```
[RSZ-0169] Max cap for driver core.lb.u_sram/A_DOUT[7] of type
RM_IHPSG13_1P_1024x8_c2_bm_bist is unreasonably small 0.000pF.
Min buffer or inverter input cap is 0.001pF
```

`d490cfb2e325` ("fix(sram): fixed max_cap units in Liberty views", #767, 2026-01-13)
corrects it to `0.064`. That one line is the **entire** diff against `7c124b7` in all
three corners, so taking the newer liberty changes nothing else.

That fix postdates `tt_um_urish_sram_test`, which is why following its pinning
exactly does not work here.

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
