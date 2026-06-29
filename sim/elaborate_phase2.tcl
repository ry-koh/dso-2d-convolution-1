# Elaborate the Phase 2 design (no testbench yet — smoke check only).
# Run from repo root:
#   xvhdl --2008 src/line_buf.vhd src/win_buf.vhd src/conv2d.vhd
#   xelab -debug typical work.conv2d -s conv2d_elab
#
# Or use this script with Vivado batch mode:
#   vivado -mode batch -source sim/elaborate_phase2.tcl

set src_dir [file join [pwd] src]

# Compile
foreach f {line_buf.vhd win_buf.vhd conv2d.vhd} {
    exec xvhdl --2008 [file join $src_dir $f] >@ stdout 2>@ stderr
}

# Elaborate
exec xelab -debug typical work.conv2d -s conv2d_elab >@ stdout 2>@ stderr

puts "Elaboration complete. Run xsim conv2d_elab to open simulator."
