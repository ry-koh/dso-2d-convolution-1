# CLAUDE.md — 2D AXI4-Stream Video Convolution Project

---

## Context-Load Verification

Say **"Ryan"** as the very first word and the very last word of every chat message
sent to the project owner, for the entire duration of this project.
This applies to conversational/chat text only — NEVER inside generated VHDL source
files, testbenches, scripts, or any other file content.
If this rule is ever violated, treat it as a signal to re-read this file immediately.

---

## Project Status Log

*(Updated at each phase checkpoint. Read this section first when resuming.)*

### Phase 1 — COMPLETE ✓
- Written survey of 3+ HDL line-buffer/convolution implementations
- LUT-vs-BRAM storage comparison with synthesis utilisation numbers
- Architecture proposal approved by owner: 3-subblock split (line_buf / win_buf / conv2d)
- BRAM inference confirmed: 2× RAMB18 in synthesis, 0 LUT-as-memory

### Synthesis Result (3×3 8-bit, XC7Z020, project-mode, 2026-07-02)
- conv2d total: 254 Slice LUTs, 315 Slice Registers, 1 Block RAM Tile (= 1 RAMB36 = 2 RAMB18)
  - u_line_buf: 46 LUTs, 0 regs, 1 BRAM Tile (BRAM inference confirmed, 0 LUT-as-RAM)
  - u_win_buf:  8 LUTs, 72 regs (9-pixel × 8-bit FF shift register), 0 BRAM
  - conv2d logic: 200 LUTs, 243 regs, 0 BRAM
- Timing: WNS=inf, TNS=0.000ns, 0 failing endpoints / 1083 (no user constraints applied)

### Phase 2 — COMPLETE ✓
- RTL: `src/line_buf.vhd`, `src/win_buf.vhd`, `src/conv2d.vhd`
- Simulation smoke-test passed (manually driven)
- Three bugs found and fixed during development (see Bug Log below)

### Phase 3 — COMPLETE ✓
- Self-checking testbench: `tb/conv2d_tb.vhd`
- Golden vector generator: `scripts/gen_vectors.py`
- Vectors: `tb/vectors/input_pixels.txt`, `tb/vectors/expected_taps.txt`
- Result: **PASS: 192 outputs checked, all matched golden vectors** (2095 ns)
- Back-pressure exercised: m_tready(0) deasserted for 10 cycles post-reset, full recovery confirmed

### Phase 4 — COMPLETE ✓ (exhaustive 324-config testbench generated 2026-07-02, TOROIDAL fixed 2026-06-30)
- RTL: `src/conv2d.vhd` updated with EDGE_MODE and FLUSH generics
- Coordinate metasystem: col_cnt_d1/row_cnt_d1 delayed in p_delay; captured as m_col_r/m_row_r
  in p_out_reg; combinational p_edge_out remaps OOB taps per EDGE_MODE
- FLUSH FSM: injects HALF_R dummy zero-rows after frame end; s_tready stalled during flush;
  SOF resets row_cnt/buf_wr_row to re-arm row_valid for next frame
- Exhaustive testbench generator: `scripts/gen_tb.py` — produces `tb/conv2d_tb.vhd` and all
  648 vector files (tb/vectors/c001_..c324_{input,expected}.txt)
- Config matrix: DATA_WIDTH=8 × kernel=[(3×3),(3×5),(3×7),(5×3),(5×5),(5×7),(7×3),(7×5),(7×7)]
  × EDGE_MODE=[ZERO,REPLICATE,TOROIDAL] × FLUSH=[false,true]
  × frame=[(8×8),(16×4),(4×16),(3×3),(5×5),(8×1)] = 324 configurations;
  324 DUT instances run in parallel on shared clock
- Back-pressure: 3 scenarios — CFG001 (3×3 ZERO off 8×8, m_tready(0)=0 post-reset 10 cy),
  CFG040 (3×5 ZERO off 3×3 frame, m_tready(0)=0 mid-sim 15 cy at cycle 210),
  CFG055 (3×5 REPLICATE on 8×8, m_tready(NUM_TAPS-1)=0 post-reset 10 cy)
