# Development Log — AI-Generated 2D AXI4-Stream Convolution

This document records the full development history of the AI-generated RTL in this
repository: what was constrained and why, what went wrong when it was not, how each
bug was diagnosed and repaired, and the context needed to compare this design against
a hand-written reference implementation.

---

## 1. Design Specification — What Had to Be Constrained Before Code Was Written

The project used phase gating: every constraint had to be written down and approved
before code was generated. The constraints established in Phase 0 before any VHDL was
produced are listed below.

### Toolchain and language
- Vivado xsim only — no external simulators
- VHDL-2008 — Vivado defaults to VHDL 1993 and will reject 2008 constructs unless
  the file type is explicitly set (right-click → Properties → File type in Vivado)
- IEEE standard packages only — no vendor libraries, no Verilog

### I/O contract
- One AXI4-Stream input: `s_tdata`, `s_tvalid`, `s_tready`, `s_tlast`, `s_tuser(0)` (SOF)
- M×N AXI4-Stream output ports, one per kernel tap: same signal set on each
- Raw pixel values only — no coefficient multiplication, no MAC stage

### Generics (compile-time only — no runtime register interface)

| Generic | Type | Meaning |
|---|---|---|
| `DATA_WIDTH` | positive | Pixel bit width (any value; testbench uses 8) |
| `KERN_ROWS` | positive | Kernel height in pixels |
| `KERN_COLS` | positive | Kernel width in pixels |
| `LINE_WIDTH` | positive | Frame width in pixels |
| `FRAME_HEIGHT` | positive | Frame height in pixels |
| `EDGE_MODE` | string | `"ZERO"` / `"REPLICATE"` / `"TOROIDAL"` |
| `FLUSH` | boolean | Whether to drain pipeline at end of frame |

### Reset and clock
- Synchronous, active-high reset (`rst : in std_logic`)
- Single clock domain — no PS/FCLK, no clock crossing

### Storage constraint — the known prior failure mode
A previous design accidentally stored row data in Slice LUTs (distributed RAM) instead
of BRAM. Before any RTL was written, Phase 1 was required to produce a written
comparison with synthesis numbers and a coding recipe that reliably infers BRAM.

**Root causes of accidental LUT-RAM inference (documented in Phase 1):**
1. Asynchronous read — any `array(index)` read outside a clocked process infers
   distributed RAM, not BRAM. BRAM requires a registered (synchronous) read port.
2. Per-element reset — BRAM arrays cannot be reset element-by-element; adding a reset
   to each array slot forces the synthesiser to use registers or LUTs instead.
3. Array too small — if the inferred array is shallower than the BRAM minimum depth
   (~512 entries for RAMB18), Vivado may choose registers or LUTs.
4. Missing `ram_style` attribute — without `attribute ram_style of <signal> : signal is "block"`,
   Vivado chooses the storage type heuristically and may pick distributed RAM.

**Coding recipe used in `line_buf.vhd` to force BRAM inference:**
```vhdl
attribute ram_style : string;
attribute ram_style of mem : signal is "block";
-- synchronous write, synchronous read, no reset on the array
process(clk)
begin
    if rising_edge(clk) then
        if wr_en = '1' then
            mem(wr_addr) <= wr_data;
        end if;
        rd_data_raw <= mem(rd_addr);   -- registered read: BRAM-safe
    end if;
end process;
```

**Synthesis result confirming correct inference:**
- `u_line_buf`: 1 Block RAM Tile (= 1 RAMB36 = 2 RAMB18), 0 LUT-as-RAM
- Without the `ram_style` attribute and synchronous read, the expected penalty for a
  1920-pixel 8-bit row is ~480 Slice LUTs vs 1 BRAM36 tile

---

## 2. What Happened When Constraints Were Not Met — Bugs Found During Development

Six bugs were found and fixed across the development phases. Each is described below
with the symptom, root cause, and fix.

### Bug 1 — BRAM row ordering (Phase 2)

**Identified by prompt:** Simulation log pasted verbatim after the first xsim run:
> `Error: MISMATCH at output index 1` (at 195 ns)

The log was pasted back into the chat and Claude diagnosed the root cause from the mismatch pattern.

**Symptom:** Older rows fed to `win_buf` were swapped on odd-numbered input rows.
The output taps for rows 1, 3, 5… were wrong; even rows were correct.

**Root cause:** The physical BRAM slot holding the oldest row alternates each row
(`buf_wr_row` cycles 0→1→0→…). Without remapping, odd rows received BRAM slots in
the wrong age order — slot 0 appeared older than slot 1 when it was actually newer.

