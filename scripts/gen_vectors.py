#!/usr/bin/env python3
"""Generate golden vectors for the conv2d Phase 4 testbench.

Usage:
    python gen_vectors.py [options]

All parameters default to the Phase 3 base case (3x3, 8-bit, 8x8, ZERO, no flush).

Tap ordering (matches win_buf / conv2d RTL):
    tap[r][c] where r=0 is the OLDEST row (furthest back in time),
    r=KERN_ROWS-1 is the CURRENT (newest) row.
    Within each row, c=0 is the MOST RECENT pixel (current column),
    c=KERN_COLS-1 is the OLDEST pixel in that row.

    m_tdata bit layout:
        tap[r][c] occupies bits ((r*KERN_COLS+c+1)*DW - 1) downto (r*KERN_COLS+c)*DW
        i.e. tap[0][0] at LSB.

Output files (written to tb/vectors/ relative to the repo root;
run this script from the repo root):
    input_pixels.txt   - one decimal pixel value per line, all frames back-to-back
    expected_taps.txt  - one line per output event: KERN_ROWS*KERN_COLS decimal
                         values (tap[0][0] through tap[KR-1][KC-1]) separated by spaces
    metadata.txt       - human-readable summary for debugging
"""

import argparse
import os


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--data-width',   type=int, default=8,       help='Pixel bit width')
    p.add_argument('--kern-rows',    type=int, default=3,       help='Kernel rows')
    p.add_argument('--kern-cols',    type=int, default=3,       help='Kernel columns')
    p.add_argument('--line-width',   type=int, default=8,       help='Image width in pixels')
    p.add_argument('--frame-height', type=int, default=8,       help='Image height in pixels')
    p.add_argument('--num-frames',   type=int, default=3,       help='Number of frames to generate')
    p.add_argument('--edge-mode',    type=str, default='ZERO',
                   choices=['ZERO', 'REPLICATE', 'TOROIDAL'],   help='Edge handling mode')
    p.add_argument('--flush',        action='store_true',        help='Model FLUSH=true pipeline')
    p.add_argument('--out-dir',      type=str, default=os.path.join('tb', 'vectors'),
                   help='Output directory')
    p.add_argument('--prefix',       type=str, default='',
                   help='Filename prefix for output files (e.g. "c1_" -> c1_input.txt)')
    return p.parse_args()


def make_frame(n, frame_height, line_width):
    """Return a flat list of FRAME_HEIGHT*LINE_WIDTH pixel values."""
    pixels = []
    for r in range(frame_height):
        for c in range(line_width):
            if n == 0:
                val = (r * line_width + c) % 256
            elif n == 1:
                val = 128
            else:
                val = 0xAA if (r + c) % 2 == 0 else 0x55
            pixels.append(val)
    return pixels


def get_pixel(frame, r, c, frame_height, line_width, edge_mode):
    """Return pixel(r, c) with edge handling."""
    if 0 <= r < frame_height and 0 <= c < line_width:
        return frame[r * line_width + c]
    if edge_mode == 'ZERO':
        return 0
    elif edge_mode == 'REPLICATE':
        rc = max(0, min(r, frame_height - 1))
        cc = max(0, min(c, line_width  - 1))
        return frame[rc * line_width + cc]
    elif edge_mode == 'TOROIDAL':
        rw = r % frame_height
        cw = c % line_width
        return frame[rw * line_width + cw]
    return 0


def compute_taps(frame, out_r, out_c, kern_rows, kern_cols, frame_height, line_width, edge_mode):
    """Centred window: tap[tr][tc] = pixel at (out_r+tr-half_r, out_c+tc-half_c)."""
    half_r = (kern_rows - 1) // 2
    half_c = (kern_cols - 1) // 2
    taps = []
    for tr in range(kern_rows):
        for tc in range(kern_cols):
            src_row = out_r + tr - half_r
            src_col = out_c + tc - half_c
            taps.append(get_pixel(frame, src_row, src_col, frame_height, line_width, edge_mode))
    return taps


