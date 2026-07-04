# How This Project Was Prompted

A summary of the prompting approach used to develop this VHDL 2D AXI4-Stream video convolution project with Claude, from blank repo to 324-configuration passing testbench. This is not a verbatim transcript — it describes the *types* of prompts used at each stage and highlights the specific phrasings that were most important for getting good results.

---

## Phase 0 — Project Setup

The very first prompt was the longest and most carefully written one in the entire project. Rather than asking Claude to generate code immediately, it established the full project contract upfront and instructed Claude to **interview the user first** before writing anything.

Key elements that made this work:

**Locked decisions stated explicitly** — the prompt listed non-negotiables that Claude was told never to deviate from:
```
- Toolchain: Xilinx Vivado, simulation via xsim only.
- Deliverable scope: SIMULATION ONLY. [...] Bitstream generation [...] explicitly OUT OF SCOPE
- I/O contract (fixed, never revise): one AXI4-Stream video input → M x N AXI4-Stream video outputs
```

**Out-of-scope stated explicitly** — rather than just saying what to do, the prompt said what *not* to do, preventing Claude from gold-plating:
```
Only make changes directly requested. Do not add features, generics, or
configuration axes beyond what was discussed.
```

**Optimization criteria requirement** — a cross-cutting rule that forced Claude to justify every design decision:
```
whenever more than one valid way exists to write a piece of VHDL [...] you MUST explicitly
state which criteria you are optimizing for [...] BEFORE presenting or picking an approach.
```

**Known failure mode seeded upfront** — a previous design had accidentally used LUTs instead of BRAM for row storage. Documenting this in the initial prompt meant the research phase actively investigated it rather than repeating the mistake:
```
Phase 1 research MUST include a written comparison of LUT-based vs. BRAM-based
row/pixel storage [...] covering at minimum: resource usage, why a synthesis tool
might infer LUT-based storage instead of BRAM [...]
```

**Context-load verification rule** — a simple naming convention ("Ryan" as first and last word of every message) that served as a persistent signal that the project memory file was still loaded across sessions:
```
say "Ryan" as the very first word and the very last word of every chat message you
send me, for the rest of this project.
```

**Phase gating** — Claude was told to complete each phase and show a concrete result before advancing:
```
Project phases, in order, not to be reordered or skipped: (1) Research [...] (2) Base case
[...] (3) Self-checking testbench [...] (4) Generalize [...]
```

After this initial prompt, the setup interview was handled with short multiple-choice answers:
```
1) a
2) d
3) what is usually the default for this?
```
```
input data width could be other data widths as well, don't just have to be 8/16/24
```

---

## Phase 1 — Research & Architecture

Once the project memory file (`CLAUDE.md`) was written, phases were started with simple one-liners:
```
ok let's begin with phase 1
```

Workflow preferences were stated as they came up, and Claude was instructed to save them permanently:
```
i will create a file in vivado, and then copy and paste your generated files into vivado
```

Synthesis results were pasted back verbatim from the Vivado Tcl console for Claude to interpret:
```
report_utilization -hierarchical
[... full Vivado output ...]
```

Approval to proceed was given with a single word:
```
approved
```

---

## Phase 2 — Base RTL & First Simulation

Simulation logs were pasted back when things failed, letting Claude diagnose the error:
```
launch_simulation
[... Vivado compilation errors ...]
ERROR: [VRFC 10-1449] this construct is only supported in VHDL 1076-2008
```

Simulation results (mismatches) were pasted in full:
```
source conv2d_tb.tcl
[...]
Error: MISMATCH at output index 1
Error: MISMATCH at output index 2
[... 40 more mismatches ...]
```

Quick clarifications were short:
```
how to set it to 3000ns
```

External reference material was uploaded directly for analysis:
```
@"Gemini_AI_code_gen.zip" this is code from my supervisor that he said was synthesisable
but i would like you to study it and see if there are any good parts that we can take from this
```

Scope was enforced by questioning when Claude overstepped:
```
why are we adding coefficient multiplication
```

Project state was saved for resumption across sessions:
```
ok we will proceed with this tomorrow, give me a prompt that i can immediately put into
tomorrow's session with no context so that it will proceed appropriately
```

---

## Phase 3 — Self-Checking Testbench

