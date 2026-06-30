#!/usr/bin/env python3
"""
Visualise conv2d golden vectors.

Usage:
    python scripts/visualize.py --config 4
    python scripts/visualize.py --config 4 --frame 1 --row 3 --col 4
    python scripts/visualize.py --list

Controls (interactive mode):
    Click any cell in the frame grid  — move kernel window to that pixel
    Left / Right arrow keys           — previous / next frame
    < / >  (comma / period)           — previous / next config

Reads tb/vectors/c??_input.txt and tb/vectors/c??_expected.txt.
Run from the repo root.
"""

import argparse
import itertools
import os
import sys

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

# ---------------------------------------------------------------------------
# Configuration table (mirrors gen_tb.py exactly)
# ---------------------------------------------------------------------------

DATA_WIDTHS  = [8, 16]
KERNEL_SIZES = [(3, 3), (5, 5), (3, 5)]
EDGE_MODES   = ["ZERO", "REPLICATE", "TOROIDAL"]
FLUSH_VALUES = [False, True]
LINE_WIDTH   = 8
FRAME_HEIGHT = 8
NUM_FRAMES   = 3

CONFIGS = []
for dw, (kr, kc), mode, flush in itertools.product(
        DATA_WIDTHS, KERNEL_SIZES, EDGE_MODES, FLUSH_VALUES):
    CONFIGS.append({
        'data_width':   dw,
        'kern_rows':    kr,
        'kern_cols':    kc,
        'line_width':   LINE_WIDTH,
        'frame_height': FRAME_HEIGHT,
        'num_frames':   NUM_FRAMES,
        'edge_mode':    mode,
        'flush':        flush,
    })

N = len(CONFIGS)


def cfg_label(cfg):
    kr, kc = cfg['kern_rows'], cfg['kern_cols']
    flush_str = "FLUSH=on" if cfg['flush'] else "FLUSH=off"
    return f"{cfg['data_width']}b {kr}x{kc} {cfg['edge_mode']} {flush_str}"


# ---------------------------------------------------------------------------
# Vector loading
# ---------------------------------------------------------------------------

