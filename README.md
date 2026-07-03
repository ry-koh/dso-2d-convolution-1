# 2D AXI4-Stream Video Convolution Window Extractor

A fully parameterised VHDL-2008 sliding-window extractor for AXI4-Stream video pipelines,
targeting the Avnet ZedBoard (Xilinx Zynq-7000 XC7Z020-CLG484-1). Designed for simulation
only; all verification is done in Vivado xsim.

---

## What This Design Does

For each output position (r, c), the design presents the M×N neighbourhood of pixel values
centred on pixel (r, c) — one AXI4-Stream port per tap. This is the raw data required for
a 2D convolution kernel: multiply each tap by its coefficient and sum. The multiplication
and accumulation are downstream and **not** part of this design.

The output for position (r, c) fires 2 clock cycles after the design accepts the pixel at
(r + HALF_R, c + HALF_C) — the bottom-right corner of the window — where
`HALF_R = (KERN_ROWS−1)/2` and `HALF_C = (KERN_COLS−1)/2`.

```
Output position (r,c)   Output taps (3×3 example — HALF_R=1, HALF_C=1)
─────────────────────   ──────────────────────────────────────────────────
                          tap[0][0]  tap[0][1]  tap[0][2]   row r−1  (oldest row)
        ↕                 tap[1][0] [tap[1][1]] tap[1][2]   row r    ([centre] = pixel(r,c))
                          tap[2][0]  tap[2][1]  tap[2][2]   row r+1  (newest row; tap[2][2] = trigger pixel)
                           col c−1    col c      col c+1
```

Trigger pixel: the design accepts (r+1, c+1) at time T; 2 cycles later m_tvalid fires and all
9 tap ports carry the window centred on (r, c).

---

## Architecture

Three sub-blocks instantiated inside the top-level `conv2d` entity:

| Block | File | Purpose |
|---|---|---|
| `conv2d` | `src/conv2d.vhd` | Top-level: counters, edge logic, FLUSH FSM, output pipeline |
| `line_buf` | `src/line_buf.vhd` | BRAM-based row store (KERN_ROWS−1 rows × LINE_WIDTH pixels); infers 2× RAMB18 on ZedBoard for 3×3 8-bit |
| `win_buf` | `src/win_buf.vhd` | FF shift-register column window (KERN_ROWS × KERN_COLS taps) |

**Pipeline depth:** 2 clock cycles from trigger pixel accepted to tap output valid
(accept → `p_delay` registers `valid_out_d1` → `p_out_reg` asserts `m_tvalid`).

**Back-pressure:** all M×N `m_tready` signals are AND-gated; the pipeline stalls cleanly
when any downstream consumer is not ready. The BRAM synchronous read is gated by `all_ready`
(passed as `rd_en` to `line_buf`) so that `lb_rd_data` holds stable during stalls —
preventing one-column-ahead prefetch errors when the pipeline resumes.

---

## Design Decisions

### 1. Why KERN_ROWS−1 BRAMs, not KERN_ROWS?

The naive approach requires 3 rows of history for a 3×3 window, suggesting 3 BRAMs.

The key insight is that the **current row does not need to be stored in BRAM at all**.
The current row's pixels are buffered inside `win_buf` — the FF shift register. As each
pixel arrives it is pushed directly into `win_buf`'s newest row and simultaneously
written to BRAM (for future rows to read it). The oldest row already in BRAM gets read
back and pushed into `win_buf`'s oldest row slot at the same time.

So for a 3×3 kernel:
- Row 2 (current) — live from the input stream, held in `win_buf` FFs, no BRAM needed
- Row 1 (previous) — stored in BRAM slot 0
- Row 0 (two rows ago) — stored in BRAM slot 1

That is **KERN_ROWS−1 = 2 BRAMs**, not 3. This is the minimum possible storage for a
causal sliding-window design with a streaming input.

---

### 2. Circular / rotating BRAM pointer (`buf_wr_row`)

`buf_wr_row` is a counter cycling `0 → 1 → 0 → 1 → …` (for a 3×3 kernel) that increments
at the end of every row. It always points to the **oldest row** currently in BRAM — the one
about to be overwritten with the new incoming row.

At any moment:
- **Write:** the incoming row is written to `BRAM[buf_wr_row]`, overwriting the oldest data.
- **Read:** all BRAM slots are read simultaneously at the next column position, but the
  output is **rotated** so that slot 0 of `lb_rd_data` is always the oldest row.

