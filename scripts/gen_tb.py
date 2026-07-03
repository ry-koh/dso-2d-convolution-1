#!/usr/bin/env python3
"""
Generate the exhaustive Phase 4 testbench and all golden vector files.

Run from repo root:
    python scripts/gen_tb.py [--html]

Produces:
    tb/conv2d_tb.vhd              -- VHDL testbench (machine-generated; do not hand-edit)
    tb/vectors/c???_input.txt     -- stimulus pixel values (one per config)
    tb/vectors/c???_expected.txt  -- expected tap values  (one per config)
    tb/visualize.html             -- interactive HTML visualiser (with --html flag)

Configuration matrix (324 configurations):

    Kernel sizes : 3x3, 3x5, 3x7, 5x3, 5x5, 5x7, 7x3, 7x5, 7x7   (9)
    Edge modes   : ZERO, REPLICATE, TOROIDAL                         (3)
    Flush        : false, true                                       (2)
    Frame sizes  : 8x8, 16x4, 4x16, 3x3, 5x5, 8x1  (LW x FH)      (6)
    Data width   : 8-bit (fixed)
    Num frames   : 3

Total: 9 * 3 * 2 * 6 = 324 configurations.

TOROIDAL definition (stream-linear, matching reference RTL):
    TOR_MAX_POS = HALF_R * LINE_WIDTH + HALF_C
    Output is delayed until TOR_MAX_POS future pixels are buffered.
    Tap (tr,tc) = global_stream[center_g + (tr-HALF_R)*LINE_WIDTH + (tc-HALF_C)].
    Negative global indices -> 0 (warmup zeros before frame 0).
    FLUSH=true: taps with tap_y<0 or tap_y>=FH or flat_idx OOB -> 0.
    FLUSH=false: stream continues across frame boundaries.
"""

import os
import json
import itertools
import argparse

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
        return frame[max(0, min(r, fh - 1)) * lw + max(0, min(c, lw - 1))]
    return 0


def compute_taps(frame, out_r, out_c, kr, kc, fh, lw, mode):
    """Centred window: tap[tr][tc] = pixel at (out_r+tr-half_r, out_c+tc-half_c)."""
    half_r = (kr - 1) // 2
    half_c = (kc - 1) // 2
    taps = []
    for tr in range(kr):
        for tc in range(kc):
            src_r = out_r + tr - half_r
            src_c = out_c + tc - half_c
            taps.append(get_pixel(frame, src_r, src_c, fh, lw, mode))
    return taps


def compute_taps_toroidal(global_stream, center_g, kr, kc, lw, fh, flush):
    """
    Stream-linear TOROIDAL tap computation matching the reference RTL.

    global_stream : flat list of all real pixels across all frames (no dummy cols)
    center_g      : global stream index of the output center pixel
    flush         : True -> zero OOB taps; False -> stream continues across frames

    For FLUSH=true:
        center_in_frame = center_g % (fh*lw)
        center_row = center_in_frame // lw
        tap_y = center_row + (tr - half_r)
        tap_flat_in_frame = center_in_frame + tap_offset
        OOB (tap_y<0, tap_y>=fh, tap_flat_in_frame<0, >=fh*lw) -> 0

    For FLUSH=false:
        tap_flat = center_g + tap_offset  (global, crosses frames)
        negative -> 0 (before frame 0 warmup); reads global_stream otherwise.
    """
    half_r = (kr - 1) // 2
    half_c = (kc - 1) // 2
    frame_pixels = lw * fh
    center_frame = center_g // frame_pixels
    center_in_frame = center_g % frame_pixels
    center_row = center_in_frame // lw
    taps = []
    for tr in range(kr):
        for tc in range(kc):
            offset = (tr - half_r) * lw + (tc - half_c)
            if flush:
                tap_y = center_row + (tr - half_r)
                tap_flat_in_frame = center_in_frame + offset
                if (tap_y < 0 or tap_y >= fh
                        or tap_flat_in_frame < 0
                        or tap_flat_in_frame >= frame_pixels):
                    taps.append(0)
                else:
                    g = center_frame * frame_pixels + tap_flat_in_frame
                    taps.append(global_stream[g] if 0 <= g < len(global_stream) else 0)
            else:
                g = center_g + offset
                taps.append(global_stream[g] if 0 <= g < len(global_stream) else 0)
    return taps


def is_stream_direct(kr, kc, fh, flush, mode):
    """True when RTL uses tor_buf path (TOROIDAL or degenerate FH <= HALF_R + !FLUSH)."""
    half_r = (kr - 1) // 2
    return mode == "TOROIDAL" or (fh <= half_r and not flush)


def gen_vectors(cfg, prefix, out_dir):
    """Write prefix_input.txt and prefix_expected.txt for cfg."""
    dw, kr, kc  = cfg['data_width'], cfg['kern_rows'], cfg['kern_cols']
    lw, fh, nf  = cfg['line_width'], cfg['frame_height'], cfg['num_frames']
    mode        = cfg['edge_mode']
    flush       = cfg['flush']

    half_r    = (kr - 1) // 2
    half_c    = (kc - 1) // 2
    eff_width = lw + half_c
    frame_pixels = lw * fh

    frames = [make_frame(fn, fh, lw, dw) for fn in range(nf)]
    all_pixels = []
    all_taps   = []

    for fn in range(nf):
        all_pixels.extend(frames[fn])

    if mode == "TOROIDAL" or is_stream_direct(kr, kc, fh, flush, mode):
        # --- Stream-linear path (tor_buf in RTL) ---
        # Build flat global stream (real pixels only, no dummy column zeros).
        global_stream = []
        for fn in range(nf):
            global_stream.extend(frames[fn])

        # Max center push that fits within the simulation window.
        # delay = HALF_R*EFF_WIDTH + HALF_C + 2 (pipeline depth from center push to output)
        # total_pushes = nf*fh*eff_width + 2  (all real rows + dummy cols + 2 drain cycles)
        delay = half_r * eff_width + half_c + 2
        total_pushes = nf * fh * eff_width + 2
        max_center_push = total_pushes - delay - 1

        if mode == "TOROIDAL":
            if flush:
                # Per-frame: FRAME_PIXELS outputs per frame.
                for fn in range(nf):
                    for n in range(frame_pixels):
                        center_g = fn * frame_pixels + n
                        taps = compute_taps_toroidal(
                            global_stream, center_g, kr, kc, lw, fh, flush=True)
                        all_taps.append(taps)
            else:
                # FLUSH=false: output for every center that fits in simulation window.
                for fn in range(nf):
                    for fr in range(fh):
                        for fc in range(lw):
                            center_push = fn * fh * eff_width + fr * eff_width + fc
                            if center_push > max_center_push:
                                continue
                            center_g = fn * frame_pixels + fr * lw + fc
                            taps = compute_taps_toroidal(
                                global_stream, center_g, kr, kc, lw, fh, flush=False)
                            all_taps.append(taps)

        else:
            # STREAM_DIRECT non-TOROIDAL (FH <= HALF_R, FLUSH=false).
            # RTL uses tor_buf but applies ZERO/REPLICATE per-frame OOB handling.
            for fn in range(nf):
                for fr in range(fh):
                    for fc in range(lw):
                        center_push = fn * fh * eff_width + fr * eff_width + fc
                        if center_push > max_center_push:
                            continue
                        taps = compute_taps(
                            frames[fn], fr, fc,
                            kr, kc, fh, lw, mode)
                        all_taps.append(taps)

    elif not flush:
        # --- Normal FLUSH=false (FH > HALF_R), ZERO or REPLICATE ---
        # Streaming tail: bottom HALF_R rows of frame N fire during frame N+1.
        for fn in range(nf):
            for row in range(fh):
                global_row = fn * fh + row
                for col_eff in range(eff_width):
                    if col_eff >= half_c and global_row >= half_r:
                        out_r_global = global_row - half_r
                        out_fn       = out_r_global // fh
                        out_r        = out_r_global % fh
                        out_c        = col_eff - half_c
                        all_taps.append(
                            compute_taps(frames[out_fn], out_r, out_c,
                                         kr, kc, fh, lw, mode))

    else:
        # --- FLUSH=true, non-TOROIDAL ---
        # Per-frame real outputs + HALF_R flush rows of zeros appended.
        for fn in range(nf):
            frame = frames[fn]
            for row in range(fh):
                for col_eff in range(eff_width):
                    if col_eff >= half_c and row >= half_r:
                        all_taps.append(
                            compute_taps(frame, row - half_r, col_eff - half_c,
                                         kr, kc, fh, lw, mode))
            for flush_row in range(half_r):
                virtual_r = fh + flush_row
                out_r = virtual_r - half_r
                for col_eff in range(eff_width):
                    if col_eff >= half_c and out_r >= 0:
                        all_taps.append(
                            compute_taps(frame, out_r, col_eff - half_c,
                                         kr, kc, fh, lw, mode))

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f'{prefix}input.txt'), 'w') as f:
        for p in all_pixels:
            f.write(f'{p}\n')
    with open(os.path.join(out_dir, f'{prefix}expected.txt'), 'w') as f:
        for taps in all_taps:
            f.write(' '.join(str(t) for t in taps) + '\n')
    return len(all_pixels), len(all_taps)


# -------------------------------------------------------------------------
# Configuration matrix: 324 configurations
# -------------------------------------------------------------------------

KERNEL_SIZES = [(3, 3), (3, 5), (3, 7), (5, 3), (5, 5), (5, 7), (7, 3), (7, 5), (7, 7)]
EDGE_MODES   = ["ZERO", "REPLICATE", "TOROIDAL"]
FLUSH_VALUES = [False, True]
FRAME_SIZES  = [(8, 8), (16, 4), (4, 16), (3, 3), (5, 5), (8, 1)]  # (LW, FH)
DATA_WIDTH   = 8
NUM_FRAMES   = 3

CONFIGS = []
for (kr, kc), mode, flush, (lw, fh) in itertools.product(
        KERNEL_SIZES, EDGE_MODES, FLUSH_VALUES, FRAME_SIZES):
    CONFIGS.append({
        'data_width':   DATA_WIDTH,
        'kern_rows':    kr,
        'kern_cols':    kc,
        'edge_mode':    mode,
        'flush':        flush,
        'line_width':   lw,
        'frame_height': fh,
        'num_frames':   NUM_FRAMES,
    })

N = len(CONFIGS)  # 324


# -------------------------------------------------------------------------
# Back-pressure scenario indices (0-based into CONFIGS)
# -------------------------------------------------------------------------

def _find(kr, kc, mode, flush, lw, fh):
    for i, c in enumerate(CONFIGS):
        if (c['kern_rows'] == kr and c['kern_cols'] == kc
                and c['edge_mode'] == mode and c['flush'] == flush
                and c['line_width'] == lw and c['frame_height'] == fh):
            return i
    return None

