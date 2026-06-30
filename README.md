# 2D AXI4-Stream Video Convolution Window Extractor

A fully parameterised VHDL-2008 sliding-window extractor for AXI4-Stream video pipelines,
targeting the Avnet ZedBoard (Xilinx Zynq-7000 XC7Z020-CLG484-1). Designed for simulation
only; all verification is done in Vivado xsim.

---

## What This Design Does

For every input pixel it receives, the design outputs the M×N neighbourhood of pixel values
centred on (and trailing) that pixel — one AXI4-Stream port per tap. This is the raw data
required for a 2D convolution kernel: multiply each tap by its coefficient and sum. The
multiplication and accumulation are downstream and **not** part of this design.

```
Input stream          Output taps (3×3 example, 9 ports)
─────────────         ──────────────────────────────────
 pixel (r,c)    →     tap[0][0]  tap[0][1]  tap[0][2]   (oldest row, leftward)
                       tap[1][0]  tap[1][1]  tap[1][2]
                       tap[2][0]  tap[2][1]  tap[2][2]   (current row, current→left)
```

---

## Architecture

Three sub-blocks instantiated inside the top-level `conv2d` entity:

| Block | File | Purpose |
|---|---|---|
| `conv2d` | `src/conv2d.vhd` | Top-level: counters, edge logic, FLUSH FSM, output pipeline |
| `line_buf` | `src/line_buf.vhd` | BRAM-based row store (KERN_ROWS−1 rows × LINE_WIDTH pixels); infers 2× RAMB18 on ZedBoard for 3×3 8-bit |
| `win_buf` | `src/win_buf.vhd` | FF shift-register column window (KERN_ROWS × KERN_COLS taps) |

**Pipeline depth:** 3 clock cycles from pixel accepted to tap output valid.

**Back-pressure:** all M×N `m_tready` signals are AND-gated; the pipeline stalls cleanly
when any downstream consumer is not ready. The BRAM synchronous read is gated by `all_ready`
so that `lb_rd_data` holds stable during stalls — preventing one-column-ahead prefetch errors
when the pipeline resumes.

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
| `FLUSH` | boolean | `false` | Inject KERN_ROWS−1 dummy rows after each frame end |

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

When `FLUSH=true`, after the last pixel of each frame the design injects `KERN_ROWS−1`
dummy zero-rows. This ensures the final `KERN_ROWS−1` real rows each produce a valid
output window (without FLUSH they would not, as their older-row taps never fill). Real
input is stalled (`s_tready` deasserted) during the flush. The next SOF pixel re-arms
the design correctly.

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

## Verified Configurations

All 58 configurations below pass bit-exactly in Vivado xsim (recommended simulation
time: 8000 ns). 3 back-pressure scenarios exercised (CFG01, CFG40, CFG41).

### Standard matrix — DATA_WIDTH × kernel × EDGE_MODE × FLUSH (8×8 frame)

| # | DATA_WIDTH | Kernel | EDGE_MODE | FLUSH | Outputs checked |
|---|---|---|---|---|---|
| CFG01 | 8 | 3×3 | ZERO | off | 192 (+ back-pressure) |
| CFG02 | 8 | 3×3 | ZERO | on | 240 |
| CFG03 | 8 | 3×3 | REPLICATE | off | 192 |
| CFG04 | 8 | 3×3 | REPLICATE | on | 240 |
| CFG05 | 8 | 3×3 | TOROIDAL | off | 192 |
| CFG06 | 8 | 3×3 | TOROIDAL | on | 240 |
| CFG07 | 8 | 5×5 | ZERO | off | 192 |
| CFG08 | 8 | 5×5 | ZERO | on | 288 |
| CFG09 | 8 | 5×5 | REPLICATE | off | 192 |
| CFG10 | 8 | 5×5 | REPLICATE | on | 288 |
| CFG11 | 8 | 5×5 | TOROIDAL | off | 192 |
| CFG12 | 8 | 5×5 | TOROIDAL | on | 288 |
| CFG13 | 8 | 3×5 | ZERO | off | 192 |
| CFG14 | 8 | 3×5 | ZERO | on | 240 |
| CFG15 | 8 | 3×5 | REPLICATE | off | 192 |
| CFG16 | 8 | 3×5 | REPLICATE | on | 240 |
| CFG17 | 8 | 3×5 | TOROIDAL | off | 192 |
| CFG18 | 8 | 3×5 | TOROIDAL | on | 240 |
| CFG19 | 16 | 3×3 | ZERO | off | 192 |
| CFG20 | 16 | 3×3 | ZERO | on | 240 |
| CFG21 | 16 | 3×3 | REPLICATE | off | 192 |
| CFG22 | 16 | 3×3 | REPLICATE | on | 240 |
| CFG23 | 16 | 3×3 | TOROIDAL | off | 192 |
| CFG24 | 16 | 3×3 | TOROIDAL | on | 240 |
| CFG25 | 16 | 5×5 | ZERO | off | 192 |
| CFG26 | 16 | 5×5 | ZERO | on | 288 |
| CFG27 | 16 | 5×5 | REPLICATE | off | 192 |
| CFG28 | 16 | 5×5 | REPLICATE | on | 288 |
| CFG29 | 16 | 5×5 | TOROIDAL | off | 192 |
| CFG30 | 16 | 5×5 | TOROIDAL | on | 288 |
| CFG31 | 16 | 3×5 | ZERO | off | 192 |
| CFG32 | 16 | 3×5 | ZERO | on | 240 |
| CFG33 | 16 | 3×5 | REPLICATE | off | 192 |
| CFG34 | 16 | 3×5 | REPLICATE | on | 240 |
| CFG35 | 16 | 3×5 | TOROIDAL | off | 192 |
| CFG36 | 16 | 3×5 | TOROIDAL | on | 240 |

