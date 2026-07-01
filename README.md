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

The intuitive starting point is: to assemble a 3×3 window you need 3 rows of history.
So you might expect 3 BRAMs.

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

Your intuition about alternating read/write pointers is exactly right. Here is how it works.

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

### TOROIDAL (causal approximation)
The image is treated as wrapping. In a causal streaming pipeline, right-edge and
bottom-edge wrap targets have not yet arrived; those positions fall back to the shift
register's natural content (zero on the very first frame, previous-frame data thereafter).

- **Column wrap:** the shift register is never cleared at row boundaries
  (`wb_new_row` held `'0'`). At col 0 of any row, positions 1..KERN_COLS−1 already
  hold the previous row's rightmost pixels — the natural causal column wrap.
- **Row wrap:** BRAM is never zeroed between frames. After the first full warm-up
  (`KERN_ROWS−1` rows, guarded by `toroidal_armed`), BRAM retains the previous
  frame's last rows and they flow through unrestricted.
- **`toroidal_armed` flag:** prevents xsim's uninitialised BRAM (`'U'`) from
  propagating before BRAM has been written for the first time.

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

All 58 configurations below pass bit-exactly in Vivado xsim (recommended simulation
time: 8000 ns). 3 back-pressure scenarios exercised (CFG01, CFG40, CFG55).

### Standard matrix — DATA_WIDTH × kernel × EDGE_MODE × FLUSH (8×8 frame)

| # | DATA_WIDTH | Kernel | EDGE_MODE | FLUSH | Outputs checked |
|---|---|---|---|---|---|
| CFG01 | 8 | 3×3 | ZERO | off | 168 (+ back-pressure) |
| CFG02 | 8 | 3×3 | ZERO | on | 192 |
| CFG03 | 8 | 3×3 | REPLICATE | off | 168 |
| CFG04 | 8 | 3×3 | REPLICATE | on | 192 |
| CFG05 | 8 | 3×3 | TOROIDAL | off | 168 |
| CFG06 | 8 | 3×3 | TOROIDAL | on | 192 |
| CFG07 | 8 | 5×5 | ZERO | off | 144 |
| CFG08 | 8 | 5×5 | ZERO | on | 192 |
| CFG09 | 8 | 5×5 | REPLICATE | off | 144 |
| CFG10 | 8 | 5×5 | REPLICATE | on | 192 |
| CFG11 | 8 | 5×5 | TOROIDAL | off | 144 |
| CFG12 | 8 | 5×5 | TOROIDAL | on | 192 |
| CFG13 | 8 | 3×5 | ZERO | off | 168 |
| CFG14 | 8 | 3×5 | ZERO | on | 192 |
| CFG15 | 8 | 3×5 | REPLICATE | off | 168 |
| CFG16 | 8 | 3×5 | REPLICATE | on | 192 |
| CFG17 | 8 | 3×5 | TOROIDAL | off | 168 |
| CFG18 | 8 | 3×5 | TOROIDAL | on | 192 |
| CFG19 | 16 | 3×3 | ZERO | off | 168 |
| CFG20 | 16 | 3×3 | ZERO | on | 192 |
| CFG21 | 16 | 3×3 | REPLICATE | off | 168 |
| CFG22 | 16 | 3×3 | REPLICATE | on | 192 |
| CFG23 | 16 | 3×3 | TOROIDAL | off | 168 |
| CFG24 | 16 | 3×3 | TOROIDAL | on | 192 |
| CFG25 | 16 | 5×5 | ZERO | off | 144 |
| CFG26 | 16 | 5×5 | ZERO | on | 192 |
| CFG27 | 16 | 5×5 | REPLICATE | off | 144 |
| CFG28 | 16 | 5×5 | REPLICATE | on | 192 |
| CFG29 | 16 | 5×5 | TOROIDAL | off | 144 |
| CFG30 | 16 | 5×5 | TOROIDAL | on | 192 |
| CFG31 | 16 | 3×5 | ZERO | off | 168 |
| CFG32 | 16 | 3×5 | ZERO | on | 192 |
| CFG33 | 16 | 3×5 | REPLICATE | off | 168 |
| CFG34 | 16 | 3×5 | REPLICATE | on | 192 |
| CFG35 | 16 | 3×5 | TOROIDAL | off | 168 |
| CFG36 | 16 | 3×5 | TOROIDAL | on | 192 |

### Non-square frames and degenerate sizes

| # | DATA_WIDTH | Kernel | EDGE_MODE | FLUSH | Frame | Outputs checked |
|---|---|---|---|---|---|---|
| CFG37 | 8 | 3×3 | ZERO | off | 16×4 | 144 |
| CFG38 | 8 | 3×3 | REPLICATE | off | 16×4 | 144 |
| CFG39 | 8 | 3×3 | TOROIDAL | off | 16×4 | 144 |
| CFG40 | 8 | 3×3 | ZERO | on | 16×4 | 192 (+ back-pressure) |
| CFG41 | 8 | 5×5 | ZERO | on | 16×4 | 192 |
| CFG42 | 8 | 3×3 | ZERO | off | 4×16 | 180 |
| CFG43 | 8 | 3×3 | REPLICATE | off | 4×16 | 180 |
| CFG44 | 8 | 3×3 | TOROIDAL | off | 4×16 | 180 |
| CFG45 | 8 | 3×3 | ZERO | off | 3×3 | 18 |
| CFG46 | 8 | 3×3 | REPLICATE | off | 3×3 | 18 |
| CFG47 | 8 | 3×3 | ZERO | on | 3×3 | 27 |
| CFG48 | 8 | 5×5 | ZERO | off | 5×5 | 45 |
| CFG49 | 8 | 5×5 | REPLICATE | off | 5×5 | 45 |
| CFG50 | 8 | 3×3 | ZERO | on | 8×1 | 24 |
| CFG51 | 8 | 5×5 | ZERO | on | 8×1 | 24 |