_BP_POST_RESET   = _find(3, 3, "ZERO",      False, 8, 8)   # 3x3 ZERO FLUSH=off post-reset BP tap=0
_BP_MID_FLUSH    = _find(3, 5, "ZERO",      False, 3, 3)   # 3x5 ZERO FLUSH=off 3x3 mid-sim BP tap=0
_BP_LARGE_KERNEL = _find(3, 5, "REPLICATE", True,  8, 8)   # 3x5 REPLICATE FLUSH=on post-reset BP tap=last

BP_INFO = {}
for idx, info in [
    (_BP_POST_RESET,   {'type': 'post_reset', 'stall_cycles': 10,
                        'note': 'Back-pressure: m_tready=0 for 10 cycles post-reset'}),
    (_BP_MID_FLUSH,    {'type': 'mid_sim', 'start_cycle': 210, 'stall_cycles': 15,
                        'note': 'Back-pressure: m_tready=0 for 15 cycles mid-sim'}),
    (_BP_LARGE_KERNEL, {'type': 'post_reset', 'stall_cycles': 10,
                        'note': 'Back-pressure: m_tready=0 for 10 cycles post-reset'}),
]:
    if idx is not None:
        BP_INFO[idx] = info


# -------------------------------------------------------------------------
# Stress scenarios: 30 configs exercising varied s_tvalid and m_tready
# timing patterns.  sv_h/sv_l = s_tvalid HIGH/LOW counts (0,0 = always high).
# mr_h/mr_l = m_tready HIGH/LOW counts (0,0 = always high).
# Base config identified by (kr, kc, mode, flush, lw, fh); vectors reused.
# -------------------------------------------------------------------------

# Each entry: (kr, kc, mode, flush, lw, fh, sv_h, sv_l, mr_h, mr_l, desc)
STRESS_SCENARIOS = [
    # ---- s_tvalid pattern only (m_tready always high) ------------------
    (3, 3, "ZERO",       False, 8, 8,  1, 1, 0, 0, "svalid 1H1L"),
    (3, 3, "ZERO",       False, 8, 8,  1, 2, 0, 0, "svalid 1H2L"),
    (3, 3, "ZERO",       False, 8, 8,  1, 4, 0, 0, "svalid 1H4L"),
    (3, 3, "ZERO",       False, 8, 8,  1, 7, 0, 0, "svalid 1H7L"),
    (3, 3, "REPLICATE",  False, 8, 8,  2, 1, 0, 0, "svalid 2H1L"),
    (5, 5, "ZERO",       False, 8, 8,  3, 2, 0, 0, "svalid 3H2L"),
    (5, 5, "REPLICATE",  False, 8, 8,  1, 3, 0, 0, "svalid 1H3L"),
    (7, 7, "ZERO",       False, 8, 8,  4, 1, 0, 0, "svalid 4H1L"),
    (7, 7, "REPLICATE",  False, 8, 8,  1, 6, 0, 0, "svalid 1H6L"),
    (3, 3, "TOROIDAL",   False, 8, 8,  2, 3, 0, 0, "svalid 2H3L"),
    (3, 3, "ZERO",        True, 8, 8,  1, 2, 0, 0, "svalid 1H2L flush"),
    (5, 3, "REPLICATE",   True, 8, 8,  3, 1, 0, 0, "svalid 3H1L flush"),
    (3, 5, "TOROIDAL",    True, 8, 8,  2, 2, 0, 0, "svalid 2H2L tor+flush"),
    # ---- m_tready pattern only (s_tvalid always high) ------------------
    (3, 3, "ZERO",       False, 8, 8,  0, 0, 2, 3, "mready 2H3L"),
    (3, 3, "REPLICATE",  False, 8, 8,  0, 0, 1, 4, "mready 1H4L"),
    (5, 5, "ZERO",       False, 8, 8,  0, 0, 3, 2, "mready 3H2L"),
    (7, 7, "ZERO",       False, 8, 8,  0, 0, 1, 1, "mready 1H1L"),
    (3, 3, "TOROIDAL",    True, 8, 8,  0, 0, 4, 1, "mready 4H1L tor+flush"),
    (5, 7, "REPLICATE",  False, 8, 8,  0, 0, 1, 3, "mready 1H3L"),
    (7, 3, "ZERO",        True, 8, 8,  0, 0, 2, 5, "mready 2H5L flush"),
    # ---- combined: both s_tvalid and m_tready vary ---------------------
    (3, 3, "ZERO",       False, 8, 8,  2, 3, 3, 2, "svalid 2H3L mready 3H2L"),
    (3, 5, "REPLICATE",  False, 8, 8,  1, 2, 2, 1, "svalid 1H2L mready 2H1L"),
    (5, 5, "ZERO",       False, 8, 8,  3, 1, 1, 3, "svalid 3H1L mready 1H3L"),
    (7, 3, "REPLICATE",  False, 8, 8,  1, 4, 4, 1, "svalid 1H4L mready 4H1L"),
    (3, 3, "TOROIDAL",   False, 8, 8,  2, 1, 1, 2, "svalid 2H1L mready 1H2L"),
    (5, 5, "REPLICATE",   True, 8, 8,  1, 3, 3, 1, "svalid 1H3L mready 3H1L"),
    (3, 7, "ZERO",       False, 8, 8,  4, 3, 2, 5, "svalid 4H3L mready 2H5L"),
    (7, 7, "REPLICATE",   True, 8, 8,  1, 1, 1, 1, "svalid 1H1L mready 1H1L"),
    (7, 7, "ZERO",       False, 8, 8,  2, 1, 1, 2, "svalid 2H1L mready 1H2L"),
    (3, 5, "ZERO",        True, 8, 8,  1, 7, 5, 2, "svalid 1H7L mready 5H2L"),
]

NS = len(STRESS_SCENARIOS)  # 30


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
    p = f"C{i:03d}"
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
    p = f"c{i:03d}"
    return f"""\
    signal {p}_rst    : std_logic := '1';
    signal {p}_sdata  : std_logic_vector({dw}-1 downto 0) := (others => '0');
    signal {p}_svalid : std_logic := '0';
    signal {p}_sready : std_logic;
    signal {p}_slast  : std_logic := '0';
    signal {p}_suser  : std_logic := '0';
    signal {p}_mdata  : std_logic_vector({dw}*{nt}-1 downto 0);
    signal {p}_mvalid : std_logic;
    signal {p}_mready : std_logic := '1';
    signal {p}_mlast  : std_logic;
    signal {p}_muser  : std_logic;"""


def gen_instance(i, cfg):
    cp = f"C{i:03d}"
    sp = f"c{i:03d}"
    return f"""\
    u_dut{i:03d} : entity work.conv2d
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
    cp = f"C{i:03d}"
    sp = f"c{i:03d}"
    lw = cfg['line_width']
    fh = cfg['frame_height']
    return f"""\
    p_stim{i:03d} : process
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
    end process p_stim{i:03d};"""


def gen_check(i, cfg, label):
    cp = f"C{i:03d}"
    sp = f"c{i:03d}"
    nt = cfg['kern_rows'] * cfg['kern_cols']
    dw = cfg['data_width']
    return f"""\
    p_check{i:03d} : process
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
            wait until rising_edge(clk) and {sp}_mvalid = '1' and {sp}_mready = '1';
            readline(f, ln);
            for tap in 0 to {nt}-1 loop
                read(ln, tv);
                exp_vec((tap+1)*{dw}-1 downto tap*{dw})
                    := std_logic_vector(to_unsigned(tv, {dw}));
            end loop;
            if {sp}_mdata /= exp_vec then
                report "CFG{i:03d} MISMATCH at output " & integer'image(out_count)
                    severity error;
                err_count := err_count+1;
            end if;
            out_count := out_count+1;
        end loop;
        file_close(f);
        if err_count = 0 then
            report "CFG{i:03d} PASS ({label}): " & integer'image(out_count)
                & " outputs checked." severity note;
        else
            report "CFG{i:03d} FAIL ({label}): " & integer'image(err_count)
                & " mismatches in " & integer'image(out_count)
                & " outputs." severity failure;
        end if;
        done_flags({i-1}) <= '1';
        wait;
    end process p_check{i:03d};"""


# -------------------------------------------------------------------------
# Stress scenario VHDL generation
# -------------------------------------------------------------------------

def _stress_base_idx(kr, kc, mode, flush, lw, fh):
    """Return 1-based config index for the stress scenario's base config."""
    idx0 = _find(kr, kc, mode, flush, lw, fh)
    assert idx0 is not None, f"Stress base config not found: {kr}x{kc} {mode} flush={flush} {lw}x{fh}"
    return idx0 + 1  # 1-based


def gen_stress_signals(j, sc):
    """Declare AXI signals for stress scenario SSjjj (j is 1-based)."""
    kr, kc, mode, flush, lw, fh, sv_h, sv_l, mr_h, mr_l, desc = sc
    dw = 8
    nt = kr * kc
    sp = f"ss{j:03d}"
    # Default mready: '1' unless a pattern is active
    mr_init = "'1'" if (mr_h == 0 and mr_l == 0) else "'0'"
    return f"""\
    signal {sp}_rst    : std_logic := '1';
    signal {sp}_sdata  : std_logic_vector({dw}-1 downto 0) := (others => '0');
    signal {sp}_svalid : std_logic := '0';
    signal {sp}_sready : std_logic;
    signal {sp}_slast  : std_logic := '0';
    signal {sp}_suser  : std_logic := '0';
    signal {sp}_mdata  : std_logic_vector({dw}*{nt}-1 downto 0);
    signal {sp}_mvalid : std_logic;
    signal {sp}_mready : std_logic := {mr_init};
    signal {sp}_mlast  : std_logic;
    signal {sp}_muser  : std_logic;"""


def gen_stress_instance(j, sc):
    """DUT instance for stress scenario SSjjj."""
    kr, kc, mode, flush, lw, fh, sv_h, sv_l, mr_h, mr_l, desc = sc
    sp = f"ss{j:03d}"
    return f"""\
    u_ss{j:03d} : entity work.conv2d
        generic map (
            DATA_WIDTH   => 8,
            KERN_ROWS    => {kr},
            KERN_COLS    => {kc},
            LINE_WIDTH   => {lw},
            FRAME_HEIGHT => {fh},
            EDGE_MODE    => "{mode}",
            FLUSH        => {vbool(flush)}
        )
        port map (
            clk      => clk,             rst      => {sp}_rst,
            s_tdata  => {sp}_sdata,      s_tvalid => {sp}_svalid,
            s_tready => {sp}_sready,     s_tlast  => {sp}_slast,
            s_tuser  => {sp}_suser,      m_tdata  => {sp}_mdata,
            m_tvalid => {sp}_mvalid,     m_tready => {sp}_mready,
            m_tlast  => {sp}_mlast,      m_tuser  => {sp}_muser
        );"""


