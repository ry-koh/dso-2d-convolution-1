# CLAUDE.md — 2D AXI4-Stream Video Convolution Project

---

## Context-Load Verification

Say **"Ryan"** as the very first word and the very last word of every chat message
sent to the project owner, for the entire duration of this project.
This applies to conversational/chat text only — NEVER inside generated VHDL source
files, testbenches, scripts, or any other file content.
If this rule is ever violated, treat it as a signal to re-read this file immediately.

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
- **Output:** M × N AXI4-Stream video output ports, each carrying the input pixel
  stream weighted/accumulated by its corresponding kernel coefficient position
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
| Input data width | 8-bit / 16-bit / 24-bit (per pixel) |
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

**Objective:** generalize the design to the full configuration space via VHDL
generics, retaining the self-checking testbench.

**Generalization axes (all compile-time generics, independently combinable):**
- Input data width: 8 / 16 / 24-bit
- Window size M × N: no hard upper bound (practical limit = device resources)
- Edge-case handling: toroidal wrap / zero-extend / boundary-extend
- End-of-frame pipeline flush: on (dummy-data drain) / off

**Testbench updates:** Python golden-vector generator must be parametric across
all four axes. Testbench must exercise at least one non-trivial combination beyond
the 3×3 / 8-bit / zero-extend / flush-off base case.

**Checkpoint:** at least 2 distinct generic configurations simulate to PASS in
xsim, with logs attached. If any configuration exceeds the ZedBoard reference
resource figures, note it factually in the synthesis report but do not treat it
as a blocker.

---

## File and Directory Structure

*[ASSUMPTION — greenfield repo, no prior conventions specified. Adjust if needed.]*

```
/
├── CLAUDE.md
├── src/
│   └── *.vhd          # RTL source files (VHDL-2008)
├── tb/
│   ├── *.vhd          # Testbench files
│   └── vectors/       # Golden vector files (generated by scripts/gen_vectors.py)
├── scripts/
│   └── gen_vectors.py # Python golden-vector generator
├── sim/
│   └── *.tcl          # xsim run scripts
└── synth/
    └── *.tcl          # Synthesis-only scripts (for utilization estimation)
```

*[ASSUMPTION — naming convention: snake_case for file names, entity names, and
signal names; ALL_CAPS for constants and generics. Adjust if the owner specifies
a different convention.]*

---

## Context — Carry Forward

*(Every decision made during the setup interview. Survives across sessions.)*

| Decision | Value |
|---|---|
| Base-case pixel width | 8-bit grayscale |
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
| Naming conventions | [ASSUMPTION] snake_case signals/entities, ALL_CAPS generics/constants |
| Baseline behavioral source | multica-ai/andrej-karpathy-skills CLAUDE.md (fetched 2026-06-29) |