Sessions were resumed with a structured cold-start prompt:
```
You are continuing a VHDL project. Read /home/user/dso-2d-convolution-1/CLAUDE.md in full
before doing anything else — it contains all project rules, constraints, status, and the
Ryan naming rule you must follow.

After reading it, confirm you have read it by summarising: current phase status, what
Phase 4 requires, and what is explicitly out of scope. Then wait for instructions.
```

Once running, simulation logs with PASS results were the handoff:
```
run 3000 ns
Note: CFG2 PASS (3x3 REPLICATE): 192 outputs checked.
Note: CFG3 PASS (3x3 TOROIDAL, OOB falls back to zero): 192 outputs checked.
Note: CFG1 PASS (3x3 ZERO, back-pressure): 192 outputs checked.
```

Expansion was requested incrementally after confirming the base case worked:
```
can we create more test benches to fully test everything
```
```
like all possible configurations, can try changing the following:
* input data width
* m x n window size
* zero/replicate/toroidal
* flush
```

---

## Phase 4 — Generalisation & Debugging

### Clarifying behavioural definitions

The most important debugging step in the project was stopping to align on the definition of "toroidal" before touching any RTL. This was done by requesting an explanation of the planned RTL approach first, then steering it through targeted questions:

```
explain to me your RTL plan before continuing
```
```
for an 8x8 grid, what is to the left of pixel 16
```
```
pixel 23 has not entered yet — that's in the future, which is why we will output
pixel 15 instead
```
```
we will use the causal approximation
```

This kind of step-by-step conceptual alignment (rather than just asking for an implementation) was essential for getting the TOROIDAL behaviour correct.

### Pasting failure logs for diagnosis

When tests failed, the simulation log was pasted with a direct instruction:
```
Error: CFG40 MISMATCH at output 210
[...]
Failure: CFG40 FAIL (8b 3x3 ZERO FLUSH=on 16x4frame): 3 mismatches in 288 outputs.

everything else passed

identify the issue
```

### Uploading reference HTML for specification

When the TOROIDAL definition needed to be pinned precisely, a reference HTML visualiser (generated by a separate tool) was uploaded as a specification document:
```
@"sim_trace.zip" this html file is my new simulation file that i generated with codex
with more test cases and the toroidal behaviour that i want — can you study it and see
the difference between this and our original directory
```

Followed by a single implementation instruction:
```
i would like the vhdl code to use the toroidal definition mentioned in the html file,
and i want you to audit to make sure the zero and replication and flush logic is correct
too, once that is done, i want you to expand the testbench to be able to test the 300+
configurations mentioned in the html file
```

### Understanding flush semantics

Rather than reading code, the concept was confirmed by restating it in plain language and asking for confirmation:
```
so this flush thing — is it basically saying: each valid output window is outputted per
real input pixel, but at the end of a frame, if flush is turned on, we will input dummy
input pixels so as to make sure this frame is completed first before we proceed to the
next frame
```
```
yes correct
```

---

## HTML Visualiser

The visualiser went through several iterations. The key prompt that broke the cycle of partial fixes was replacing incremental patch requests with a full replan:

```
how about we just replan this html file because there seems to be a lot of issues with it

objectives:
* i want to be able to see what happens at each clock cycle
* i want to be able to toggle between the different tests
* i want to know what is the input data and the output data at any given clock cycle
* even if the clock cycle is currently setting up or doing something else i want to be
  able to visualise this

ask me questions if you are unsure of what to do
```

Layout requirements were stated as user needs, not technical specs:
```
make sure the html file shows everything properly within the page, i had to zoom in to
see the whole screen
```

Correctness was confirmed by cross-referencing with the simulation:
```
i want you to audit and make sure that the output of what i am checking against is
exactly the same as the simulation
```
```
is it possible to make the html file rely on the same files used in the simulation so i
know they are definitely exactly the same
```

---

## Documentation & Cleanup

Audits were requested with explicit criteria:
```
now audit the whole directory ensuring the following:
1) readme shows explicitly how the RTL works and explains design decisions
2) readme explains testbenches, why these values were chosen, and what was tested
3) the simulation shows exactly what happens within the RTL
4) remove unnecessary files and ensure vector test files are updated
```

Tone in the README was corrected with a direct example:
```
in readme it says: "Your intuition about alternating read/write pointers is exactly right."

i don't want this kind of conversation in the readme — the readme should only be about
the information
```

Redundant files were identified by asking what they did, then deciding:
```
whats gen_vectors.py and visualize.py for
```
```
wait if by running gen_tb, it will already generate the html file, then i don't need
visualize.py, so just delete it
```

