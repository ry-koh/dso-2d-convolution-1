#!/usr/bin/env python3
"""Generate golden vectors for the conv2d Phase 3 testbench.

Fixed parameters (matching Phase 2 base case):
    DATA_WIDTH   = 8
    KERN_ROWS    = 3,  KERN_COLS = 3
    LINE_WIDTH   = 8,  FRAME_HEIGHT = 8
    NUM_FRAMES   = 3
    Edge mode    : zero-extend
    Flush        : off

Tap ordering (matches win_buf / conv2d RTL):
    tap[r][c] where r=0 is the OLDEST row (furthest back in time),
    r=KERN_ROWS-1 is the CURRENT (newest) row.
    Within each row, c=0 is the MOST RECENT pixel (current column),
    c=KERN_COLS-1 is the OLDEST pixel in that row.

    m_tdata bit layout:
        tap[r][c] occupies bits ((r*KERN_COLS+c+1)*DW - 1) downto (r*KERN_COLS+c)*DW
        i.e. tap[0][0] at LSB, tap[2][2] at MSB.

Output files (written to tb/vectors/ relative to the repo root;
run this script from the repo root):
    input_pixels.txt   - one decimal pixel value per line, all frames back-to-back
    expected_taps.txt  - one line per output event: 9 decimal values (tap[0][0]
                         through tap[2][2]) separated by spaces
    metadata.txt       - human-readable summary for debugging
"""

import os

DATA_WIDTH   = 8
KERN_ROWS    = 3
KERN_COLS    = 3
LINE_WIDTH   = 8
FRAME_HEIGHT = 8
NUM_FRAMES   = 3


def make_frame(n: int) -> list:
    """Return a flat list of FRAME_HEIGHT*LINE_WIDTH pixel values."""
    pixels = []
    for r in range(FRAME_HEIGHT):
        for c in range(LINE_WIDTH):
            if n == 0:
                val = (r * LINE_WIDTH + c) % 256        # ramp 0..63
            elif n == 1:
                val = 128                                # constant
            else:
                val = 0xAA if (r + c) % 2 == 0 else 0x55  # checkerboard
            pixels.append(val)
    return pixels


def get_pixel(frame: list, r: int, c: int) -> int:
    """Return pixel(r,c) with zero-extend for out-of-bounds."""
    if r < 0 or c < 0 or r >= FRAME_HEIGHT or c >= LINE_WIDTH:
        return 0
    return frame[r * LINE_WIDTH + c]


def compute_taps(frame: list, row: int, col: int) -> list:
    """Return the 9 expected tap values for output at input position (row, col).

    The output register in conv2d fires one cycle after the pixel is accepted.
    At that point win_buf holds the window that was shifted in when the pixel
    at (row, col) was accepted.  So the expected taps for output event N are
    computed from input pixel N.
    """
    taps = []
    for r in range(KERN_ROWS):
        for c in range(KERN_COLS):
            src_row = row - (KERN_ROWS - 1 - r)   # 0 -> row-(K-1), K-1 -> row
            src_col = col - c                       # 0 -> col,       K-1 -> col-(K-1)
            taps.append(get_pixel(frame, src_row, src_col))
    return taps


def main():
    out_dir = os.path.join('tb', 'vectors')
    os.makedirs(out_dir, exist_ok=True)

    all_pixels = []
    all_taps   = []

    for fn in range(NUM_FRAMES):
        frame = make_frame(fn)
        all_pixels.extend(frame)
        for row in range(FRAME_HEIGHT):
            for col in range(LINE_WIDTH):
                all_taps.append(compute_taps(frame, row, col))

    # input_pixels.txt: one decimal integer per line
    with open(os.path.join(out_dir, 'input_pixels.txt'), 'w') as f:
        for p in all_pixels:
            f.write(f'{p}\n')

    # expected_taps.txt: 9 space-separated decimal integers per line
    # order: tap[0][0] tap[0][1] tap[0][2] tap[1][0] ... tap[2][2]
    with open(os.path.join(out_dir, 'expected_taps.txt'), 'w') as f:
        for taps in all_taps:
            f.write(' '.join(str(t) for t in taps) + '\n')

    # metadata.txt: human-readable for debugging
    with open(os.path.join(out_dir, 'metadata.txt'), 'w') as f:
        f.write(f'DATA_WIDTH   = {DATA_WIDTH}\n')
        f.write(f'KERN_ROWS    = {KERN_ROWS}\n')
        f.write(f'KERN_COLS    = {KERN_COLS}\n')
        f.write(f'LINE_WIDTH   = {LINE_WIDTH}\n')
        f.write(f'FRAME_HEIGHT = {FRAME_HEIGHT}\n')
        f.write(f'NUM_FRAMES   = {NUM_FRAMES}\n')
        f.write(f'Total input pixels  : {len(all_pixels)}\n')
        f.write(f'Total output events : {len(all_taps)}\n')
        f.write(f'\nFirst 3 input pixels: {all_pixels[:3]}\n')
        f.write(f'First expected taps:\n')
        for i, t in enumerate(all_taps[:4]):
            f.write(f'  output {i} (row={i//LINE_WIDTH}, col={i%LINE_WIDTH}): {t}\n')

    print(f'Written to {out_dir}/')
    print(f'  input_pixels.txt  : {len(all_pixels)} lines')
    print(f'  expected_taps.txt : {len(all_taps)} lines')
    print(f'  metadata.txt      : parameter summary')


if __name__ == '__main__':
    main()
