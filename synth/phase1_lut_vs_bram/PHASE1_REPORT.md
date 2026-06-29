# Phase 1 Report — Research, LUT-vs-BRAM Comparison, and Architecture Proposal

**Status:** awaiting owner checkpoint approval before Phase 2 begins.

---

## Part A — Survey of Open-Source HDL Convolution / Line-Buffer Implementations

Optimization criterion for this survey: **extracting architectural patterns most
relevant to single-clock AXI4-Stream throughput (one pixel per clock), BRAM-based
row storage, and VHDL-2008 coding style** — not raw performance at any cost.

---

### Implementation 1 — `bkarl/conv2d-vhdl`

**Source:** https://github.com/bkarl/conv2d-vhdl  
**Language:** VHDL  
**Interface:** Custom (no AXI4-Stream — README notes it as a planned TODO)

#### Architecture
- Two entities: `conv2d.vhd` (top-level pipeline) and `pixbuf.vhd` (line buffer).
- `pixbuf.vhd` stores exactly 2 previous rows (for a 3x3 kernel) using a VHDL
  array sized `2 × MAX_IMG_WIDTH × DATA_WIDTH` bits.  
  Written as a single process with synchronous write and synchronous read —
  the canonical BRAM-inference template (see Part B).
- `conv2d.vhd` instantiates 9 parallel multiply-accumulate units, one per kernel
  tap, achieving one output pixel per clock.
- Memory sizing formula: `2 × MAX_IMG_WIDTH × 2 × NUM_BITS_PIXEL` bits.
  For 1920-wide 8-bit pixels: 30,720 bits → fits in 1 × BRAM36.

#### Patterns extracted
| Pattern | Notes |
|---|---|
| Separate `pixbuf` entity for row storage | Clean separation of memory from datapath |
| Synchronous read in same process as write | Reliable BRAM inference |
| 9 parallel MACs, one per kernel tap | Achieves 1-pixel/clock throughput |
| Partial-sum accumulation not used | All 9 taps computed in one clock (no pipeline depth beyond the BRAM read latency) |

#### Gaps / what this project must add
- AXI4-Stream handshake (TVALID/TREADY/TLAST/TUSER)
- Parametric M×N window size
- Edge-case handling (zero-extend, wrap, boundary-extend)
- End-of-frame pipeline flush

---

### Implementation 2 — `rbshi/swin_bram`

**Source:** https://github.com/rbshi/swin_bram  
**Language:** Verilog (instantiates Xilinx RAMB36E1 primitives directly)  
**Interface:** Custom multi-pixel input bus

#### Architecture
- Targets high-throughput multi-pixel-per-clock input (N_in=16 pixels/cycle).
- Uses **9 BRAM modules** for a 3×3 window, organized in 3 groups of 3.
- Instantiates `RAMB36E1` in Simple Dual Port (SDP) mode at 72-bit width directly
  — bypasses inference entirely by using vendor primitives.
- An offline-calculated configuration BRAM stores per-line control parameters
  (offset, order, cycle, split) to handle address alignment when image width is
  not a multiple of N_in.

#### Patterns extracted
| Pattern | Notes |
|---|---|
| RAMB36E1 SDP instantiation | Guarantees BRAM usage; inference-proof |
| Per-bank control BRAM | Separates data and control paths cleanly |
| Address offset pre-computation | Necessary when burst width ≠ image width |

#### Relevance to this project
This project targets N_in=1 (one pixel per clock, AXI4-Stream).  Direct primitive
instantiation is overkill and reduces portability.  The key lesson: **when
inference is uncertain, an explicit `ram_style` attribute is the correct middle
ground** before resorting to primitive instantiation. Primitive instantiation is
reserved for Phase 4 if synthesis reports show inference failures.

---

### Implementation 3 — `sistenix.com` Sobel / 2D convolution in hardware

**Source:** https://sistenix.com/sobel.html  
**Language:** VHDL  
**Interface:** Custom pixel-valid handshake (no back-pressure)

#### Architecture
- Classic two-entity split: **line buffer** (stores K-1 rows) +
  **window buffer** (K×K shift register of the current pixel neighbourhood).