def compute_taps_toroidal(push_history, kr, kc, eff_width):
    """Causal TOROIDAL: offset = (kr-1-tr)*eff_width + (kc-1-tc); returns 0 if OOB."""
    taps = []
    n = len(push_history)
    for tr in range(kr):
        for tc in range(kc):
            offset = (kr - 1 - tr) * eff_width + (kc - 1 - tc)
            idx = n - 1 - offset
            taps.append(push_history[idx] if idx >= 0 else 0)
    return taps


def main():
    args = parse_args()

    DW   = args.data_width
    KR   = args.kern_rows
    KC   = args.kern_cols
    LW   = args.line_width
    FH   = args.frame_height
    NF   = args.num_frames
    MODE = args.edge_mode

    os.makedirs(args.out_dir, exist_ok=True)
    PFX = args.prefix

    HALF_R    = (KR - 1) // 2
    HALF_C    = (KC - 1) // 2
    EFF_WIDTH = LW + HALF_C

    all_pixels   = []
    all_taps     = []
    push_history = []   # every push including dummy-col zeros (for TOROIDAL causal model)

    for fn in range(NF):
        frame = make_frame(fn, FH, LW)
        all_pixels.extend(frame)

        for row in range(FH):
            for col_eff in range(EFF_WIDTH):
                pix = frame[row * LW + col_eff] if col_eff < LW else 0
                push_history.append(pix)
                if col_eff >= HALF_C and row >= HALF_R:
                    out_r = row - HALF_R
                    out_c = col_eff - HALF_C
                    if MODE == 'TOROIDAL':
                        all_taps.append(compute_taps_toroidal(push_history, KR, KC, EFF_WIDTH))
                    else:
                        all_taps.append(compute_taps(frame, out_r, out_c, KR, KC, FH, LW, MODE))

        if args.flush and HALF_R > 0:
            for flush_row in range(HALF_R):
                virtual_r = FH + flush_row
                out_r = virtual_r - HALF_R
                for col_eff in range(EFF_WIDTH):
                    push_history.append(0)
                    # out_r < 0 when FH < HALF_R: RTL suppresses valid_out_d1.
                    if col_eff >= HALF_C and out_r >= 0:
                        out_c = col_eff - HALF_C
                        if MODE == 'TOROIDAL':
                            all_taps.append(compute_taps_toroidal(push_history, KR, KC, EFF_WIDTH))
                        else:
                            all_taps.append(compute_taps(frame, out_r, out_c, KR, KC, FH, LW, MODE))

    # input pixels: one decimal integer per line (real frame pixels only)
    input_file = os.path.join(args.out_dir, f'{PFX}input.txt')
    with open(input_file, 'w') as f:
        for p in all_pixels:
            f.write(f'{p}\n')

    # expected taps: KR*KC space-separated decimal integers per line
    expected_file = os.path.join(args.out_dir, f'{PFX}expected.txt')
    with open(expected_file, 'w') as f:
        for taps in all_taps:
            f.write(' '.join(str(t) for t in taps) + '\n')

    # metadata
    with open(os.path.join(args.out_dir, f'{PFX}metadata.txt'), 'w') as f:
        f.write(f'DATA_WIDTH   = {DW}\n')
        f.write(f'KERN_ROWS    = {KR}\n')
        f.write(f'KERN_COLS    = {KC}\n')
        f.write(f'LINE_WIDTH   = {LW}\n')
        f.write(f'FRAME_HEIGHT = {FH}\n')
        f.write(f'NUM_FRAMES   = {NF}\n')
        f.write(f'EDGE_MODE    = {MODE}\n')
        f.write(f'FLUSH        = {args.flush}\n')
        f.write(f'Total input pixels  : {len(all_pixels)}\n')
        f.write(f'Total output events : {len(all_taps)}\n')

    print(f'Written to {args.out_dir}/')
    print(f'  {os.path.basename(input_file)}    : {len(all_pixels)} lines')
    print(f'  {os.path.basename(expected_file)} : {len(all_taps)} lines')


if __name__ == '__main__':
    main()
