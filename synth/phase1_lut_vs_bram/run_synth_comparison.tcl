# Phase 1 synthesis-only comparison: LUT-style vs BRAM-style row buffer.
# Run from the Vivado Tcl console or batch mode:
#   vivado -mode batch -source run_synth_comparison.tcl
#
# Produces two out-of-context synthesis reports in ./reports/:
#   lut_style_utilization.rpt
#   bram_style_utilization.rpt
#
# Target: XC7Z020-CLG484-1 (Avnet ZedBoard, Zynq-7000 PL)

set script_dir [file dirname [info script]]
set report_dir [file join $script_dir reports]
file mkdir $report_dir

# --------------------------------------------------------------------------
# Helper: synthesise a single top-level file out-of-context and write reports
# --------------------------------------------------------------------------
proc synth_ooc {entity vhd_file report_prefix report_dir part} {
    set proj_name "tmp_${entity}"
    set proj_dir  "/tmp/${proj_name}"

    create_project $proj_name $proj_dir -part $part -force
    set_property target_language VHDL [current_project]

    read_vhdl -vhdl2008 $vhd_file
    set_property top $entity [current_fileset]

    # Out-of-context synthesis: no I/O buffers, no constraints from a board
    synth_design -top $entity \
                 -part $part  \
                 -mode out_of_context

    report_utilization -file [file join $report_dir "${report_prefix}_utilization.rpt"]
    report_timing_summary -file [file join $report_dir "${report_prefix}_timing.rpt"] \
                          -max_paths 5

    close_project
    file delete -force $proj_dir
}

set part "xc7z020clg484-1"

puts "=== Synthesising LUT-style row buffer ==="
synth_ooc row_buf_lut_style \
    [file join $script_dir row_buf_lut_style.vhd] \
    lut_style \
    $report_dir \
    $part

puts "=== Synthesising BRAM-style row buffer ==="
synth_ooc row_buf_bram_style \
    [file join $script_dir row_buf_bram_style.vhd] \
    bram_style \
    $report_dir \
    $part

puts ""
puts "=== COMPARISON SUMMARY ==="
puts "Reports written to: $report_dir"
puts "Key lines to check in each _utilization.rpt:"
puts "  Slice LUTs (as Memory) -> should be 0 for BRAM style"
puts "  Block RAM Tile          -> should be 0 for LUT style, >=1 for BRAM style"
puts "Done."