- Testbench ready to paste into Vivado and run; golden vectors audited bit-exact against RTL
- TOROIDAL implementation (stream-linear model):
  - `TOR_MAX_POS = HALF_R * LINE_WIDTH + HALF_C` — tor_buf shift register depth
  - Output fires when center pixel is `TOR_MAX_POS` positions ahead in the global pixel stream
  - Tap (tr,tc) = global_stream[center_g + (tr-HALF_R)*LINE_WIDTH + (tc-HALF_C)]
  - Negative global indices → 0 (warmup zeros before frame 0 pixel 0)
  - FLUSH=true: taps with tap_y<0 or tap_y>=FH or flat_idx OOB → 0 (per-frame boundary)
  - FLUSH=false: stream continues across frame boundaries (no reset)
  - STREAM_DIRECT constant: `EDGE_MODE="TOROIDAL" or (FH<=HALF_R and not FLUSH)` — uses
    tor_buf path instead of line_buf/win_buf for degenerate frame heights too
  - `toroidal_armed` flag guards `wb_row_valid` until BRAM fully written once, preventing
    xsim 'U' propagation. `wb_new_row` suppressed unconditionally for TOROIDAL. See Bug 6.

---

## Bug Log

*(Bugs found during development. Preserved so the same mistakes are not repeated.)*

### Bug 1 — BRAM row ordering (Phase 2)
- **Symptom:** Older rows fed to win_buf were swapped on odd-numbered input rows.
- **Root cause:** Physical BRAM holding the oldest row alternates each row (buf_wr_row cycles 0→1→0→…). Without remapping, odd rows received rows in the wrong age order.
- **Fix:** Added `row_base` port to `line_buf`. Combinational `p_permute` process rotates `rd_data` output so slot 0 is always the oldest row: `phys := (row_base + slot) mod NUM_ROWS`. `conv2d` passes `buf_wr_row` as `row_base`.

### Bug 2 — Delta-cycle timing (Phase 3)
- **Symptom:** All outputs from index 1 onwards were the window for the *previous* pixel.
- **Root cause:** `p_out_reg` and `win_buf`'s `p_shift` both fire at the same rising edge. At delta 0, `wb_tap_out` (combinational on `win`) still reflects the old window; `p_shift` updates `win` at delta 1. So `p_out_reg` captured the wrong (pre-shift) window.
- **Fix:** Added `pixel_accepted_d1` (1-cycle registered delay of `pixel_accepted`). `p_out_reg` now gates on `pixel_accepted_d1`, reading `wb_tap_out` one cycle later after `p_shift` has settled.

### Bug 3 — Win_buf column boundary stale data (Phase 3)
- **Symptom:** For cols 0 and 1 of every row after row 0, the two oldest column tap positions contained the previous row's trailing pixels instead of zero.
- **Root cause:** `win_buf` is a shift register; it does not reset between rows. At the start of each new row, positions 1 and 2 (col−1, col−2) held leftover data.
- **Fix:** Added `new_row` signal in `conv2d` (high when `col_cnt=0` and `pixel_accepted='1'`). Added `new_row` port to `win_buf`; when asserted, positions 1..KERN_COLS−1 are forced to zero instead of shifting old values.

### Bug 5 — REPLICATE coordinate wrong at SOF after FLUSH (Phase 4)
- **Symptom:** For REPLICATE+FLUSH configs, the first real output of each frame after a flush had 0 in the older-row taps instead of the correct clamped first-row pixel value.
- **Root cause:** At the SOF pixel, `p_counters` resets `row_cnt` to 0, but VHDL processes read pre-update signal values at the same rising edge. So `p_delay` captured `row_cnt = KERN_ROWS-1` (the post-flush residual) into `row_cnt_d1` instead of 0. With `m_row_r = KERN_ROWS-1`, all tap `cy` values were ≥ 0 and `p_edge_out` skipped REPLICATE clamping; `win_buf`'s `row_valid=0` zeros then passed through uncorrected.
- **Fix:** In `p_delay`, added an explicit SOF check: `elsif pixel_accepted = '1' and s_tuser = '1' then row_cnt_d1 <= 0`. This overrides the stale counter value and ensures `p_edge_out` sees the correct coordinate (row 0) for the first output of every new frame.

