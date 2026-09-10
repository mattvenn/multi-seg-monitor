# Gamma dithering investigation -- where things stand

Working notes, 2026-09-10. Not a specification -- this documents an in-progress
side investigation, uncommitted, so the session can be picked back up without
re-deriving context.

## How this started

The actual goal was multi-Pmod-adapter support (PmodVGA, the TT 6-bit/Tiny VGA
Pmod, and a future custom Pmod using more output pins for higher colour
precision on a single channel). That's still the real task -- see "The
original task, still open" below. This side quest grew out of one question
along the way: *is there a way to get better colour resolution out of a new
Pmod?*, which led to dithering as a general technique, and then to "let's
test it on hardware we already have" before designing new hardware.

## What we found on hardware, in order

1. Flashed the then-current bitstream and looked at the internal generator:
   saw 9 distinct grey levels (8 lit + black), not the 12 the gamma table
   could produce -- because the generator's pattern
   (`gen_int = gen_col[3:0] | 4'h1`) only ever lights odd brightness indices.
2. Built `tools/make_gamma_ramp.py` (new file) to get a genuine 0-15 ramp on
   screen via streaming, since neither existing pattern generator covers all
   16 indices.
3. On the streamed ramp: bottom 8 indices (0-7) were all visibly distinct;
   top ~5 (roughly indices 11-15) looked identical, and nothing looked like
   true white even at index 15.
