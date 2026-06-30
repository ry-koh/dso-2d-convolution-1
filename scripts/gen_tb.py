#!/usr/bin/env python3
"""
Generate the exhaustive Phase 4 + extended testbench and all golden vector files.

Run from repo root:
    python scripts/gen_tb.py

Produces:
    tb/conv2d_tb.vhd              -- VHDL testbench (machine-generated; do not hand-edit)
    tb/vectors/c??_input.txt      -- stimulus pixel values (one per config)
    tb/vectors/c??_expected.txt   -- expected tap values  (one per config)

Configuration matrix (58 configurations total):

  Original 36 (Phase 4, cfg01-cfg36):
    DATA_WIDTH : 8, 16
    Kernel     : 3x3, 5x5, 3x5
    EDGE_MODE  : ZERO, REPLICATE, TOROIDAL
    FLUSH      : false, true
    Frame      : 8x8, 3 frames

  Extended 22 (cfg37-cfg58):
    Group A (cfg37-41): Non-square wide-short frame LW=16, FH=4
      3x3 ZERO/REPLICATE/TOROIDAL FLUSH=off, 3x3 ZERO FLUSH=on, 5x5 ZERO FLUSH=on
    Group B (cfg42-44): Non-square tall-narrow frame LW=4, FH=16
      3x3 ZERO/REPLICATE/TOROIDAL FLUSH=off
    Group C (cfg45-49): Degenerate frame = kernel size (every pixel is a border)
      3x3 ZERO/REPLICATE FLUSH=off, 3x3 ZERO FLUSH=on, 5x5 ZERO/REPLICATE FLUSH=off
    Group D (cfg50-51): Single-row frame FH=1 with FLUSH=on
      3x3 ZERO, 5x5 ZERO
    Group E (cfg52-54): 24-bit data width
      24b 3x3 ZERO FLUSH=off, 24b 3x3 REPLICATE FLUSH=off, 24b 5x5 ZERO FLUSH=on
    Group F (cfg55-58): 7x7 kernel
      ZERO/REPLICATE/TOROIDAL FLUSH=off, ZERO FLUSH=on

Back-pressure scenarios:
  cfg01: m_tready(0) deasserted 10 cycles post-reset (Phase 3 regression, stall/recovery)
  cfg55: m_tready(NUM_TAPS-1) deasserted 10 cycles post-reset (last tap port back-pressure)
  cfg40: m_tready(0) deasserted 15 cycles during flush phase (BP while flush FSM active)

Notes:
  - TOROIDAL expected vectors use causal wrap model (no far-edge wrap, falls back to zero).
  - For >8-bit pixels, scale = 2^(DW-8)+1 ensures all bytes non-zero.
  - Recommended simulation run time: 8000 ns.
"""

import os
import itertools

# -------------------------------------------------------------------------
# Frame + tap helper functions
# -------------------------------------------------------------------------

def make_frame(n, fh, lw, dw):
    """Return flat list of fh*lw pixel values for frame n, DATA_WIDTH dw."""
    scale = (1 << (dw - 8)) + 1 if dw > 8 else 1
    pixels = []
    for r in range(fh):
        for c in range(lw):
            if n == 0:
                raw = (r * lw + c) % 256
            elif n == 1:
                raw = 128
            else:
                raw = 0xAA if (r + c) % 2 == 0 else 0x55
            pixels.append((raw * scale) & ((1 << dw) - 1))
    return pixels


def get_pixel(frame, r, c, fh, lw, mode):
    """Pixel at (r,c) with edge handling (ZERO and REPLICATE only)."""
    if 0 <= r < fh and 0 <= c < lw:
        return frame[r * lw + c]
    if mode == "REPLICATE":
        return frame[max(0, min(r, fh-1)) * lw + max(0, min(c, lw-1))]
    return 0   # ZERO


def compute_taps(frame, row, col, kr, kc, fh, lw, mode):
    taps = []
    for r in range(kr):
        for c in range(kc):
            taps.append(get_pixel(frame, row-(kr-1-r), col-c, fh, lw, mode))
    return taps