Design questions were raised as concise questions rather than change requests, leaving the decision to discussion:
```
m_tready : in std_logic_vector(KERN_ROWS * KERN_COLS - 1 downto 0); should be only 1 bit?
```

---

## Post-Phase 4 — Visualiser Iteration and Debugging

### Updating synthesis numbers

When new Vivado synthesis results were available, three screenshots were uploaded directly
and the instruction was as terse as possible:
```
update these as utilisation
```
Claude extracted the numbers from the screenshots and updated CLAUDE.md's synthesis block.
No text description was needed — images of the Vivado report were sufficient.

### Fixing a performance regression (42 MB HTML file)

After the stress scenarios were added, the visualiser stopped loading. The diagnosis came
from a short observation rather than an error message:
```
the visualization does not show the stress tests
```
The root cause was that embedding full cycle-by-cycle traces for all 324 configs produced a
42 MB file that caused the browser to hang before the JavaScript dropdown population
finished. Three options were presented; the user picked one by number:
```
option 3
```
(Delta compression — short-key dicts omitting null fields, BRAM stored as write events
rather than full snapshots, with a lazy checkpoint cache in JS.) This reduced the file from
42 MB to 8 MB while restoring the full cycle view for all 324 configs.

### Clarifying scope of the stress scenario overlay

After seeing the stress scenario overlay, the user identified two concrete problems:
```
1) theres no way to go back to main screen
2) i don't know what im looking at, can you use a design skill to make the screen more
   clear and understandable what im looking at
```
The fix was:
- Moving the back button inside the overlay (the sidebar button was unreachable behind
  the overlay at z-index:10)
- Adding an SVG digital waveform replacing block characters
- Adding plain-language explanations of what the scenario tests

### Asking for a third signal row

Rather than requesting a specific implementation, the user described what they didn't
understand and asked a conceptual question:
```
i don't understand what the output is supposed to be? like is it s_tvalid HIGH and
m_tready HIGH then there should be output?
```
This led to adding a third waveform row — "data flows" (blue filled bars) showing
`s_tvalid AND m_tready` — and rewriting the explanation panels to make the handshake
rule explicit.

### Asking how verification actually works

Two sharp questions about correctness came as plain natural language:
```
how do we check if the clock cycle actually doesn't send anything, in those clock cycles
where either s_tvalid is low or m_tready is low, since the output file is the same both
when we vary s_tvalid and m_tready and when we don't

also, if s_tvalid is low in this clock cycle, then should it not send data in this clock
cycle or the next clock cycle
```
These were answered by reading the generated VHDL checker process (`wait until rising_edge(clk) and mvalid='1' and mready='1'`) and explaining exactly how the handshake gates the comparison, including the blind spot and why it's acceptable.

### Directory audit

The audit was requested with a numbered list of concrete targets rather than a vague
"check everything":
```
i want you to audit the whole directory now

1) update the prompts.md with the new prompts i used
2) update README for information especially where we change e.g. testbench
3) update README to show what each file in this directory is for like gen_tb.py
4) audit any other markdown files and just make sure that it accurately depicts what
   is actually in this directory
```

---

## Patterns That Worked Well

| Pattern | Why it worked |
|---|---|
| Long, detailed initial prompt with locked decisions | Prevented Claude from gold-plating or drifting in later sessions |
| Pasting raw Vivado output back verbatim | Gave Claude the exact error context without any translation loss |
| Asking "explain your plan before continuing" | Caught misaligned assumptions before they were baked into RTL |
| Cold-start prompt that forces CLAUDE.md re-read | Maintained continuity across context resets |
| Uploading a reference file as a specification | More precise than describing behaviour in words |
| Asking "what does X do?" before deciding to delete it | Avoided deleting files that turned out to still be needed |
| Full replan prompt when incremental fixes stalled | Broke out of local optima in the UI iteration |
| Single-word approvals ("approved", "yes correct") | Kept pace high after decisions were already made |
| Uploading screenshots for data entry | "update these as utilisation" + three screenshots was faster and less error-prone than transcribing numbers |
| Describing what you don't understand, not what to fix | "I don't understand what the output is supposed to be" led to a better solution than "add a third row" would have |
| Numbered list for audit requests | Gave Claude a checklist to tick off rather than a vague directive |
| Presenting options and letting the user choose | "option 3" kept the user in control of the size/fidelity trade-off without requiring them to understand the implementation |