def load_vectors(cfg_idx, vec_dir):
    """Return (frames, taps_per_frame, flush_taps_per_frame)."""
    cfg = CONFIGS[cfg_idx]
    prefix = f"c{cfg_idx+1:02d}_"
    lw, fh, nf = cfg['line_width'], cfg['frame_height'], cfg['num_frames']
    kr, kc    = cfg['kern_rows'], cfg['kern_cols']
    flush      = cfg['flush']

    in_path  = os.path.join(vec_dir, prefix + "input.txt")
    exp_path = os.path.join(vec_dir, prefix + "expected.txt")

    for p in (in_path, exp_path):
        if not os.path.exists(p):
            sys.exit(f"File not found: {p}\nRun scripts/gen_tb.py first.")

    # Input pixels — fh*lw per frame
    pixels = []
    with open(in_path) as f:
        for line in f:
            line = line.strip()
            if line:
                pixels.append(int(line))

    frames = []
    for fn in range(nf):
        start = fn * fh * lw
        arr = np.array(pixels[start:start + fh * lw], dtype=np.int32)
        frames.append(arr.reshape(fh, lw))

    # Expected taps — one line per output event
    all_taps = []
    with open(exp_path) as f:
        for line in f:
            line = line.strip()
            if line:
                all_taps.append(list(map(int, line.split())))

    # Partition into per-frame real taps and flush taps
    real_per_frame  = fh * lw
    flush_per_frame = (kr - 1) * lw if (flush and kr > 1) else 0
    total_per_frame = real_per_frame + flush_per_frame

    frame_taps  = []   # shape [nf][fh][lw][kr*kc]
    flush_taps  = []   # shape [nf][kr-1][lw][kr*kc]  (empty if flush=False)

    for fn in range(nf):
        base = fn * total_per_frame
        real_block = all_taps[base : base + real_per_frame]
        ft = []
        for r in range(fh):
            row_taps = []
            for c in range(lw):
                row_taps.append(real_block[r * lw + c])
            ft.append(row_taps)
        frame_taps.append(ft)

        if flush and kr > 1:
            flush_block = all_taps[base + real_per_frame : base + total_per_frame]
            fft = []
            for fr in range(kr - 1):
                frow = []
                for c in range(lw):
                    frow.append(flush_block[fr * lw + c])
                fft.append(frow)
            flush_taps.append(fft)
        else:
            flush_taps.append([])

    return frames, frame_taps, flush_taps


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def draw_frame_grid(ax, frame, highlight_row, highlight_col, cfg, title):
    """Draw the input frame as a colour-coded grid with cell value labels."""
    fh, lw = frame.shape
    dw = cfg['data_width']
    vmax = (1 << dw) - 1

    ax.clear()
    ax.set_title(title, fontsize=10, pad=6)
    ax.set_xlim(-0.5, lw - 0.5)
    ax.set_ylim(fh - 0.5, -0.5)   # row 0 at top
    ax.set_xticks(range(lw))
    ax.set_yticks(range(fh))
    ax.set_xlabel("col")
    ax.set_ylabel("row")
    ax.tick_params(length=0)

    # Colour cells
    cmap = plt.get_cmap("YlOrRd")
    for r in range(fh):
        for c in range(lw):
            val  = int(frame[r, c])
            norm = val / vmax if vmax > 0 else 0
            colour = cmap(norm)
            rect = mpatches.FancyBboxPatch(
                (c - 0.48, r - 0.48), 0.96, 0.96,
                boxstyle="round,pad=0.02", linewidth=0,
                facecolor=colour, zorder=1)
            ax.add_patch(rect)
            brightness = 0.299*colour[0] + 0.587*colour[1] + 0.114*colour[2]
            txt_colour = "black" if brightness > 0.5 else "white"
            hex_str = f"0x{val:0{(dw+3)//4}X}" if dw > 8 else f"{val}"
            ax.text(c, r, hex_str, ha="center", va="center",
                    fontsize=7, color=txt_colour, zorder=3)

    # Kernel footprint highlight
    kr, kc = cfg['kern_rows'], cfg['kern_cols']
    top  = highlight_row - (kr - 1)
    left = highlight_col - (kc - 1)
    for dr in range(kr):
        for dc in range(kc):
            pr, pc = top + dr, left + dc
            if 0 <= pr < fh and 0 <= pc < lw:
                alpha = 0.35
            else:
                alpha = 0.0   # OOB — show nothing extra
            rect = mpatches.FancyBboxPatch(
                (pc - 0.48, pr - 0.48), 0.96, 0.96,
                boxstyle="round,pad=0.02", linewidth=0,
                facecolor="royalblue", alpha=alpha, zorder=2)
            ax.add_patch(rect)

    # Selected pixel marker
    circle = plt.Circle((highlight_col, highlight_row), 0.3,
                         color="royalblue", fill=False, linewidth=2, zorder=4)
    ax.add_patch(circle)
    ax.grid(False)


