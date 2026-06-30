-- Self-checking testbench for conv2d Phase 4.
-- Drives N frames of pixels, compares every output against golden vectors,
-- and prints PASS or FAIL.
--
-- Configure the constants in the "DUT parameters" section, generate matching
-- golden vectors with gen_vectors.py, copy the two vector files into the xsim
-- working directory, and run the simulation.
--
-- Two required Phase 4 configurations:
--
--   Config 1 — 3x3 REPLICATE, FLUSH=false (default below):
--     python scripts/gen_vectors.py --kern-rows 3 --kern-cols 3 \
--         --edge-mode REPLICATE --num-frames 3
--     C_KERN_ROWS=3, C_KERN_COLS=3, C_EDGE_MODE="REPLICATE", C_FLUSH=false
--
--   Config 2 — 5x5 ZERO, FLUSH=false:
--     python scripts/gen_vectors.py --kern-rows 5 --kern-cols 5 \
--         --edge-mode ZERO --num-frames 3
--     C_KERN_ROWS=5, C_KERN_COLS=5, C_EDGE_MODE="ZERO", C_FLUSH=false
--
-- Change the constants below and rerun for each configuration.
--
-- Architecture: two decoupled processes (same approach as Phase 3).
--   p_stim  : drives pixels; handles TUSER / TLAST / back-pressure.
--   p_check : waits for m_tvalid='1', reads expected_taps.txt, compares.
--
-- Back-pressure: m_tready(0) is deasserted for 10 cycles after reset release.
--
-- FILE PATH NOTE:
--   Copy tb/vectors/input_pixels.txt and tb/vectors/expected_taps.txt into
--   the xsim working directory before running simulation.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.textio.all;

entity conv2d_tb is
end entity conv2d_tb;

architecture tb of conv2d_tb is

    -- -----------------------------------------------------------------------
    -- DUT parameters — change these to switch between test configurations.
    -- Must match the gen_vectors.py arguments used to produce the vectors.
    -- -----------------------------------------------------------------------
    constant C_DATA_WIDTH   : positive := 8;
    constant C_KERN_ROWS    : positive := 3;      -- Config 1: 3  | Config 2: 5
    constant C_KERN_COLS    : positive := 3;      -- Config 1: 3  | Config 2: 5
    constant C_LINE_WIDTH   : positive := 8;
    constant C_FRAME_HEIGHT : positive := 8;
    constant C_NUM_FRAMES   : positive := 3;
    constant C_EDGE_MODE    : string   := "REPLICATE"; -- Config 1 | Config 2: "ZERO"
    constant C_FLUSH        : boolean  := false;

    constant C_NUM_TAPS  : positive := C_KERN_ROWS * C_KERN_COLS;
    constant CLK_PERIOD  : time     := 10 ns;

    constant INPUT_FILE    : string := "input_pixels.txt";
    constant EXPECTED_FILE : string := "expected_taps.txt";

    -- DUT ports
    signal clk      : std_logic := '0';
    signal rst      : std_logic := '1';
    signal s_tdata  : std_logic_vector(C_DATA_WIDTH - 1 downto 0) := (others => '0');
    signal s_tvalid : std_logic := '0';
    signal s_tready : std_logic;
    signal s_tlast  : std_logic := '0';
    signal s_tuser  : std_logic := '0';
    signal m_tdata  : std_logic_vector(C_DATA_WIDTH * C_NUM_TAPS - 1 downto 0);
    signal m_tvalid : std_logic;
    signal m_tready : std_logic_vector(C_NUM_TAPS - 1 downto 0) := (others => '1');
    signal m_tlast  : std_logic;
    signal m_tuser  : std_logic;

    signal sim_done  : boolean := false;
    signal stim_done : boolean := false;