def compute_taps_toroidal(push_history, kr, kc, lw):
    """Causal TOROIDAL taps from push history.

    Tap (r,c) = pixel pushed (kr-1-r)*lw + c steps before current push.
    Returns 0 if history is too short (start of stream).
    """
    taps = []
    n = len(push_history)
    for r in range(kr):
        for c in range(kc):
            offset = (kr - 1 - r) * lw + c
            idx = n - 1 - offset
            taps.append(push_history[idx] if idx >= 0 else 0)
    return taps


def gen_vectors(cfg, prefix, out_dir):
    """Write prefix_input.txt and prefix_expected.txt for cfg."""
    dw, kr, kc  = cfg['data_width'], cfg['kern_rows'], cfg['kern_cols']
    lw, fh, nf  = cfg['line_width'], cfg['frame_height'], cfg['num_frames']
    mode        = cfg['edge_mode']
    flush       = cfg['flush']

    all_pixels   = []
    all_taps     = []
    push_history = []

    for fn in range(nf):
        frame = make_frame(fn, fh, lw, dw)
        all_pixels.extend(frame)

        for row in range(fh):
            for col in range(lw):
                if mode == "TOROIDAL":
                    push_history.append(frame[row * lw + col])
                    all_taps.append(compute_taps_toroidal(push_history, kr, kc, lw))
                else:
                    all_taps.append(compute_taps(frame, row, col, kr, kc, fh, lw, mode))

        if flush and kr > 1:
            for flush_idx in range(1, kr):
                vrow = fh - 1 + flush_idx
                for col in range(lw):
                    if mode == "TOROIDAL":
                        push_history.append(0)
                        all_taps.append(compute_taps_toroidal(push_history, kr, kc, lw))
                    else:
                        taps = []
                        for r in range(kr):
                            for c in range(kc):
                                src_row = vrow - (kr - 1 - r)
                                src_col = col - c
                                if src_row >= fh:
                                    taps.append(0)
                                else:
                                    taps.append(get_pixel(frame, src_row, src_col, fh, lw, mode))
                        all_taps.append(taps)

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f'{prefix}input.txt'), 'w') as f:
        for p in all_pixels:
            f.write(f'{p}\n')
    with open(os.path.join(out_dir, f'{prefix}expected.txt'), 'w') as f:
        for taps in all_taps:
            f.write(' '.join(str(t) for t in taps) + '\n')
    return len(all_pixels), len(all_taps)


# -------------------------------------------------------------------------
# Configuration matrix
# -------------------------------------------------------------------------

# Original 36 (Phase 4) — 8x8 frame, 3 frames
DATA_WIDTHS  = [8, 16]
KERNEL_SIZES = [(3, 3), (5, 5), (3, 5)]
EDGE_MODES   = ["ZERO", "REPLICATE", "TOROIDAL"]
FLUSH_VALUES = [False, True]

CONFIGS = []
for dw, (kr, kc), mode, flush in itertools.product(
        DATA_WIDTHS, KERNEL_SIZES, EDGE_MODES, FLUSH_VALUES):
    CONFIGS.append({
        'data_width':   dw,
        'kern_rows':    kr,
        'kern_cols':    kc,
        'edge_mode':    mode,
        'flush':        flush,
        'line_width':   8,
        'frame_height': 8,
        'num_frames':   3,
    })

# Group A: Wide-short frame (LW=16, FH=4) — stresses col-boundary and row-sparse cases
# cfg37-41
for mode in ["ZERO", "REPLICATE", "TOROIDAL"]:
    CONFIGS.append({'data_width': 8, 'kern_rows': 3, 'kern_cols': 3, 'edge_mode': mode,
                    'flush': False, 'line_width': 16, 'frame_height': 4, 'num_frames': 3})
# cfg40: 3x3 ZERO FLUSH=on wide-short (back-pressure during flush applied here)
CONFIGS.append({'data_width': 8, 'kern_rows': 3, 'kern_cols': 3, 'edge_mode': "ZERO",
                'flush': True,  'line_width': 16, 'frame_height': 4, 'num_frames': 3})
# cfg41: 5x5 FLUSH=on wide-short (FH=4 < KERN_ROWS=5: no real outputs, all outputs are flush)
CONFIGS.append({'data_width': 8, 'kern_rows': 5, 'kern_cols': 5, 'edge_mode': "ZERO",
                'flush': True,  'line_width': 16, 'frame_height': 4, 'num_frames': 3})