### Bug 6 — TOROIDAL xsim BRAM uninitialised propagation (Phase 4)
- **Symptom:** All 12 TOROIDAL configs failed from output 0. CFG05 (3×3) had exactly 18 mismatches in 192 outputs.
- **Root cause:** `wb_row_valid=(others=>'1')` was unconditional for TOROIDAL. xsim initialises BRAM to `'U'` (uninitialised), not 0. Forcing row_valid all-ones caused `'U'` to flow through win_buf and reach the output comparator. The golden model (`push_history` returning 0 for `idx<0`) expected 0, producing a mismatch on every output where an unwritten BRAM slot was accessed.
- **Fix:** Added `toroidal_armed` signal (registered, never resets after latch). Latches `'1'` once `row_cnt >= KERN_ROWS-1` (all BRAM slots written at least once). `wb_row_valid` forced all-ones only when `EDGE_MODE="TOROIDAL" and toroidal_armed='1'`; before that, normal `row_valid` masking zeroes unwritten slots. `wb_new_row` stays unconditionally `'0'` for TOROIDAL (col wrap must apply from push 1; `row_valid` provides the correct zeroing for unwritten rows independently).

### Bug 4 — BRAM inter-frame stale data (Phase 3)
- **Symptom:** Rows 0 and 1 of frames 1 and 2 used the previous frame's last two rows from BRAM instead of zero-padding.
- **Root cause:** BRAM is not cleared between frames. When a new frame starts, `row_cnt` resets to 0 but BRAM still holds the previous frame's last KERN_ROWS−1 rows. The design relied on Vivado initialising BRAM to 0 at simulation start (correct for frame 0 only).
- **Fix:** Added `row_valid` vector in `conv2d`: bit r = '1' when `row_cnt >= KERN_ROWS−1−r` (enough rows have been seen within the current frame). Added `row_valid` port to `win_buf`; when bit r = '0', zero is inserted at `win(r)(0)` instead of the stale BRAM value. `row_cnt` resets at each frame boundary so the guard re-arms automatically.

---

## What This Project Is NOT Doing

*(Explicit non-scope. Do not implement any of the following unless the owner explicitly adds them as a new phase.)*

- **No coefficient multiplication or MAC stage.** The design outputs raw window tap values — one pixel per tap port. Multiplying taps by kernel coefficients and accumulating the result is downstream and out of scope.
- **No bitstream generation or board bring-up.** Simulation only. Never run implementation or generate a bitstream.
- **No runtime register interface.** All configuration is compile-time generics. No AXI-Lite control plane, no register map.
- **No PS/FCLK integration.** Free-running testbench clock only.
- **No mixed-language modules.** VHDL-2008 and IEEE standard packages only.
- **No shell scripts or Makefile flows.** Copy-paste into Vivado editor only.

---

## Supervisor's Reference Code — Assessment (`Gemini_AI_code_gen.zip`)

*(Reviewed 2026-06-29. Do not copy wholesale — use specific ideas only.)*

### `sliding_window_2d_padded.vhd` — borrow one idea only
- **Borrow:** The coordinate metasystem (`win_coord_x` / `win_coord_y`). Tracking the original image coordinates of each tap in the window is a clean way to implement edge modes in Phase 4 — detect out-of-bounds by coordinate comparison and dispatch to ZERO / REPLICATE / TOROIDAL without cluttered RTL conditionals.
- **Do NOT use:** Line buffer implementation. Supervisor's own comment: *"Running synthesis does not lead to BRAM allocation."* 2D array with initialiser + asynchronous read = LUT-as-memory, not BRAM. Also contains known bugs in the REPLICATE clamping logic (supervisor's inline comments flag this), single m_tready (wrong I/O contract), active-low reset (wrong for this project), and rd_ptr = wr_ptr always (no pre-fetch offset).

### `rec_pln_adder_tree_scalable.vhd` — not relevant to current scope
- The recursive pipelined adder tree structure is correct and would be useful for a MAC stage. However, a MAC stage is explicitly out of scope (see above). File also contains syntax errors (`range 0` instead of `downto 0`).

---

## Behavioral Baseline

*(Merged from multica-ai/andrej-karpathy-skills — applied at every decision point.)*

### Think Before Coding
Do not assume. Do not hide confusion. Surface tradeoffs explicitly before picking
an approach. When a request has multiple valid interpretations, list them and ask
rather than silently choosing one.

### Simplicity First
Implement only what was requested. No features beyond what was asked. No abstractions
invented for single-use code. If a solution could be materially shorter without
losing correctness, rewrite it. Before committing to an approach, ask: would a
senior engineer consider this overcomplicated?

### Surgical Changes
When modifying existing code, touch only what the task requires. Clean up only mess
your own changes introduced. Match the existing style. Do not refactor adjacent code
unless that refactoring is the task.

### Goal-Driven Execution
Transform every request into a verifiable outcome with explicit success criteria.
For multi-step tasks, write a brief plan with specific verification steps before
starting — not a vague "I will do X" but a concrete "the result passes when Y is
observed." Do not claim completion without demonstrating the success criterion.