def gen_stress_stim(j, sc, base_idx_1):
    """
    Stimulus process for stress scenario SSjjj.
    Drives s_tvalid in a sv_h HIGH / sv_l LOW repeating pattern.
    (sv_h=0, sv_l=0) → always valid (same as the normal stim process).
    """
    kr, kc, mode, flush, lw, fh, sv_h, sv_l, mr_h, mr_l, desc = sc
    dw = 8
    sp = f"ss{j:03d}"
    base_pre = f"c{base_idx_1:03d}_"
    in_file  = f'"{base_pre}input.txt"'

    always_valid = (sv_h == 0 and sv_l == 0)
    period = sv_h + sv_l

    if always_valid:
        # Identical to gen_stim — always assert svalid, wait on sready
        return f"""\
    p_stim_ss{j:03d} : process
        file     f   : text;
        variable ln  : line;
        variable pv  : integer;
        variable col : natural range 0 to {lw}-1;
        variable row : natural range 0 to {fh}-1;
    begin
        wait for CLK_PERIOD * 5;
        wait until rising_edge(clk);
        {sp}_rst <= '0';
        file_open(f, {in_file}, read_mode);
        col := 0;  row := 0;
        while not endfile(f) loop
            readline(f, ln);  read(ln, pv);
            {sp}_sdata  <= std_logic_vector(to_unsigned(pv, 8));
            if row = 0 and col = 0 then {sp}_suser <= '1'; else {sp}_suser <= '0'; end if;
            if col = {lw}-1 then {sp}_slast <= '1'; else {sp}_slast <= '0'; end if;
            {sp}_svalid <= '1';
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
    end process p_stim_ss{j:03d};"""
    else:
        # Pattern: sv_h HIGH then sv_l LOW, repeating.
        # A variable 'was_valid' records whether svalid was high this cycle,
        # avoiding the delta-cycle ambiguity of reading the signal after assignment.
        return f"""\
    p_stim_ss{j:03d} : process
        file     f        : text;
        variable ln       : line;
        variable pv       : integer;
        variable col      : natural range 0 to {lw}-1;
        variable row      : natural range 0 to {fh}-1;
        variable sv_phase : natural range 0 to {period}-1 := 0;
        variable was_valid : boolean;
    begin
        wait for CLK_PERIOD * 5;
        wait until rising_edge(clk);
        {sp}_rst <= '0';
        file_open(f, {in_file}, read_mode);
        col := 0;  row := 0;
        while not endfile(f) loop
            readline(f, ln);  read(ln, pv);
            {sp}_sdata  <= std_logic_vector(to_unsigned(pv, 8));
            if row = 0 and col = 0 then {sp}_suser <= '1'; else {sp}_suser <= '0'; end if;
            if col = {lw}-1 then {sp}_slast <= '1'; else {sp}_slast <= '0'; end if;
            -- Drive svalid in {sv_h}H/{sv_l}L pattern until the pixel is accepted.
            loop
                if sv_phase < {sv_h} then
                    {sp}_svalid <= '1';
                    was_valid := true;
                else
                    {sp}_svalid <= '0';
                    was_valid := false;
                end if;
                wait until rising_edge(clk);
                sv_phase := (sv_phase + 1) mod {period};
                exit when was_valid and {sp}_sready = '1';
            end loop;
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
    end process p_stim_ss{j:03d};"""


def gen_stress_mready_proc(j, sc):
    """
    m_tready pattern process for stress scenario SSjjj.
    Returns None when mr_h=0 and mr_l=0 (always ready; no process needed).
    """
    kr, kc, mode, flush, lw, fh, sv_h, sv_l, mr_h, mr_l, desc = sc
    if mr_h == 0 and mr_l == 0:
        return None
    sp = f"ss{j:03d}"
    period = mr_h + mr_l
    return f"""\
    -- Stress SS{j:03d}: m_tready {mr_h}H/{mr_l}L pattern
    p_mr_ss{j:03d} : process
        variable mr_phase : natural range 0 to {period}-1 := 0;
    begin
        wait until {sp}_rst = '0';
        loop
            if mr_phase < {mr_h} then
                {sp}_mready <= '1';
            else
                {sp}_mready <= '0';
            end if;
            wait until rising_edge(clk);
            mr_phase := (mr_phase + 1) mod {period};
        end loop;
    end process p_mr_ss{j:03d};"""


def gen_stress_check(j, sc, base_idx_1, done_offset):
    """Check process for stress scenario SSjjj; reads base config's expected file."""
    kr, kc, mode, flush, lw, fh, sv_h, sv_l, mr_h, mr_l, desc = sc
    dw = 8
    nt = kr * kc
    sp = f"ss{j:03d}"
    base_pre = f"c{base_idx_1:03d}_"
    exp_file = f'"{base_pre}expected.txt"'
    label = f"SS{j:03d} {kr}x{kc} {mode} {'FLUSH=on' if flush else 'FLUSH=off'} [{desc}]"
    return f"""\
    p_check_ss{j:03d} : process
        file     f         : text;
        variable ln        : line;
        variable tv        : integer;
        variable exp_vec   : std_logic_vector(8*{nt}-1 downto 0);
        variable out_count : natural := 0;
        variable err_count : natural := 0;
    begin
        wait until {sp}_rst = '0';
        file_open(f, {exp_file}, read_mode);
        while not endfile(f) loop
            wait until rising_edge(clk) and {sp}_mvalid = '1' and {sp}_mready = '1';
            readline(f, ln);
            for tap in 0 to {nt}-1 loop
                read(ln, tv);
                exp_vec((tap+1)*8-1 downto tap*8)
                    := std_logic_vector(to_unsigned(tv, 8));
            end loop;
            if {sp}_mdata /= exp_vec then
                report "SS{j:03d} MISMATCH at output " & integer'image(out_count)
                    severity error;
                err_count := err_count+1;
            end if;
            out_count := out_count+1;
        end loop;
        file_close(f);
        if err_count = 0 then
            report "SS{j:03d} PASS ({label}): "
                & integer'image(out_count) & " outputs checked." severity note;
        else
            report "SS{j:03d} FAIL ({label}): " & integer'image(err_count)
                & " mismatches in " & integer'image(out_count)
                & " outputs." severity failure;
        end if;
        done_flags({done_offset}) <= '1';
        wait;
    end process p_check_ss{j:03d};"""


# -------------------------------------------------------------------------
# HTML visualiser — cycle-accurate trace generation
# -------------------------------------------------------------------------

def cfg_label_html(c):
    lw, fh = c['line_width'], c['frame_height']
    fs = f" {lw}×{fh}" if (lw, fh) != (8, 8) else ""
    fl = "FLUSH=on" if c['flush'] else "FLUSH=off"
    return f"{c['data_width']}b {c['kern_rows']}×{c['kern_cols']} {c['edge_mode']} {fl}{fs}"


def gen_trace(cfg, frames_2d, expected_taps, bp_stall=0):
    """
    Build a delta-compressed cycle-accurate trace for the HTML visualiser.

    Returns a dict:
      { 'nrows': int, 'lw': int, 'c': [compressed_cycle, ...] }

    Each compressed cycle omits null/default fields and uses short keys:
      cy   - cycle number
      ph   - phase string (RESET/STALL/INPUT/DUMMY_COL/FLUSH/DRAIN)
      v    - input pixel value (INPUT/FLUSH only)
      fn   - frame index
      r    - row index
      ec   - effective column index
      wr   - BRAM write [phys_row, col, value] (only when write occurs)
      wrow - buf_wr_row (only emitted when it changes)
      er   - True when endRow (last DUMMY_COL of a row)
      oi   - expected vector index when output fires
      ok   - output kind: 's'=stream, 'f'=flush
      ofn  - output frame
      or   - output row
      oc   - output col

    BRAM state is reconstructed in JS by replaying 'wr' events.
    Tap vectors are looked up from cfg.expected[oi] in JS.
    """
    kr, kc = cfg['kern_rows'], cfg['kern_cols']
    lw, fh, nf = cfg['line_width'], cfg['frame_height'], cfg['num_frames']
    mode  = cfg['edge_mode']
    flush = cfg['flush']
    half_r = (kr - 1) // 2
    half_c = (kc - 1) // 2
    num_brams  = kr - 1
    eff_width  = lw + half_c

    flush_rows     = half_r if flush else 0
    rows_per_frame = fh + flush_rows
    frame_pushes   = rows_per_frame * eff_width

    # ---------- build push_to_exp mapping ----------
    push_to_exp = {}
    exp_i = 0
    total_pushes = nf * frame_pushes + 2

    if mode == "TOROIDAL" or is_stream_direct(kr, kc, fh, flush, mode):
        delay = half_r * eff_width + half_c + 2
        if mode == "TOROIDAL" and flush:
            for fn in range(nf):
                for fr in range(fh):
                    for fc in range(lw):
                        op = fn * frame_pushes + (fr + half_r) * eff_width + fc + half_c + 2
                        push_to_exp[op] = (exp_i, fn, fr, fc)
                        exp_i += 1
        else:
            max_cp = total_pushes - delay - 1
            for fn in range(nf):
                for fr in range(fh):
                    for fc in range(lw):
                        cp = fn * fh * eff_width + fr * eff_width + fc
                        if cp > max_cp:
                            continue
                        op = cp + delay
                        push_to_exp[op] = (exp_i, fn, fr, fc)
                        exp_i += 1
    elif not flush:
        for fn in range(nf):
            for row in range(fh):
                global_row = fn * fh + row
                for col_eff in range(eff_width):
                    if col_eff >= half_c and global_row >= half_r:
                        fc      = col_eff - half_c
                        out_r_g = global_row - half_r
                        out_fn  = out_r_g // fh
                        out_r   = out_r_g % fh
                        op = fn * fh * eff_width + row * eff_width + fc + half_c + 2
                        push_to_exp[op] = (exp_i, out_fn, out_r, fc)
                        exp_i += 1
    else:
        for fn in range(nf):
            for row in range(fh):
                for col_eff in range(eff_width):
                    if col_eff >= half_c and row >= half_r:
                        fc = col_eff - half_c
                        op = fn * frame_pushes + row * eff_width + fc + half_c + 2
                        push_to_exp[op] = (exp_i, fn, row - half_r, fc)
                        exp_i += 1
            for flush_row in range(half_r):
                vr    = fh + flush_row
                out_r = vr - half_r
                for col_eff in range(eff_width):
                    if col_eff >= half_c and out_r >= 0:
                        fc = col_eff - half_c
                        op = fn * frame_pushes + vr * eff_width + fc + half_c + 2
                        push_to_exp[op] = (exp_i, fn, out_r, fc)
                        exp_i += 1

    # ---------- build push sequence ----------
    pushes = []
    for fn in range(nf):
        for fr in range(fh):
            for ec in range(eff_width):
                if ec < lw:
                    pushes.append(('INPUT',     fn, fr, ec, frames_2d[fn][fr][ec]))
                else:
                    pushes.append(('DUMMY_COL', fn, fr, ec, None))
        if flush:
            for flush_r in range(half_r):
                ar = fh + flush_r
                for ec in range(eff_width):
                    pushes.append(('FLUSH', fn, ar, ec, 0))
    for _ in range(2):
        pushes.append(('DRAIN', None, None, None, None))

    # ---------- emit compressed cycles ----------
    cycles = []
    cycle  = 0
    buf_wr_row    = 0
    prev_wrow     = -1   # sentinel so first wrow is always emitted

    # RESET cycles
    for _ in range(2):
        cycles.append({'cy': cycle, 'ph': 'RESET'})
        cycle += 1

    # STALL cycles
    for _ in range(bp_stall):
        cycles.append({'cy': cycle, 'ph': 'STALL'})
        cycle += 1

    # Push cycles
    for pidx, (phase, fn, fr, ec, val) in enumerate(pushes):
        if phase in ('DUMMY_COL', 'FLUSH') and ec == lw and num_brams > 0:
            buf_wr_row = (buf_wr_row + 1) % num_brams

        cc = {'cy': cycle, 'ph': phase}

        # wrow delta (only emit when it changes)
        if buf_wr_row != prev_wrow:
            cc['wrow'] = buf_wr_row
            prev_wrow = buf_wr_row

        # BRAM write
        if phase in ('INPUT', 'FLUSH') and num_brams > 0 and ec < lw:
            cc['wr'] = [buf_wr_row, ec, val]

        # Input fields (omit for DRAIN)
        if phase in ('INPUT', 'DUMMY_COL', 'FLUSH'):
            cc['fn'] = fn
            cc['r']  = fr
            cc['ec'] = ec
            if val is not None:
                cc['v'] = val
            if phase == 'DUMMY_COL' and ec == eff_width - 1:
                cc['er'] = True

        # Output fields
        if pidx in push_to_exp:
            ei, out_fn, out_r, out_c = push_to_exp[pidx]
            if ei < len(expected_taps):
                cc['oi']  = ei
                cc['ok']  = 'f' if (flush and out_r is not None and out_r >= fh) else 's'
                cc['ofn'] = out_fn
                cc['or']  = out_r
                cc['oc']  = out_c

        cycles.append(cc)
        cycle += 1

    return {'nrows': num_brams, 'lw': lw, 'c': cycles}