# Group B: Tall-narrow frame (LW=4, FH=16) — stresses row-depth and short-row TLAST
# cfg42-44
for mode in ["ZERO", "REPLICATE", "TOROIDAL"]:
    CONFIGS.append({'data_width': 8, 'kern_rows': 3, 'kern_cols': 3, 'edge_mode': mode,
                    'flush': False, 'line_width': 4, 'frame_height': 16, 'num_frames': 3})

# Group C: Degenerate frame = kernel size (every pixel is a border pixel)
# cfg45: 3x3 frame, 3x3 ZERO FLUSH=off — only last pixel has fully valid window
CONFIGS.append({'data_width': 8, 'kern_rows': 3, 'kern_cols': 3, 'edge_mode': "ZERO",
                'flush': False, 'line_width': 3, 'frame_height': 3, 'num_frames': 3})
# cfg46: 3x3 frame, 3x3 REPLICATE — all 9 outputs have REPLICATE-padded taps
CONFIGS.append({'data_width': 8, 'kern_rows': 3, 'kern_cols': 3, 'edge_mode': "REPLICATE",
                'flush': False, 'line_width': 3, 'frame_height': 3, 'num_frames': 3})
# cfg47: 3x3 frame, 3x3 ZERO FLUSH=on — flush adds 2 extra rows to tiny frame
CONFIGS.append({'data_width': 8, 'kern_rows': 3, 'kern_cols': 3, 'edge_mode': "ZERO",
                'flush': True,  'line_width': 3, 'frame_height': 3, 'num_frames': 3})
# cfg48: 5x5 frame, 5x5 ZERO FLUSH=off
CONFIGS.append({'data_width': 8, 'kern_rows': 5, 'kern_cols': 5, 'edge_mode': "ZERO",
                'flush': False, 'line_width': 5, 'frame_height': 5, 'num_frames': 3})
# cfg49: 5x5 frame, 5x5 REPLICATE
CONFIGS.append({'data_width': 8, 'kern_rows': 5, 'kern_cols': 5, 'edge_mode': "REPLICATE",
                'flush': False, 'line_width': 5, 'frame_height': 5, 'num_frames': 3})

# Group D: Single-row frame (FH=1) with FLUSH=on
# cfg50: 3x3, FH=1 — flush injects 2 dummy rows for 1 real row
CONFIGS.append({'data_width': 8, 'kern_rows': 3, 'kern_cols': 3, 'edge_mode': "ZERO",
                'flush': True,  'line_width': 8, 'frame_height': 1, 'num_frames': 3})
# cfg51: 5x5, FH=1 — flush injects 4 dummy rows (4x overhead)
CONFIGS.append({'data_width': 8, 'kern_rows': 5, 'kern_cols': 5, 'edge_mode': "ZERO",
                'flush': True,  'line_width': 8, 'frame_height': 1, 'num_frames': 3})

# Group E: 24-bit data width
# cfg52
CONFIGS.append({'data_width': 24, 'kern_rows': 3, 'kern_cols': 3, 'edge_mode': "ZERO",
                'flush': False, 'line_width': 8, 'frame_height': 8, 'num_frames': 3})
# cfg53
CONFIGS.append({'data_width': 24, 'kern_rows': 3, 'kern_cols': 3, 'edge_mode': "REPLICATE",
                'flush': False, 'line_width': 8, 'frame_height': 8, 'num_frames': 3})
# cfg54
CONFIGS.append({'data_width': 24, 'kern_rows': 5, 'kern_cols': 5, 'edge_mode': "ZERO",
                'flush': True,  'line_width': 8, 'frame_height': 8, 'num_frames': 3})

# Group F: 7x7 kernel
# cfg55: ZERO FLUSH=off — back-pressure on last tap port applied here
CONFIGS.append({'data_width': 8, 'kern_rows': 7, 'kern_cols': 7, 'edge_mode': "ZERO",
                'flush': False, 'line_width': 8, 'frame_height': 8, 'num_frames': 3})