### Optimization Criteria Requirement (cross-cutting)
Whenever more than one valid way exists to write a piece of VHDL or structure a
block — coding style, pipelining depth, storage type, control structure, etc. —
**explicitly state which criteria you are optimizing for** (e.g. throughput/latency,
area/resource usage, power, readability/maintainability, timing closure margin, or
a combination with stated priority) **before** presenting or choosing an approach.
Never silently default to one style. If it is unclear which criteria matter most for
a given decision, ask before proceeding.

---

## Fixed I/O Contract

*(Never revise without explicit owner approval.)*

- **Input:** one AXI4-Stream video input port
- **Output:** M × N AXI4-Stream video output ports, each carrying the **raw pixel value** at the corresponding window tap position (tap[r][c] = pixel at row offset r, column offset c from the current pixel). There is no coefficient multiplication — that is downstream and out of scope.
- **Signal set (all ports):** TDATA, TVALID, TREADY, TLAST, TUSER[0] (SOF flag)
  — Xilinx standard video AXI4-Stream profile

---

## Locked Decisions

### Toolchain
- Vivado (Xilinx), simulation via **xsim only**
- VHDL-2008, IEEE standard packages only
- No external or vendor libraries; no Verilog/SystemVerilog/mixed-language modules

### Deliverable Scope
**SIMULATION ONLY.** Success criteria are passing testbenches in xsim.
Bitstream generation, hardware deployment, timing closure sign-off, and on-board
bring-up are **explicitly out of scope**. Do not attempt implementation runs or
board bring-up unless the owner explicitly requests them later.

### Estimation Method
When area, timing, or resource tradeoffs require real numbers, run **Vivado
synthesis only** (out-of-context or project-mode, stopping before implementation)
to obtain utilization and estimated timing reports. Never run implementation or
generate a bitstream for estimation purposes.

### Target Device
Avnet ZedBoard — Zynq-7000 **XC7Z020-CLG484-1** (Artix-7 class PL).
Reference figures: 140 Block RAMs, 220 DSP48E1 slices, 13,300 logic slices.
This is a **reporting reference** for synthesis-only utilization estimates — it is
not a hard resource ceiling. Exceeding e.g. 140 BRAMs is a fact to note, not a
failure condition.

### Clock and Reset
- Single clock domain
- Synchronous active-high reset (`rst : in std_logic`)
- Free-running testbench clock — no Zynq PS/FCLK integration

### Architecture (open — to be finalized in Phase 1)
A 3-subblock split (flush-control / BRAM row-buffer / register column-buffer) is
the owner's starting hypothesis, not a requirement. Phase 1 must evaluate this and
alternatives with written justification before any RTL is written.

### Known Prior Failure Mode — Must Be Investigated in Phase 1
A previous design accidentally stored row data in LUTs (distributed RAM / FFs via
inferred logic) instead of dedicated BRAM. Phase 1 research **must** include:
1. A written comparison of LUT-based vs. BRAM-based row/pixel storage
2. Synthesis-only utilization numbers for each, using the target device above
3. An explanation of why synthesis tools infer LUT-based storage when BRAM was
   intended (coding style, reset conditions, array sizing, read/write port style)
4. VHDL coding guidelines that reliably infer BRAM where BRAM is intended
5. A clear statement in the final Phase 1 architecture proposal of which storage
   style was chosen for row buffers and why

This written comparison must exist **before** the Phase 1 proposal is finalized.

### Phase 4 Configuration — Compile-Time Generics
All four configuration axes are fixed at compile-time via VHDL generics.
No runtime register interface. All axes are independently combinable.

| Axis | Values |
|---|---|
| Input data width | Any positive integer width (in bits per pixel); 8/16/24-bit are the documented reference cases but the generic must accept any value |
| Window size M × N | No hard upper bound; practical limit is device resources |
| Edge-case handling mode | Toroidal wrap / zero-extend / boundary-extend |
| End-of-frame pipeline flush | On / Off |

---

## Project Phases

**Rule:** complete each phase checkpoint before advancing. Do not skip or reorder.

---

### Phase 1 — Research and Architecture Proposal

**Objective:** survey real open-source HDL convolution/line-buffer implementations,
extract reusable patterns, produce a written architecture proposal.

**Required deliverables before Phase 2 begins:**
1. Written survey of at least 3 real HDL convolution or line-buffer implementations,
   noting patterns relevant to AXI4-Stream video pipelines
