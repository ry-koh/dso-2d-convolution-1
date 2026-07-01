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

        tor_max_pos = half_r * lw + half_c

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
                # FLUSH=false: outputs begin at global index TOR_MAX_POS.
                # Each frame produces FRAME_PIXELS outputs once warm.
                for fn_out in range(nf):
                    fn_start = fn_out * frame_pixels
                    for n in range(frame_pixels):
                        center_g = fn_start + n
                        if center_g < tor_max_pos:
                            continue
                        taps = compute_taps_toroidal(
                            global_stream, center_g, kr, kc, lw, fh, flush=False)
                        all_taps.append(taps)

        else:
            # STREAM_DIRECT non-TOROIDAL (FH <= HALF_R, FLUSH=false).
            # RTL uses tor_buf but applies ZERO/REPLICATE per-frame OOB handling.
            # Golden: same as compute_taps per-frame, skipping warmup positions.
            for fn_out in range(nf):
                fn_start = fn_out * frame_pixels
                for n in range(frame_pixels):
                    center_g = fn_start + n
                    if center_g < tor_max_pos:
                        continue
                    center_row = n // lw
                    center_col = n % lw
                    taps = compute_taps(
                        frames[fn_out], center_row, center_col,
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

_BP_POST_RESET   = _find(3, 3, "ZERO",  False, 8, 8)   # 3x3 ZERO FLUSH=off post-reset BP
_BP_LARGE_KERNEL = _find(7, 7, "ZERO",  False, 8, 8)   # 7x7 ZERO FLUSH=off large-kernel BP
_BP_MID_FLUSH    = _find(3, 3, "ZERO",  True,  8, 8)   # 3x3 ZERO FLUSH=on mid-flush BP

BP_INFO = {}
for idx, info in [
    (_BP_POST_RESET,   {'type': 'post_reset', 'stall_cycles': 10,
                        'note': 'Back-pressure: m_tready(0)=0 for 10 cycles post-reset'}),
    (_BP_LARGE_KERNEL, {'type': 'post_reset', 'stall_cycles': 10,
                        'note': 'Back-pressure: m_tready(NUM_TAPS-1)=0 for 10 cycles post-reset'}),
    (_BP_MID_FLUSH,    {'type': 'mid_sim', 'start_cycle': 210, 'stall_cycles': 15,
                        'note': 'Back-pressure: m_tready(0)=0 for 15 cycles mid-flush'}),
]:
    if idx is not None:
        BP_INFO[idx] = info


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
    signal {p}_mready : std_logic_vector({nt}-1 downto 0) := (others => '1');
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
            wait until rising_edge(clk) and {sp}_mvalid = '1' and (and {sp}_mready) = '1';
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
# HTML visualiser
# -------------------------------------------------------------------------

def cfg_label_html(c):
    lw, fh = c['line_width'], c['frame_height']
    fs = f" {lw}×{fh}" if (lw, fh) != (8, 8) else ""
    fl = "FLUSH=on" if c['flush'] else "FLUSH=off"
    return f"{c['data_width']}b {c['kern_rows']}×{c['kern_cols']} {c['edge_mode']} {fl}{fs}"


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

        cs.append({
            'id':           idx + 1,
            'kern_rows':    kr,
            'kern_cols':    kc,
            'half_r':       half_r,
            'half_c':       half_c,
            'edge_mode':    mode,
            'flush':        flush,
            'streaming':    streaming,
            'line_width':   lw,
            'frame_height': fh,
            'num_frames':   nf,
            'data_width':   dw,
            'label':        cfg_label_html(cfg),
            'frames':       frames_data,
            'expected':     expected_data,
            'bp':           BP_INFO.get(idx),
        })
    return json.dumps({'configs': cs}, separators=(',', ':'))


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>conv2d Golden Vector Visualiser</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#12121f;--bg2:#1a1a2e;--bg3:#141428;
  --bdr:#252540;--text:#e0e0e0;--dim:#6a7a8a;
  --blue:#3a8fff;--green:#44bb66;--amber:#dfb050;--cyan:#33bbbb;--red:#e05050;
}
body{font-family:system-ui,sans-serif;display:flex;height:100vh;overflow:hidden;
     background:var(--bg);color:var(--text);font-size:13px}