# cfg56
CONFIGS.append({'data_width': 8, 'kern_rows': 7, 'kern_cols': 7, 'edge_mode': "REPLICATE",
                'flush': False, 'line_width': 8, 'frame_height': 8, 'num_frames': 3})
# cfg57
CONFIGS.append({'data_width': 8, 'kern_rows': 7, 'kern_cols': 7, 'edge_mode': "TOROIDAL",
                'flush': False, 'line_width': 8, 'frame_height': 8, 'num_frames': 3})
# cfg58
CONFIGS.append({'data_width': 8, 'kern_rows': 7, 'kern_cols': 7, 'edge_mode': "ZERO",
                'flush': True,  'line_width': 8, 'frame_height': 8, 'num_frames': 3})

N = len(CONFIGS)  # 58


# -------------------------------------------------------------------------
# VHDL generation helpers
# -------------------------------------------------------------------------

def vbool(b):
    return "true" if b else "false"


def cfg_label(cfg):
    kr, kc = cfg['kern_rows'], cfg['kern_cols']
    lw, fh = cfg['line_width'], cfg['frame_height']
    return (f"{cfg['data_width']}b {kr}x{kc} {cfg['edge_mode']}"
            f" FLUSH={'on' if cfg['flush'] else 'off'}"
            f" {lw}x{fh}frame")


def gen_constants(i, cfg, prefix):
    kr, kc, dw = cfg['kern_rows'], cfg['kern_cols'], cfg['data_width']
    lw, fh     = cfg['line_width'], cfg['frame_height']
    p = f"C{i:02d}"
    return f"""\
    constant {p}_DW        : positive := {dw};
    constant {p}_KR        : positive := {kr};
    constant {p}_KC        : positive := {kc};
    constant {p}_LW        : positive := {lw};
    constant {p}_FH        : positive := {fh};
    constant {p}_EDGE_MODE : string   := "{cfg['edge_mode']}";
    constant {p}_FLUSH     : boolean  := {vbool(cfg['flush'])};
    constant {p}_NUM_TAPS  : positive := {kr * kc};
    constant {p}_IN_FILE   : string   := "{prefix}input.txt";
    constant {p}_EXP_FILE  : string   := "{prefix}expected.txt";"""


def gen_signals(i, cfg):
    dw = cfg['data_width']
    nt = cfg['kern_rows'] * cfg['kern_cols']
    p = f"c{i:02d}"
    return f"""\
    signal {p}_rst    : std_logic := '1';
    signal {p}_sdata  : std_logic_vector({dw}-1 downto 0) := (others => '0');
    signal {p}_svalid : std_logic := '0';
    signal {p}_sready : std_logic;
    signal {p}_slast  : std_logic := '0';
    signal {p}_suser  : std_logic := '0';
    signal {p}_mdata  : std_logic_vector({dw}*{nt}-1 downto 0);
    signal {p}_mvalid : std_logic;
    signal {p}_mready : std_logic_vector({nt}-1 downto 0) := (others => '1');
    signal {p}_mlast  : std_logic;
    signal {p}_muser  : std_logic;"""


def gen_instance(i, cfg):
    cp = f"C{i:02d}"
    sp = f"c{i:02d}"
    return f"""\
    u_dut{i:02d} : entity work.conv2d
        generic map (
            DATA_WIDTH   => {cp}_DW,
            KERN_ROWS    => {cp}_KR,
            KERN_COLS    => {cp}_KC,
            LINE_WIDTH   => {cp}_LW,
            FRAME_HEIGHT => {cp}_FH,
            EDGE_MODE    => {cp}_EDGE_MODE,
            FLUSH        => {cp}_FLUSH
        )
        port map (
            clk      => clk,           rst      => {sp}_rst,
            s_tdata  => {sp}_sdata,    s_tvalid => {sp}_svalid,
            s_tready => {sp}_sready,   s_tlast  => {sp}_slast,
            s_tuser  => {sp}_suser,    m_tdata  => {sp}_mdata,
            m_tvalid => {sp}_mvalid,   m_tready => {sp}_mready,
            m_tlast  => {sp}_mlast,    m_tuser  => {sp}_muser
        );"""