The rotation is handled by `p_permute` in `line_buf`:

```
physical slot (row_base + slot) mod NUM_ROWS  →  logical output slot
```

where `row_base` is `buf_wr_row` from `conv2d`. So even though the physical BRAM being
written alternates, the logical view seen by `win_buf` is always age-ordered: slot 0 =
two rows ago, slot 1 = one row ago (for a 3×3 kernel).

**Why pass `buf_wr_row` as both the write pointer and the read base?**
Because the BRAM slot currently being written is the one that *was* the oldest — its old
content has already been consumed by `win_buf` in the previous row cycle, and now it is
being filled with the new data. So at any given moment, `buf_wr_row` points to the
physically oldest slot, which is exactly the rotation base needed to produce age-ordered
output.

**Why not reset `buf_wr_row` at frame boundaries?**
If `FRAME_HEIGHT mod (KERN_ROWS−1) ≠ 0`, resetting the pointer at SOF would break
the age ordering on the next frame. The pointer is left to free-run modulo `KERN_ROWS−1`
across frames; `row_valid` handles the correctness concern (zeroing unwritten slots at
the start of a new frame) independently.

---

### 3. Why separate `line_buf` (BRAM) and `win_buf` (FFs)?

| Concern | BRAM | FFs |
|---|---|---|
| Storing a full 1920-pixel row | 1× RAMB18 (18 kb) | ~15,360 FFs — not viable |
| Holding a 3×3 window (9 bytes) | Wasteful; BRAM minimum depth is 512 words | 9 FFs — ideal |
| Read latency | 1 clock (synchronous read) | Combinational (zero latency) |
| Synthesis inference | `ram_style="block"` forces BRAM | Plain registers |

The split avoids both problems: long rows go to BRAM (area-efficient), the small local
window goes to FFs (zero-latency, no inference ambiguity).

The synchronous read latency of BRAM drives the 2-stage pipeline: the trigger pixel is
accepted at T, BRAM data settles at T+1, and `p_out_reg` captures the full window at T+2
(asserts `m_tvalid`). Without BRAM (e.g. fully FF-based storage), the pipeline would be
1 stage — but BRAM read latency makes the extra register stage unavoidable.

---

### 4. Why FF shift registers inside `win_buf` rather than a second BRAM?

The column window is at most `KERN_COLS` pixels wide per row, and there are `KERN_ROWS`
rows — so for a 3×3 kernel that is 9 bytes total. A BRAM has a minimum capacity of
512 × 18 bits on Xilinx 7-series; using one for 9 bytes would consume an entire RAMB18
while only using ~0.1% of its capacity.

FFs have no minimum size. Nine 8-bit FFs are synthesised as 72 slice registers; the
shift-in / shift-right behaviour maps directly to flip-flop enable/mux logic with no
inference ambiguity.

**Trade-off:** for very wide kernels (e.g. 7×7) the column window grows to 49 pixels per
row, still only 49 FFs per row. This remains fine on any device. The BRAM count grows with
`KERN_ROWS−1`, not `KERN_COLS`, so wide kernels are free.

---

### 5. Back-pressure: AND-gating `m_tready` and gating the BRAM read enable

All `KERN_ROWS × KERN_COLS` downstream `m_tready` signals are ANDed into one `all_ready`
signal. The pipeline stalls completely when `all_ready = '0'`: no new pixel is accepted,
`win_buf` does not shift, and — critically — the BRAM read is also gated.

**Why gate the BRAM read?** Without this gate, `lb_rd_col` is `col_cnt + 1` (read
one column ahead). During a stall, `col_cnt` freezes but the BRAM would continue
overwriting `lb_rd_data` with `mem[col_cnt+1]` on every clock. When the stall ends,
`win_buf` would shift in the wrong column value. The fix is passing `all_ready` as `rd_en`
to `line_buf`, so the BRAM output register is frozen during stalls.

**Trade-off of AND-gating all taps:** the design stalls if *any* downstream consumer is
slow. For a MAC array where all multipliers tick in lockstep this is fine and simplest.
A design where different downstream consumers run at different rates would need per-tap
FIFOs — not needed here.

---

### 6. Edge modes: why coordinate-based remapping at the output stage?