#sidebar{width:210px;min-width:160px;background:var(--bg2);display:flex;flex-direction:column;
  padding:8px 6px;gap:5px;overflow-y:auto;border-right:1px solid var(--bdr);flex-shrink:0}
#sidebar h2{font-size:10px;text-transform:uppercase;letter-spacing:1.5px;color:var(--dim);
  padding-bottom:4px;border-bottom:1px solid var(--bdr)}
.fg{display:flex;flex-direction:column;gap:2px}
.fg>span{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.5px}
.fg select{padding:3px 6px;border-radius:4px;border:1px solid var(--bdr);
  background:#0e1428;color:#c0d0e0;font-size:12px;cursor:pointer}
#cfg-count{font-size:10px;color:var(--dim);text-align:right}
#config-list{flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:1px}
.ci{padding:4px 7px;border-radius:4px;cursor:pointer;font-size:11px;line-height:1.5;
    border-left:3px solid transparent}
.ci:hover{background:#1e2840}.ci.active{background:#1e3a7a;border-left-color:var(--blue)}
.ci.hidden{display:none}
.cn{font-weight:700;color:#5a9fff;font-size:10px}.ci.active .cn{color:#90c0ff}
#main{flex:1;overflow:auto;padding:16px;display:flex;flex-direction:column;gap:16px}
h1{font-size:18px;color:#90c0ff}
.info-row{display:flex;gap:10px;flex-wrap:wrap}
.badge{padding:3px 8px;border-radius:3px;font-size:11px;font-weight:700;
  background:#1e3a7a;color:#90c0ff;border:1px solid #3a6aaa}
.section{background:var(--bg2);border:1px solid var(--bdr);border-radius:6px;padding:12px}
.section h3{font-size:12px;text-transform:uppercase;color:var(--dim);margin-bottom:10px}
.frames-row{display:flex;gap:10px;flex-wrap:wrap}
.frame-card{border:1px solid var(--bdr);border-radius:4px;padding:8px;background:var(--bg3)}
.frame-card h4{font-size:11px;color:var(--dim);margin-bottom:6px}
.pixel-grid{display:grid;gap:2px}
.px{display:flex;align-items:center;justify-content:center;
    border-radius:2px;cursor:pointer;border:1px solid transparent}
.px:hover{border-color:var(--amber)}.px.selected{border-color:var(--amber);outline:2px solid var(--amber)}
.tap-matrix{display:grid;gap:2px;margin-top:4px}
.tap-cell{width:40px;height:28px;display:flex;align-items:center;justify-content:center;
    font-size:11px;border-radius:2px;background:#0e1428;border:1px solid var(--bdr)}
.tap-cell.center{border-color:var(--amber);background:#1e2810}
.tap-cell.oob{color:var(--dim)}
.tap-axis{width:40px;height:28px;display:flex;align-items:center;justify-content:center;
    font-size:10px;color:var(--dim)}
#expected-table{width:100%;border-collapse:collapse;font-size:11px;font-family:monospace}
#expected-table th,#expected-table td{border:1px solid var(--bdr);padding:3px 6px;text-align:left}
#expected-table th{background:#1e2840;color:var(--dim)}
#expected-table tr:hover td{background:#1e2840}
</style>
</head>
<body>
<div id="sidebar">
  <h2>conv2d Visualiser</h2>
  <div class="fg"><span>Kernel</span>
    <select id="kernelSel" onchange="filterList()"></select></div>
  <div class="fg"><span>Edge Mode</span>
    <select id="edgeSel" onchange="filterList()">
      <option value="">All</option>
      <option>ZERO</option><option>REPLICATE</option><option>TOROIDAL</option>
    </select></div>
  <div class="fg"><span>Flush</span>
    <select id="flushSel" onchange="filterList()">
      <option value="">All</option><option>off</option><option>on</option>
    </select></div>
  <div class="fg"><span>Frame size</span>
    <select id="frameSel" onchange="filterList()"></select></div>
  <div id="cfg-count"></div>
  <hr style="border-color:var(--bdr);margin:2px 0">
  <div id="config-list"></div>
</div>
<div id="main">
  <h1 id="cfg-title">Select a configuration</h1>
  <div class="info-row" id="info-row"></div>
  <div style="display:flex;gap:16px;flex-wrap:wrap">
    <div class="section" style="flex:0 0 auto">
      <h3>Frames <span style="font-size:10px;text-transform:none;color:var(--dim)">(click pixel to inspect tap window)</span></h3>
      <div class="frames-row" id="frames-row"></div>
    </div>
    <div class="section" style="flex:0 0 auto">
      <h3>Tap window</h3>
      <div id="tap-display"><p style="color:var(--dim)">Click a pixel above.</p></div>
    </div>
  </div>
  <div class="section">
    <h3>Expected output vectors &mdash; <span id="exp-count">0</span> outputs</h3>
    <div style="max-height:300px;overflow:auto">
      <table id="expected-table">
        <thead id="exp-thead"></thead>
        <tbody id="exp-tbody"></tbody>
      </table>
    </div>
  </div>
</div>
<script>
const DATA = __JSON_DATA__;
let cfgIndex = 0;

function el(id){return document.getElementById(id);}

function init(){
  const kernels=[...new Set(DATA.configs.map(c=>`${c.kern_rows}x${c.kern_cols}`))];
  const frames=[...new Set(DATA.configs.map(c=>`${c.line_width}x${c.frame_height}`))];
  populate(el('kernelSel'),['All',...kernels]);
  populate(el('frameSel'),['All',...frames]);
  buildList();
  selectCfg(0);
}

function populate(sel,vals){
  sel.innerHTML='';
  vals.forEach(v=>{const o=document.createElement('option');o.value=v==='All'?'':v;o.textContent=v;sel.appendChild(o);});
}

function filterList(){
  const k=el('kernelSel').value,e=el('edgeSel').value,fl=el('flushSel').value,fr=el('frameSel').value;
  let vis=0;
  document.querySelectorAll('.ci').forEach(node=>{
    const c=DATA.configs[+node.dataset.idx];
    const show=(k?`${c.kern_rows}x${c.kern_cols}`===k:true)&&(e?c.edge_mode===e:true)
              &&(fl?(c.flush?'on':'off')===fl:true)&&(fr?`${c.line_width}x${c.frame_height}`===fr:true);
    node.classList.toggle('hidden',!show);
    if(show)vis++;
  });
  el('cfg-count').textContent=`${vis} / ${DATA.configs.length} shown`;
}

function buildList(){
  const list=el('config-list');list.innerHTML='';
  DATA.configs.forEach((c,i)=>{
    const d=document.createElement('div');d.className='ci';d.dataset.idx=i;
    d.innerHTML=`<span class="cn">CFG${String(c.id).padStart(3,'0')}</span><br>${c.label}`;
    d.onclick=()=>selectCfg(i);
    list.appendChild(d);
  });
  filterList();
}

function selectCfg(idx){
  cfgIndex=idx;
  document.querySelectorAll('.ci').forEach(n=>n.classList.toggle('active',+n.dataset.idx===idx));
  const c=DATA.configs[idx];
  el('cfg-title').textContent=`CFG${String(c.id).padStart(3,'0')} — ${c.label}`;
  const badges=[c.streaming?'streaming':'flush-mode',c.bp?'back-pressure':null].filter(Boolean);
  el('info-row').innerHTML=badges.map(b=>`<span class="badge">${b}</span>`).join('');
  renderFrames(c);
  renderExpected(c);
  el('tap-display').innerHTML='<p style="color:var(--dim)">Click a pixel above.</p>';
}

function renderFrames(c){
  const host=el('frames-row');host.innerHTML='';
  const ps=c.line_width>12?14:c.line_width>8?18:24;
  c.frames.forEach((frame,fn)=>{
    const card=document.createElement('div');card.className='frame-card';
    card.innerHTML=`<h4>Frame ${fn} &mdash; ${c.line_width}×${c.frame_height}</h4>`;
    const grid=document.createElement('div');grid.className='pixel-grid';
    grid.style.gridTemplateColumns=`repeat(${c.line_width},${ps}px)`;
    frame.forEach((row,r)=>row.forEach((val,col)=>{
      const px=document.createElement('div');px.className='px';
      px.style.width=px.style.height=`${ps}px`;
      px.style.fontSize=`${Math.max(7,ps-13)}px`;
      const norm=val/((1<<Math.min(c.data_width,16))-1);
      px.style.background=`rgb(${Math.round(10+norm*58)},${Math.round(33+norm*188)},${Math.round(32+norm*158)})`;
      px.title=`Frame ${fn} (${r},${col}) = ${val}`;
      px.textContent=ps>=18?val:'';
      px.onclick=()=>{
        document.querySelectorAll('.px.selected').forEach(n=>n.classList.remove('selected'));
        px.classList.add('selected');
        showTaps(c,fn,r,col);
      };
      grid.appendChild(px);
    }));
    card.appendChild(grid);host.appendChild(card);
  });
}

function showTaps(c,fn,r,col){
  const frame=c.frames[fn];
  const host=el('tap-display');host.innerHTML='';
  const title=document.createElement('p');
  title.style.cssText='font-size:11px;color:var(--dim);margin-bottom:6px';
  title.textContent=`Frame ${fn} (${r}, ${col}) = ${frame[r][col]}`;
  host.appendChild(title);
  const matrix=document.createElement('div');
  matrix.className='tap-matrix';
  matrix.style.gridTemplateColumns=`repeat(${c.kern_cols+1},40px)`;
  const ax=document.createElement('div');ax.className='tap-axis';ax.textContent='tap';matrix.appendChild(ax);
  for(let tc=0;tc<c.kern_cols;tc++){const a=document.createElement('div');a.className='tap-axis';a.textContent=`c${tc}`;matrix.appendChild(a);}
  for(let tr=0;tr<c.kern_rows;tr++){
    const ra=document.createElement('div');ra.className='tap-axis';ra.textContent=`r${tr}`;matrix.appendChild(ra);
    for(let tc=0;tc<c.kern_cols;tc++){
      const sr=r+tr-c.half_r,sc=col+tc-c.half_c;
      const cell=document.createElement('div');cell.className='tap-cell';
      if(tr===c.half_r&&tc===c.half_c)cell.classList.add('center');
      let val;
      if(c.edge_mode==='ZERO'){
        val=(sr>=0&&sr<c.frame_height&&sc>=0&&sc<c.line_width)?frame[sr][sc]:0;
      }else if(c.edge_mode==='REPLICATE'){
        val=frame[Math.max(0,Math.min(sr,c.frame_height-1))][Math.max(0,Math.min(sc,c.line_width-1))];
      }else{
        if(sr>=0&&sr<c.frame_height&&sc>=0&&sc<c.line_width){val=frame[sr][sc];}
        else{val='~';cell.classList.add('oob');}
      }
      cell.textContent=val;matrix.appendChild(cell);
    }
  }
  host.appendChild(matrix);
}

function renderExpected(c){
  const thead=el('exp-thead'),tbody=el('exp-tbody');
  thead.innerHTML='';tbody.innerHTML='';
  el('exp-count').textContent=c.expected.length;
  if(!c.expected.length){
    tbody.innerHTML='<tr><td colspan="2" style="color:var(--dim)">No data — run gen_tb.py first.</td></tr>';return;
  }
  const hdr=document.createElement('tr');
  ['#','taps (tap[0][0] .. tap[KR-1][KC-1])'].forEach(t=>{const th=document.createElement('th');th.textContent=t;hdr.appendChild(th);});
  thead.appendChild(hdr);
  c.expected.forEach((taps,i)=>{
    const tr=document.createElement('tr');
    let td=document.createElement('td');td.textContent=i;tr.appendChild(td);
    td=document.createElement('td');td.style.fontFamily='monospace';td.textContent=taps.join(' ');tr.appendChild(td);
    tbody.appendChild(tr);
  });
}

init();
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

    header = f"""\
-- conv2d_tb.vhd -- exhaustive Phase 4 testbench ({N} configurations)
-- MACHINE-GENERATED by scripts/gen_tb.py -- do not hand-edit.
--
-- Configuration matrix: {N} = 9 kernels x 3 edge modes x 2 flush x 6 frame sizes, 8-bit.
--
-- Before simulation: copy tb/vectors/c???_{{input,expected}}.txt into the xsim working dir.
-- Recommended run time: 20000 ns.

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
        prefix = f"c{i:03d}_"
        label  = cfg_label(cfg)
        const_blocks .append(gen_constants(i, cfg, prefix))
        signal_blocks.append(gen_signals(i, cfg))
        inst_blocks  .append(gen_instance(i, cfg))
        stim_blocks  .append(gen_stim(i, cfg))
        check_blocks .append(gen_check(i, cfg, label))

    # Back-pressure scenarios.
    for idx_0, desc, port_expr, port_rest_expr in [
        (_BP_POST_RESET,   "3x3 ZERO FLUSH=off 8x8 — m_tready(0) stalled 10 cycles post-reset",
         "(0) <= '0'", f"(C{(_BP_POST_RESET+1):03d}_NUM_TAPS-1 downto 1) <= (others => '1')"),
        (_BP_LARGE_KERNEL, "7x7 ZERO FLUSH=off 8x8 — m_tready(NUM_TAPS-1) stalled 10 cycles post-reset",
         f"(C{(_BP_LARGE_KERNEL+1):03d}_NUM_TAPS-1) <= '0'",
         f"(C{(_BP_LARGE_KERNEL+1):03d}_NUM_TAPS-2 downto 0) <= (others => '1')"),
        (_BP_MID_FLUSH,    "3x3 ZERO FLUSH=on 8x8 — m_tready(0) stalled 15 cycles mid-flush",
         None, None),
    ]:
        if idx_0 is None:
            continue
        n = idx_0 + 1
        sp = f"c{n:03d}"
        cp = f"C{n:03d}"
        if desc.startswith("3x3 ZERO FLUSH=on"):
            bp_blocks.append(f"""\
    -- BP: cfg{n:03d} ({desc})
    p_bp_{n:03d} : process
    begin
        wait until {sp}_rst = '0';
        wait for CLK_PERIOD * 210;
        {sp}_mready(0) <= '0';
        wait for CLK_PERIOD * 15;
        {sp}_mready(0) <= '1';
        wait;
    end process p_bp_{n:03d};""")
        else:
            bp_blocks.append(f"""\
    -- BP: cfg{n:03d} ({desc})
    p_bp_{n:03d} : process
    begin
        {sp}_mready{port_expr};
        {sp}_mready{port_rest_expr};
        wait until {sp}_rst = '0';
        wait for CLK_PERIOD * 10;
        {sp}_mready <= (others => '1');
        wait;
    end process p_bp_{n:03d};""")

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

    print(f"\nWrote {tb_path}  ({N} configs, {len(bp_blocks)} BP scenarios)")
    print(f"Recommended simulation run time: 20000 ns")

    if args.html:
        gen_html(vec_dir, html_path)


if __name__ == '__main__':
    main()