def draw_tap_window(ax, taps, cfg, row, col):
    """Draw the KERN_ROWS x KERN_COLS tap window in natural image order.

    Display layout matches the frame grid:
      top-left  = oldest row, leftmost (oldest) col
      bottom-right = current row, current col  (circled)

    RTL tap ordering: tap[r][c] where c=0 is most-recent col.
    Display column dc maps to tap column c = kc-1-dc (flip horizontally).
    """
    kr, kc = cfg['kern_rows'], cfg['kern_cols']
    dw     = cfg['data_width']
    vmax   = (1 << dw) - 1
    mode   = cfg['edge_mode']
    lw_img = cfg['line_width']
    fh_img = cfg['frame_height']

    ax.clear()
    ax.set_title(f"Kernel window at (row={row}, col={col})", fontsize=10, pad=6)
    ax.set_xlim(-0.5, kc - 0.5)
    ax.set_ylim(kr - 0.5, -0.5)   # row 0 at top
    ax.set_xticks(range(kc))
    ax.set_yticks(range(kr))

    # x-axis: display col dc → image col = col - (kc-1-dc)
    x_labels = []
    for dc in range(kc):
        img_col = col - (kc - 1 - dc)
        x_labels.append(str(img_col))
    ax.set_xticklabels(x_labels, fontsize=8)

    # y-axis: display row r → image row = row - (kr-1-r)
    y_labels = []
    for r in range(kr):
        img_row = row - (kr - 1 - r)
        y_labels.append(str(img_row))
    ax.set_yticklabels(y_labels, fontsize=8)

    ax.set_xlabel("image col", fontsize=8)
    ax.set_ylabel("image row", fontsize=8)
    ax.tick_params(length=0)

    cmap = plt.get_cmap("YlOrRd")

    for r in range(kr):
        for dc in range(kc):
            # dc = display column; c = RTL tap column (flipped)
            c       = kc - 1 - dc
            tap_idx = r * kc + c
            val     = taps[tap_idx]
            norm    = val / vmax if vmax > 0 else 0

            src_row = row - (kr - 1 - r)
            src_col = col - c          # = col - (kc-1-dc)
            is_oob  = not (0 <= src_row < fh_img and 0 <= src_col < lw_img)

            # ZERO and TOROIDAL both produce 0 for OOB in a causal
            # streaming pipeline (wrapped pixels from a previous row/frame
            # are not present in the shift-register window).
            # REPLICATE clamps to the nearest valid tap.
            if is_oob and mode in ("ZERO", "TOROIDAL"):
                face       = (0.85, 0.85, 0.85, 1.0)
                edge_color = "steelblue" if mode == "TOROIDAL" else "none"
            elif is_oob and mode == "REPLICATE":
                face       = cmap(norm)
                edge_color = "steelblue"
            else:
                face       = cmap(norm)
                edge_color = "none"

            rect = mpatches.FancyBboxPatch(
                (dc - 0.48, r - 0.48), 0.96, 0.96,
                boxstyle="round,pad=0.02", linewidth=1.5,
                edgecolor=edge_color,
                facecolor=face, zorder=1)
            ax.add_patch(rect)

            brightness = 0.299*face[0] + 0.587*face[1] + 0.114*face[2]
            txt_colour = "black" if brightness > 0.5 else "white"
            hex_str    = f"0x{val:0{(dw+3)//4}X}" if dw > 8 else f"{val}"
            oob_marker = "*" if is_oob else ""
            ax.text(dc, r, f"{hex_str}{oob_marker}",
                    ha="center", va="center",
                    fontsize=8, color=txt_colour, zorder=3)

    # Current pixel = bottom-right of display (r=kr-1, dc=kc-1)
    rect = mpatches.FancyBboxPatch(
        (kc - 1 - 0.48, kr - 1 - 0.48), 0.96, 0.96,
        boxstyle="round,pad=0.02", linewidth=2.5,
        edgecolor="royalblue", facecolor="none", zorder=4)
    ax.add_patch(rect)
    ax.grid(False)

    if mode == "ZERO":
        oob_note = "* = OOB → 0 (zero-extend)"
    elif mode == "REPLICATE":
        oob_note = "* = OOB → clamped to nearest edge pixel"
    else:
        oob_note = "* = OOB → 0 (true wrap needs frame buffer; causal limit)"
    ax.text(0.01, 0.01, oob_note, transform=ax.transAxes,
            fontsize=7, color="grey", va="bottom")