### Non-square frames and degenerate sizes

| # | DATA_WIDTH | Kernel | EDGE_MODE | FLUSH | Frame | Outputs checked |
|---|---|---|---|---|---|---|
| CFG37 | 8 | 3×3 | ZERO | off | 16×4 | 192 (+ back-pressure) |
| CFG38 | 8 | 3×3 | REPLICATE | off | 16×4 | 192 |
| CFG39 | 8 | 3×3 | TOROIDAL | off | 16×4 | 192 |
| CFG40 | 8 | 3×3 | ZERO | on | 16×4 | 288 (+ back-pressure) |
| CFG41 | 8 | 5×5 | ZERO | on | 16×4 | 384 (+ back-pressure) |
| CFG42 | 8 | 3×3 | ZERO | off | 4×16 | 192 |
| CFG43 | 8 | 3×3 | REPLICATE | off | 4×16 | 192 |
| CFG44 | 8 | 3×3 | TOROIDAL | off | 4×16 | 192 |
| CFG45 | 8 | 3×3 | ZERO | off | 3×3 | 27 |
| CFG46 | 8 | 3×3 | REPLICATE | off | 3×3 | 27 |
| CFG47 | 8 | 3×3 | ZERO | on | 3×3 | 45 |
| CFG48 | 8 | 5×5 | ZERO | off | 5×5 | 75 |
| CFG49 | 8 | 5×5 | REPLICATE | off | 5×5 | 75 |
| CFG50 | 8 | 3×3 | ZERO | on | 8×1 | 72 |
| CFG51 | 8 | 5×5 | ZERO | on | 8×1 | 120 |

### Additional data widths and kernel sizes

| # | DATA_WIDTH | Kernel | EDGE_MODE | FLUSH | Outputs checked |
|---|---|---|---|---|---|
| CFG52 | 24 | 3×3 | ZERO | off | 192 |
| CFG53 | 24 | 3×3 | REPLICATE | off | 192 |
| CFG54 | 24 | 5×5 | ZERO | on | 288 |
| CFG55 | 8 | 7×7 | ZERO | off | 192 |
| CFG56 | 8 | 7×7 | REPLICATE | off | 192 |
| CFG57 | 8 | 7×7 | TOROIDAL | off | 192 |
| CFG58 | 8 | 7×7 | ZERO | on | 336 |

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
  vectors/
    c01_input.txt     Stimulus pixels for CFG01
    c01_expected.txt  Expected tap values for CFG01
    ...               (116 files total, c01..c58 × input/expected)

scripts/
  gen_tb.py           Regenerates conv2d_tb.vhd and all 116 vector files.
                      Run: python scripts/gen_tb.py  (from repo root)
  gen_vectors.py      Standalone parametric vector generator for a single config.
                      Run: python scripts/gen_vectors.py --help
  visualize.py        Interactive matplotlib visualizer — shows input frame heatmap
                      and tap window side-by-side for any config and pixel position.
                      Run: python scripts/visualize.py  (requires matplotlib)

synth/
  phase1_lut_vs_bram/
    PHASE1_REPORT.md      Phase 1 research report: HDL survey, LUT-vs-BRAM comparison,
                          synthesis utilisation numbers, architecture proposal
    row_buf_bram_style.vhd  Reference: BRAM-inferring coding style (2× RAMB18 on ZedBoard)
    row_buf_lut_style.vhd   Reference: LUT-RAM style (for comparison — do not use)

CLAUDE.md             Full project log: decisions, bug log, phase status, constraints
README.md             This file
```

---

## How to Run in Vivado

1. Create a Vivado project targeting **XC7Z020-CLG484-1**.
2. Add `src/line_buf.vhd`, `src/win_buf.vhd`, `src/conv2d.vhd` as design sources.
3. Add `tb/conv2d_tb.vhd` as a simulation source.
4. Copy the entire `tb/vectors/` folder to a path accessible from the simulation
   working directory (Vivado xsim uses the project's `<project>.sim/sim_1/behav/xsim/`
   folder; place `vectors/` there, or adjust the path constants in `conv2d_tb.vhd`).
5. Set `conv2d_tb` as the top-level simulation unit.
6. Run Behavioral Simulation. The testbench prints `PASS` or `FAIL` for each config.

To regenerate the testbench and vectors after changing frame parameters or the config
matrix, run `python scripts/gen_tb.py` from the repo root, then repeat from step 2.

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