Three edge modes are supported. They could be implemented in many places in the pipeline
(input gating, BRAM write masking, win_buf logic). Doing it at the **output stage** in
`p_edge_out` is cleanest because:

- `win_buf` only ever sees zero-or-data; it needs no awareness of edge mode.
- All three modes share the same shift-register hardware; only the final mux differs.
- ZERO mode is essentially free: `win_buf`'s `new_row` / `row_valid` mechanism already
  inserts zeros at the right positions.
- REPLICATE and TOROIDAL are applied combinationally on the registered tap data in
  `p_edge_out` using delayed coordinates (`m_col_r`, `m_row_r`) that were pipelined
  alongside the pixel data.

**REPLICATE limitation:** only the top and left edges can ever be out-of-bounds in a
causal streaming pipeline. Right-edge and bottom-edge pixels always arrive *after* the
current pixel, so they are never available to clamp to. This is fundamental to single-pass
streaming — full-frame buffering would be needed for true symmetric padding.

**TOROIDAL limitation:** same causal constraint. Column wrap (current pixel → right edge of
previous row) falls out naturally because `win_buf` is not cleared between rows. Row wrap
(current row → last rows of previous frame) falls out because BRAM is not zeroed between
frames. Far-right and far-bottom wrap cannot be done without a full frame buffer.

---

### 7. FLUSH mode: why inject dummy rows rather than stall?