def build_html_json(vec_dir):
    cs = []
    for idx, cfg in enumerate(CONFIGS):
        dw, kr, kc = cfg['data_width'], cfg['kern_rows'], cfg['kern_cols']
        lw, fh, nf = cfg['line_width'], cfg['frame_height'], cfg['num_frames']
        mode = cfg['edge_mode']
        flush = cfg['flush']
        half_r = (kr - 1) // 2
        half_c = (kc - 1) // 2
        streaming = (not flush) or (mode == 'TOROIDAL')

        pre = f"c{idx + 1:03d}_"
        ip = os.path.join(vec_dir, pre + "input.txt")
        ep = os.path.join(vec_dir, pre + "expected.txt")

        frames_data = []
        expected_data = []
        if os.path.exists(ip) and os.path.exists(ep):
            with open(ip) as f:
                pxs = [int(x) for x in f if x.strip()]
            for fn in range(nf):
                raw = pxs[fn * fh * lw:(fn + 1) * fh * lw]
                frames_data.append(
                    [[raw[r * lw + c] for c in range(lw)] for r in range(fh)])
            with open(ep) as f:
                expected_data = [list(map(int, x.split())) for x in f if x.strip()]

        eff_width = lw + half_c
        bp_info = BP_INFO.get(idx)
        bp_stall = (bp_info['stall_cycles']
                    if bp_info and bp_info.get('type') == 'post_reset'
                    else 0)
        trace = gen_trace(cfg, frames_data, expected_data, bp_stall) if frames_data else None

        cs.append({
            'id':             idx + 1,
            'kern_rows':      kr,
            'kern_cols':      kc,
            'half_r':         half_r,
            'half_c':         half_c,
            'edge_mode':      mode,
            'flush':          flush,
            'streaming':      streaming,
            'line_width':     lw,
            'frame_height':   fh,
            'num_frames':     nf,
            'data_width':     dw,
            'effective_width': eff_width,
            'label':          cfg_label_html(cfg),
            'frames':         frames_data,
            'expected':       expected_data,
            'bp':             bp_info,
            'trace':          trace,
        })

    # Build stress scenario metadata for the HTML.
    ss_list = []
    for j, sc in enumerate(STRESS_SCENARIOS, start=1):
        kr, kc, mode, flush, lw, fh, sv_h, sv_l, mr_h, mr_l, desc = sc
        base_idx_1 = _stress_base_idx(kr, kc, mode, flush, lw, fh)
        ss_list.append({
            'id':        j,
            'label':     f"SS{j:03d}: {kr}×{kc} {mode} {'FLUSH=on' if flush else 'FLUSH=off'} — {desc}",
            'kr':        kr, 'kc': kc, 'mode': mode, 'flush': flush,
            'lw':        lw, 'fh': fh,
            'sv_h':      sv_h, 'sv_l': sv_l,
            'mr_h':      mr_h, 'mr_l': mr_l,
            'desc':      desc,
            'base_cfg':  base_idx_1,
        })

    return json.dumps({'configs': cs, 'stress': ss_list}, separators=(',', ':'))


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>conv2d Cycle Simulator</title>
<style>
:root {
  --ink: #eef6f4;
  --paper: #07100f;
  --paper-2: #0e1d1b;
  --panel: #102522;
  --panel-2: #152f2b;
  --line: #2f7068;
  --line-soft: rgba(109, 226, 207, .22);
  --muted: #91aaa5;
  --amber: #f0b85a;
  --cyan: #67e8d2;
  --blue: #64a8ff;
  --red: #ff7066;
  --green: #78e08f;
  --shadow: rgba(0, 0, 0, .46);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  height: 100vh;
  overflow: hidden;
  background:
    linear-gradient(90deg, rgba(103,232,210,.06) 1px, transparent 1px),
    linear-gradient(rgba(103,232,210,.045) 1px, transparent 1px),
    var(--paper);
  background-size: 16px 16px;
  color: var(--ink);
  font-family: Georgia, "Times New Roman", serif;
}
button, select, input {
  font: inherit;
}
.window-scope {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  height: 100vh;
}
aside {
  border-right: 3px solid var(--line);
  background: rgba(7, 16, 15, .96);
  padding: 18px;
  overflow: auto;
  min-height: 0;
}
main {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  min-width: 0;
  min-height: 0;
  position: relative;
}
h1 {
  margin: 0 0 8px;
  font-size: 30px;
  line-height: 1;
  letter-spacing: 0;
}
.subtitle {
  color: var(--muted);
  font-size: 13px;
  line-height: 1.35;
}
.controls {
  display: grid;
  gap: 12px;
  margin-top: 18px;
}
label {
  display: grid;
  gap: 5px;
  color: var(--muted);
  font-size: 12px;
  text-transform: uppercase;
}
select, input[type="range"] {
  width: 100%;
}
select {
  border: 2px solid var(--line);
  background: var(--paper-2);
  color: var(--ink);
  padding: 8px;
}
.transport {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 6px;
}
button {
  border: 2px solid var(--line);
  background: var(--paper-2);
  color: var(--ink);
  min-height: 36px;
  cursor: pointer;
  box-shadow: 3px 3px 0 rgba(103,232,210,.18);
}
button:hover, button:focus-visible {
  background: #183834;
  outline: 2px solid var(--cyan);
  outline-offset: 2px;
}
.meter {
  border: 2px solid var(--line);
  background: var(--panel);
  padding: 10px;
  box-shadow: 4px 4px 0 var(--shadow);
}
.meter strong {
  display: block;
  font-size: 24px;
}
.legend {
  display: grid;
  gap: 7px;
  margin-top: 14px;
  font-size: 12px;
}
.swatch {
  display: inline-block;
  width: 12px;
  height: 12px;
  border: 1px solid var(--line);
  margin-right: 6px;
  vertical-align: -1px;
}
.top {
  border-bottom: 3px solid var(--line);
  padding: 14px 18px;
  background: rgba(7, 16, 15, .96);
}
.top-grid {
  display: grid;
  grid-template-columns: 1.2fr repeat(4, minmax(120px, auto));
  gap: 10px;
  align-items: stretch;
}
.plate {
  border: 2px solid var(--line);
  background: linear-gradient(180deg, var(--panel), #0b1816);
  padding: 9px 11px;
}
.plate span {
  display: block;
  color: var(--muted);
  font-size: 11px;
  text-transform: uppercase;
}
.plate strong {
  display: block;
  font-size: 18px;
  margin-top: 2px;
}
.content {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(380px, .9fr);
  min-height: 0;
  overflow: hidden;
}
.left, .right {
  min-width: 0;
  min-height: 0;
  padding: 16px 18px;
}
.left {
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr);
  overflow: hidden;
}
.right {
  border-left: 3px solid var(--line);
  background: rgba(8, 19, 18, .82);
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto minmax(160px, .55fr);
  overflow: hidden;
}
.data-scroll {
  min-height: 0;
  overflow: auto;
  padding-right: 4px;
}
.section-title {
  margin: 0 0 9px;
  font-size: 13px;
  text-transform: uppercase;
  color: var(--muted);
}
.quick-jumps {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 6px;
}
.quick-jumps button {
  min-height: 34px;
  box-shadow: none;
  font-size: 12px;
}
.story-card {
  border: 2px solid var(--line);
  background: linear-gradient(90deg, rgba(103,232,210,.12), rgba(16,37,34,.9));
  padding: 12px 14px;
  margin-bottom: 12px;
}
.story-card h2 {
  margin: 0 0 4px;
  font-size: 15px;
}
.story-card p {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}
.phase-help {
  margin-top: 8px;
  color: var(--cyan);
}
.view-toggle {
  display: flex;
  gap: 8px;
  margin: 0 0 10px;
}
.view-toggle button {
  min-height: 30px;
  padding: 0 12px;
  box-shadow: none;
}
.view-toggle button.active {
  background: #16443e;
  border-color: var(--cyan);
  color: var(--cyan);
}
.data-section.hidden {
  display: none;
}
.status-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 16px;
}
.readout {
  border: 2px solid var(--line);
  background: rgba(16, 37, 34, .9);
  padding: 12px;
  min-height: 112px;
  box-shadow: 0 0 0 1px rgba(103,232,210,.08), 0 14px 28px var(--shadow);
}
.readout h2 {
  margin: 0 0 8px;
  font-size: 16px;
}
.readout p {
  margin: 4px 0;
  color: var(--muted);
  font-size: 13px;
}
.badge {
  display: inline-block;
  padding: 2px 7px;
  border: 1px solid var(--line);
  background: var(--paper-2);
  color: var(--ink);
  font-size: 12px;
}
.phase-RESET { background: #263331; }
.phase-STALL { background: #5d3c12; color: #ffe2a8; }
.phase-INPUT { background: #164425; color: #bdffc9; }
.phase-DUMMY_COL { background: #123b54; color: #c9e9ff; }
.phase-FLUSH { background: #0f514a; color: #c8fff4; }
.phase-DRAIN { background: #33304c; color: #e3dcff; }
.signal-panel {
  overflow: auto;
  border: 2px solid var(--line);
  background:
    linear-gradient(90deg, rgba(103,232,210,.045) 1px, transparent 1px),
    linear-gradient(rgba(103,232,210,.035) 1px, transparent 1px),
    rgba(8, 19, 18, .94);
  background-size: 22px 22px;
  padding: 14px;
  margin-bottom: 16px;
  box-shadow: inset 0 0 26px rgba(103,232,210,.06);
}
.tap-matrix {
  display: grid;
  grid-template-columns: 34px repeat(var(--tap-cols), minmax(66px, 1fr));
  gap: 6px;
  min-width: max-content;
}
.tap-axis,
.tap-cell {
  min-height: 42px;
  display: grid;
  place-items: center;
}
.tap-axis {
  color: var(--muted);
  font-size: 11px;
  border: 1px solid rgba(103,232,210,.12);
  background: rgba(16,37,34,.54);
}
.tap-cell {
  position: relative;
  border: 1px solid rgba(103,232,210,.3);
  background: linear-gradient(180deg, rgba(21,47,43,.96), rgba(8,19,18,.95));
  color: var(--ink);
  font-family: "Courier New", monospace;
  font-size: 15px;
  font-weight: 700;
  box-shadow: inset 0 0 18px rgba(0,0,0,.24);
}
.tap-cell.center {
  border-color: var(--blue);
  background: linear-gradient(180deg, rgba(49,91,133,.82), rgba(17,41,61,.95));
  box-shadow: inset 0 0 0 2px rgba(100,168,255,.36), 0 0 18px rgba(100,168,255,.12);
}
.tap-cell::after {
  content: attr(data-coord);
  position: absolute;
  top: 4px;
  left: 6px;
  color: var(--muted);
  font-size: 9px;
  font-family: Georgia, "Times New Roman", serif;
  font-weight: 400;
}
.frames {
  display: grid;
  gap: 10px;
  min-height: 0;
  overflow: auto;
  padding-right: 4px;
}
.frame-card {
  border: 2px solid var(--line);
  background: rgba(16,37,34,.74);
  padding: 10px;
}
.frame-head {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8px;
  font-size: 13px;
  color: var(--muted);
}
.pixel-grid {
  display: grid;
  gap: 3px;
  justify-content: start;
  align-content: start;
}
.px {
  width: var(--pixel-size, 22px);
  height: var(--pixel-size, 22px);
  border: 1px solid rgba(103,232,210,.18);
  color: transparent;
  font-size: 0;
  box-shadow: inset 0 0 10px rgba(255,255,255,.03);
}
.px.current-in { outline: 3px solid var(--green); z-index: 2; }
.px.current-out { outline: 3px solid var(--blue); z-index: 2; }
.px.window { box-shadow: inset 0 0 0 3px rgba(100,168,255,.32); }
.bram-card {
  border: 1px solid rgba(103,232,210,.35);
  background: rgba(9, 24, 22, .82);
  padding: 10px;
  margin-bottom: 12px;
}
.bram-title {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
  color: var(--muted);
  font-size: 12px;
}
.bram-title strong {
  color: var(--ink);
}
.bram-strip {
  display: grid;
  grid-template-columns: repeat(var(--bram-cols), minmax(48px, 1fr));
  gap: 5px;
  min-width: max-content;
}
.bram-cell {
  position: relative;
  min-height: 42px;
  border: 1px solid rgba(103,232,210,.24);
  background: linear-gradient(180deg, rgba(17,43,40,.95), rgba(7,16,15,.94));
  display: grid;
  place-items: center;
  color: var(--ink);
  font-family: "Courier New", monospace;
  font-size: 14px;
  font-weight: 700;
}
.bram-cell::before {
  content: attr(data-addr);
  position: absolute;
  top: 3px;
  left: 5px;
  color: var(--muted);
  font-size: 9px;
  font-family: Georgia, "Times New Roman", serif;
  font-weight: 400;
}
.bram-cell.zero {
  color: #7f9691;
  background: rgba(9, 18, 17, .82);
}
.timeline {
  min-height: 0;
  overflow: auto;
  border: 2px solid var(--line);
  background: rgba(8, 19, 18, .9);
}
.tick {
  display: grid;
  grid-template-columns: 64px 92px minmax(0, 1fr);
  gap: 8px;
  padding: 6px 8px;
  border-bottom: 1px solid var(--line-soft);
  cursor: pointer;
  font-size: 12px;
}
.tick.active {
  background: #143c37;
  box-shadow: inset 5px 0 0 var(--cyan);
}
.mono {
  font-family: "Courier New", monospace;
}
@media (max-width: 1100px) {
  body { height: auto; overflow: auto; }
  .window-scope { grid-template-columns: 1fr; height: auto; }
  aside { border-right: 0; border-bottom: 3px solid var(--line); }
  .content { grid-template-columns: 1fr; }
  .left { overflow: visible; }
  .data-scroll { overflow: visible; }
  .right { border-left: 0; border-top: 3px solid var(--line); overflow: visible; display: block; }
  .top-grid, .status-grid { grid-template-columns: 1fr 1fr; }
}
</style>
</head>
<body>
<div class="window-scope">
  <aside>
    <h1>conv2d<br>cycle scope</h1>
    <div class="subtitle">A static simulator for the generated tests. It shows each accepted stream push, delayed output, BRAM contents, and all source frames.</div>
    <div class="controls">
      <label>Window size<select id="windowSelect"></select></label>
      <label>Edge mode<select id="edgeSelect"></select></label>
      <label>Flush<select id="flushSelect"></select></label>
      <label>Frame size<select id="frameSizeSelect"></select></label>
      <label>Cycle<input id="cycleRange" type="range" min="0" value="0"></label>
      <div class="transport">
        <button id="firstBtn" title="First cycle">|&lt;</button>
        <button id="prevBtn" title="Previous cycle">&lt;</button>
        <button id="playBtn" title="Play">Play</button>
        <button id="nextBtn" title="Next cycle">&gt;</button>
        <button id="lastBtn" title="Last cycle">&gt;|</button>
      </div>
      <div class="meter">
        <span>Cycle</span>
        <strong id="cycleMeter">0 / 0</strong>
      </div>
      <label>Jump to event</label>
      <div class="quick-jumps">
        <button id="firstOutputBtn" title="Jump to first valid output">First output</button>
        <button id="nextOutputBtn" title="Jump to next valid output">Next output</button>
        <button id="nextStallBtn" title="Jump to next back-pressure stall">Next stall</button>
        <button id="nextFlushBtn" title="Jump to next flush cycle">Next flush</button>
      </div>
    </div>
    <div class="legend">
      <div><span class="swatch" style="background:#164425"></span>accepted real input</div>
      <div><span class="swatch" style="background:#0f514a"></span>flush injected zero</div>
      <div><span class="swatch" style="background:#64a8ff"></span>current output centre</div>
      <div><span class="swatch" style="background:#78e08f"></span>current input pixel</div>
    </div>
    <div class="controls" style="margin-top:18px">
      <label style="color:var(--amber);font-size:12px;text-transform:uppercase">
        Stress scenarios
        <select id="stressSelect" style="margin-top:4px">
          <option value="-1">— none selected —</option>
        </select>
      </label>
      <button id="stressClearBtn" style="display:none">← Back to base configs</button>
    </div>
  </aside>
  <main>
    <div class="top">
      <div class="top-grid">
        <div class="plate"><span>Config</span><strong id="configTitle"></strong></div>
        <div class="plate"><span>Kernel</span><strong id="kernelPlate"></strong></div>
        <div class="plate"><span>Frame</span><strong id="framePlate"></strong></div>
        <div class="plate"><span>Edge</span><strong id="edgePlate"></strong></div>
        <div class="plate"><span>Mode</span><strong id="modePlate"></strong></div>
      </div>
    </div>
    <div class="content">
      <div class="left">
        <section class="story-card">
          <h2>Cycle story</h2>
          <p id="cycleStory"></p>
          <p id="phaseHelp" class="phase-help"></p>
        </section>
        <div class="status-grid">
          <section class="readout">
            <h2>What is happening</h2>
            <div id="phaseBadge" class="badge"></div>
            <p id="noteText"></p>
          </section>
          <section class="readout">
            <h2>Input</h2>
            <p id="inputText"></p>
            <p id="writeText"></p>
          </section>
          <section class="readout">
            <h2>Output</h2>
            <p id="outputText"></p>
            <p id="tapText"></p>
          </section>
        </div>
        <div class="data-scroll">
          <div class="view-toggle" aria-label="Raw data view">
            <button id="essentialViewBtn" class="active">Essential</button>
            <button id="allViewBtn">All data</button>
          </div>
          <h3 class="section-title">Output tap grid</h3>
          <div id="tapGrid" class="signal-panel"></div>
          <section id="physicalSection" class="data-section">
            <h3 class="section-title">BRAM physical rows</h3>
            <div id="bramPhysical"></div>
          </section>
          <section id="logicalSection" class="data-section hidden">
            <h3 class="section-title">BRAM logical age order</h3>
            <div id="bramLogical"></div>
          </section>
        </div>
      </div>
      <div class="right">
        <h3 class="section-title">Three-frame overview</h3>
        <div id="frames" class="frames"></div>
        <h3 class="section-title" style="margin-top:16px">Timeline</h3>
        <div id="timeline" class="timeline"></div>
      </div>
    </div>
  </main>
</div>
<!-- Stress scenario info overlay (hidden until a stress scenario is selected) -->
<div id="stressOverlay" style="display:none;position:absolute;inset:0;background:var(--paper);overflow:auto;padding:24px;z-index:10">
  <h2 id="stressTitle" style="margin:0 0 6px;color:var(--amber)"></h2>
  <p id="stressSubtitle" style="margin:0 0 20px;color:var(--muted);font-size:13px"></p>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;max-width:800px">
    <div class="plate" style="padding:14px">
      <span>Base config</span><strong id="stressBase"></strong>
    </div>
    <div class="plate" style="padding:14px">
      <span>Kernel / Frame</span><strong id="stressKernFrame"></strong>
    </div>
    <div class="plate" style="padding:14px">
      <span>Edge mode / Flush</span><strong id="stressEdge"></strong>
    </div>
    <div class="plate" style="padding:14px">
      <span>Pattern</span><strong id="stressPattern"></strong>
    </div>
  </div>
  <div style="margin-top:20px">
    <h3 style="color:var(--muted);font-size:12px;text-transform:uppercase;margin:0 0 10px">Signal waveform (first 30 cycles after reset)</h3>
    <div id="stressWave" style="font-family:monospace;font-size:13px;overflow-x:auto"></div>
  </div>
  <div style="margin-top:20px;padding:14px;border:2px solid var(--line);max-width:800px">
    <p style="margin:0;color:var(--muted);font-size:13px">
      This stress scenario uses the same golden vectors as the base config above. The DUT produces
      identical outputs — only the timing of <code>s_tvalid</code> and <code>m_tready</code> varies.
      A PASS in the simulation confirms the pipeline correctly handles all stall/restart combinations.
      Switch back to the base config to view the cycle-accurate trace.
    </p>
  </div>
</div>
<script id="sim-data" type="application/json">__JSON_DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('sim-data').textContent);
let cfgIndex = 0;
let cycleIndex = 0;
let timer = null;
let viewMode = 'essential';

const el = id => document.getElementById(id);
const windowSelect = el('windowSelect');
const edgeSelect = el('edgeSelect');
const flushSelect = el('flushSelect');
const frameSizeSelect = el('frameSizeSelect');
const cycleRange = el('cycleRange');

function clamp(n, lo, hi) { return Math.max(lo, Math.min(hi, n)); }
function currentConfig() { return DATA.configs[cfgIndex]; }
function traceLen(cfg) { return cfg.trace && cfg.trace.c ? cfg.trace.c.length : 0; }
function lastCycleCy(cfg) { const t = cfg.trace; return t && t.c && t.c.length ? t.c[t.c.length - 1].cy : 0; }

// ---- Delta-compressed trace expansion ------------------------------------
// Traces are stored as short-key dicts (see gen_trace in gen_tb.py).
// getBramAt() replays BRAM writes up to index idx using cached checkpoints.
// expandCycle() reconstructs the full cycle object the renderer expects.

function initBramCheckpoints(cfg) {
  if (cfg._bcp !== undefined) return;
  const t = cfg.trace;
  if (!t || !t.c || !t.c.length || !t.nrows) { cfg._bcp = null; return; }
  const nrows = t.nrows, lw = t.lw;
  const emptyRows = () => Array.from({length: nrows}, () => new Array(lw).fill(0));
  const cps = [{ idx: 0, rows: emptyRows(), wrow: 0 }];
  let rows = emptyRows(), wrow = 0;
  t.c.forEach((cc, i) => {
    if (cc.wrow !== undefined) wrow = cc.wrow;
    if (cc.wr) rows[cc.wr[0]][cc.wr[1]] = cc.wr[2];
    if ((i + 1) % 64 === 0) cps.push({ idx: i + 1, rows: rows.map(r => r.slice()), wrow });
  });
  cfg._bcp = cps;
}

function getBramAt(cfg, idx) {
  const t = cfg.trace;
  if (!t || !t.c || !t.nrows) return { physical: [], logical: [], writeRow: 0 };
  initBramCheckpoints(cfg);
  if (!cfg._bcp) return { physical: [], logical: [], writeRow: 0 };
  const nrows = t.nrows, lw = t.lw;
  const cps = cfg._bcp;
  let cp = cps[0];
  for (let i = 1; i < cps.length && cps[i].idx <= idx; i++) cp = cps[i];
  const rows = cp.rows.map(r => r.slice());
  let wrow = cp.wrow;
  for (let i = cp.idx; i <= idx; i++) {
    const cc = t.c[i]; if (!cc) break;
    if (cc.wrow !== undefined) wrow = cc.wrow;
    if (cc.wr) rows[cc.wr[0]][cc.wr[1]] = cc.wr[2];
  }
  const logical = Array.from({length: nrows}, (_, s) => rows[(wrow + s) % nrows]);
  return { physical: rows, logical, writeRow: wrow };
}

function expandCycle(cfg, idx) {
  const t = cfg.trace;
  if (!t || !t.c || !t.c.length) return null;
  const cc = t.c[idx];
  const ph = cc.ph;
  const hasInput = ph === 'INPUT' || ph === 'DUMMY_COL' || ph === 'FLUSH';
  const inKinds = { RESET: 'reset', STALL: 'stall', INPUT: 'real', DUMMY_COL: 'column-pad', FLUSH: 'flush', DRAIN: 'drain' };
  const inCol = (cc.ec !== undefined && cc.ec < t.lw) ? cc.ec : null;
  const write = cc.wr ? { row: cc.wr[0], col: cc.wr[1], value: cc.wr[2] } : null;
  const hasOut = cc.oi !== undefined;
  const taps = hasOut && cfg.expected ? cfg.expected[cc.oi] : null;
  const bram = getBramAt(cfg, idx);
  return {
    cycle:   cc.cy,
    phase:   ph,
    note:    buildNote(ph, cc, t.lw),
    stalled: ph === 'STALL',
    input: {
      kind:         inKinds[ph] || 'drain',
      valid:        hasInput,
      value:        cc.v !== undefined ? cc.v : null,
      frame:        cc.fn !== undefined ? cc.fn : null,
      row:          cc.r  !== undefined ? cc.r  : null,
      col:          inCol,
      effectiveCol: cc.ec !== undefined ? cc.ec : null,
      endRow:       cc.er === true,
      write,
    },
    output: {
      valid:         hasOut,
      frame:         hasOut ? cc.ofn : null,
      row:           hasOut ? cc.or  : null,
      col:           hasOut ? cc.oc  : null,
      kind:          hasOut ? (cc.ok === 'f' ? 'flush' : 'stream') : null,
      taps,
      expectedIndex: hasOut ? cc.oi : null,
    },
    bram,
  };
}

function buildNote(ph, cc, lw) {
  if (ph === 'RESET') return 'Synchronous reset active.';
  if (ph === 'STALL') return 'Downstream back-pressure; pipeline stalled.';
  if (ph === 'DRAIN') return 'No new input; draining pipeline tail.';
  if (ph === 'INPUT') return `Frame ${cc.fn}, row ${cc.r}, col ${cc.ec} accepted; BRAM row ${cc.wr ? cc.wr[0] : '?'} col ${cc.ec} updated.`;
  if (ph === 'DUMMY_COL') return `Dummy column ${cc.ec - lw + 1} for frame ${cc.fn}, row ${cc.r}.`;
  if (ph === 'FLUSH') return `Flush zero, frame ${cc.fn} row ${cc.r}, col ${cc.ec}; BRAM row ${cc.wr ? cc.wr[0] : '?'} col ${cc.ec} cleared.`;
  return '';
}

function currentCycle() { return expandCycle(currentConfig(), cycleIndex); }

function init() {
  populateSelector(windowSelect, uniqueValues(cfg => `${cfg.kern_rows}x${cfg.kern_cols}`));
  populateSelector(edgeSelect, uniqueValues(cfg => cfg.edge_mode));
  populateSelector(flushSelect, uniqueValues(cfg => cfg.flush ? 'on' : 'off'));
  populateSelector(frameSizeSelect, uniqueValues(cfg => `${cfg.line_width}x${cfg.frame_height}`));
  [windowSelect, edgeSelect, flushSelect, frameSizeSelect].forEach(select => {
    select.addEventListener('change', selectConfigFromControls);
  });
  cycleRange.addEventListener('input', () => {
    cycleIndex = Number(cycleRange.value);
    renderCycleOnly();
  });
  el('firstBtn').onclick = () => { cycleIndex = 0; stop(); renderCycleOnly(); };
  el('prevBtn').onclick = () => { cycleIndex = clamp(cycleIndex - 1, 0, traceLen(currentConfig()) - 1); stop(); renderCycleOnly(); };
  el('nextBtn').onclick = () => { cycleIndex = clamp(cycleIndex + 1, 0, traceLen(currentConfig()) - 1); stop(); renderCycleOnly(); };
  el('lastBtn').onclick = () => { cycleIndex = traceLen(currentConfig()) - 1; stop(); renderCycleOnly(); };
  el('playBtn').onclick = togglePlay;
  el('firstOutputBtn').onclick = () => jumpTo('first-output');
  el('nextOutputBtn').onclick = () => jumpTo('next-output');
  el('nextStallBtn').onclick = () => jumpTo('next-stall');
  el('nextFlushBtn').onclick = () => jumpTo('next-flush');
  el('essentialViewBtn').onclick = () => setViewMode('essential');
  el('allViewBtn').onclick = () => setViewMode('all');
  document.addEventListener('keydown', event => {
    if (event.target.tagName === 'SELECT' || event.target.tagName === 'INPUT') return;
    if (event.key === 'ArrowRight') { el('nextBtn').click(); event.preventDefault(); }
    if (event.key === 'ArrowLeft') { el('prevBtn').click(); event.preventDefault(); }
    if (event.key === ' ') { togglePlay(); event.preventDefault(); }
  });
  render();
}

function uniqueValues(fn) {
  return Array.from(new Set(DATA.configs.map(fn)));
}

function populateSelector(select, values) {
  select.innerHTML = '';
  values.forEach(value => {
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = value;
    select.appendChild(opt);
  });
}

function selectConfigFromControls() {
  const idx = DATA.configs.findIndex(cfg =>
    `${cfg.kern_rows}x${cfg.kern_cols}` === windowSelect.value &&
    cfg.edge_mode === edgeSelect.value &&
    (cfg.flush ? 'on' : 'off') === flushSelect.value &&
    `${cfg.line_width}x${cfg.frame_height}` === frameSizeSelect.value
  );
  if (idx >= 0) {
    cfgIndex = idx;
    cycleIndex = 0;
    stop();
    render();
  }
}

function setViewMode(mode) {
  viewMode = mode;
  renderDataVisibility();
}

function renderDataVisibility() {
  el('essentialViewBtn').classList.toggle('active', viewMode === 'essential');
  el('allViewBtn').classList.toggle('active', viewMode === 'all');
  el('logicalSection').classList.toggle('hidden', viewMode !== 'all');
}

// Fast scan on raw compressed cycles — avoids full expandCycle per entry
function findCycleIndex(rawPred, start = 0) {
  const t = currentConfig().trace;
  if (!t || !t.c) return -1;
  for (let i = start; i < t.c.length; i++) {
    if (rawPred(t.c[i])) return i;
  }
  return -1;
}

function jumpTo(kind) {
  const rawPreds = {
    'first-output': cc => cc.oi !== undefined,
    'next-output':  cc => cc.oi !== undefined,
    'next-stall':   cc => cc.ph === 'STALL',
    'next-flush':   cc => cc.ph === 'FLUSH',
  };
  const start = kind === 'first-output' ? 0 : cycleIndex + 1;
  let idx = findCycleIndex(rawPreds[kind], start);
  if (idx < 0 && kind !== 'first-output') idx = findCycleIndex(rawPreds[kind], 0);
  if (idx >= 0) {
    cycleIndex = idx;
    stop();
    renderCycleOnly();
  }
}

function togglePlay() {
  if (timer) { stop(); return; }
  el('playBtn').textContent = 'Pause';
  timer = setInterval(() => {
    if (cycleIndex >= traceLen(currentConfig()) - 1) { stop(); return; }
    cycleIndex += 1;
    renderCycleOnly();
  }, 220);
}

function stop() {
  clearInterval(timer);
  timer = null;
  el('playBtn').textContent = 'Play';
}

function render() {
  const cfg = currentConfig();
  windowSelect.value = `${cfg.kern_rows}x${cfg.kern_cols}`;
  edgeSelect.value = cfg.edge_mode;
  flushSelect.value = cfg.flush ? 'on' : 'off';
  frameSizeSelect.value = `${cfg.line_width}x${cfg.frame_height}`;
  el('configTitle').textContent = `CFG${String(cfg.id).padStart(3, '0')}`;
  el('kernelPlate').textContent = `${cfg.kern_rows}x${cfg.kern_cols}`;
  el('framePlate').textContent = `${cfg.line_width}x${cfg.frame_height} x ${cfg.num_frames}`;
  el('edgePlate').textContent = cfg.edge_mode;
  el('modePlate').textContent = cfg.streaming ? 'streaming' : 'flush';
  cycleRange.max = String(traceLen(cfg) - 1);
  renderTimeline();
  renderCycleOnly();
  renderDataVisibility();
}

function renderCycleOnly() {
  const cfg = currentConfig();
  cycleIndex = clamp(cycleIndex, 0, traceLen(cfg) - 1);
  cycleRange.value = String(cycleIndex);
  const cyc = currentCycle();
  if (!cyc) return;
  el('cycleMeter').textContent = `${cyc.cycle} / ${lastCycleCy(cfg)}`;
  el('phaseBadge').textContent = cyc.phase;
  el('phaseBadge').className = `badge phase-${cyc.phase}`;
  el('cycleStory').textContent = cycleStory(cfg, cyc);
  el('phaseHelp').textContent = phaseHelp(cyc.phase);
  el('noteText').textContent = cyc.note;
  renderInput(cyc);
  renderOutput(cfg, cyc);
  renderTapGrid(cfg, cyc.output.taps);
  renderBram(cyc);
  renderFrames(cfg, cyc);
  updateTimelineCursor();
  renderDataVisibility();
}

function phaseHelp(phase) {
  const help = {
    RESET: 'Reset clears counters and valid flags before the stream begins.',
    STALL: 'Back-pressure is active, so input, BRAM reads, and the output pipeline hold their state.',
    INPUT: 'A real frame pixel is accepted and written into the rotating BRAM row.',
    DUMMY_COL: 'A synthetic column is shown after the row. TOROIDAL preview uses previous-row suffix for left-edge taps and next-row prefix for right-edge taps.',
    FLUSH: 'A zero row is injected after a frame to push bottom-edge windows out before the next frame.',
    DRAIN: 'No new input is accepted; delayed pipeline state is being shown.',
  };
  return help[phase] || '';
}

function cycleStory(cfg, cyc) {
  const input = cyc.input;
  const output = cyc.output;
  const inputPart = input.valid
    ? `${input.kind} push ${input.value} at ${input.frame !== null ? `frame ${input.frame}` : 'no frame'}${input.col !== null ? ` (${input.row},${input.col})` : ''}`
    : input.kind === 'stall' ? 'input is held by back-pressure' : 'no input push';
  const outputPart = output.valid
    ? `output window for frame ${output.frame} (${output.row},${output.col}) is valid`
    : 'no output window is valid yet';
  const memoryPart = input.write
    ? `BRAM row ${input.write.row}, col ${input.write.col} is updated`
    : 'BRAM contents hold';
  return `${inputPart}; ${outputPart}; ${memoryPart}.`;
}

function renderInput(cyc) {
  const input = cyc.input;
  if (!input.valid) {
    el('inputText').textContent = input.kind === 'stall' ? 'No input accepted; downstream back-pressure holds the pipeline.' : `No input push (${input.kind}).`;
  } else {
    const pos = input.col === null ? `effective column ${input.effectiveCol}` : `(${input.row}, ${input.col})`;
    el('inputText').textContent = `${input.kind}: value ${input.value} from frame ${input.frame}, ${pos}`;
  }
  el('writeText').textContent = input.write
    ? `BRAM write: physical row ${input.write.row}, col ${input.write.col} = ${input.write.value}`
    : 'BRAM write: none this cycle';
}

function renderOutput(cfg, cyc) {
  const out = cyc.output;
  if (!out.valid) {
    el('outputText').textContent = 'm_tvalid = 0; no output window this cycle.';
    el('tapText').textContent = '';
    return;
  }
  el('outputText').textContent = `m_tvalid = 1; frame ${out.frame}, output (${out.row}, ${out.col}), ${out.kind}`;
  el('tapText').textContent = `Expected vector index ${out.expectedIndex}; ${cfg.kern_rows * cfg.kern_cols} taps shown below.`;
}

function renderTapGrid(cfg, taps) {
  const host = el('tapGrid');
  host.innerHTML = '';
  if (!taps || !taps.length) {
    host.textContent = 'No output taps on this cycle.';
    return;
  }
  const matrix = document.createElement('div');
  matrix.className = 'tap-matrix';
  matrix.style.setProperty('--tap-cols', cfg.kern_cols);
  matrix.appendChild(axisCell('tap'));
  for (let c = 0; c < cfg.kern_cols; c++) matrix.appendChild(axisCell(`c${c}`));
  for (let r = 0; r < cfg.kern_rows; r++) {
    matrix.appendChild(axisCell(`r${r}`));
    for (let c = 0; c < cfg.kern_cols; c++) {
      const cell = document.createElement('div');
      cell.className = 'tap-cell';
      if (r === cfg.half_r && c === cfg.half_c) cell.classList.add('center');
      cell.dataset.coord = `r${r} c${c}`;
      cell.title = `tap[${r}][${c}]`;
      cell.textContent = taps[r * cfg.kern_cols + c];
      matrix.appendChild(cell);
    }
  }
  host.appendChild(matrix);
}

function axisCell(label) {
  const cell = document.createElement('div');
  cell.className = 'tap-axis';
  cell.textContent = label;
  return cell;
}

function renderBram(cyc) {
  renderBramRows(el('bramPhysical'), cyc.bram.physical, 'physical');
  renderBramRows(el('bramLogical'), cyc.bram.logical, 'logical age');
}

function renderBramRows(host, rows, title) {
  host.innerHTML = '';
  if (!rows.length) {
    host.textContent = 'No BRAM rows for this configuration.';
    return;
  }
  rows.forEach((row, i) => {
    const block = document.createElement('div');
    block.className = 'bram-card';
    const head = document.createElement('div');
    head.className = 'bram-title';
    head.innerHTML = `<strong>${title} row ${i}</strong><span>${row.length} columns</span>`;
    block.appendChild(head);
    const strip = document.createElement('div');
    strip.className = 'bram-strip';
    strip.style.setProperty('--bram-cols', row.length);
    row.forEach((value, col) => {
      const cell = document.createElement('div');
      cell.className = 'bram-cell';
      if (Number(value) === 0) cell.classList.add('zero');
      cell.dataset.addr = `c${col}`;
      cell.title = `${title} row ${i}, col ${col}`;
      cell.textContent = value;
      strip.appendChild(cell);
    });
    block.appendChild(strip);
    host.appendChild(block);
  });
}

function renderFrames(cfg, cyc) {
  const host = el('frames');
  host.innerHTML = '';
  const pixelSize = cfg.line_width > 12 ? 14 : cfg.line_width > 8 ? 18 : 24;
  cfg.frames.forEach((frame, fn) => {
    const card = document.createElement('div');
    card.className = 'frame-card';
    const head = document.createElement('div');
    head.className = 'frame-head';
    head.innerHTML = `<strong>Frame ${fn}</strong><span>${cfg.line_width}x${cfg.frame_height}</span>`;
    card.appendChild(head);
    const grid = document.createElement('div');
    grid.className = 'pixel-grid';
    grid.style.setProperty('--pixel-size', `${pixelSize}px`);
    grid.style.gridTemplateColumns = `repeat(${cfg.line_width}, var(--pixel-size))`;
    for (let r = 0; r < cfg.frame_height; r++) {
      for (let c = 0; c < cfg.line_width; c++) {
        const value = frame[r][c];
        const px = document.createElement('div');
        px.className = 'px';
        px.title = `Frame ${fn} (${r}, ${c}) = ${value}`;
        const norm = Number(value) / ((1 << Math.min(cfg.data_width, 16)) - 1);
        const red = Math.round(10 + norm * 58);
        const green = Math.round(33 + norm * 188);
        const blue = Math.round(32 + norm * 158);
        px.style.background = `rgb(${red}, ${green}, ${blue})`;
        if (cyc.input.valid && cyc.input.kind === 'real' && cyc.input.frame === fn && cyc.input.row === r && cyc.input.col === c) {
          px.classList.add('current-in');
        }
        if (cyc.output.valid && cyc.output.frame === fn && cyc.output.row === r && cyc.output.col === c) {
          px.classList.add('current-out');
        }
        if (cyc.output.valid && cyc.output.frame === fn) {
          const rr = r - cyc.output.row + cfg.half_r;
          const cc = c - cyc.output.col + cfg.half_c;
          if (rr >= 0 && rr < cfg.kern_rows && cc >= 0 && cc < cfg.kern_cols) px.classList.add('window');
        }
        grid.appendChild(px);
      }
    }
    card.appendChild(grid);
    host.appendChild(card);
  });
}

function renderTimeline() {
  const cfg = currentConfig();
  const host = el('timeline');
  host.innerHTML = '';
  const t = cfg.trace;
  if (!t || !t.c) { host.textContent = 'No trace data.'; return; }
  t.c.forEach((cc, i) => {
    const tick = document.createElement('div');
    tick.className = 'tick';
    tick.dataset.index = String(i);
    tick.onclick = () => { cycleIndex = i; stop(); renderCycleOnly(); };
    const outStr = cc.oi !== undefined
      ? `out f${cc.ofn} (${cc.or},${cc.oc})`
      : 'no output';
    tick.innerHTML = `<span class="mono">#${cc.cy}</span><span>${cc.ph}</span><span>${outStr}</span>`;
    host.appendChild(tick);
  });
}

function updateTimelineCursor() {
  document.querySelectorAll('.tick.active').forEach(node => node.classList.remove('active'));
  const node = document.querySelector(`.tick[data-index="${cycleIndex}"]`);
  if (node) {
    node.classList.add('active');
    node.scrollIntoView({block: 'nearest'});
  }
}

init();

// ---- Stress scenario UI ------------------------------------------------
const stressSelect   = el('stressSelect');
const stressClearBtn = el('stressClearBtn');
const stressOverlay  = el('stressOverlay');
const mainEl = document.querySelector('main');

function buildStressWave(svh, svl, mrh, mrl, cycles) {
  const alwaysV = svh === 0 && svl === 0;
  const alwaysR = mrh === 0 && mrl === 0;
  let sv = '', mr = '', time = 'Cycle: ';
  for (let t = 0; t < cycles; t++) {
    const hi = t < 10 ? t.toString() : (t % 10).toString();
    time += hi;
    if (alwaysV) { sv += '▀'; }
    else { sv += (t % (svh + svl)) < svh ? '▀' : '░'; }
    if (alwaysR) { mr += '▀'; }
    else { mr += (t % (mrh + mrl)) < mrh ? '▀' : '░'; }
  }
  const row = (label, wave, color) =>
    `<div style="display:grid;grid-template-columns:90px 1fr;gap:8px;margin-bottom:6px;align-items:center">` +
    `<span style="color:${color};white-space:nowrap">${label}</span>` +
    `<span style="color:${color};letter-spacing:2px;white-space:nowrap">${wave}</span></div>`;
  return `<div style="white-space:nowrap;overflow-x:auto">`
    + `<div style="display:grid;grid-template-columns:90px 1fr;gap:8px;margin-bottom:4px">` +
    `<span style="color:var(--muted);font-size:11px">       </span>` +
    `<span style="color:var(--muted);font-size:11px">${[...time].join('')}</span></div>`
    + row('s_tvalid', sv, 'var(--green)')
    + row('m_tready', mr, 'var(--amber)')
    + `<div style="margin-top:8px;color:var(--muted);font-size:11px">▀ = HIGH &nbsp; ░ = LOW</div>`
    + '</div>';
}

(DATA.stress || []).forEach((ss, i) => {
  const opt = document.createElement('option');
  opt.value = i;
  opt.textContent = ss.label;
  stressSelect.appendChild(opt);
});

stressSelect.addEventListener('change', () => {
  const val = Number(stressSelect.value);
  if (val < 0) { hideStress(); return; }
  showStress(DATA.stress[val]);
});

stressClearBtn.addEventListener('click', () => {
  stressSelect.value = '-1';
  hideStress();
});

function showStress(ss) {
  el('stressTitle').textContent = `SS${String(ss.id).padStart(3,'0')}: ${ss.kr}×${ss.kc} ${ss.mode} — ${ss.desc}`;
  el('stressSubtitle').textContent = `Stress scenario — golden vectors from base config CFG${String(ss.base_cfg).padStart(3,'0')}`;
  el('stressBase').textContent  = `CFG${String(ss.base_cfg).padStart(3,'0')}`;
  el('stressKernFrame').textContent = `${ss.kr}×${ss.kc} / ${ss.lw}×${ss.fh}`;
  el('stressEdge').textContent = `${ss.mode} / FLUSH=${ss.flush ? 'on' : 'off'}`;
  const svDesc = ss.sv_h === 0 && ss.sv_l === 0 ? 'always high'  : `${ss.sv_h}H ${ss.sv_l}L`;
  const mrDesc = ss.mr_h === 0 && ss.mr_l === 0 ? 'always high' : `${ss.mr_h}H ${ss.mr_l}L`;
  el('stressPattern').textContent = `s_tvalid: ${svDesc} / m_tready: ${mrDesc}`;
  el('stressWave').innerHTML = buildStressWave(ss.sv_h, ss.sv_l, ss.mr_h, ss.mr_l, 48);
  stressOverlay.style.display = 'block';
  stressClearBtn.style.display = 'block';
}

function hideStress() {
  stressOverlay.style.display = 'none';
  stressClearBtn.style.display = 'none';
}
</script>
</body>
</html>
"""


def gen_html(vec_dir, out_path):
    json_data = build_html_json(vec_dir)
    html = HTML_TEMPLATE.replace('__JSON_DATA__', json_data)
    with open(out_path, 'w') as f:
        f.write(html)
    print(f"Wrote {out_path}")


# -------------------------------------------------------------------------
# Main
# -------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Generate conv2d testbench and vectors.')
    parser.add_argument('--html', action='store_true',
                        help='Also generate tb/visualize.html')
    args = parser.parse_args()

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    vec_dir   = os.path.join(repo_root, 'tb', 'vectors')
    tb_path   = os.path.join(repo_root, 'tb', 'conv2d_tb.vhd')
    html_path = os.path.join(repo_root, 'tb', 'visualize.html')

    print(f"Generating vectors for {N} configurations...")
    for i, cfg in enumerate(CONFIGS, start=1):
        prefix = f"c{i:03d}_"
        nin, nout = gen_vectors(cfg, prefix, vec_dir)
        label = cfg_label(cfg)
        print(f"  cfg{i:03d} {label:<60s}  {nin} in / {nout} expected")

    total_flags = N + NS  # 324 base configs + 30 stress scenarios

    header = f"""\
-- conv2d_tb.vhd -- exhaustive testbench ({N} configs + {NS} stress scenarios)
-- MACHINE-GENERATED by scripts/gen_tb.py -- do not hand-edit.
--
-- Configuration matrix: {N} = 9 kernels x 3 edge modes x 2 flush x 6 frame sizes, 8-bit.
-- Stress scenarios: {NS} additional DUTs with varied s_tvalid and m_tready timing patterns.
--
-- Before simulation: copy tb/vectors/c???_{{input,expected}}.txt into the xsim working dir.
-- Recommended run time: 40000 ns (stress scenarios run longer due to timing gaps).

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.textio.all;

entity conv2d_tb is
end entity conv2d_tb;

architecture tb of conv2d_tb is

    constant CLK_PERIOD : time := 10 ns;

    signal clk        : std_logic := '0';
    signal done_flags : std_logic_vector({total_flags}-1 downto 0) := (others => '0');
    signal sim_done   : boolean := false;

"""

    const_blocks  = []
    signal_blocks = []
    inst_blocks   = []
    bp_blocks     = []
    stim_blocks   = []
    check_blocks  = []

    for i, cfg in enumerate(CONFIGS, start=1):
        prefix = f"c{i:03d}_"
        label  = cfg_label(cfg)
        const_blocks .append(gen_constants(i, cfg, prefix))
        signal_blocks.append(gen_signals(i, cfg))
        inst_blocks  .append(gen_instance(i, cfg))
        stim_blocks  .append(gen_stim(i, cfg))
        check_blocks .append(gen_check(i, cfg, label))

    # Back-pressure scenarios.
    for idx_0, desc, delay_cycles, stall_cycles in [
        (_BP_POST_RESET,   "post_reset — m_tready stalled 10 cycles post-reset",   None, 10),
        (_BP_LARGE_KERNEL, "post_reset_last — m_tready stalled 10 cycles post-reset", None, 10),
        (_BP_MID_FLUSH,    "mid_sim — m_tready stalled 15 cycles mid-sim",          210,  15),
    ]:
        if idx_0 is None:
            continue
        n = idx_0 + 1
        sp = f"c{n:03d}"
        if delay_cycles is None:
            bp_blocks.append(f"""\
    -- BP: cfg{n:03d} ({desc})
    p_bp_{n:03d} : process
    begin
        {sp}_mready <= '0';
        wait until {sp}_rst = '0';
        wait for CLK_PERIOD * {stall_cycles};
        {sp}_mready <= '1';
        wait;
    end process p_bp_{n:03d};""")
        else:
            bp_blocks.append(f"""\
    -- BP: cfg{n:03d} ({desc})
    p_bp_{n:03d} : process
    begin
        wait until {sp}_rst = '0';
        wait for CLK_PERIOD * {delay_cycles};
        {sp}_mready <= '0';
        wait for CLK_PERIOD * {stall_cycles};
        {sp}_mready <= '1';
        wait;
    end process p_bp_{n:03d};""")

    # Stress scenario blocks.
    ss_sig_blocks   = []
    ss_inst_blocks  = []
    ss_stim_blocks  = []
    ss_mr_blocks    = []
    ss_check_blocks = []

    for j, sc in enumerate(STRESS_SCENARIOS, start=1):
        kr, kc, mode, flush, lw, fh, sv_h, sv_l, mr_h, mr_l, desc = sc
        base_idx_1  = _stress_base_idx(kr, kc, mode, flush, lw, fh)
        done_offset = N + j - 1  # index into done_flags
        ss_sig_blocks  .append(gen_stress_signals(j, sc))
        ss_inst_blocks .append(gen_stress_instance(j, sc))
        ss_stim_blocks .append(gen_stress_stim(j, sc, base_idx_1))
        mr_proc = gen_stress_mready_proc(j, sc)
        if mr_proc:
            ss_mr_blocks.append(mr_proc)
        ss_check_blocks.append(gen_stress_check(j, sc, base_idx_1, done_offset))

    body = "\n\n".join([
        "    -- Per-DUT configuration constants and file names",
        "\n\n".join(const_blocks),
        "    -- Per-DUT AXI signals",
        "\n\n".join(signal_blocks),
        "    -- Stress scenario AXI signals",
        "\n\n".join(ss_sig_blocks),
    ])

    begin_section = "\n\n".join(filter(None, [
        f"    sim_done <= true when done_flags = (done_flags'range => '1') else false;",
        "    clk <= not clk after CLK_PERIOD / 2 when not sim_done else '0';",
        "    -- DUT instances (base 324 configs)",
        "\n\n".join(inst_blocks),
        "\n\n".join(bp_blocks) if bp_blocks else None,
        "    -- Stress scenario DUT instances",
        "\n\n".join(ss_inst_blocks),
        "    -- Stimulus processes (base configs)",
        "\n\n".join(stim_blocks),
        "    -- Stress scenario stimulus processes",
        "\n\n".join(ss_stim_blocks),
        ("    -- Stress scenario m_tready pattern processes\n\n" + "\n\n".join(ss_mr_blocks)) if ss_mr_blocks else None,
        "    -- Checker processes (base configs)",
        "\n\n".join(check_blocks),
        "    -- Stress scenario checker processes",
        "\n\n".join(ss_check_blocks),
    ]))

    vhdl = (
        header
        + body
        + "\n\nbegin\n\n"
        + begin_section
        + "\n\nend architecture tb;\n"
    )

    with open(tb_path, 'w') as f:
        f.write(vhdl)

    print(f"\nWrote {tb_path}  ({N} base configs, {len(bp_blocks)} BP scenarios, {NS} stress scenarios)")
    print(f"Recommended simulation run time: 40000 ns")

    if args.html:
        gen_html(vec_dir, html_path)


if __name__ == '__main__':
    main()