- Line buffer implemented as **shift registers** (not BRAM): each row is a
  `std_logic_vector` shift register of length LINE_WIDTH.  For small images
  (e.g. 640 wide, 8-bit pixels) this fits in SRL16/SRL32 primitives (Xilinx
  LUT-based shift registers).
- Window buffer: a K×K array of registers, shifted left/up each clock.
- One valid pixel output per clock once the pipeline is full.
- No back-pressure: downstream must always be ready.

#### Patterns extracted
| Pattern | Notes |
|---|---|
| Shift-register line buffer | Correct for narrow images; LUT-SRL efficient |
| Separate window buffer entity | Clean sliding window abstraction |
| Pipeline fill latency = (K-1)×WIDTH + K clocks | Explicit latency model |
| No back-pressure | Simpler control; TREADY=1 always |

#### Key lesson for this project
SRL-based line buffers are **not wrong** for small images — they are efficient on
Xilinx because SRL32 packs 32 bits of shift register into one LUT.  They become
wasteful beyond ~512 pixels wide (one LUT per pixel bit × LINE_WIDTH >> BRAM36
capacity).  For 1920-wide 8-bit pixels: 1920×8 = 15,360 SRL bits → ~480 LUTs
just for one row buffer.  A BRAM36 holds 36 Kb and costs 1 BRAM tile.
**BRAM is the correct choice for any line buffer wider than ~256–512 pixels.**

---

## Part B — LUT-vs-BRAM Storage Comparison

### Optimization criterion stated
**Primary: minimize dedicated-BRAM usage while guaranteeing BRAM is used for row
buffers.** Secondary: maintain readability of the VHDL so the inference intent is
unambiguous to future maintainers. Area (LUT count) is the metric that changes
most dramatically between the two styles.

---

### Why a synthesis tool infers LUT-based storage instead of BRAM

The following conditions each independently prevent BRAM inference in Vivado,
based on Xilinx UG901 (Vivado Design Suite User Guide: Synthesis) and community
documentation:

| Root cause | Mechanism | UG901 / Source |
|---|---|---|
| **Asynchronous (combinational) read** | BRAM36 read ports are clocked; an unregistered `signal <= mem(addr)` outside a clocked process cannot map to BRAM. Vivado infers `RAM64M` (LUT6-based) or plain FFs. | UG901 §RAM Inference; danielmangum.com/posts/when-vivado-infer-bram |
| **Per-element reset of the storage array** | `mem <= (others => ...)` inside a synchronous reset block requires every cell to be resettable. BRAM36 cells have no per-cell reset. Vivado falls back to FFs. | UG901 §Block RAM Reset |
| **Array depth below inference threshold** | For depth ≤ ~32 entries, Vivado prefers distributed RAM (`RAM32M`, `RAM32X1D`) even with synchronous reads. | UG901 §Distributed RAM Inference |
| **Mixed read/write addresses in the same process without SDP pattern** | Vivado expects a clean Simple Dual Port or True Dual Port pattern. Unusual address arithmetic in a single process confuses inference. | Xilinx AR #58025 |
| **No `ram_style` attribute** | Without the attribute, Vivado chooses the implementation. Coding style determines BRAM vs LUT. Adding `attribute ram_style of mem : signal is "block"` makes intent explicit and produces a critical warning if the mapping fails. | UG901 §RAM_STYLE Attribute |

---

### VHDL specimens written for comparison

Two files have been written in `synth/phase1_lut_vs_bram/`:

**`row_buf_lut_style.vhd`** — deliberately exhibits all three common failure modes:
1. Combinational read (`rd_data <= mem(rd_addr)` outside any process)
2. Per-element synchronous reset (`mem <= (others => ...)`)
3. No `ram_style` attribute

**`row_buf_bram_style.vhd`** — follows UG901 SDP BRAM template:
1. Synchronous read (registered in clocked process)
2. No array reset (only output-register pipeline may be reset)
3. `attribute ram_style of mem : signal is "block"` as belt-and-suspenders