2. LUT-vs-BRAM storage comparison (see "Known Prior Failure Mode" above), with
   optimization criteria stated and synthesis-only utilization numbers attached
3. Architecture proposal: either the 3-subblock hypothesis, a modified version,
   or a different structure — with written justification grounded in explicit
   optimization criteria, and a clear statement of which row-buffer storage style
   was chosen and why

**Checkpoint:** owner reviews and approves the written proposal. No RTL until
approval is given.

---

### Phase 2 — Base Case: Fixed 3×3 Convolution

**Objective:** implement and simulate a working 3×3 convolution with the fixed
base-case parameters below.

**Base-case parameters (fixed for Phase 2):**
- Pixel data width: 8-bit (grayscale)
- Window size: 3×3
- AXI4-Stream signals: TDATA (8-bit), TVALID, TREADY, TLAST, TUSER[0] (SOF)
- Clock/reset: single domain, synchronous active-high reset
- Edge mode: zero-extend (simplest; generalized in Phase 4)
- Flush: off (simplest; generalized in Phase 4)

**Checkpoint:** design elaborates cleanly in xsim (no elaboration errors), and
a basic smoke simulation (manually driven, not yet self-checking) shows plausible
output pixel values for a known input pattern. Show the waveform or log excerpt.

---

### Phase 3 — Self-Checking Testbench

**Objective:** build a self-checking testbench that verifies the Phase 2 DUT
against Python-generated golden vectors, with back-pressure exercised.

**Testbench requirements:**
- Reference model: Python script generates golden output vectors for a known input
  frame; vectors are written to a file; testbench reads via VHDL textio and
  compares bit-exactly to DUT output
- Back-pressure: testbench must deassert TREADY on at least one output port during
  simulation and verify the pipeline stalls and recovers correctly
- Stimulus: minimum 3–5 frames of small size (e.g. 8×8 pixels) to exercise
  inter-frame flush/reset behavior
- Pass criterion: bit-exact match on every output pixel across all frames, with
  back-pressure recovery confirmed

**Checkpoint:** xsim simulation runs to completion with a printed PASS message
(or explicit FAIL with mismatch location). Attach the simulation log showing PASS.

---

### Phase 4 — Full Generalization

**Objective:** generalize the window extractor to the full configuration space
via VHDL generics, retaining the self-checking testbench.

**What is already generic and working (no changes needed):**
- `DATA_WIDTH`: any positive integer — verified at 8-bit, expected to hold for 16/24-bit
- `KERN_ROWS` / `KERN_COLS`: any size — verified at 3×3, expected to hold for 5×5

**What needs to be added:**

| Generic | Type | Values | Current state |
|---|---|---|---|
| `EDGE_MODE` | string | `"ZERO"` / `"REPLICATE"` / `"TOROIDAL"` | Only `"ZERO"` implemented |
| `FLUSH` | boolean | `true` / `false` | Only `false` implemented |

**EDGE_MODE implementation plan:**
- Use a coordinate metasystem (inspired by supervisor's code): track `coord_x` / `coord_y` for each tap position as the window shifts. Out-of-bounds detection is then a coordinate comparison.
- `"ZERO"`: already working via `new_row` / `row_valid` mechanism (Phase 3 fix). Keep as-is.
- `"REPLICATE"`: clamp out-of-bounds coordinates to the nearest valid edge pixel. Implementation: in the output stage, when coordinate is out-of-bounds, substitute the nearest in-bounds tap value from the window.
- `"TOROIDAL"`: wrap out-of-bounds coordinates modulo frame dimensions. Implementation: coordinate mod IMG_WIDTH / IMG_HEIGHT.

**FLUSH implementation plan:**
- When `FLUSH=true`, after the last pixel of a frame (TLAST asserted), inject `KERN_ROWS−1` dummy rows of zeros to flush the pipeline so every input pixel in the frame produces an output. Currently the bottom `KERN_ROWS−1` rows never get a valid output window.
- When `FLUSH=false` (current behaviour), no dummy rows injected.

**Testbench and golden vector updates:**
- `gen_vectors.py` must be parametric: accept `EDGE_MODE`, `FLUSH`, `KERN_ROWS`, `KERN_COLS`, `DATA_WIDTH`, `LINE_WIDTH`, `FRAME_HEIGHT` as arguments
- Must simulate at least 2 distinct configurations, e.g.:
  1. 3×3, 8-bit, REPLICATE, FLUSH=false
  2. 5×5, 8-bit, ZERO, FLUSH=false

**Checkpoint:** at least 2 distinct generic configurations simulate to PASS in
xsim, with logs attached. If any configuration exceeds the ZedBoard reference
resource figures, note it factually in the synthesis report but do not treat it
as a blocker.

---

## Vivado Workflow — Owner's Preferred Method

**This is the locked workflow. Do not suggest shell scripts, Makefiles, or TCL
automation as the primary path. The owner creates a Vivado project manually and
copies generated file content directly into Vivado's editor.**

### Step-by-step (applies to every phase)
1. Owner creates a Vivado project (or opens the existing one) targeting
   XC7Z020-CLG484-1.
2. For each `.vhd` file generated here: owner creates a new source file in
   Vivado and **pastes the file content in**.
3. To elaborate/simulate: owner uses Vivado's GUI (Run Simulation →
   Run Behavioral Simulation) or the Tcl Console inside Vivado.
