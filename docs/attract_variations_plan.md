# Attract-mode generator: slow variations, DIP-switch palette control

> **Running this elsewhere.** This file lives on branch `attract-zoneplate`.
> On another computer, `git fetch && git switch attract-zoneplate` and point
> Claude at `docs/attract_variations_plan.md`.
>
> The session memory from the first computer won't be there, so this file
> carries the numbers that matter. Repo conventions are in `CLAUDE.md`, and
> the model is `tools/attract_proto.py`.

## Context
The internal generator now draws a zone plate (`src/zoneplate.v`) in place of
the old hex test pattern. It's on branch **`attract-zoneplate`** at commit
`20742b3`, pushed to `github.com/mattvenn/multi-seg-monitor`, and CI is green:
GDS, precheck, GL test, formal and cocotb all pass.

Matt wants three things added:
- **Slow variations for interest.** The rings' flow speed (phase speed) and
  the sources' speed (drift) change gradually. There must be **no sudden
  changes**: only speeds vary, and positions and phases never jump.
- **Palette changes.** Automatic changes that fade to black and back, or a
  manual mode that holds one palette.
- **DIP switches.** The spare switches choose between those behaviours.

The chip should still start in the generator, and the RP2350 streams when it
drives `uio[7]` high. That already works: `stream_mode = uio_in[7]`.

**Area is the constraint.** The die is 2×2 tiles, which is a fixed decision.
Current CI numbers:
- `design__instance__area__stdcell` is 48,660 µm².
- Counting the SRAM macro plus its 10 µm halo (60,746 µm² fixed) against the
  126,685 µm² core, the die is **86.4%** full. Matt's comfort lines are 80%,
  and 85% "with work".
- This work is estimated at +3–4k µm² synthesised, about 89–90%.

Measure early, and trim if needed (see step 8).