### Additional data widths and kernel sizes

| # | DATA_WIDTH | Kernel | EDGE_MODE | FLUSH | Outputs checked |
|---|---|---|---|---|---|
| CFG52 | 24 | 3×3 | ZERO | off | 168 |
| CFG53 | 24 | 3×3 | REPLICATE | off | 168 |
| CFG54 | 24 | 5×5 | ZERO | on | 192 |
| CFG55 | 8 | 7×7 | ZERO | off | 120 (+ back-pressure) |
| CFG56 | 8 | 7×7 | REPLICATE | off | 120 |
| CFG57 | 8 | 7×7 | TOROIDAL | off | 120 |
| CFG58 | 8 | 7×7 | ZERO | on | 192 |

---

## File Structure

```
src/
  conv2d.vhd          Top-level RTL — paste into Vivado as a design source
  line_buf.vhd        BRAM row buffer sub-block
  win_buf.vhd         FF shift-register column window sub-block

tb/
  conv2d_tb.vhd       Self-checking testbench (machine-generated by gen_tb.py)
                      Runs all 58 DUT instances in parallel on a shared clock
  visualize.html      Self-contained interactive visualiser (generated by visualize.py)
                      Open directly in any browser — no server or install needed
  vectors/
    c01_input.txt     Stimulus pixels for CFG01
    c01_expected.txt  Expected tap values for CFG01
    ...               (116 files total, c01..c58 × input/expected)

scripts/
  gen_tb.py           Regenerates conv2d_tb.vhd and all 116 vector files.
                      Run: python scripts/gen_tb.py  (from repo root)
  gen_vectors.py      Standalone parametric vector generator for a single config.
                      Run: python scripts/gen_vectors.py --help
  visualize.py        Generates tb/visualize.html — the interactive visualiser.
                      Run: python scripts/visualize.py  (from repo root)

CLAUDE.md             Full project log: decisions, bug log, phase status, constraints
README.md             This file
```

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

`tb/visualize.html` is a self-contained HTML file with all 58 configurations' golden
vector data embedded. No server, install, or network connection is required — just open
it in any modern browser.

### Generating / Updating

```
python scripts/visualize.py
```

Run from the repo root. Reads vector files from `tb/vectors/` and writes
`tb/visualize.html` (~915 KB). Re-run whenever vectors change.

### Selecting a Configuration

Use the **Config** dropdown (top-left) to pick any of the 58 test configurations.
Each entry shows data width, kernel size, edge mode, flush state, and frame dimensions.

### Explore Mode

Click the **Explore** button (or press `E`) to enter Explore mode.

- **Click any pixel** in the frame heatmap to select it.
- The **tap window panel** (right side) shows the M×N neighbourhood for that pixel,
  colour-coded by age: newest pixel is brightest.
- The selected pixel and its window footprint are highlighted on the heatmap.
- Use the **frame selector** to switch between the multiple frames in the dataset.

### Simulate Mode

Click the **Simulate** button (or press `S`) to enter Simulate mode.

Simulate mode steps through every clock cycle at which the DUT produces a valid output,
showing the 2-stage pipeline in real time.

#### Pipeline diagram

Two stage cards are shown left-to-right:

| Stage | Colour | What it shows |
|---|---|---|
| **ACCEPT** | Green | The trigger pixel accepted from the input stream 2 cycles before this output. This is pixel (out_r + HALF_R, out_c + HALF_C) — the bottom-right corner of the window. |
| **OUTPUT** | Blue | The window output now valid at `m_tdata` / `m_tvalid`. The centre of this window is pixel (out_r, out_c). |

Each card shows the pixel's frame number, row, column, and raw value. Flush-injected
pixels (dummy zero rows when FLUSH=on) are labelled *flush* and appear in the padding
border of the frame canvas.

The frame canvas highlights both pipeline positions simultaneously: the trigger pixel
(ACCEPT, green border) and the output window footprint (OUTPUT, blue border).

#### Playback controls

| Control | Action |
|---|---|
| ⏮ | Jump to first output |
| ⏪ | Step back one cycle |
| ▶ / ⏸ | Play / pause automatic playback |
| ⏩ | Step forward one cycle |
| ⏭ | Jump to last output |
| Scrubber bar | Drag to any position in the sequence |
| Speed selector | 0.5× / 1× / 2× / 4× / 10× / 25× playback speed |

#### Keyboard shortcuts (Simulate mode)

| Key | Action |
|---|---|
| `←` / `→` | Step back / forward one cycle |
| `Home` / `End` | Jump to first / last output |
| `Space` | Play / pause |
| `,` / `.` | Previous / next configuration |

---

## Synthesis Resource Reference (XC7Z020, 3×3, 8-bit)

From out-of-context synthesis only — not a timing-closed implementation:

| Resource | Used | Available |
|---|---|---|
| RAMB18 | 2 | 280 |
| Slice LUTs (logic) | ~50 | 53,200 |
| Slice Registers | ~80 | 106,400 |
| DSP48E1 | 0 | 220 |

BRAM inference is confirmed: 0 LUT-as-RAM. Larger kernels add one RAMB18 per additional
row buffer slot. The design scales with device resources; there is no hard upper bound on
kernel size beyond what the device can accommodate.

---

## What This Design Does NOT Do

- No coefficient multiplication or MAC stage (that is downstream).
- No bitstream generation or board bring-up (simulation only).
- No runtime register interface (all configuration is compile-time generics).
- No Zynq PS / FCLK integration (free-running testbench clock only).
- No right-edge or bottom-edge true toroidal wrap (requires full-frame buffering;
  those positions fall back to causal history content instead).