**Fix:** Added `row_base` port to `line_buf`. A combinational `p_permute` process rotates
the `rd_data` output so slot 0 is always the oldest row:
`phys := (row_base + slot) mod NUM_ROWS`. `conv2d` passes `buf_wr_row` as `row_base`.

---

### Bug 2 — Delta-cycle timing in output register (Phase 3)

**Identified by prompt:** Same simulation log as Bug 1 — the MISMATCH error at output index 1
persisted after the Bug 1 fix. The updated log was pasted back and Claude identified the
delta-cycle ordering issue from the remaining mismatch pattern.

**Symptom:** All outputs from index 1 onwards were the window for the *previous* pixel,
not the current one.

**Root cause:** `p_out_reg` (which captures the window to output registers) and `win_buf`'s
`p_shift` (which shifts the window by one pixel) both fire at the same rising clock edge.
At delta 0, `wb_tap_out` still reflects the old window; `p_shift` updates `win` at delta 1.
`p_out_reg` read `wb_tap_out` before `p_shift` had settled, capturing one pixel too early.

**Fix:** Added `pixel_accepted_d1` — a 1-cycle registered delay of `pixel_accepted`.
`p_out_reg` now gates on `pixel_accepted_d1`, reading `wb_tap_out` one cycle later after
`p_shift` has fully settled.

---

### Bug 3 — Win_buf column boundary stale data (Phase 3)

**Identified by prompt:** Continued simulation log review after Bugs 1 and 2 were fixed.
Mismatches remained at specific column positions, which were pasted back for diagnosis.

**Symptom:** For columns 0 and 1 of every row after row 0, the two oldest column tap
positions contained the previous row's trailing pixels instead of zero.

**Root cause:** `win_buf` is a shift register; it does not reset between rows. At the
start of each new row, positions 1 and 2 (col−1, col−2) held leftover data from the
previous row's end.

**Fix:** Added `new_row` signal in `conv2d` (asserted when `col_cnt=0` and
`pixel_accepted='1'`). Added `new_row` port to `win_buf`; when asserted, positions
`1..KERN_COLS−1` are forced to zero instead of shifting old values in.

---

### Bug 4 — BRAM inter-frame stale data (Phase 3)

**Identified by prompt:** Request to expand testing to multiple frames:
> *"can we create more test benches to fully test everything"* → *"like all possible configurations"*

Running 3 frames revealed the inter-frame corruption that was invisible in single-frame tests.

**Symptom:** Rows 0 and 1 of frames 1 and 2 used the previous frame's last two rows
from BRAM instead of zero-padding.

**Root cause:** BRAM is not cleared between frames. When a new frame starts, `row_cnt`
resets to 0 but BRAM still holds the previous frame's last `KERN_ROWS−1` rows. The
design initially relied on Vivado initialising BRAM to 0 at simulation start, which
is only correct for frame 0.

**Fix:** Added `row_valid` vector in `conv2d`: bit `r = '1'` when
`row_cnt >= KERN_ROWS−1−r` (enough rows have been received within the current frame).
Added `row_valid` port to `win_buf`; when bit `r = '0'`, zero is inserted at `win(r)(0)`
instead of the stale BRAM value. `row_cnt` resets at each frame boundary so the guard
re-arms automatically for every new frame.

---

### Bug 5 — REPLICATE coordinate wrong at start-of-frame after FLUSH (Phase 4)

**Identified by prompt:** After expanding to the full 324-configuration testbench:
> `Error: CFG10 MISMATCH at output 96` / `Error: CFG28 MISMATCH at output 96`

The failing configs were specifically REPLICATE+FLUSH combinations. The simulation log
was pasted back and Claude narrowed the fault to the SOF cycle of each new frame.

**Symptom:** For REPLICATE+FLUSH configurations, the first real output of each frame
after a flush had zeros in the older-row taps instead of the correct clamped
first-row pixel value.

**Root cause:** At the SOF pixel, `p_counters` resets `row_cnt` to 0, but VHDL processes
read pre-update signal values at the same rising edge. So `p_delay` captured the stale
`row_cnt = KERN_ROWS−1` (post-flush residual) into `row_cnt_d1` instead of 0. With
`m_row_r = KERN_ROWS−1`, all tap `cy` coordinates were ≥ 0 and `p_edge_out` skipped
REPLICATE clamping; `win_buf`'s `row_valid=0` zeros then passed through uncorrected.