The attract-mode study that led here also rejected: plasma (too big),
interference alongside the zone plate (doesn't fit at 2×2), dithering, and
per-digit sampling. Don't revisit them.

## Switch map (generator mode; all DIPs off = default attract mode)
| `ui_in` | Off (default) | On |
|---|---|---|
| [0] | PmodVGA | Tiny VGA (**reset strap only**, unchanged; never live) |
| [3:1] | starting palette (strap, unchanged) | the palette held in manual mode |
| [4] | **auto**: next palette every 512 frames with a fade through black | **manual**: hold palette `[3:1]`, live |
| [5] | phase speed varies slowly | steady phase speed |
| [6] | drift varies slowly | steady drift |
| [7] | spare | spare |

Rules:
- **Generator mode only.** The switches are read only while `!stream_mode`.
  While streaming, `ui_in` is pixel data and is ignored.
- **Host takeover.** A new `dip_live` flop is set on reset and cleared by any
  valid config header (magic `0xA`), the same way a header already sets
  `cycle_en`. While it's clear, the last applied switch settings stay put.
  This is needed because the RP2350 drives these pins when it sends a packet.
- **Debounce.** Sample `ui_in[6:1]` at `frame_start`, and apply it only when
  two consecutive samples agree. That rejects a packet byte sitting on the
  bus across a single frame boundary.
- **Auto-change condition.** Automatic palette changes happen when
  `cycle_en && !(dip_live && manual)`. `cycle_en` keeps its existing meaning
  for hosts.

## Design

### 1. Motion registers (`src/zoneplate.v`)
Both registers are reset to 0 and update on `frame_start`, in the same edge
where `frame_ctr` advances.

**`ring_ph`**, 9 bits (7 integer + 2 fraction):
- Update: `ring_ph += rate_ph`, in quarter units.
- Steady: `rate_ph = 4`, which is today's speed of 1 per frame.
- Varying: `rate_ph = tri(frame_ctr[12:8], 5) - 3`, giving −3..12. That runs
  from slightly reversed, through stopped, to 3×. It changes by one step every
  256 frames, over a 2.3-minute cycle.
- The fold's phase term becomes `ring_ph[8:2]`, replacing `frame[6:0]`.

**`t_src`**, 17 bits (14 integer + 3 fraction):
- Update: `t_src += rate_t`, in eighths.
- Steady: `rate_t = 8`.
- Varying: `rate_t = 4 + tri(frame_ctr[13:10], 4)`, giving 4..11 (0.5–1.4×)
  over a 4.5-minute cycle.
- The source products (`f24`, `f16`, `f20`) use `t = t_src[16:3]` instead of
  `frame`. With `rate_t = 8` this equals the old behaviour.

**New inputs:** `vary_phase`, `vary_drift` and the top bits of `frame_ctr` (for
the slow rate waves). `frame` stays for these rate waves only.

**Timing:** the prologue already computes the source points on the cycle after
`frame_start`, so it sees the updated `t_src`.

### 2. Palette auto-change and fade
**Timing (`src/multi_seg_monitor.v`):**
- The change step moves from every 256 frames to every 512: `frame_wrap`
  becomes `frame_start && frame_ctr[8:0] == 9'h1FF`.
- Fade level: with `k = frame_ctr[8:0]`, fade = `k < 32 ? k >> 1 : k >= 480 ? (511 - k) >> 1 : 15`.
  This is purely combinational, with no new flops.
- The fade applies only while auto-changes are active.
- The palette switches at `frame_wrap`. Frames 511 and 0 are both at fade
  level 0, so the change is hidden in black.

**Where the fade is applied (`src/zoneplate.v`):** each level becomes
`(level * (fade + 1)) >> 4` before it is stored in `lo`/`hi`. Reuse the model's
`apply_fade()` in `tools/attract_proto.py`.

**Reset value of `frame_ctr`:** reset it to **32**, not 0, so power-up starts at
full brightness rather than mid fade-in. The gate-level and gold captures run a
few frames after reset, and those checks (lit fraction, all 16 DAC codes) need
a bright frame. Comment why.

### 3. `src/config_port.v`
Add:
- `dip_live`;
- the debounced switch sample and its applied state: `manual`, `steady_phase`,
  `steady_drift`;
- a manual-mode palette load. When `dip_live && manual && !stream_mode` and the
  applied `[3:1]` differs from `preset_idx`, load it, reusing the existing
  `preset_sel` / `palette_presets` path.

Export `vary_phase`/`vary_drift` (or the applied switch bits) to the core,
which wires them to `zoneplate`.

Update the header comment. It documents the protocol and is referenced by
`README.md` and `CLAUDE.md`.

### 4. Python model (`tools/attract_proto.py`)
- Split the zone plate so it can be driven from either source:
  - `zoneplate_at(t, phase, fade=15, **params)` is the core;
  - `zoneplate_frame(frame, ...)` keeps working through it, with `t = frame`
    and `phase = (frame * phase_speed) >> 2`.
- Add a bit-exact model of the chip's per-frame state:
  - `chip_step(state, frame_ctr, vary_phase, vary_drift)` advances
    `(ring_ph, t_src)`;
  - `chip_fade(frame_ctr, auto)` gives the fade level;
  - a `chip` effect simulates from reset to frame N with constant switches.
- Give the `chip` effect `PARAMS` sliders `vary_phase` 0/1 and `vary_drift`
  0/1. The palette builder (`tools/palette_builder/`) then picks it up with no
  builder changes. The preview shows the variations and the brightness dips at
  512-frame boundaries; it doesn't cycle the palette itself.

### 5. Tests (`test/test_multi_seg.py`, cocotb)
**Update:**
- `test_generator_matches_zoneplate_model` reads `zp.t_src`, `zp.ring_ph` and
  `frame_ctr` from the hierarchy (it is `@rtl_only`) and checks every segment
  against `zoneplate_at(...)` with the fade.
- `test_generator_cycles_through_presets` pokes `frame_ctr` to `0x1FF` instead
  of `0xFF`.

**Add:**
- **Motion step:** set the switches and poke `frame_ctr` to a few chosen
  values (including across an LFO step). Across one `frame_start`, the
  registers must equal `chip_step`, for all four switch combinations.
- **Manual palette:** `ui_in[4]=1` with `[3:1]=5` gives `preset_idx == 5` after
  two frames. Flip the switches and it follows. A valid config header then
  freezes it (`dip_live` cleared), and `stream_mode` ignores the switches.
- **Fade:** poke `frame_ctr` near 511 and near 0, capture a frame, and compare
  it with the model. Also check that manual mode never fades.

**Regenerate** the gold images with `SIM=verilator make -C test gold`. The
`$|` bug in that target is already fixed on this branch. **Look at the
images** before committing.

### 6. Formal (`formal/config_port.sby`)
The existing properties in `config_port.v` pin down which events may change
`preset_idx` and `cycle_en`. Extend them with:
- the manual-load path;
- `dip_live` falling only on a valid header;
- `pmod_type` still never changing except on reset or a header.

### 7. Docs and firmware
- **README:** switch table and bring-up behaviour, plus a note that the chip
  has no pull on `uio[7]`, so what the demoboard does with it at power-up
  decides whether you see the generator. Still unverified.
- **CLAUDE.md:** the generator is the zone plate, not hex; the Pinout section
  describes the live `ui_in` switches; drop the stale "precheck fails on 2672
  KLayout DRC violations". Precheck now passes.
- **`info.yaml`:** description and the `ui_in` pin text.
- **Firmware:** `firmware/gen_mode.py` drives `ui_in` as outputs for the strap,
  then maybe sends a packet. Check it still behaves as intended with live
  switches, and document it: a packet clears `dip_live`, and pins left driven
  act as switch settings until then.

### 8. Area: measure and trim if needed
**Local sizing** (difference against the branch as it is now; placement adds
about ×1.15). Download the IHP liberty from
`https://raw.githubusercontent.com/IHP-GmbH/IHP-Open-PDK/main/ihp-sg13g2/libs.ref/sg13g2_stdcell/lib/sg13g2_stdcell_slow_1p08V_125C.lib`,
then:
```
yosys -p "read_verilog -DIHP_SRAM src/*.v (all but _tt_fpga_top.v); synth -top tt_um_multi_seg_monitor -flatten;
          dfflibmap -liberty LIB; abc -liberty LIB -D 25000; opt_clean; stat -liberty LIB"
```
The branch today synthesises to **37.1k µm²** (main with hex was 20.3k).

**If it's over budget, trim in this order:**
1. `t_src` fraction 3 → 2 bits, with the rate range halved.
2. The fade as a shift-based approximation instead of the multiply. This
   changes the model too.
3. Move the zone plate's four per-frame points (`pt[]`, 40 flops plus enable
   muxes, ~2.7k) into the SRAM scratch pad.