4. To run synthesis-only for utilization estimates: owner uses
   Vivado's GUI (Run Synthesis, then view Utilization report).

### What I generate
- Complete, self-contained `.vhd` file content ready to paste.
- Python scripts (for golden vector generation) as plain text to paste into
  a file outside Vivado.
- Any Tcl snippets needed go in the Vivado Tcl Console, clearly labelled.

### What I do NOT generate
- Shell scripts intended to be run from a terminal.
- Makefile-based flows.
- Automation that assumes Vivado is on the system PATH.

### Checkpoint instructions (applies to every phase)
When a checkpoint says "elaborate" or "simulate":
- Paste all listed `.vhd` files into Vivado as sources.
- Click **Run Simulation → Run Behavioral Simulation** (for testbench checkpoints)
  or **Run Synthesis** (for utilization checkpoints).
- Report back what Vivado shows (errors, warnings, waveform result, or
  utilization summary).

---

## File and Directory Structure

*Used as a reference for file naming; actual files live in Vivado's project.
The repo here stores the source text for version control.*

```
/
├── CLAUDE.md
├── README.md
├── src/
│   └── *.vhd               # RTL source files (VHDL-2008) — paste into Vivado
├── tb/
│   ├── conv2d_tb.vhd       # Testbench (machine-generated by gen_tb.py; do not hand-edit)
│   ├── visualize.html      # Interactive HTML visualiser (generated by gen_tb.py --html)
│   └── vectors/            # 648 golden vector files: c001..c324_{input,expected}.txt
├── scripts/
│   ├── gen_tb.py           # Generates conv2d_tb.vhd + all 648 vector files + visualize.html
│   ├── gen_vectors.py      # Standalone single-config vector generator (parametric)
│   └── visualize.py        # Standalone HTML visualiser generator (reads existing vectors)
└── synth/
    └── phase*/             # Reference files for synthesis-only utilization runs
```

*[ASSUMPTION — naming convention: snake_case for file names, entity names, and
signal names; ALL_CAPS for constants and generics.]*

---

## Context — Carry Forward

*(Every decision made during the setup interview. Survives across sessions.)*

| Decision | Value |
|---|---|
| Base-case pixel width | 8-bit grayscale (generic accepts any positive integer width) |
| AXI4-Stream signal set | TDATA, TVALID, TREADY, TLAST, TUSER[0] (SOF) — Xilinx video profile |
| Clock domain | Single |
| Reset style | Synchronous, active-high |
| PS/FCLK integration | No — free-running testbench clock only |
| Phase 3 reference model | Python golden vectors, bit-exact match via textio |
| Phase 3 back-pressure | Required — TREADY deasserted, recovery verified |
| Phase 3 minimum stimulus | 3–5 frames, ~8×8 pixels each |
| Phase 4 window size bound | No hard maximum — practical limit is device resources |
| Phase 4 configurability | All axes compile-time generics, independently combinable |
| Phase 4 edge modes | Toroidal wrap / zero-extend / boundary-extend — all three required |
| Phase 4 flush | On/Off — both required |
| Repo state at project start | Greenfield (empty) |
| Vivado workflow | Owner creates project manually, pastes .vhd content into Vivado editor — no shell scripts or PATH-based automation |
| Naming conventions | [ASSUMPTION] snake_case signals/entities, ALL_CAPS generics/constants |
| Baseline behavioral source | multica-ai/andrej-karpathy-skills CLAUDE.md (fetched 2026-06-29) |