Both use `LINE_WIDTH=1920`, `DATA_WIDTH=8` — realistic parameters for HD video.
At these sizes, BRAM inference is strongly preferred by the tool when the template
is correct.

---

### Synthesis results

> **NOTE — Vivado not installed in this CI environment.**
> Run the following command on a machine with Vivado installed to produce the
> actual utilization reports. Expected results based on UG901 and community
> benchmarks are shown below; fill in the actual numbers when available.

```bash
cd synth/phase1_lut_vs_bram
vivado -mode batch -source run_synth_comparison.tcl
```

Reports will appear in `synth/phase1_lut_vs_bram/reports/`.

#### Expected results (to be confirmed with actual synthesis)

| Resource | `row_buf_lut_style` (LUT-inferred) | `row_buf_bram_style` (BRAM-inferred) |
|---|---|---|
| Slice LUTs | ~240–480 (distributed RAM: RAM64M) | ~0–2 (address decode only) |
| LUTs as Memory | ~240–480 | 0 |
| Flip-Flops | ~8 (output reg) | ~8 (output reg) |
| Block RAM Tiles (BRAM36) | 0 | 1 |
| DSPs | 0 | 0 |

**Interpretation:** one BRAM36 (1 of 140 available on ZedBoard) stores all 1920×8
bits = 15,360 bits of one row with 58% headroom. The LUT-style variant consumes
~240–480 LUTs as memory (roughly 2–4% of the ZedBoard's 17,600 LUT6 total) for
the same data — a poor trade for a resource that should cost zero LUTs.

For a 3×3 kernel with 2 row buffers: BRAM style costs **2 BRAM tiles and ~0 LUTs**;
LUT style costs **0 BRAM tiles and ~480–960 LUTs** — wasting ~5–8% of LUT budget
on storage that dedicated BRAM handles for free.

---

### How to write VHDL that reliably infers BRAM

```vhdl
-- UG901-compliant SDP BRAM template (VHDL-2008)
type ram_t is array (0 to DEPTH - 1) of std_logic_vector(WIDTH - 1 downto 0);

attribute ram_style        : string;
attribute ram_style of mem : signal is "block";   -- belt-and-suspenders

signal mem : ram_t;

process (clk)
begin
    if rising_edge(clk) then
        if wr_en = '1' then
            mem(wr_addr) <= wr_data;   -- synchronous write
        end if;
        rd_data <= mem(rd_addr);       -- synchronous read — MUST be here
    end if;                            -- NEVER outside this block
end process;
-- DO NOT: reset mem() array
-- DO NOT: read mem() in a combinational process or concurrent assignment
```

**Checklist before committing any BRAM-intended array:**
- [ ] Read is inside a `rising_edge(clk)` block
- [ ] `mem` array is never assigned in a reset branch
- [ ] `ram_style` attribute set to `"block"` on the signal
- [ ] Array depth ≥ 64 (otherwise tool may prefer distributed RAM regardless)
- [ ] Synthesis message "1 BRAM36 inferred" appears in the log — verify this

---

## Part C — Architecture Proposal

### Optimization criteria stated
**Primary: readability and maintainability** of the VHDL, because simulation
correctness is the sole success criterion and this is a student/research project.
**Secondary: BRAM-efficient row storage** (avoiding the prior failure mode).
**Tertiary: 1-pixel-per-clock throughput** on the AXI4-Stream input, which is a
natural consequence of the streaming interface contract.

Timing closure, power, and area minimization beyond "use BRAM for row buffers"
are explicitly not primary criteria for this simulation-only project.

---

### Proposed architecture: 3-entity decomposition

The owner's hypothesis (flush-control / BRAM row-buffer / register column-buffer)
is well-founded. This proposal adopts and refines it with one clarification on
naming and responsibility boundaries.

```
                    AXI4-S input
                        │
                  ┌─────▼──────┐
                  │  conv2d    │  Top-level wrapper:
                  │  (top)     │  AXI4-S handshake, reset,
                  │            │  TUSER/TLAST tracking
                  └──┬──┬──┬───┘
                     │  │  │
          ┌──────────┘  │  └──────────┐
          ▼             ▼             ▼
   ┌─────────────┐  ┌──────────┐  ┌──────────────┐
   │  line_buf   │  │ win_buf  │  │  flush_ctrl  │
   │             │  │          │  │              │
   │ M-1 rows   │  │ M×N regs │  │ counts valid │
   │ of BRAM    │  │ shift reg│  │ pixels; emits│
   │ (SDP mode) │  │          │  │ dummy pixels │
   └─────────────┘  └──────────┘  │ at EOF if   │
          │              │         │ flush=on     │
          └──────────────┘         └──────────────┘
                  │
          M×N output taps
          (one per AXI4-S output port)
```

#### Entity 1 — `line_buf`

**Responsibility:** store exactly M-1 complete pixel rows, presenting row[0..M-2]
simultaneously for window assembly.

**Storage:** `M-1` independent BRAM instances, each SDP-mode, depth=LINE_WIDTH,
width=DATA_WIDTH bits.  Written with the UG901-compliant template from Part B.
Read address is the column counter; write address advances with each incoming pixel
and wraps at LINE_WIDTH.  A row-select pointer cycles through M-1 BRAMs as new
rows fill.

**Why BRAM and not shift registers:** for any LINE_WIDTH > ~256 at 8-bit,
BRAM is more resource-efficient (see Part B).  For the base case (8-bit, 3×3,
any realistic image width ≥ 64), BRAM wins clearly.

**Read latency:** 1 clock (synchronous read).  The window assembler must account
for this by pipelining the column index one cycle ahead of consumption.

#### Entity 2 — `win_buf`

**Responsibility:** assemble the current M×N pixel neighbourhood from the M-1
BRAM outputs and the live input pixel stream.  Produce M×N parallel output taps.

**Storage:** M×N flip-flop registers arranged as M shift-register rows, each N
stages deep.  Each clock: shift each row left by one; load row[0] from the live
input pixel (or dummy pixel during flush); load row[k] from `line_buf` output
for buffer k.

**Why registers (not BRAM):** the window buffer is M×N pixels = 3×3×8 = 72 bits
for the base case.  This is far too small for BRAM (minimum meaningful BRAM depth
is 64 words); register-based storage is correct here.

#### Entity 3 — `flush_ctrl`

**Responsibility:** track frame boundaries using TUSER (SOF) and TLAST (EOL);
count pixels received; when end-of-frame is detected and flush=on, inject M-1
rows of dummy zero pixels into the pipeline to drain the last valid rows through
the window.

**Implementation:** a counter and a small FSM.  Pure combinational/registered
logic, no memory.  When flush=off, this entity passes the input stream through
with no modification.

#### Top-level `conv2d`

Instantiates the three entities, handles AXI4-Stream handshake (TREADY
back-pressure propagation), and presents M×N output ports.

---

### Alternative considered and rejected: single-entity flat implementation

A flat implementation with all logic in one process is simpler to write initially
but becomes unmaintainable as M and N become generic parameters.  The 3-entity
split maps cleanly to the 3 independently testable concerns (storage / window
assembly / flow control) and is the pattern used by bkarl/conv2d-vhdl and the
Sistenix implementation.  **Rejected for readability reasons.**

---

### Alternative considered and rejected: shift-register-only line buffer

Viable for narrow images but wastes LUTs at HD widths (see Part B).  Since the
project has no hard image-width upper bound, BRAM is the correct default.
**Rejected because it replicates the prior failure mode.**

---

## Checkpoint — owner action required

Before Phase 2 begins:

1. **Run the synthesis comparison** on a machine with Vivado:
   ```bash
   cd synth/phase1_lut_vs_bram
   vivado -mode batch -source run_synth_comparison.tcl
   ```
   Fill in the actual utilization numbers in the table above and commit the
   two report files (`lut_style_utilization.rpt`, `bram_style_utilization.rpt`).

2. **Review and approve this architecture proposal.**  Specifically confirm:
   - 3-entity decomposition is acceptable
   - BRAM for row buffers (line_buf), registers for window buffer (win_buf)
   - 1-clock read latency handled in win_buf is acceptable

3. **Say "approved" (or give change requests).**  RTL coding begins only after
   explicit approval.