IHP has no enable flops, so every conditionally loaded register costs a 2:1
mux per bit. Prefer free-running registers where the FSM allows, as `dif`/`mag`
already do.

## Verification
1. `source ~/asic/oss-cad-suite/environment`.
2. Run the cocotb suite with both memory models:
   - `SIM=verilator make -C test`, then with `IHP_SRAM=1`;
   - `rm -rf test/sim_build/rtl` between the two;
   - on macOS, run `ln -sfn ~/asic/oss-cad-suite/lib test/sim_build/lib`
     first if Verilator can't find libpython;
   - check `! grep failure test/results.xml`.
3. Run the whole suite once under Icarus (`make -C test`), which is what CI
   uses.
4. `make formal`: all proofs pass, including the extended `config_port`.
5. `cd tools && python3 test_palettes.py && python3 palette_builder/test_palette_builder.py`.
6. FPGA: `make build/tt_um_multi_seg_monitor.asc`. The Makefile already has
   `synth_ice40 -dsp`.
   - Fmax should be at least ~33.6 MHz; `main` ranges 33.7–35.2 MHz across
     seeds.
   - Keep new adders off the DSP and FSM enables.
   - The critical path is currently `y_px → cy → config_port`, and the new
     DIP logic lands in `config_port`, so watch it.
7. Preview in `tools/palette_builder/palette_builder.py`, Pattern tab, effect
   `chip`: toggle the variation sliders and play. Motion must be smooth, with
   no jumps.
8. Commit on `attract-zoneplate` and push. CI builds the GDS on every push.
   - Download the `GDS_logs` artifact:
     `gh run download <id> -n GDS_logs` → `runs/wokwi/final/metrics.json`.
   - Report `design__instance__area__stdcell` and
     (60,746 + stdcell) / 126,685.
   - Check timing, LVS, precheck and the GL test all pass.
9. On hardware: power up with no firmware and confirm the zone plate appears.
   Then flip each switch and confirm its behaviour. Finally, stream from the
   RP2350.
