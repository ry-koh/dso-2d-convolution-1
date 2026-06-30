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


def compute_taps(frame, row, col, kern_rows, kern_cols, frame_height, line_width, edge_mode):
    """Return the expected tap values for the output produced when input pixel (row, col)
    is accepted.  The output fires one cycle after acceptance; win_buf holds the window
    that was shifted in at that pixel.

    Tap ordering:
        tap[r][c]: src_row = row - (KERN_ROWS-1-r),  src_col = col - c
    """
    taps = []
    for r in range(kern_rows):
        for c in range(kern_cols):
            src_row = row - (kern_rows - 1 - r)
            src_col = col - c
            taps.append(get_pixel(frame, src_row, src_col,
                                  frame_height, line_width, edge_mode))
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

    all_pixels = []
    all_taps   = []

    for fn in range(NF):
        frame = make_frame(fn, FH, LW)
        all_pixels.extend(frame)
        for row in range(FH):
            for col in range(LW):
                all_taps.append(compute_taps(frame, row, col, KR, KC, FH, LW, MODE))

        # When FLUSH=true, KERN_ROWS-1 dummy zero-rows are injected after each
        # frame.  Each dummy row produces an output window; the Python model
        # computes those windows by treating the dummy pixels as row (FH) onwards.
        if args.flush and KR > 1:
            dummy_frame = frame  # real pixel data still in BRAM for older rows
            for flush_row in range(1, KR):   # rows FH..FH+KR-2 (zero-filled)
                for col in range(LW):
                    # During flush, push_data=0 feeds row (FH + flush_row - 1)
                    # The RTL's row_cnt advances past FRAME_HEIGHT into flush rows,
                    # so src_row for the flush output is (FH - 1 + flush_row) - (KR-1-r)
                    virtual_row = FH - 1 + flush_row
                    taps = []
                    for r in range(KR):
                        for c in range(KC):
                            src_row = virtual_row - (KR - 1 - r)
                            src_col = col - c
                            if src_row >= FH:
                                # Dummy zero row
                                taps.append(0)
                            else:
                                taps.append(get_pixel(dummy_frame, src_row, src_col,
                                                      FH, LW, MODE))
                    all_taps.append(taps)
                # Dummy zero pixels for this flush row (not written to input file —
                # the RTL generates them internally, not from the AXI stream)

    # input_pixels.txt: one decimal integer per line (real frame pixels only)
    with open(os.path.join(args.out_dir, 'input_pixels.txt'), 'w') as f:
        for p in all_pixels:
            f.write(f'{p}\n')

    # expected_taps.txt: KR*KC space-separated decimal integers per line
    with open(os.path.join(args.out_dir, 'expected_taps.txt'), 'w') as f:
        for taps in all_taps:
            f.write(' '.join(str(t) for t in taps) + '\n')

    # metadata.txt
    with open(os.path.join(args.out_dir, 'metadata.txt'), 'w') as f:
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
    print(f'  input_pixels.txt  : {len(all_pixels)} lines')
    print(f'  expected_taps.txt : {len(all_taps)} lines')
    print(f'  metadata.txt      : parameter summary')


if __name__ == '__main__':
    main()
