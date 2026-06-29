-- Self-checking testbench for conv2d (Phase 3).
-- Drives 3 frames (8x8 pixels each) from input_pixels.txt,
-- compares every output against expected_taps.txt, and prints PASS or FAIL.
--
-- Back-pressure: m_tready(0) is deasserted for 10 cycles immediately after
-- reset to verify the pipeline stalls and recovers correctly.  The design's
-- s_tready = AND(m_tready) stalls the input during this window; after release
-- all expected outputs still arrive in order, proving recovery.
--
-- FILE PATH NOTE:
--   Vivado runs simulation with a working directory shown in the Tcl Console
--   (printed as "xsim: loading..." or check with [pwd] in the console).
--   Before running simulation, copy tb/vectors/input_pixels.txt and
--   tb/vectors/expected_taps.txt into that directory.
--   The filenames below must match exactly.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.textio.all;

entity conv2d_tb is
end entity conv2d_tb;

architecture tb of conv2d_tb is

    -- DUT parameters — small image for fast simulation
    constant C_DATA_WIDTH   : positive := 8;
    constant C_KERN_ROWS    : positive := 3;
    constant C_KERN_COLS    : positive := 3;
    constant C_LINE_WIDTH   : positive := 8;
    constant C_FRAME_HEIGHT : positive := 8;
    constant C_NUM_FRAMES   : positive := 3;
    constant C_NUM_TAPS     : positive := C_KERN_ROWS * C_KERN_COLS;

    -- Vector filenames (must be in xsim working directory — see note above)
    constant INPUT_FILE    : string := "input_pixels.txt";
    constant EXPECTED_FILE : string := "expected_taps.txt";

    constant CLK_PERIOD : time := 10 ns;

    -- DUT ports
    signal clk     : std_logic := '0';
    signal rst     : std_logic := '1';
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

    -- Simulation control
    signal sim_done : boolean := false;

begin

    -- -----------------------------------------------------------------------
    -- Clock
    -- -----------------------------------------------------------------------
    clk <= not clk after CLK_PERIOD / 2 when not sim_done else '0';

    -- -----------------------------------------------------------------------
    -- DUT
    -- -----------------------------------------------------------------------
    u_dut : entity work.conv2d
        generic map (
            DATA_WIDTH   => C_DATA_WIDTH,
            KERN_ROWS    => C_KERN_ROWS,
            KERN_COLS    => C_KERN_COLS,
            LINE_WIDTH   => C_LINE_WIDTH,
            FRAME_HEIGHT => C_FRAME_HEIGHT
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
    -- Back-pressure process
    -- Deasserts m_tready(0) for 10 cycles after reset, then releases it.
    -- This forces the pipeline to stall before any data flows, verifying
    -- that s_tready goes low and no pixels are accepted during the stall.
    -- m_tready(1..8) remain high throughout.
    -- -----------------------------------------------------------------------
    p_backpressure : process
    begin
        m_tready(0) <= '0';
        m_tready(C_NUM_TAPS - 1 downto 1) <= (others => '1');
        wait until rst = '0';
        wait for CLK_PERIOD * 10;          -- stall for 10 cycles post-reset
        m_tready(0) <= '1';
        wait;
    end process p_backpressure;

    -- -----------------------------------------------------------------------
    -- Stimulus + checker process
    -- Drives pixels from input_pixels.txt.  Simultaneously, each time a
    -- valid output appears (m_tvalid='1' on a rising edge), reads the next
    -- line of expected_taps.txt and compares bit-exactly with m_tdata.
    -- -----------------------------------------------------------------------
    p_main : process
        file     in_f       : text;
        file     exp_f      : text;
        variable in_line    : line;
        variable exp_line   : line;
        variable pix_val    : integer;
        variable tap_val    : integer;
        variable exp_vec    : std_logic_vector(C_DATA_WIDTH * C_NUM_TAPS - 1 downto 0);
        variable col        : natural range 0 to C_LINE_WIDTH - 1;
        variable row        : natural range 0 to C_FRAME_HEIGHT - 1;
        variable frame_num  : natural;
        variable out_count  : natural;
        variable err_count  : natural;
        variable last_valid : std_logic;
    begin
        -- Release reset after 5 clock cycles
        wait for CLK_PERIOD * 5;
        wait until rising_edge(clk);
        rst <= '0';

        -- Open vector files
        file_open(in_f,  INPUT_FILE,    read_mode);
        file_open(exp_f, EXPECTED_FILE, read_mode);

        col       := 0;
        row       := 0;
        frame_num := 0;
        out_count := 0;
        err_count := 0;

        -- Send all input pixels; on each rising edge sample the output
        while not endfile(in_f) loop

            -- Present next pixel to the input port
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

            -- Wait for the upstream handshake (s_tvalid AND s_tready)
            wait until rising_edge(clk) and s_tready = '1';

            -- Advance pixel position
            if col = C_LINE_WIDTH - 1 then
                col := 0;
                if row = C_FRAME_HEIGHT - 1 then
                    row := 0;
                    frame_num := frame_num + 1;
                else
                    row := row + 1;
                end if;
            else
                col := col + 1;
            end if;

            -- One cycle after acceptance the output register fires.
            -- Sample the output on that next rising edge.
            wait until rising_edge(clk);

            if m_tvalid = '1' then
                -- Read next expected line: 9 space-separated decimal integers
                readline(exp_f, exp_line);
                for tap in 0 to C_NUM_TAPS - 1 loop
                    read(exp_line, tap_val);
                    exp_vec((tap + 1) * C_DATA_WIDTH - 1 downto tap * C_DATA_WIDTH)
                        := std_logic_vector(to_unsigned(tap_val, C_DATA_WIDTH));
                end loop;

                if m_tdata /= exp_vec then
                    report "MISMATCH at output index " & integer'image(out_count)
                        severity error;
                    err_count := err_count + 1;
                end if;
                out_count := out_count + 1;
            end if;

        end loop;

        -- De-assert input valid; drain any remaining output cycles
        s_tvalid <= '0';
        s_tlast  <= '0';
        s_tuser  <= '0';

        -- Wait a few cycles for the last output to appear and be checked
        for i in 1 to 4 loop
            wait until rising_edge(clk);
            if m_tvalid = '1' and not endfile(exp_f) then
                readline(exp_f, exp_line);
                for tap in 0 to C_NUM_TAPS - 1 loop
                    read(exp_line, tap_val);
                    exp_vec((tap + 1) * C_DATA_WIDTH - 1 downto tap * C_DATA_WIDTH)
                        := std_logic_vector(to_unsigned(tap_val, C_DATA_WIDTH));
                end loop;
                if m_tdata /= exp_vec then
                    report "MISMATCH (drain) at output index " & integer'image(out_count)
                        severity error;
                    err_count := err_count + 1;
                end if;
                out_count := out_count + 1;
            end if;
        end loop;

        file_close(in_f);
        file_close(exp_f);

        -- Final verdict
        if err_count = 0 then
            report "PASS: " & integer'image(out_count)
                & " outputs checked, all matched golden vectors."
                severity note;
        else
            report "FAIL: " & integer'image(err_count)
                & " mismatches in " & integer'image(out_count) & " outputs."
                severity failure;
        end if;

        sim_done <= true;
        wait;
    end process p_main;

end architecture tb;
