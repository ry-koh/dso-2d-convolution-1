#!/usr/bin/env bash
# Compile and elaborate the Phase 2 RTL under xsim.
# Run from repo root with Vivado on PATH:
#   bash sim/elaborate_phase2.sh
set -euo pipefail

xvhdl --2008 src/line_buf.vhd src/win_buf.vhd src/conv2d.vhd
xelab -debug typical work.conv2d -s conv2d_elab

echo "Elaboration OK — no errors."