**Fix:** In `p_delay`, added an explicit SOF check:
```vhdl
elsif pixel_accepted = '1' and s_tuser = '1' then
    row_cnt_d1 <= 0;
```
This overrides the stale counter value and ensures `p_edge_out` sees coordinate row 0
for the first output of every new frame.

---

### Bug 6 — TOROIDAL xsim BRAM uninitialised propagation (Phase 4)

**Identified by prompt:** Same 324-config run that surfaced Bug 5:
> `Error: CFG005 MISMATCH at output 20` / `Error: CFG112 MISMATCH at output 16`

The pattern of all TOROIDAL configs failing from output 0 or very early was distinctive.
Claude identified it as xsim's `'U'` initialisation leaking through via the unconditional
`row_valid` override.

**Symptom:** All 12 TOROIDAL configurations failed from output 0 in xsim. CFG05
(3×3 TOROIDAL) had exactly 18 mismatches in 192 outputs.

**Root cause:** `wb_row_valid` was forced all-ones unconditionally for TOROIDAL mode.
xsim initialises BRAM to `'U'` (uninitialised), not 0. Forcing `row_valid` all-ones
before all BRAM slots had been written caused `'U'` to flow through `win_buf` and
reach the output comparator. The golden model expected 0 for positions that hadn't
been written yet, producing a mismatch on every affected output.

**Fix:** Added `toroidal_armed` signal (registered, latches `'1'` once
`row_cnt >= KERN_ROWS−1` — i.e., all BRAM slots have been written at least once).
`wb_row_valid` is forced all-ones only when `EDGE_MODE="TOROIDAL" and toroidal_armed='1'`;
before that, normal `row_valid` masking zeros unwritten slots.

---

### Bug 7 — BRAM read-ahead during s_tvalid LOW cycles (stress scenarios)

**Identified by prompt:** Supervisor requirement to test non-continuous handshaking:
> *"supervisor says that we need to test backpressure and making the input data not smooth
> (e.g. not every clock cycle have input data) he mentions that we can vary like 2-high 3-low
> or 4-high 1-low and vary tready and tvalid to catch any issues"*

Adding the stress scenarios exposed this bug immediately on first run:
> `Error: SS004 MISMATCH at output 26`

**Symptom:** SS001–SS008 and SS011–SS012 failed when `s_tvalid` was patterned LOW.
Output taps from BRAM-sourced rows were shifted by one or more columns relative to
the expected values.

**Root cause:** `line_buf rd_en` was wired to `all_ready` (the AND of all `m_tready`
bits). During a `s_tvalid` LOW cycle, `all_ready='1'` but no pixel was accepted
(`push='0'`). The BRAM kept reading — advancing its output register to column N+1
while `col_cnt` stayed at N. The next real push received column N+1's BRAM data
instead of column N's data, silently corrupting every window tap sourced from BRAM.

**Fix:** Changed `rd_en => all_ready` to `rd_en => push`. With `push='0'`, the BRAM
output register holds the last correct value until the next real pixel fires. Base
configs (push='1' every cycle) and `m_tready` stalls (`all_ready='0'` ⇒ `push='0'`)
were unaffected by this change.

---

### Bug 8 — TOROIDAL spurious flush on s_tvalid LOW cycles (stress scenarios)

**Identified by prompt:** Same stress scenario run that surfaced Bug 7:
> `Error: SS010 MISMATCH`

SS010 uses a TOROIDAL base config with a `s_tvalid` 2H/3L pattern — the combination
that triggered the spurious flush path.

**Symptom:** SS010 (TOROIDAL, `s_tvalid` 2H/3L pattern) produced extra outputs that
had no match in the expected file.

**Root cause:** A `p_toroidal` elif branch injected `HALF_C` zero pushes whenever
`s_tvalid='0'`. This path was designed for degenerate `STREAM_DIRECT` non-TOROIDAL
configurations (frame height ≤ HALF_R, FLUSH=false). Real TOROIDAL pipelines with
FLUSH=false need no inter-frame zero injection — the next frame's pixels provide the
tail naturally. With a `s_tvalid` LOW-cycle pattern, this elif fired on every LOW
cycle, producing spurious outputs.

**Fix:** Added `and EDGE_MODE /= "TOROIDAL"` guard to the elif branch.

---

### Bug 9 — TOROIDAL FLUSH=off golden model expected output count (post-simulation)

**Identified by prompt:** After the full 354-scenario run appeared to hang:
> *"I suspect that there is an issue in the code because I ran for 160,000 ns and it is still not done"*