def gen_stim(i, cfg):
    cp = f"C{i:02d}"
    sp = f"c{i:02d}"
    lw = cfg['line_width']
    fh = cfg['frame_height']
    return f"""\
    p_stim{i:02d} : process
        file     f   : text;
        variable ln  : line;
        variable pv  : integer;
        variable col : natural range 0 to {lw}-1;
        variable row : natural range 0 to {fh}-1;
    begin
        wait for CLK_PERIOD * 5;
        wait until rising_edge(clk);
        {sp}_rst <= '0';
        file_open(f, {cp}_IN_FILE, read_mode);
        col := 0;  row := 0;
        while not endfile(f) loop
            readline(f, ln);  read(ln, pv);
            {sp}_sdata  <= std_logic_vector(to_unsigned(pv, {cp}_DW));
            {sp}_svalid <= '1';
            if row = 0 and col = 0 then {sp}_suser <= '1'; else {sp}_suser <= '0'; end if;
            if col = {lw}-1 then {sp}_slast <= '1'; else {sp}_slast <= '0'; end if;
            wait until rising_edge(clk) and {sp}_sready = '1';
            if col = {lw}-1 then
                col := 0;
                if row = {fh}-1 then row := 0; else row := row+1; end if;
            else
                col := col+1;
            end if;
        end loop;
        {sp}_svalid <= '0';  {sp}_slast <= '0';  {sp}_suser <= '0';
        file_close(f);
        wait;
    end process p_stim{i:02d};"""


def gen_check(i, cfg, label):
    cp = f"C{i:02d}"
    sp = f"c{i:02d}"
    nt = cfg['kern_rows'] * cfg['kern_cols']
    dw = cfg['data_width']
    return f"""\
    p_check{i:02d} : process
        file     f         : text;
        variable ln        : line;
        variable tv        : integer;
        variable exp_vec   : std_logic_vector({dw}*{nt}-1 downto 0);
        variable out_count : natural := 0;
        variable err_count : natural := 0;
    begin
        wait until {sp}_rst = '0';
        file_open(f, {cp}_EXP_FILE, read_mode);
        while not endfile(f) loop
            wait until rising_edge(clk) and {sp}_mvalid = '1';
            readline(f, ln);
            for tap in 0 to {nt}-1 loop
                read(ln, tv);
                exp_vec((tap+1)*{dw}-1 downto tap*{dw})
                    := std_logic_vector(to_unsigned(tv, {dw}));
            end loop;
            if {sp}_mdata /= exp_vec then
                report "CFG{i:02d} MISMATCH at output " & integer'image(out_count)
                    severity error;
                err_count := err_count+1;
            end if;
            out_count := out_count+1;
        end loop;
        file_close(f);
        if err_count = 0 then
            report "CFG{i:02d} PASS ({label}): " & integer'image(out_count)
                & " outputs checked." severity note;
        else
            report "CFG{i:02d} FAIL ({label}): " & integer'image(err_count)
                & " mismatches in " & integer'image(out_count)
                & " outputs." severity failure;
        end if;
        done_flags({i-1}) <= '1';
        wait;
    end process p_check{i:02d};"""


# -------------------------------------------------------------------------
# Main
# -------------------------------------------------------------------------