begin

    clk <= not clk after CLK_PERIOD / 2 when not sim_done else '0';

    u_dut : entity work.conv2d
        generic map (
            DATA_WIDTH   => C_DATA_WIDTH,
            KERN_ROWS    => C_KERN_ROWS,
            KERN_COLS    => C_KERN_COLS,
            LINE_WIDTH   => C_LINE_WIDTH,
            FRAME_HEIGHT => C_FRAME_HEIGHT,
            EDGE_MODE    => C_EDGE_MODE,
            FLUSH        => C_FLUSH
        )
        port map (
            clk      => clk,
            rst      => rst,
            s_tdata  => s_tdata,
            s_tvalid => s_tvalid,
            s_tready => s_tready,
            s_tlast  => s_tlast,
            s_tuser  => s_tuser,
            m_tdata  => m_tdata,
            m_tvalid => m_tvalid,
            m_tready => m_tready,
            m_tlast  => m_tlast,
            m_tuser  => m_tuser
        );

    -- -----------------------------------------------------------------------
    -- Back-pressure: deassert m_tready(0) for 10 cycles after reset.
    -- -----------------------------------------------------------------------
    p_backpressure : process
    begin
        m_tready(0) <= '0';
        m_tready(C_NUM_TAPS - 1 downto 1) <= (others => '1');
        wait until rst = '0';
        wait for CLK_PERIOD * 10;
        m_tready(0) <= '1';
        wait;
    end process p_backpressure;

    -- -----------------------------------------------------------------------
    -- Stimulus: drives all frames from input_pixels.txt.
    -- -----------------------------------------------------------------------
    p_stim : process
        file     in_f      : text;
        variable in_line   : line;
        variable pix_val   : integer;
        variable col       : natural range 0 to C_LINE_WIDTH   - 1;
        variable row       : natural range 0 to C_FRAME_HEIGHT - 1;
        variable frame_num : natural;
    begin
        wait for CLK_PERIOD * 5;
        wait until rising_edge(clk);
        rst <= '0';

        file_open(in_f, INPUT_FILE, read_mode);
        col := 0;  row := 0;  frame_num := 0;

        while not endfile(in_f) loop
            readline(in_f, in_line);
            read(in_line, pix_val);

            s_tdata  <= std_logic_vector(to_unsigned(pix_val, C_DATA_WIDTH));
            s_tvalid <= '1';
            if row = 0 and col = 0 then
                s_tuser <= '1';
            else
                s_tuser <= '0';
            end if;
            if col = C_LINE_WIDTH - 1 then
                s_tlast <= '1';
            else
                s_tlast <= '0';
            end if;

            wait until rising_edge(clk) and s_tready = '1';

            if col = C_LINE_WIDTH - 1 then
                col := 0;
                if row = C_FRAME_HEIGHT - 1 then
                    row       := 0;
                    frame_num := frame_num + 1;
                else
                    row := row + 1;
                end if;
            else
                col := col + 1;
            end if;
        end loop;

        s_tvalid <= '0';
        s_tlast  <= '0';
        s_tuser  <= '0';
        file_close(in_f);
        stim_done <= true;
        wait;
    end process p_stim;

    -- -----------------------------------------------------------------------
    -- Checker: compares every m_tvalid beat against expected_taps.txt.
    -- Samples at a rising edge where m_tvalid is already '1' (registered
    -- output settled in the previous clock's delta-1).
    -- -----------------------------------------------------------------------
    p_check : process
        file     exp_f     : text;
        variable exp_line  : line;
        variable tap_val   : integer;
        variable exp_vec   : std_logic_vector(C_DATA_WIDTH * C_NUM_TAPS - 1 downto 0);
        variable out_count : natural;
        variable err_count : natural;
    begin
        wait until rst = '0';
        file_open(exp_f, EXPECTED_FILE, read_mode);
        out_count := 0;
        err_count := 0;

        while not endfile(exp_f) loop
            wait until rising_edge(clk) and m_tvalid = '1';

            readline(exp_f, exp_line);
            for tap in 0 to C_NUM_TAPS - 1 loop
                read(exp_line, tap_val);
                exp_vec((tap + 1) * C_DATA_WIDTH - 1 downto tap * C_DATA_WIDTH)
                    := std_logic_vector(to_unsigned(tap_val, C_DATA_WIDTH));
            end loop;

            if m_tdata /= exp_vec then
                report "MISMATCH at output " & integer'image(out_count)
                    & "  got=" & integer'image(to_integer(unsigned(m_tdata)))
                    & "  exp=" & integer'image(to_integer(unsigned(exp_vec)))
                    severity error;
                err_count := err_count + 1;
            end if;
            out_count := out_count + 1;
        end loop;

        file_close(exp_f);

        if err_count = 0 then
            report "PASS: " & integer'image(out_count)
                & " outputs checked, all matched golden vectors."
                & "  EDGE_MODE=" & C_EDGE_MODE
                severity note;
        else
            report "FAIL: " & integer'image(err_count)
                & " mismatches in " & integer'image(out_count) & " outputs."
                & "  EDGE_MODE=" & C_EDGE_MODE
                severity failure;
        end if;

        sim_done <= true;
        wait;
    end process p_check;

end architecture tb;