Without FLUSH, the last `KERN_ROWS−1` real rows of a frame never produce a valid output
window — there is no older-row data above them from within the same frame. They do
eventually produce output in the *next* frame (as their data sits in BRAM while the next
frame's rows arrive), but that mixes frames.

FLUSH solves this by injecting `HALF_R = (KERN_ROWS−1)/2` zero-filled dummy rows after the
last real pixel, stalling the real input (`s_tready` deasserted) until the flush completes.
This pushes the final `HALF_R` real rows through the pipeline before the next frame's SOF
arrives.

The cost is `HALF_R × LINE_WIDTH` extra clock cycles per frame — 1 × 1920 = 1920 cycles for
a 3×3 kernel on a 1080p stream — which is negligible against the 2,073,600 cycles per frame
at 1080p.

---

## Generics

All configuration is compile-time. There is no runtime register interface.

| Generic | Type | Default | Description |
|---|---|---|---|
| `DATA_WIDTH` | positive | 8 | Bits per pixel (any positive integer; 8/16/24 verified) |
| `KERN_ROWS` | positive | 3 | Kernel height M (any value; 3, 5, 7 verified) |
| `KERN_COLS` | positive | 3 | Kernel width N (any value; 3, 5, 7 verified) |
| `LINE_WIDTH` | positive | 1920 | Pixels per line |
| `FRAME_HEIGHT` | positive | 1080 | Lines per frame |
| `EDGE_MODE` | string | `"ZERO"` | Edge handling: `"ZERO"`, `"REPLICATE"`, `"TOROIDAL"` |
| `FLUSH` | boolean | `false` | Inject HALF_R = (KERN_ROWS−1)/2 dummy rows after each frame end |

---

## Port Map

```vhdl
entity conv2d is
    generic (DATA_WIDTH, KERN_ROWS, KERN_COLS, LINE_WIDTH, FRAME_HEIGHT, EDGE_MODE, FLUSH);
    port (
        clk      : in  std_logic;
        rst      : in  std_logic;                              -- synchronous, active-high

        s_tdata  : in  std_logic_vector(DATA_WIDTH-1 downto 0);
        s_tvalid : in  std_logic;
        s_tready : out std_logic;
        s_tlast  : in  std_logic;                             -- end of line
        s_tuser  : in  std_logic;                             -- SOF (start of frame)

        m_tdata  : out std_logic_vector(DATA_WIDTH*KERN_ROWS*KERN_COLS-1 downto 0);
        m_tvalid : out std_logic;
        m_tready : in  std_logic_vector(KERN_ROWS*KERN_COLS-1 downto 0);
        m_tlast  : out std_logic;
        m_tuser  : out std_logic
    );
end entity conv2d;
```

`m_tdata` packs all taps: tap[r][c] occupies bits `((r*KERN_COLS+c+1)*DW−1 downto (r*KERN_COLS+c)*DW)`.

---

## Edge Modes

### ZERO
Out-of-bounds tap positions output 0. Implemented via `new_row` (clears the column shift
register at each row start) and `row_valid` (zeroes BRAM slots not yet written within the
current frame). No output-stage remapping needed.

### REPLICATE
Out-of-bounds positions clamp to the nearest valid edge pixel. In a causal pipeline only
the top edge (rows above row 0) and left edge (columns left of col 0) can be out-of-bounds.
Implemented in the combinational `p_edge_out` stage using delayed pixel coordinates
(`m_col_r`, `m_row_r`) to detect OOB and substitute the nearest in-bounds tap value.

### TOROIDAL (stream-linear causal model)
TOROIDAL uses a **stream-linear** model rather than coordinate-based wrap. The design
treats the entire pixel stream as one flat sequence and delays each output by
`TOR_MAX_POS = HALF_R × LINE_WIDTH + HALF_C` positions:

```
Tap (tr, tc) = global_stream[ center_g + (tr − HALF_R) × LINE_WIDTH + (tc − HALF_C) ]
```

where `center_g` is the global stream index of the output's centre pixel. Negative
global indices produce 0 (warmup zeros before the first pixel of frame 0). This is
implemented via a `tor_buf` shift register of depth `2 × TOR_MAX_POS + 1` inside
`conv2d`, bypassing `line_buf`/`win_buf` entirely (the `STREAM_DIRECT` constant
selects this path whenever `EDGE_MODE = "TOROIDAL"` or when `FRAME_HEIGHT ≤ HALF_R`
with `FLUSH = false`).

**FLUSH=false:** the stream continues across frame boundaries; taps read from the
previous frame's content naturally (no per-frame reset).

**FLUSH=true:** per-frame boundary enforced — taps with `tap_y < 0` or
`tap_y ≥ FRAME_HEIGHT`, or with a flat-in-frame index out of range, produce 0.

**`toroidal_armed` flag:** for the `line_buf`/`win_buf` path when
`STREAM_DIRECT = false` would apply (never for pure TOROIDAL, but present as a
guard): prevents xsim uninitialised BRAM (`'U'`) from propagating before all BRAM
slots have been written at least once.

**Causal limitation:** only the top-left region of the wrap is available causally.
Right-edge and bottom-edge wrap targets have not yet arrived in a single-pass stream;
those positions naturally resolve to the shift register / BRAM content from the
previous row or frame, which is exactly the stream-linear model above.

---

## FLUSH Mode

When `FLUSH=true`, after the last pixel of each frame the design injects `HALF_R =
(KERN_ROWS−1)/2` dummy zero-rows. In the centred window model, output position (out_r, out_c)
fires when the trigger pixel at row `out_r + HALF_R` is accepted. Without flush, the last
`HALF_R` real rows of a frame never produce output because there are no rows above them
within the same frame. Flush injects exactly `HALF_R` zero-filled dummy rows, pushing those
real rows through. Real input is stalled (`s_tready` deasserted) during the flush. The next
SOF pixel re-arms the design correctly.

The cost is `HALF_R × EFF_WIDTH` extra clock cycles per frame — 1 × 1920 = 1920 cycles for
a 3×3 kernel on a 1080p stream — negligible against the 2,073,600 cycles per frame at 1080p.

---

## Verified Configurations

The testbench covers **324 configurations** generated by `scripts/gen_tb.py`. All
324 DUT instances run in parallel on a shared clock. 3 back-pressure scenarios are
exercised (CFG001, CFG040, CFG055).

### Configuration matrix

| Axis | Values | Count |
|---|---|---|
| Kernel size (KERN_ROWS × KERN_COLS) | 3×3, 3×5, 3×7, 5×3, 5×5, 5×7, 7×3, 7×5, 7×7 | 9 |
| EDGE_MODE | ZERO, REPLICATE, TOROIDAL | 3 |
| FLUSH | false, true | 2 |
| Frame size (LINE_WIDTH × FRAME_HEIGHT) | 8×8, 16×4, 4×16, 3×3, 5×5, 8×1 | 6 |
| DATA_WIDTH | 8 (fixed) | 1 |
| **Total** | **9 × 3 × 2 × 6** | **324** |

Enumeration order (outer → inner): kernel → edge mode → flush → frame size.

### Back-pressure scenarios

| CFG | Config | Scenario |
|---|---|---|
| CFG001 | 3×3 ZERO FLUSH=off 8×8 | `m_tready(0)` deasserted for 10 cycles post-reset |
| CFG040 | 3×5 ZERO FLUSH=off 3×3 frame | `m_tready(0)` deasserted for 15 cycles starting at cycle 210 |
| CFG055 | 3×5 REPLICATE FLUSH=on 8×8 | `m_tready(NUM_TAPS−1)` deasserted for 10 cycles post-reset |

---

## File Structure

```
src/
  conv2d.vhd          Top-level RTL — paste into Vivado as a design source
  line_buf.vhd        BRAM row buffer sub-block
  win_buf.vhd         FF shift-register column window sub-block

tb/
  conv2d_tb.vhd       Self-checking testbench (machine-generated by gen_tb.py)
                      Runs all 324 DUT instances in parallel on a shared clock
  visualize.html      Self-contained interactive visualiser (generated by gen_tb.py --html)
                      Open directly in any browser — no server or install needed
  vectors/
    c001_input.txt    Stimulus pixels for CFG001
    c001_expected.txt Expected tap values for CFG001
    ...               (648 files total, c001..c324 × input/expected)

scripts/
  gen_tb.py           Regenerates conv2d_tb.vhd, all 648 vector files, and visualize.html.
                      Run: python scripts/gen_tb.py [--html]  (from repo root)

CLAUDE.md             Full project log: decisions, bug log, phase status, constraints
README.md             This file
```

---

## Test Generation (`scripts/gen_tb.py`)

`gen_tb.py` is a code generator. Running it produces the 648 vector files, the VHDL
testbench, and optionally the HTML visualiser. Nothing in `tb/` should be hand-edited —
it all comes from this script.

```
python scripts/gen_tb.py [--html]
```

### 1. Helper functions

**`make_frame(n, fh, lw, dw)`** — generates synthetic pixel data for frame `n`. Frame 0
is a ramp (`r*lw+c mod 256`), frame 1 is flat `128`, frame 2 is a checkerboard of
`0xAA`/`0x55`. Deterministic and chosen to be easy to reason about.

**`get_pixel(frame, r, c, fh, lw, mode)`** — looks up a pixel with ZERO or REPLICATE
boundary handling. Returns 0 for out-of-bounds with ZERO; clamps to the nearest edge
with REPLICATE.

**`compute_taps(frame, out_r, out_c, kr, kc, fh, lw, mode)`** — assembles the full
M×N tap window centred on `(out_r, out_c)` by calling `get_pixel` for each tap position.

**`compute_taps_toroidal(global_stream, center_g, kr, kc, lw, fh, flush)`** — the
TOROIDAL version. Indexes into the flat `global_stream` list using
`center_g + (tr−HALF_R)*LINE_WIDTH + (tc−HALF_C)`. Negative indices return 0 (warmup
zeros before frame 0). With FLUSH=true it also zeroes taps that fall outside the current
frame's row range.

**`is_stream_direct(kr, kc, fh, flush, mode)`** — returns True when the RTL uses the
`tor_buf` path instead of `line_buf`/`win_buf`. This is always true for TOROIDAL, and
also true for degenerate frame heights where `FH <= HALF_R` with FLUSH=false.

### 2. Configuration matrix

Builds the 324-entry `CONFIGS` list using `itertools.product` over all axis combinations.
Then identifies the three back-pressure config indices using `_find()`:

| CFG | Config | Scenario |
|---|---|---|
| CFG001 | 3×3 ZERO FLUSH=off 8×8 | `m_tready(0)` held low 10 cycles post-reset |
| CFG040 | 3×5 ZERO FLUSH=off 3×3 frame | `m_tready(0)` held low 15 cycles from cycle 210 |
| CFG055 | 3×5 REPLICATE FLUSH=on 8×8 | `m_tready(NUM_TAPS−1)` held low 10 cycles post-reset |

### 3. `gen_vectors` — writing the vector files

For each configuration, computes what the DUT should output and writes two files.
`c???_input.txt` contains one integer per line — every pixel of every frame in raster
order, fed into `s_tdata`. `c???_expected.txt` contains one line per output event, each
line holding `KERN_ROWS × KERN_COLS` space-separated tap values for the checker to
compare against `m_tdata`.

Four code paths, matching the four RTL paths:

| Path | Condition | How taps are computed |
|---|---|---|
| TOROIDAL FLUSH=on | `EDGE_MODE="TOROIDAL"`, `FLUSH=true` | `compute_taps_toroidal` with per-frame zeroing |
| TOROIDAL FLUSH=off | `EDGE_MODE="TOROIDAL"`, `FLUSH=false` | `compute_taps_toroidal` using global stream |
| STREAM_DIRECT non-TOROIDAL | `FH <= HALF_R`, `FLUSH=false` | `compute_taps` with ZERO/REPLICATE, per-frame |
| Normal FLUSH=off | `FH > HALF_R`, `FLUSH=false` | `compute_taps` with streaming tail logic |
| Normal FLUSH=on | non-TOROIDAL, `FLUSH=true` | `compute_taps` per frame + flush rows |

### 4. VHDL testbench generation

`main()` loops over all 324 configs and assembles the VHDL file from five types of
per-DUT block, plus shared boilerplate:

**Constants** — `C???_DW`, `C???_KR`, `C???_KC`, `C???_LW`, `C???_FH`, and the input/
expected file path strings for one DUT instance.

**Signals** — all AXI signals (`c???_sdata`, `c???_mready`, etc.) for one DUT.

**Instance** — one `conv2d` entity instantiated with the correct generic map and port
map.

**Stimulus process** — reads `c???_input.txt` line by line via VHDL textio, drives
`s_tdata`/`s_tvalid`/`s_tlast`/`s_tuser` with correct AXI handshake and SOF/TLAST
framing, and waits for `s_tready` on each pixel.

**Checker process** — reads `c???_expected.txt` line by line, waits for `m_tvalid`,
compares the full `m_tdata` vector against the expected value, and prints `PASS` or
`FAIL cfg???` with mismatch details. Sets its bit in `done_flags` when all expected
outputs have been checked.

**Back-pressure processes** — one process per BP scenario, deasserts the appropriate
`m_tready` bit at the configured time and reasserts it after the stall period.

`sim_done` goes true when all 324 `done_flags` bits are set, which stops the clock.

### 5. HTML generation (`--html`)

`build_html_json()` reads every `c???_input.txt` and `c???_expected.txt` already on
disk, calls `gen_trace()` to build the cycle-accurate push/output mapping for the
visualiser timeline, and serialises everything to a JSON blob. `gen_html()` injects
that blob into the self-contained HTML/CSS/JS template and writes `tb/visualize.html`.

---

## How to Run in Vivado

1. Create a Vivado project targeting **XC7Z020-CLG484-1**.
2. Add `src/line_buf.vhd`, `src/win_buf.vhd`, `src/conv2d.vhd` as design sources.
3. Add `tb/conv2d_tb.vhd` as a simulation source.
4. **Set all four `.vhd` files to VHDL 2008.** Right-click each file in the Sources
   panel → Properties → File type → **VHDL 2008**. The testbench uses VHDL-2008
   constructs (unary reduction operator `(and ...)`) that will not compile under
   VHDL 1993 — Vivado defaults to 1993, so this step is required.
5. Copy the entire `tb/vectors/` folder to a path accessible from the simulation
   working directory (Vivado xsim uses the project's `<project>.sim/sim_1/behav/xsim/`
   folder; place `vectors/` there, or adjust the path constants in `conv2d_tb.vhd`).
6. Set `conv2d_tb` as the top-level simulation unit.
7. Run Behavioral Simulation. The testbench prints `PASS` or `FAIL` for each config.

To regenerate the testbench and vectors after changing frame parameters or the config
matrix, run `python scripts/gen_tb.py` from the repo root, then repeat from step 2.

---

## Interactive Visualiser (`tb/visualize.html`)

`tb/visualize.html` is a self-contained HTML file. Open it directly in any modern
browser — no server, install, or network connection required.

### Relationship to the simulation

The visualiser embeds the data from the same `c???_input.txt` and `c???_expected.txt`
files that the VHDL testbench checks via textio. The tap values shown in the visualiser
are the exact bytes the simulation compares against. If all 324 configurations print
PASS in xsim, the visualiser and the testbench are provably showing the same data.

### Generating / Updating

```
python scripts/gen_tb.py --html
```

Run from the repo root. Regenerates `tb/conv2d_tb.vhd`, all 648 vector files, and
`tb/visualize.html` in one pass.

### Selecting a Configuration

Use the sidebar dropdowns to filter by **Window size**, **Edge mode**, **Flush**, and
**Frame size**. The status plates at the top of the main panel show the active Config
number, kernel dimensions, frame dimensions, edge mode, and flush/streaming mode.

### Cycle scrubber and transport

The **Cycle** slider steps through every clock cycle in the simulation trace for the
selected configuration. Transport buttons jump to the first or last cycle, or step one
cycle at a time. The cycle counter shows the current position out of the total.

**Quick-jump buttons** skip directly to the next event of interest:

| Button | Jumps to |
|---|---|
| First output | First cycle with a valid output |
| Next output | Next cycle with a valid output |
| Next stall | Next back-pressure stall cycle |
| Next flush | Next flush-injected zero cycle |

### Cycle phases

Each cycle is colour-coded by pipeline phase:

| Phase | Colour | Meaning |
|---|---|---|
| INPUT | Green | A real input pixel was accepted this cycle |
| FLUSH | Teal | A flush-injected zero row was pushed this cycle |
| STALL | Amber | Back-pressure — downstream held `m_tready` low |
| DUMMY_COL | Blue | Column-padding push (right of the last real pixel in a row) |
| DRAIN | Purple | Drain cycles after the last push |
| RESET | Dark | Reset or pre-simulation idle |

### Information panels

**Cycle story** — a plain-text narrative describing exactly what happened this cycle:
what pixel was accepted, what BRAM slot was written, whether an output fired.

**What is happening / Input / Output** — three readout cards showing:
- the phase badge and any pipeline notes
- the input pixel value, its frame/row/column, and which BRAM row was written
- the output tap vector index and the M×N tap values from the golden vector file

**Output tap grid** — the M×N window of tap values for the current output cycle,
laid out in kernel order. Values are read directly from the golden vector file.

**BRAM physical rows / BRAM logical age order** — shows the BRAM contents at the
current cycle, either in physical slot order or rotated to age order (oldest row
first). Toggle between views with the **Essential / All data** buttons.

### Three-frame overview and timeline

The right panel shows all three input frames as pixel heatmaps, with the current
input pixel and output window footprint highlighted. Below the frames, a **Timeline**
strip shows every cycle in the trace colour-coded by phase, with the current cycle
marked — click any cell to jump directly to that cycle.

---

## Synthesis Resource Reference

Device: XC7Z020-CLG484-1. Project-mode synthesis, no implementation, no timing constraints.
Numbers below are for the default generic values (KERN_ROWS=3, KERN_COLS=3, DATA_WIDTH=8).
Resource counts scale with kernel size and data width — see the Scaling section below.

### Utilisation by block

| Block | Slice LUTs (/ 53,200) | Slice Registers (/ 106,400) | Block RAM Tiles (/ 140) |
|---|---|---|---|
| **conv2d** (total) | **254** | **315** | **1** |
| u_line_buf | 46 | 0 | 1 |
| u_win_buf | 8 | 72 | 0 |
| conv2d logic | 200 | 243 | 0 |

1 Block RAM Tile = 1 RAMB36 = 2 RAMB18. BRAM inference confirmed: `u_line_buf` uses the
RAMB36 and 0 LUT-as-RAM. `u_win_buf`'s 72 registers are the 9-pixel × 8-bit FF shift
register. No DSP48E1 used (0 / 220).

### Timing

| Metric | Value |
|---|---|
| Worst Negative Slack (WNS) | inf (no constraints) |
| Total Negative Slack (TNS) | 0.000 ns |
| Failing endpoints | 0 / 1083 |

No user timing constraints were applied. To obtain a meaningful Fmax estimate, add a
clock constraint (e.g. `create_clock -period 10.0 [get_ports clk]`) before synthesis.

### Scaling

Larger kernels add one RAMB18 per additional row buffer slot (`KERN_ROWS − 1` total).
Slice LUT and register counts grow with `KERN_ROWS × KERN_COLS` (the tap output
pipeline registers) and `KERN_COLS` (the `win_buf` shift register width).

---

## What This Design Does NOT Do

- No coefficient multiplication or MAC stage (that is downstream).
- No bitstream generation or board bring-up (simulation only).
- No runtime register interface (all configuration is compile-time generics).
- No Zynq PS / FCLK integration (free-running testbench clock only).
- No right-edge or bottom-edge true toroidal wrap (requires full-frame buffering;
  those positions fall back to causal history content instead).
