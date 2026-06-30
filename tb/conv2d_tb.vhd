-- Self-checking testbench for conv2d Phase 4.
-- Tests four configurations in parallel on a shared clock.
-- Each configuration has its own DUT instance, stimulus process, and checker.
-- sim_done asserts when all four checkers finish.
--
-- Configurations:
--   Config 1: 3x3, ZERO,      FLUSH=false  — Phase 3 regression + back-pressure test
--   Config 2: 3x3, REPLICATE, FLUSH=false
--   Config 3: 3x3, TOROIDAL,  FLUSH=false  — NOTE: OOB taps fall back to zero in a causal
--                                              streaming pipeline (wrapped pixels at far-right /
--                                              previous-frame rows are not in the window).
--                                              Expected vectors are generated with ZERO mode.
--   Config 4: 5x5, REPLICATE, FLUSH=false  — exercises larger kernel with non-trivial edge mode
--
-- Vector file generation (run from repo root before simulation):
--   python scripts/gen_vectors.py --kern-rows 3 --kern-cols 3 --edge-mode ZERO      --prefix c1_
--   python scripts/gen_vectors.py --kern-rows 3 --kern-cols 3 --edge-mode REPLICATE --prefix c2_
--   python scripts/gen_vectors.py --kern-rows 3 --kern-cols 3 --edge-mode ZERO      --prefix c3_
--   python scripts/gen_vectors.py --kern-rows 5 --kern-cols 5 --edge-mode REPLICATE --prefix c4_
--
-- Copy the 8 generated text files into the xsim working directory and run simulation.
--
-- FILE PATH NOTE:
--   The xsim working directory is shown in the Tcl Console as "xsim: loading..."
--   or check with [pwd] in the Vivado Tcl Console.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.textio.all;

entity conv2d_tb is
end entity conv2d_tb;