4. Isolated this to the shared gamma table / wrapper truncation, not the
   stream path or the new script, by reproducing the *same* symptom on the
   internal generator (`firmware/gen_mode.py`, new file, needed because
   `mpremote run` doesn't persist FPGA bring-up state across separate
   invocations -- bitstream push and pixel-clock start have to happen in the
   same session as the mode-pin write, which a bare `Pin(32, ...)` exec
   command doesn't do).
5. Root cause for *why* nothing was distinguishable at the top: `gamma.v`
   had been deliberately changed (commit `49dacbe`) to round straight to
   4-bit target precision rather than 6-bit-then-truncate, specifically to
   avoid a double-rounding bug. Side effect: the bottom 2 bits of `level`
   were **always exactly zero** -- there was no fractional information left
   to dither with even in principle.

## What's implemented now (uncommitted)

- `src/gamma.v`: reverted to true 6-bit precision
  (`round((idx/15)**(1/2.2)*63)`), the same values the table had before
  `49dacbe`. Bottom 2 bits are meaningful again.
- `src/multi_seg_monitor.v`: two new core outputs, `px_x_lsb`/`px_y_lsb`
  (pixel position parity). **Important, non-obvious debugging result:**
  these must be **combinational** off live `x_px`/`y_px`, *not* registered
  one cycle behind like `hsync`/`vsync`. Registering them the "safe by
  analogy" way silently swapped which of a segment's pixels rounded up vs.
  down -- caught by the new cocotb test, not by inspection. See the comment
  at the `assign px_x_lsb = ...` site for why.
- `src/tt_um_multi_seg_monitor.v`: replaced flat `grey = level[5:2]`
  truncation with a 2x2 ordered (Bayer) dither using the previously-always-
  zero `level[1:0]` remainder and the new parity bits. Clamps at code 15
  (nothing to dither into above the DAC's max code).
- `tools/segments.py`: `GAMMA` table updated to match; new `dither()` +
  `DITHER_THRESHOLD` as the Python-side oracle (mirrors the RTL, per this
  project's "geometry is written down twice" convention).
- `tools/test_segments.py`: replaced the now-obsolete
  `test_gamma_truncates_without_extra_rounding_error` (asserted the old,
  now-reverted invariant) with `test_gamma_is_true_6bit_precision` and
  `test_dither_fraction_matches_remainder`.
- `test/test_multi_seg.py`: updated `test_stream_frame` and the delay-sweep's
  `analyse()` to expect dithered values instead of flat truncation; added
  `test_dither_spreads_across_pixel_parity`, which checks all 4 phases of
  one segment's pixel parity against a known nonzero-remainder brightness
  index.
- `test/gold/generator.png`, `test/gold/stream.png`: regenerated
  (`make -C test gold`) and visually checked -- geometry intact, no
  corruption, and a zoomed crop confirms the dither checkerboard texture
  really is present pixel-for-pixel as intended.
- Full test suite (minus the delay sweep, which wasn't rerun this session)
  passes, including the full 18944-segment round-trip against the new
  dithered expected values.
- `firmware/seg_player.py`: locally edited (uncommitted) to point at the
  test ramp; check the current default path before relying on it.

## The open question this leaves

**Simulation confirms the dithering is bit-exact correct. On real hardware,
you reported no visible difference at all** -- not with the internal
generator, not with the streamed testcard ramp, not with the video you'd
tested earlier. That's the unresolved thing to pick up next.

Possibilities, not yet distinguished:

- The effect is real but too fine-grained to perceive at normal viewing
  distance on this monitor -- a 1-2 pixel checkerboard at 800x600 may just
  read as a slightly different flat shade, and precisely in the perceptually
  compressed bright region this started from, "slightly different" may not
  clear the threshold of visibility at all. This would mean dithering
  doesn't actually solve the *perceptual* problem, only the *digital
  duplicate* problem (which, per the finding above, wasn't even what was
  reachable by the odd-only generator pattern to begin with).
- The 4-bit PmodVGA DAC's physical ceiling doesn't move -- confirmed
  separately: index 15 is unaffected by this change on purpose (nothing to
  dither into above the max code), so if "no full white" is a real R-2R
  ladder voltage-ceiling issue, dithering was never going to touch it. But
  the "top 5 look identical" observation involved indices below 15 too
  (e.g. 11-14), and those *should* show some change.
- Something didn't actually reach the board as expected -- worth double
  checking the bitstream timestamp matches the latest build
  (`build/tt_um_multi_seg_monitor.bin`) actually pushed was the one built
  after all RTL edits above, not a stale one from earlier in the session.
- The monitor/eye combination may fundamentally not resolve DAC-level
  differences this small regardless of dithering, which would be evidence
  *for* the original real-precision-Pmod idea rather than dithering as a
  fix on today's hardware.

## Suggested next steps

1. Rebuild fresh and re-flash, confirming via timestamp/checksum that the
   board is actually running the latest bitstream, before concluding the
   effect is genuinely imperceptible rather than not-yet-deployed.
2. Try an exaggerated test case: a large solid-filled region (not just a
   thin 7-segment stroke) at a known nonzero-remainder level immediately
   adjacent to a flat reference at the two codes it dithers between, side by
   side, to make any change as large and easy to spot as possible.
3. If still no visible difference: treat this as evidence that dithering
   alone doesn't solve the bright-end perceptual compression on today's
   4-bit PmodVGA output, and that real added precision (the future Pmod)
   is the more promising direction after all -- worth revisiting the
   original multi-Pmod design conversation with this result in hand.

## The original task, still open

Multi-Pmod-adapter support was the actual ask this session started from.
Decisions made in conversation, not yet written into a spec or implemented:

- Selection mechanism: a strap sampled from `ui_in` once at reset release
  (not a runtime-live pin), decoding to one of the Pmod modes. Also decided:
  fold "internal generator vs. streamed" source-select into the same
  reset-time strap, freeing `uio[7]` as a live pin for whichever mode needs
  it most.
- Exactly three Pmods planned, no more: today's Digilent PmodVGA, the TT
  6-bit/Tiny VGA Pmod (spec'd in `SPEC.md` section 7 as "Prototype mode" but
  never actually wired into the current RTL), and a future custom Pmod using
  up to 16 pins (14 colour + 2 sync) for a genuinely higher-precision single
  channel.
- The future Pmod's higher precision is **not** "spread today's 4-bit value
  across more pins" -- the user was explicit that the *host* needs to
  actually send that much real data. That directly costs columns under the
  fixed 256-byte line-buffer-row constraint (bits/segment == bytes/digit,
  columns = floor(256 / bits_per_segment); at 14 bits/segment that's only 18
  columns against today's 64). This trade-off is unresolved -- no decision
  yet on accepting fewer columns, growing the buffer (needs a bigger die,
  real ASIC area cost), or something else.
- This dithering work is a first real instance of the general pattern
  intended for that design: compute a canonical value once, adapt it to
  each Pmod's actual pin/DAC precision in the wrapper, rather than
  hand-tuning a separate table per mode.

None of the multi-Pmod work has started in RTL yet -- this session only got
as far as the design conversation above, then diverted into the dithering
side quest.