def draw_flush_taps(ax, flush_taps_frame, cfg, flush_row, col):
    """Draw a flush tap window in natural image order (oldest col on left)."""
    kr, kc = cfg['kern_rows'], cfg['kern_cols']
    taps = flush_taps_frame[flush_row][col]
    fh   = cfg['frame_height']
    virtual_row = fh + flush_row

    ax.clear()
    ax.set_title(f"FLUSH kernel window (virtual row={virtual_row}, col={col})", fontsize=10, pad=6)
    ax.set_xlim(-0.5, kc - 0.5)
    ax.set_ylim(kr - 0.5, -0.5)
    ax.set_xticks(range(kc))
    ax.set_yticks(range(kr))

    x_labels = [str(col - (kc - 1 - dc)) for dc in range(kc)]
    y_labels  = [str(virtual_row - (kr - 1 - r)) for r in range(kr)]
    ax.set_xticklabels(x_labels, fontsize=8)
    ax.set_yticklabels(y_labels, fontsize=8)
    ax.set_xlabel("image col", fontsize=8)
    ax.set_ylabel("image row (>=8 = flush zero)", fontsize=8)
    ax.tick_params(length=0)

    dw   = cfg['data_width']
    vmax = (1 << dw) - 1
    cmap = plt.get_cmap("YlOrRd")

    for r in range(kr):
        for dc in range(kc):
            c       = kc - 1 - dc          # RTL tap column (flipped)
            tap_idx = r * kc + c
            val     = taps[tap_idx]
            norm    = val / vmax if vmax > 0 else 0
            src_row = virtual_row - (kr - 1 - r)
            is_zero_row = src_row >= fh

            face = (0.85, 0.85, 0.85, 1.0) if is_zero_row else cmap(norm)
            rect = mpatches.FancyBboxPatch(
                (dc - 0.48, r - 0.48), 0.96, 0.96,
                boxstyle="round,pad=0.02", linewidth=1.5,
                edgecolor="grey" if is_zero_row else "none",
                facecolor=face, zorder=1)
            ax.add_patch(rect)

            brightness = 0.299*face[0] + 0.587*face[1] + 0.114*face[2]
            txt_colour = "black" if brightness > 0.5 else "white"
            hex_str = f"0x{val:0{(dw+3)//4}X}" if dw > 8 else f"{val}"
            ax.text(dc, r, hex_str, ha="center", va="center",
                    fontsize=8, color=txt_colour, zorder=3)

    # Current pixel = bottom-right
    rect = mpatches.FancyBboxPatch(
        (kc - 1 - 0.48, kr - 1 - 0.48), 0.96, 0.96,
        boxstyle="round,pad=0.02", linewidth=2.5,
        edgecolor="royalblue", facecolor="none", zorder=4)
    ax.add_patch(rect)
    ax.grid(False)
    ax.text(0.01, 0.01, "grey = injected zero row", transform=ax.transAxes,
            fontsize=7, color="grey", va="bottom")


# ---------------------------------------------------------------------------
# Interactive viewer
# ---------------------------------------------------------------------------