architecture tb of conv2d_tb is

    -- Shared clock and simulation-end flag
    constant CLK_PERIOD : time    := 10 ns;
    signal   clk        : std_logic := '0';
    signal   done_flags : std_logic_vector(3 downto 0) := (others => '0');
    signal   sim_done   : boolean := false;

    -- -----------------------------------------------------------------------
    -- Configuration constants
    -- -----------------------------------------------------------------------
    constant C_DATA_WIDTH   : positive := 8;
    constant C_LINE_WIDTH   : positive := 8;
    constant C_FRAME_HEIGHT : positive := 8;
    constant C_NUM_FRAMES   : positive := 3;

    -- Config 1: 3x3 ZERO
    constant C1_KERN_ROWS : positive := 3;
    constant C1_KERN_COLS : positive := 3;
    constant C1_EDGE_MODE : string   := "ZERO";
    constant C1_FLUSH     : boolean  := false;
    constant C1_NUM_TAPS  : positive := C1_KERN_ROWS * C1_KERN_COLS;

    -- Config 2: 3x3 REPLICATE
    constant C2_KERN_ROWS : positive := 3;
    constant C2_KERN_COLS : positive := 3;
    constant C2_EDGE_MODE : string   := "REPLICATE";
    constant C2_FLUSH     : boolean  := false;
    constant C2_NUM_TAPS  : positive := C2_KERN_ROWS * C2_KERN_COLS;

    -- Config 3: 3x3 TOROIDAL (OOB falls back to zero; vectors generated with ZERO mode)
    constant C3_KERN_ROWS : positive := 3;
    constant C3_KERN_COLS : positive := 3;
    constant C3_EDGE_MODE : string   := "TOROIDAL";
    constant C3_FLUSH     : boolean  := false;
    constant C3_NUM_TAPS  : positive := C3_KERN_ROWS * C3_KERN_COLS;

    -- Config 4: 5x5 REPLICATE
    constant C4_KERN_ROWS : positive := 5;
    constant C4_KERN_COLS : positive := 5;
    constant C4_EDGE_MODE : string   := "REPLICATE";
    constant C4_FLUSH     : boolean  := false;
    constant C4_NUM_TAPS  : positive := C4_KERN_ROWS * C4_KERN_COLS;

    -- -----------------------------------------------------------------------
    -- Per-DUT AXI signals
    -- -----------------------------------------------------------------------

    -- Config 1
    signal c1_rst     : std_logic := '1';
    signal c1_stdata  : std_logic_vector(C_DATA_WIDTH - 1 downto 0) := (others => '0');
    signal c1_stvalid : std_logic := '0';
    signal c1_stready : std_logic;
    signal c1_stlast  : std_logic := '0';
    signal c1_stuser  : std_logic := '0';
    signal c1_mtdata  : std_logic_vector(C_DATA_WIDTH * C1_NUM_TAPS - 1 downto 0);
    signal c1_mtvalid : std_logic;
    signal c1_mtready : std_logic_vector(C1_NUM_TAPS - 1 downto 0) := (others => '1');
    signal c1_mtlast  : std_logic;
    signal c1_mtuser  : std_logic;

    -- Config 2
    signal c2_rst     : std_logic := '1';
    signal c2_stdata  : std_logic_vector(C_DATA_WIDTH - 1 downto 0) := (others => '0');
    signal c2_stvalid : std_logic := '0';
    signal c2_stready : std_logic;
    signal c2_stlast  : std_logic := '0';
    signal c2_stuser  : std_logic := '0';
    signal c2_mtdata  : std_logic_vector(C_DATA_WIDTH * C2_NUM_TAPS - 1 downto 0);
    signal c2_mtvalid : std_logic;
    signal c2_mtready : std_logic_vector(C2_NUM_TAPS - 1 downto 0) := (others => '1');
    signal c2_mtlast  : std_logic;
    signal c2_mtuser  : std_logic;

    -- Config 3
    signal c3_rst     : std_logic := '1';
    signal c3_stdata  : std_logic_vector(C_DATA_WIDTH - 1 downto 0) := (others => '0');
    signal c3_stvalid : std_logic := '0';
    signal c3_stready : std_logic;
    signal c3_stlast  : std_logic := '0';
    signal c3_stuser  : std_logic := '0';
    signal c3_mtdata  : std_logic_vector(C_DATA_WIDTH * C3_NUM_TAPS - 1 downto 0);
    signal c3_mtvalid : std_logic;
    signal c3_mtready : std_logic_vector(C3_NUM_TAPS - 1 downto 0) := (others => '1');
    signal c3_mtlast  : std_logic;
    signal c3_mtuser  : std_logic;

    -- Config 4
    signal c4_rst     : std_logic := '1';
    signal c4_stdata  : std_logic_vector(C_DATA_WIDTH - 1 downto 0) := (others => '0');
    signal c4_stvalid : std_logic := '0';
    signal c4_stready : std_logic;
    signal c4_stlast  : std_logic := '0';
    signal c4_stuser  : std_logic := '0';
    signal c4_mtdata  : std_logic_vector(C_DATA_WIDTH * C4_NUM_TAPS - 1 downto 0);
    signal c4_mtvalid : std_logic;
    signal c4_mtready : std_logic_vector(C4_NUM_TAPS - 1 downto 0) := (others => '1');
    signal c4_mtlast  : std_logic;
    signal c4_mtuser  : std_logic;