The `done_flags` hex value was shared and decoded to show exactly which 53 scenarios
(all TOROIDAL FLUSH=off) had never completed. This was a golden model bug, not an RTL bug.

**Symptom:** All 51 TOROIDAL FLUSH=off base configurations (and SS010, SS025) never
set their `done_flag`. The simulation ran to 160,000 ns without completing.

**Root cause:** In `gen_tb.py`, `eff_width = lw + half_c` at line 141 accounts for
dummy column-pad pushes (HALF_C extra pushes per row) that occur in the non-TOROIDAL
path. However, the RTL gates `col_pad_push` off for TOROIDAL:
```vhdl
col_pad_push <= '1' when (EDGE_MODE /= "TOROIDAL" and HALF_C > 0
                          and col_cnt >= LINE_WIDTH) else '0';
```
The golden model computed `center_push` using `eff_width = lw + half_c`, which counted
more pushes than the RTL actually made, so it generated 1–3 extra expected outputs per
configuration. The checker waited for outputs that the RTL would never produce.

**Fix:** In the TOROIDAL FLUSH=off branch of `gen_vectors()`, introduced
`eff_width_tor = lw` and recomputed `delay`, `total_pushes`, `max_center_push`, and
`center_push` using it. The non-TOROIDAL path is unchanged.

---

## 3. Reference Generic Values for Comparison Against Hand-Written Code

The following defaults are used as the standard comparison point between the
AI-generated design and the hand-written reference implementation:

```vhdl
generic (
    DATA_WIDTH   : positive := 8;
    KERN_ROWS    : positive := 7;
    KERN_COLS    : positive := 7;
    LINE_WIDTH   : positive := 1280;
    FRAME_HEIGHT : positive := 720;
    EDGE_MODE    : string   := "REPLICATE";
    FLUSH        : boolean  := false
);
```

These represent a realistic HD video pipeline configuration: 7×7 kernel on 720p input
with REPLICATE edge padding and no end-of-frame flush.

### Synthesis results — reference generics (7×7, 8-bit, 1280×720, REPLICATE, FLUSH=false)

XC7Z020-CLG484-1, project-mode synthesis, no timing constraints:

| Block | Slice LUTs (/ 53,200) | Slice Registers (/ 106,400) | F7 Muxes (/ 26,600) | F8 Muxes (/ 13,300) | Block RAM Tiles (/ 140) | Bonded IOB (/ 200) | BUFGCTRL (/ 32) |
|---|---|---|---|---|---|---|---|
| **conv2d** (total) | **10,794** | **2,500** | **3,487** | **647** | **3** | **409** ⚠ | **1** |
| u_win_buf | 42 | 392 | 0 | 0 | 0 | 0 | 0 |
| u_line_buf | 111 | 0 | 0 | 0 | 3 | 0 | 0 |
| conv2d logic | 10,641 | 2,108 | 3,487 | 647 | 0 | — | — |

**⚠ IOB over-limit (409 / 200):** A 7×7 kernel exposes 49 output tap ports. Each tap carries
TDATA (8 bits) + TVALID + TREADY + TLAST + TUSER = 12 signals; 49 taps + 1 input port + clk/rst
gives over 590 port pins, which exceeds the XC7Z020's 200 physical IOBs. This is expected and
is not a problem for simulation — xsim simulates logic, not pin assignment. In a real
implementation, the tap outputs would connect on-chip (to a MAC array or PS interconnect) and
would never need to reach physical pins. The IOB count in the table reflects Vivado treating every
top-level port as a candidate for a physical pin during synthesis.

**BRAM tiles (3):** `KERN_ROWS − 1 = 6` row buffer slots, each 1,280 × 8 = 10,240 bits.
Vivado packs pairs of row buffers into single RAMB36 tiles (each 36 Kb), giving 3 RAMB36 tiles
for 6 rows.

**F7/F8 Muxes:** The large mux count arises from the combinational `p_edge_out` remapping stage,
which selects among 49 tap positions for REPLICATE clamping using cascaded LUT6+MUXF7+MUXF8
primitives. This is a direct consequence of choosing the coordinate-based output mux approach
(option A in Phase 4) for area efficiency over a pipeline-fill approach.

### Synthesis results — testbench configuration (3×3, 8-bit, DATA_WIDTH=8)

XC7Z020-CLG484-1, project-mode synthesis, no timing constraints:

| Block | Slice LUTs (/ 53,200) | Slice Registers (/ 106,400) | Block RAM Tiles (/ 140) | Bonded IOB (/ 200) | BUFGCTRL (/ 32) |
|---|---|---|---|---|---|
| **conv2d** (total) | **208** | **390** | **1** | **89** | **1** |
| u_line_buf | 51 | 0 | 1 | 0 | 0 |
| u_win_buf | 8 | 72 | 0 | 0 | 0 |
| conv2d logic | 149 | 318 | 0 | — | — |

### Scaling summary

| Resource | Scales with |
|---|---|
| Slice LUTs (logic) | `KERN_ROWS × KERN_COLS` (output mux for each tap) |
| Slice Registers | `KERN_ROWS × KERN_COLS × DATA_WIDTH` (tap pipeline registers) |
| F7/F8 Muxes | `KERN_ROWS × KERN_COLS` (REPLICATE/ZERO mux tree depth) |
| Block RAM Tiles | `ceil((KERN_ROWS − 1) × LINE_WIDTH × DATA_WIDTH / 36,864)` |
| Bonded IOB | `(KERN_ROWS × KERN_COLS + 1) × (DATA_WIDTH + 4) + 2` — exceeds device limit for large kernels |

---

## 4. Development Time Comparison

| Metric | Hand-written (supervisor) | AI-generated (this repository) |
|---|---|---|
| Development time | ~3 weeks | ~1 week |
| Speed improvement | — | **~3× faster** |
| Verification | — | 324 configurations + 3 back-pressure + 30 stress scenarios, all PASS |
| Bugs found during development | — | 9 (all fixed; see §2 above) |

The hand-written implementation was produced by an experienced VHDL engineer.
The AI-generated implementation was produced in one week using Claude with phase-gated
prompting: no code was written until a written architecture proposal was approved, and
each phase required a concrete simulation result before advancing.

---

## 5. Code Comprehensibility Assessment

The current codebase has a known comprehensibility limitation: the RTL files
(`src/conv2d.vhd`, `src/line_buf.vhd`, `src/win_buf.vhd`) contain minimal inline
comments. A reader unfamiliar with the design would need to cross-reference
`README.md` and `CLAUDE.md` (which contain full explanations of every design decision)
to understand non-obvious signal interactions such as:

- Why `pixel_accepted_d1` exists (Bug 2 — delta-cycle timing)
- Why `row_valid` is a vector and not a single bit (Bug 4 — inter-frame stale BRAM)
- Why `wb_new_row` is unconditionally `'0'` for TOROIDAL (Bug 6 — `toroidal_armed` guard)
- Why `rd_en => push` and not `rd_en => all_ready` (Bug 7 — s_tvalid LOW cycle BRAM read-ahead)
- The `STREAM_DIRECT` constant and when `tor_buf` replaces `line_buf`/`win_buf`

These explanations exist in `README.md` (architecture and design decisions) and
`CLAUDE.md` (bug log). The RTL itself does not repeat them inline, which is a gap
identified by the supervisor for the comparison evaluation.

---

## 6. Verification Coverage

All 357 test scenarios passed in xsim. Measured simulation run time: ~21,835 ns.

### Base configuration matrix (324 scenarios)

| Axis | Values |
|---|---|
| Kernel size | 3×3, 3×5, 3×7, 5×3, 5×5, 5×7, 7×3, 7×5, 7×7 |
| EDGE_MODE | ZERO, REPLICATE, TOROIDAL |
| FLUSH | false, true |
| Frame size (LINE_WIDTH × FRAME_HEIGHT) | 8×8, 16×4, 4×16, 3×3, 5×5, 8×1 |

### Back-pressure scenarios (3)

| ID | Configuration | Stall pattern |
|---|---|---|
| CFG001 | 3×3 ZERO FLUSH=off 8×8 | `m_tready(0)` LOW for 10 cycles post-reset |
| CFG040 | 3×5 ZERO FLUSH=off 3×3 | `m_tready(0)` LOW for 15 cycles from cycle 210 |
| CFG055 | 3×5 REPLICATE FLUSH=on 8×8 | `m_tready(NUM_TAPS−1)` LOW for 10 cycles post-reset |

### Stress scenarios (30)

Varied `s_tvalid` and/or `m_tready` with H/L burst patterns (e.g. 2 cycles HIGH,
3 cycles LOW). Three groups: `s_tvalid` only, `m_tready` only, both simultaneously.
Each stress DUT reuses the golden vectors of its base configuration; a transfer
occurs only when both signals are HIGH at the same rising clock edge.