def main():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    vec_dir  = os.path.join(repo_root, 'tb', 'vectors')
    tb_path  = os.path.join(repo_root, 'tb', 'conv2d_tb.vhd')

    print(f"Generating vectors for {N} configurations...")
    for i, cfg in enumerate(CONFIGS, start=1):
        prefix = f"c{i:02d}_"
        nin, nout = gen_vectors(cfg, prefix, vec_dir)
        label = cfg_label(cfg)
        print(f"  cfg{i:02d} {label:<55s}  {nin} in / {nout} expected")

    header = f"""\
-- conv2d_tb.vhd -- exhaustive Phase 4 + extended testbench
-- MACHINE-GENERATED by scripts/gen_tb.py -- do not hand-edit.
--
-- Tests {N} configurations in parallel on a shared clock.
-- See gen_tb.py header for full configuration matrix.
--
-- Before simulation: copy all tb/vectors/c??_{{input,expected}}.txt
-- into the xsim working directory.
-- Recommended run time: 8000 ns.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.textio.all;

entity conv2d_tb is
end entity conv2d_tb;

architecture tb of conv2d_tb is

    constant CLK_PERIOD : time := 10 ns;

    signal clk        : std_logic := '0';
    signal done_flags : std_logic_vector({N}-1 downto 0) := (others => '0');
    signal sim_done   : boolean := false;

"""

    const_blocks  = []
    signal_blocks = []
    inst_blocks   = []
    bp_blocks     = []
    stim_blocks   = []
    check_blocks  = []

    for i, cfg in enumerate(CONFIGS, start=1):
        prefix = f"c{i:02d}_"
        label  = cfg_label(cfg)
        const_blocks .append(gen_constants(i, cfg, prefix))
        signal_blocks.append(gen_signals(i, cfg))
        inst_blocks  .append(gen_instance(i, cfg))
        stim_blocks  .append(gen_stim(i, cfg))
        check_blocks .append(gen_check(i, cfg, label))

    # Back-pressure scenario 1: cfg01 — stall m_tready(0) 10 cycles post-reset
    bp_blocks.append("""\
    -- BP scenario 1: cfg01 — m_tready(0) stalled 10 cycles post-reset (Phase 3 regression)
    p_bp01 : process
    begin
        c01_mready(0) <= '0';
        c01_mready(C01_NUM_TAPS-1 downto 1) <= (others => '1');
        wait until c01_rst = '0';
        wait for CLK_PERIOD * 10;
        c01_mready(0) <= '1';
        wait;
    end process p_bp01;""")

    # Back-pressure scenario 2: cfg55 (7x7 ZERO FLUSH=off) — stall last tap port
    bp_blocks.append("""\
    -- BP scenario 2: cfg55 (7x7 ZERO FLUSH=off) — m_tready(NUM_TAPS-1) stalled 10 cycles
    -- Verifies that back-pressure on the LAST tap port stalls the pipeline correctly.
    p_bp55 : process
    begin
        c55_mready(C55_NUM_TAPS-1) <= '0';
        c55_mready(C55_NUM_TAPS-2 downto 0) <= (others => '1');
        wait until c55_rst = '0';
        wait for CLK_PERIOD * 10;
        c55_mready(C55_NUM_TAPS-1) <= '1';
        wait;
    end process p_bp55;""")

    # Back-pressure scenario 3: cfg40 (3x3 ZERO FLUSH=on, LW=16, FH=4) — stall during flush
    # Real pixels: 3 frames * 16*4 = 192 pixels, taking ~192 cycles after reset.
    # Wait 210 cycles post-reset (well into flush territory) then hold BP for 15 cycles.
    bp_blocks.append("""\
    -- BP scenario 3: cfg40 (3x3 ZERO FLUSH=on, LW=16 FH=4) — m_tready(0) stalled mid-flush.
    -- Real stimulus ends ~cycle 197; deassert at cycle 210, hold 15 cycles into flush phase.
    p_bp40 : process
    begin
        wait until c40_rst = '0';
        wait for CLK_PERIOD * 210;
        c40_mready(0) <= '0';
        wait for CLK_PERIOD * 15;
        c40_mready(0) <= '1';
        wait;
    end process p_bp40;""")

    body = "\n\n".join([
        "    -- Per-DUT configuration constants and file names",
        "\n\n".join(const_blocks),
        "    -- Per-DUT AXI signals",
        "\n\n".join(signal_blocks),
    ])

    begin_section = "\n\n".join([
        f"    sim_done <= true when done_flags = (done_flags'range => '1') else false;",
        "    clk <= not clk after CLK_PERIOD / 2 when not sim_done else '0';",
        "    -- DUT instances",
        "\n\n".join(inst_blocks),
        "\n\n".join(bp_blocks),
        "    -- Stimulus processes",
        "\n\n".join(stim_blocks),
        "    -- Checker processes",
        "\n\n".join(check_blocks),
    ])

    vhdl = (
        header
        + body
        + "\n\nbegin\n\n"
        + begin_section
        + "\n\nend architecture tb;\n"
    )

    with open(tb_path, 'w') as f:
        f.write(vhdl)

    print(f"\nWrote {tb_path}  ({N} configs, {sum(1 for b in bp_blocks)} BP scenarios)")
    print(f"Recommended simulation run time: 8000 ns")


if __name__ == '__main__':
    main()