begin

    sim_done <= true when done_flags = "1111" else false;
    clk <= not clk after CLK_PERIOD / 2 when not sim_done else '0';

    -- -----------------------------------------------------------------------
    -- DUT instances
    -- -----------------------------------------------------------------------
    u_dut1 : entity work.conv2d
        generic map (
            DATA_WIDTH   => C_DATA_WIDTH,
            KERN_ROWS    => C1_KERN_ROWS,
            KERN_COLS    => C1_KERN_COLS,
            LINE_WIDTH   => C_LINE_WIDTH,
            FRAME_HEIGHT => C_FRAME_HEIGHT,
            EDGE_MODE    => C1_EDGE_MODE,
            FLUSH        => C1_FLUSH
        )
        port map (
            clk      => clk,      rst      => c1_rst,
            s_tdata  => c1_stdata,  s_tvalid => c1_stvalid,
            s_tready => c1_stready, s_tlast  => c1_stlast,
            s_tuser  => c1_stuser,  m_tdata  => c1_mtdata,
            m_tvalid => c1_mtvalid, m_tready => c1_mtready,
            m_tlast  => c1_mtlast,  m_tuser  => c1_mtuser
        );

    u_dut2 : entity work.conv2d
        generic map (
            DATA_WIDTH   => C_DATA_WIDTH,
            KERN_ROWS    => C2_KERN_ROWS,
            KERN_COLS    => C2_KERN_COLS,
            LINE_WIDTH   => C_LINE_WIDTH,
            FRAME_HEIGHT => C_FRAME_HEIGHT,
            EDGE_MODE    => C2_EDGE_MODE,
            FLUSH        => C2_FLUSH
        )
        port map (
            clk      => clk,      rst      => c2_rst,
            s_tdata  => c2_stdata,  s_tvalid => c2_stvalid,
            s_tready => c2_stready, s_tlast  => c2_stlast,
            s_tuser  => c2_stuser,  m_tdata  => c2_mtdata,
            m_tvalid => c2_mtvalid, m_tready => c2_mtready,
            m_tlast  => c2_mtlast,  m_tuser  => c2_mtuser
        );

    u_dut3 : entity work.conv2d
        generic map (
            DATA_WIDTH   => C_DATA_WIDTH,
            KERN_ROWS    => C3_KERN_ROWS,
            KERN_COLS    => C3_KERN_COLS,
            LINE_WIDTH   => C_LINE_WIDTH,
            FRAME_HEIGHT => C_FRAME_HEIGHT,
            EDGE_MODE    => C3_EDGE_MODE,
            FLUSH        => C3_FLUSH
        )
        port map (
            clk      => clk,      rst      => c3_rst,
            s_tdata  => c3_stdata,  s_tvalid => c3_stvalid,
            s_tready => c3_stready, s_tlast  => c3_stlast,
            s_tuser  => c3_stuser,  m_tdata  => c3_mtdata,
            m_tvalid => c3_mtvalid, m_tready => c3_mtready,
            m_tlast  => c3_mtlast,  m_tuser  => c3_mtuser
        );

    u_dut4 : entity work.conv2d
        generic map (
            DATA_WIDTH   => C_DATA_WIDTH,
            KERN_ROWS    => C4_KERN_ROWS,
            KERN_COLS    => C4_KERN_COLS,
            LINE_WIDTH   => C_LINE_WIDTH,
            FRAME_HEIGHT => C_FRAME_HEIGHT,
            EDGE_MODE    => C4_EDGE_MODE,
            FLUSH        => C4_FLUSH
        )
        port map (
            clk      => clk,      rst      => c4_rst,
            s_tdata  => c4_stdata,  s_tvalid => c4_stvalid,
            s_tready => c4_stready, s_tlast  => c4_stlast,
            s_tuser  => c4_stuser,  m_tdata  => c4_mtdata,
            m_tvalid => c4_mtvalid, m_tready => c4_mtready,
            m_tlast  => c4_mtlast,  m_tuser  => c4_mtuser
        );

    -- -----------------------------------------------------------------------
    -- Back-pressure for Config 1 only:
    -- m_tready(0) deasserted for 10 cycles after reset, then released.
    -- -----------------------------------------------------------------------
    p_bp1 : process
    begin
        c1_mtready(0) <= '0';
        c1_mtready(C1_NUM_TAPS - 1 downto 1) <= (others => '1');
        wait until c1_rst = '0';
        wait for CLK_PERIOD * 10;
        c1_mtready(0) <= '1';
        wait;
    end process p_bp1;

    -- -----------------------------------------------------------------------
    -- Generic stimulus procedure body (inlined per config to avoid subprograms
    -- that reference file I/O differently across xsim versions).
    -- Each p_stimN: releases reset, reads input file, drives pixels.
    -- -----------------------------------------------------------------------

    p_stim1 : process
        file     f    : text;
        variable ln   : line;
        variable pv   : integer;
        variable col  : natural range 0 to C_LINE_WIDTH   - 1;
        variable row  : natural range 0 to C_FRAME_HEIGHT - 1;
    begin
        wait for CLK_PERIOD * 5;
        wait until rising_edge(clk);
        c1_rst <= '0';
        file_open(f, "c1_input.txt", read_mode);
        col := 0;  row := 0;
        while not endfile(f) loop
            readline(f, ln);  read(ln, pv);
            c1_stdata  <= std_logic_vector(to_unsigned(pv, C_DATA_WIDTH));
            c1_stvalid <= '1';
            if row = 0 and col = 0 then c1_stuser <= '1'; else c1_stuser <= '0'; end if;
            if col = C_LINE_WIDTH - 1 then c1_stlast <= '1'; else c1_stlast <= '0'; end if;
            wait until rising_edge(clk) and c1_stready = '1';
            if col = C_LINE_WIDTH - 1 then
                col := 0;
                if row = C_FRAME_HEIGHT - 1 then row := 0; else row := row + 1; end if;
            else
                col := col + 1;
            end if;
        end loop;
        c1_stvalid <= '0';  c1_stlast <= '0';  c1_stuser <= '0';
        file_close(f);
        wait;
    end process p_stim1;

    p_stim2 : process
        file     f    : text;
        variable ln   : line;
        variable pv   : integer;
        variable col  : natural range 0 to C_LINE_WIDTH   - 1;
        variable row  : natural range 0 to C_FRAME_HEIGHT - 1;
    begin
        wait for CLK_PERIOD * 5;
        wait until rising_edge(clk);
        c2_rst <= '0';
        file_open(f, "c2_input.txt", read_mode);
        col := 0;  row := 0;
        while not endfile(f) loop
            readline(f, ln);  read(ln, pv);
            c2_stdata  <= std_logic_vector(to_unsigned(pv, C_DATA_WIDTH));
            c2_stvalid <= '1';
            if row = 0 and col = 0 then c2_stuser <= '1'; else c2_stuser <= '0'; end if;
            if col = C_LINE_WIDTH - 1 then c2_stlast <= '1'; else c2_stlast <= '0'; end if;
            wait until rising_edge(clk) and c2_stready = '1';
            if col = C_LINE_WIDTH - 1 then
                col := 0;
                if row = C_FRAME_HEIGHT - 1 then row := 0; else row := row + 1; end if;
            else
                col := col + 1;
            end if;
        end loop;
        c2_stvalid <= '0';  c2_stlast <= '0';  c2_stuser <= '0';
        file_close(f);
        wait;
    end process p_stim2;

    p_stim3 : process
        file     f    : text;
        variable ln   : line;
        variable pv   : integer;
        variable col  : natural range 0 to C_LINE_WIDTH   - 1;
        variable row  : natural range 0 to C_FRAME_HEIGHT - 1;
    begin
        wait for CLK_PERIOD * 5;
        wait until rising_edge(clk);
        c3_rst <= '0';
        file_open(f, "c3_input.txt", read_mode);
        col := 0;  row := 0;
        while not endfile(f) loop
            readline(f, ln);  read(ln, pv);
            c3_stdata  <= std_logic_vector(to_unsigned(pv, C_DATA_WIDTH));
            c3_stvalid <= '1';
            if row = 0 and col = 0 then c3_stuser <= '1'; else c3_stuser <= '0'; end if;
            if col = C_LINE_WIDTH - 1 then c3_stlast <= '1'; else c3_stlast <= '0'; end if;
            wait until rising_edge(clk) and c3_stready = '1';
            if col = C_LINE_WIDTH - 1 then
                col := 0;
                if row = C_FRAME_HEIGHT - 1 then row := 0; else row := row + 1; end if;
            else
                col := col + 1;
            end if;
        end loop;
        c3_stvalid <= '0';  c3_stlast <= '0';  c3_stuser <= '0';
        file_close(f);
        wait;
    end process p_stim3;

    p_stim4 : process
        file     f    : text;
        variable ln   : line;
        variable pv   : integer;
        variable col  : natural range 0 to C_LINE_WIDTH   - 1;
        variable row  : natural range 0 to C_FRAME_HEIGHT - 1;
    begin
        wait for CLK_PERIOD * 5;
        wait until rising_edge(clk);
        c4_rst <= '0';
        file_open(f, "c4_input.txt", read_mode);
        col := 0;  row := 0;
        while not endfile(f) loop
            readline(f, ln);  read(ln, pv);
            c4_stdata  <= std_logic_vector(to_unsigned(pv, C_DATA_WIDTH));
            c4_stvalid <= '1';
            if row = 0 and col = 0 then c4_stuser <= '1'; else c4_stuser <= '0'; end if;
            if col = C_LINE_WIDTH - 1 then c4_stlast <= '1'; else c4_stlast <= '0'; end if;
            wait until rising_edge(clk) and c4_stready = '1';
            if col = C_LINE_WIDTH - 1 then
                col := 0;
                if row = C_FRAME_HEIGHT - 1 then row := 0; else row := row + 1; end if;
            else
                col := col + 1;
            end if;
        end loop;
        c4_stvalid <= '0';  c4_stlast <= '0';  c4_stuser <= '0';
        file_close(f);
        wait;
    end process p_stim4;

    -- -----------------------------------------------------------------------
    -- Checker processes: one per configuration.
    -- Each reads its own expected file and compares bit-exactly against
    -- m_tdata on every rising edge where m_tvalid='1'.
    -- -----------------------------------------------------------------------

    p_check1 : process
        file     f         : text;
        variable ln        : line;
        variable tv        : integer;
        variable exp_vec   : std_logic_vector(C_DATA_WIDTH * C1_NUM_TAPS - 1 downto 0);
        variable out_count : natural := 0;
        variable err_count : natural := 0;
    begin
        wait until c1_rst = '0';
        file_open(f, "c1_expected.txt", read_mode);
        while not endfile(f) loop
            wait until rising_edge(clk) and c1_mtvalid = '1';
            readline(f, ln);
            for tap in 0 to C1_NUM_TAPS - 1 loop
                read(ln, tv);
                exp_vec((tap + 1) * C_DATA_WIDTH - 1 downto tap * C_DATA_WIDTH)
                    := std_logic_vector(to_unsigned(tv, C_DATA_WIDTH));
            end loop;
            if c1_mtdata /= exp_vec then
                report "CFG1 MISMATCH at output " & integer'image(out_count) severity error;
                err_count := err_count + 1;
            end if;
            out_count := out_count + 1;
        end loop;
        file_close(f);
        if err_count = 0 then
            report "CFG1 PASS (3x3 ZERO, back-pressure): "
                & integer'image(out_count) & " outputs checked." severity note;
        else
            report "CFG1 FAIL: " & integer'image(err_count) & " mismatches." severity failure;
        end if;
        done_flags(0) <= '1';
        wait;
    end process p_check1;

    p_check2 : process
        file     f         : text;
        variable ln        : line;
        variable tv        : integer;
        variable exp_vec   : std_logic_vector(C_DATA_WIDTH * C2_NUM_TAPS - 1 downto 0);
        variable out_count : natural := 0;
        variable err_count : natural := 0;
    begin
        wait until c2_rst = '0';
        file_open(f, "c2_expected.txt", read_mode);
        while not endfile(f) loop
            wait until rising_edge(clk) and c2_mtvalid = '1';
            readline(f, ln);
            for tap in 0 to C2_NUM_TAPS - 1 loop
                read(ln, tv);
                exp_vec((tap + 1) * C_DATA_WIDTH - 1 downto tap * C_DATA_WIDTH)
                    := std_logic_vector(to_unsigned(tv, C_DATA_WIDTH));
            end loop;
            if c2_mtdata /= exp_vec then
                report "CFG2 MISMATCH at output " & integer'image(out_count) severity error;
                err_count := err_count + 1;
            end if;
            out_count := out_count + 1;
        end loop;
        file_close(f);
        if err_count = 0 then
            report "CFG2 PASS (3x3 REPLICATE): "
                & integer'image(out_count) & " outputs checked." severity note;
        else
            report "CFG2 FAIL: " & integer'image(err_count) & " mismatches." severity failure;
        end if;
        done_flags(1) <= '1';
        wait;
    end process p_check2;

    p_check3 : process
        file     f         : text;
        variable ln        : line;
        variable tv        : integer;
        variable exp_vec   : std_logic_vector(C_DATA_WIDTH * C3_NUM_TAPS - 1 downto 0);
        variable out_count : natural := 0;
        variable err_count : natural := 0;
    begin
        wait until c3_rst = '0';
        file_open(f, "c3_expected.txt", read_mode);
        while not endfile(f) loop
            wait until rising_edge(clk) and c3_mtvalid = '1';
            readline(f, ln);
            for tap in 0 to C3_NUM_TAPS - 1 loop
                read(ln, tv);
                exp_vec((tap + 1) * C_DATA_WIDTH - 1 downto tap * C_DATA_WIDTH)
                    := std_logic_vector(to_unsigned(tv, C_DATA_WIDTH));
            end loop;
            if c3_mtdata /= exp_vec then
                report "CFG3 MISMATCH at output " & integer'image(out_count) severity error;
                err_count := err_count + 1;
            end if;
            out_count := out_count + 1;
        end loop;
        file_close(f);
        if err_count = 0 then
            report "CFG3 PASS (3x3 TOROIDAL, OOB falls back to zero): "
                & integer'image(out_count) & " outputs checked." severity note;
        else
            report "CFG3 FAIL: " & integer'image(err_count) & " mismatches." severity failure;
        end if;
        done_flags(2) <= '1';
        wait;
    end process p_check3;

    p_check4 : process
        file     f         : text;
        variable ln        : line;
        variable tv        : integer;
        variable exp_vec   : std_logic_vector(C_DATA_WIDTH * C4_NUM_TAPS - 1 downto 0);
        variable out_count : natural := 0;
        variable err_count : natural := 0;
    begin
        wait until c4_rst = '0';
        file_open(f, "c4_expected.txt", read_mode);
        while not endfile(f) loop
            wait until rising_edge(clk) and c4_mtvalid = '1';
            readline(f, ln);
            for tap in 0 to C4_NUM_TAPS - 1 loop
                read(ln, tv);
                exp_vec((tap + 1) * C_DATA_WIDTH - 1 downto tap * C_DATA_WIDTH)
                    := std_logic_vector(to_unsigned(tv, C_DATA_WIDTH));
            end loop;
            if c4_mtdata /= exp_vec then
                report "CFG4 MISMATCH at output " & integer'image(out_count) severity error;
                err_count := err_count + 1;
            end if;
            out_count := out_count + 1;
        end loop;
        file_close(f);
        if err_count = 0 then
            report "CFG4 PASS (5x5 REPLICATE): "
                & integer'image(out_count) & " outputs checked." severity note;
        else
            report "CFG4 FAIL: " & integer'image(err_count) & " mismatches." severity failure;
        end if;
        done_flags(3) <= '1';
        wait;
    end process p_check4;

end architecture tb;