class Viewer:
    def __init__(self, cfg_idx, init_frame, init_row, init_col, vec_dir):
        self.vec_dir  = vec_dir
        self.cfg_idx  = cfg_idx
        self.frame_n  = init_frame
        self.row      = init_row
        self.col      = init_col
        self.mode     = "real"   # "real" or "flush"
        self.flush_row = 0
        self._load()
        self._build_fig()

    def _load(self):
        self.frames, self.frame_taps, self.flush_taps = load_vectors(
            self.cfg_idx, self.vec_dir)
        self.cfg = CONFIGS[self.cfg_idx]
        # Clamp positions to valid range
        nf = self.cfg['num_frames']
        fh = self.cfg['frame_height']
        lw = self.cfg['line_width']
        self.frame_n   = max(0, min(self.frame_n,   nf - 1))
        self.row       = max(0, min(self.row,        fh - 1))
        self.col       = max(0, min(self.col,        lw - 1))
        self.flush_row = 0
        self.mode      = "real"

    def _build_fig(self):
        self.fig = plt.figure(figsize=(13, 6))
        self.fig.canvas.mpl_connect("button_press_event", self._on_click)
        self.fig.canvas.mpl_connect("key_press_event",    self._on_key)

        # Layout: left = frame, right = tap window, bottom = info bar
        self.ax_frame = self.fig.add_axes([0.03, 0.12, 0.44, 0.80])
        self.ax_tap   = self.fig.add_axes([0.54, 0.12, 0.44, 0.80])
        self.ax_info  = self.fig.add_axes([0.0,  0.0,  1.0,  0.08])
        self.ax_info.axis("off")

        self._draw()
        plt.show()

    def _draw(self):
        cfg  = self.cfg
        fn   = self.frame_n
        fh   = cfg['frame_height']
        kr   = cfg['kern_rows']
        flush = cfg['flush']

        # Frame title
        frame_title = (f"Input frame {fn}  "
                       f"[← → frames | click cell | < > configs]")
        draw_frame_grid(self.ax_frame, self.frames[fn],
                        self.row, self.col, cfg, frame_title)

        # Tap window
        if self.mode == "real":
            taps = self.frame_taps[fn][self.row][self.col]
            draw_tap_window(self.ax_tap, taps, cfg, self.row, self.col)
        else:
            draw_flush_taps(self.ax_tap, self.flush_taps[fn],
                            cfg, self.flush_row, self.col)

        # Info bar
        flush_hint = ""
        if flush and kr > 1:
            flush_hint = f"  |  F = toggle flush rows (fr={self.flush_row})"
        info = (f"CFG{self.cfg_idx+1:02d}: {cfg_label(cfg)}"
                f"  |  frame={fn}/{cfg['num_frames']-1}"
                f"  |  pixel=({self.row},{self.col})"
                f"  |  mode={self.mode}"
                f"{flush_hint}")
        self.ax_info.clear()
        self.ax_info.axis("off")
        self.ax_info.text(0.5, 0.5, info, ha="center", va="center",
                          fontsize=9, transform=self.ax_info.transAxes)

        self.fig.canvas.draw_idle()

    def _on_click(self, event):
        if event.inaxes is not self.ax_frame:
            return
        c = int(round(event.xdata))
        r = int(round(event.ydata))
        fh = self.cfg['frame_height']
        lw = self.cfg['line_width']
        if 0 <= r < fh and 0 <= c < lw:
            self.row  = r
            self.col  = c
            self.mode = "real"
            self._draw()

    def _on_key(self, event):
        cfg   = self.cfg
        nf    = cfg['num_frames']
        fh    = cfg['frame_height']
        lw    = cfg['line_width']
        kr    = cfg['kern_rows']
        flush = cfg['flush']

        if event.key == "right":
            self.frame_n = (self.frame_n + 1) % nf
            self.mode    = "real"
        elif event.key == "left":
            self.frame_n = (self.frame_n - 1) % nf
            self.mode    = "real"
        elif event.key in (",", "<"):
            self.cfg_idx = max(0, self.cfg_idx - 1)
            self._load()
        elif event.key in (".", ">"):
            self.cfg_idx = min(N - 1, self.cfg_idx + 1)
            self._load()
        elif event.key == "up":
            self.row = max(0, self.row - 1)
            self.mode = "real"
        elif event.key == "down":
            self.row = min(fh - 1, self.row + 1)
            self.mode = "real"
        elif event.key == "f" and flush and kr > 1:
            if self.mode == "flush":
                self.flush_row = (self.flush_row + 1) % (kr - 1)
            else:
                self.mode      = "flush"
                self.flush_row = 0
        elif event.key == "r":
            self.mode = "real"
        else:
            return
        self._draw()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", "-c", type=int, default=4,
                    help="Config number 1..36 (default: 4 = 8b 3x3 REPLICATE FLUSH=on)")
    ap.add_argument("--frame",  "-n", type=int, default=0,
                    help="Initial frame index 0..2 (default: 0)")
    ap.add_argument("--row",    "-r", type=int, default=3,
                    help="Initial pixel row (default: 3)")
    ap.add_argument("--col",    "-x", type=int, default=3,
                    help="Initial pixel col (default: 3)")
    ap.add_argument("--vec-dir", type=str,
                    default=os.path.join("tb", "vectors"),
                    help="Directory containing vector files (default: tb/vectors)")
    ap.add_argument("--list", "-l", action="store_true",
                    help="Print all 36 configs and exit")
    args = ap.parse_args()

    if args.list:
        for i, cfg in enumerate(CONFIGS, 1):
            print(f"  {i:02d}: {cfg_label(cfg)}")
        return

    cfg_idx = args.config - 1
    if not (0 <= cfg_idx < N):
        sys.exit(f"--config must be 1..{N}")

    print(f"Loading CFG{args.config:02d}: {cfg_label(CONFIGS[cfg_idx])}")
    print(f"Vector dir: {args.vec_dir}")
    print()
    print("Controls:")
    print("  Click frame cell      — move kernel window")
    print("  Left / Right arrows   — previous / next frame")
    print("  Up / Down arrows      — move row")
    print("  < / > (comma/period)  — previous / next config")
    if CONFIGS[cfg_idx]['flush']:
        print("  F                     — cycle through flush rows")
        print("  R                     — return to real frame view")

    Viewer(cfg_idx, args.frame, args.row, args.col, args.vec_dir)


if __name__ == "__main__":
    main()
