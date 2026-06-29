-- Top-level 2D convolution core.
-- Fixed Phase 2 parameters: 3x3 kernel, 8-bit pixels, zero-extend edges,
-- flush off, synchronous active-high reset, single clock domain.
--
-- I/O contract: one AXI4-Stream video input -> 3x3 = 9 AXI4-Stream outputs,
-- each carrying the input pixel stream weighted by its kernel tap position.
-- Tap ordering matches win_buf: row 0 = oldest, row M-1 = newest;
-- col 0 = oldest, col N-1 = newest within each row.
--
-- Pipeline:
--   Cycle 0: pixel accepted from input (TVALID & TREADY)
--   Cycle 1: line_buf read data available (1-clock BRAM latency)
--   Cycle 1: win_buf updated with that data
--   Cycle 1: tap_out valid and forwarded to output ports
--
-- Back-pressure: TREADY to upstream is deasserted when any output port
-- deasserts TREADY (combinational AND of all downstream TREADY signals).
-- The pipeline stalls cleanly: no data is accepted or shifted when stalled.
--
-- Validity: output TVALID is asserted only once the window is full, i.e.
-- after (KERN_ROWS-1)*LINE_WIDTH + KERN_COLS pixels have been consumed.
-- TLAST and TUSER are propagated from input, delayed to match pipeline latency.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity conv2d is
    generic (
        DATA_WIDTH : positive := 8;
        KERN_ROWS  : positive := 3;
        KERN_COLS  : positive := 3;
        LINE_WIDTH : positive := 1920;
        FRAME_HEIGHT : positive := 1080
    );
    port (
        clk  : in std_logic;
        rst  : in std_logic;

        -- AXI4-Stream input
        s_tdata  : in  std_logic_vector(DATA_WIDTH - 1 downto 0);
        s_tvalid : in  std_logic;
        s_tready : out std_logic;
        s_tlast  : in  std_logic;
        s_tuser  : in  std_logic;   -- bit 0 = SOF

        -- AXI4-Stream outputs: KERN_ROWS * KERN_COLS ports, flattened.
        -- Port k carries the pixel weighted by tap k (same indexing as tap_out).
        -- All ports share the same TVALID/TLAST/TUSER; each has its own TDATA.
        m_tdata  : out std_logic_vector(DATA_WIDTH * KERN_ROWS * KERN_COLS - 1 downto 0);
        m_tvalid : out std_logic;
        m_tready : in  std_logic_vector(KERN_ROWS * KERN_COLS - 1 downto 0);
        m_tlast  : out std_logic;
        m_tuser  : out std_logic
    );
end entity conv2d;

architecture rtl of conv2d is

    constant NUM_TAPS   : positive := KERN_ROWS * KERN_COLS;
    constant NUM_BUF_ROWS : positive := KERN_ROWS - 1;

    -- Column and row counters
    signal col_cnt   : natural range 0 to LINE_WIDTH - 1;
    signal row_cnt   : natural range 0 to FRAME_HEIGHT - 1;

    -- Which BRAM row buffer receives the next incoming row
    signal buf_wr_row : natural range 0 to NUM_BUF_ROWS - 1;

    -- Window fill counter: counts pixels consumed; output invalid until full
    signal fill_cnt   : natural range 0 to
                        (KERN_ROWS - 1) * LINE_WIDTH + KERN_COLS;
    signal win_valid  : std_logic;

    -- Pipeline delay registers for TLAST/TUSER (1 cycle = BRAM read latency)
    signal tlast_d1  : std_logic;
    signal tuser_d1  : std_logic;
    signal valid_d1  : std_logic;

    -- Internal TREADY: stall when any downstream not ready
    signal all_ready : std_logic;

    -- line_buf connections
    signal lb_wr_en   : std_logic;
    signal lb_wr_col  : natural range 0 to LINE_WIDTH - 1;
    signal lb_rd_col  : natural range 0 to LINE_WIDTH - 1;
    signal lb_rd_data : std_logic_vector(DATA_WIDTH * NUM_BUF_ROWS - 1 downto 0);

    -- win_buf connections
    signal wb_shift   : std_logic;
    signal wb_tap_out : std_logic_vector(DATA_WIDTH * NUM_TAPS - 1 downto 0);

    -- Pixel accepted this cycle
    signal pixel_accepted : std_logic;

begin

    -- -----------------------------------------------------------------------
    -- Back-pressure: stall unless all downstream ports are ready
    -- -----------------------------------------------------------------------
    process (m_tready)
        variable v : std_logic;
    begin
        v := '1';
        for i in 0 to NUM_TAPS - 1 loop
            v := v and m_tready(i);
        end loop;
        all_ready <= v;
    end process;

    s_tready <= all_ready;

    pixel_accepted <= s_tvalid and all_ready;

    -- -----------------------------------------------------------------------
    -- Column / row / buf-row counters
    -- -----------------------------------------------------------------------
    p_counters : process (clk)
    begin
        if rising_edge(clk) then
            if rst = '1' then
                col_cnt    <= 0;
                row_cnt    <= 0;
                buf_wr_row <= 0;
                fill_cnt   <= 0;
                win_valid  <= '0';
            elsif pixel_accepted = '1' then
                -- Fill counter (saturates)
                if fill_cnt < (KERN_ROWS - 1) * LINE_WIDTH + KERN_COLS then
                    fill_cnt <= fill_cnt + 1;
                else
                    win_valid <= '1';
                end if;

                -- Column counter
                if col_cnt = LINE_WIDTH - 1 then
                    col_cnt <= 0;
                    -- Row counter
                    if row_cnt = FRAME_HEIGHT - 1 then
                        row_cnt    <= 0;
                        buf_wr_row <= 0;
                    else
                        row_cnt <= row_cnt + 1;
                        -- Rotate which BRAM row is written next
                        if buf_wr_row = NUM_BUF_ROWS - 1 then
                            buf_wr_row <= 0;
                        else
                            buf_wr_row <= buf_wr_row + 1;
                        end if;
                    end if;
                else
                    col_cnt <= col_cnt + 1;
                end if;
            end if;
        end if;
    end process p_counters;

    -- -----------------------------------------------------------------------
    -- line_buf wiring
    -- Write the incoming pixel into the current BRAM row.
    -- Read address is one column ahead to compensate for 1-clock BRAM latency:
    -- the read issued at col N returns data at cycle N+1, when win_buf shifts.
    -- -----------------------------------------------------------------------
    lb_wr_en  <= pixel_accepted;
    lb_wr_col <= col_cnt;

    -- Read address: next column (wraps). win_buf shift happens in the same
    -- cycle that lb_rd_data is valid, so we pre-fetch one column ahead.
    lb_rd_col <= 0 when col_cnt = LINE_WIDTH - 1 else col_cnt + 1;

    -- -----------------------------------------------------------------------
    -- 1-cycle pipeline delay for TLAST / TUSER / TVALID
    -- -----------------------------------------------------------------------
    p_pipe_delay : process (clk)
    begin
        if rising_edge(clk) then
            if rst = '1' then
                tlast_d1 <= '0';
                tuser_d1 <= '0';
                valid_d1 <= '0';
            elsif all_ready = '1' then
                tlast_d1 <= s_tlast and s_tvalid;
                tuser_d1 <= s_tuser and s_tvalid;
                valid_d1 <= pixel_accepted and win_valid;
            end if;
        end if;
    end process p_pipe_delay;

    wb_shift <= pixel_accepted;

    -- -----------------------------------------------------------------------
    -- Sub-block instantiation
    -- -----------------------------------------------------------------------
    u_line_buf : entity work.line_buf
        generic map (
            DATA_WIDTH => DATA_WIDTH,
            LINE_WIDTH => LINE_WIDTH,
            NUM_ROWS   => NUM_BUF_ROWS
        )
        port map (
            clk     => clk,
            wr_en   => lb_wr_en,
            wr_row  => buf_wr_row,
            wr_col  => lb_wr_col,
            wr_data => s_tdata,
            rd_col  => lb_rd_col,
            rd_data => lb_rd_data
        );

    u_win_buf : entity work.win_buf
        generic map (
            DATA_WIDTH => DATA_WIDTH,
            KERN_ROWS  => KERN_ROWS,
            KERN_COLS  => KERN_COLS
        )
        port map (
            clk      => clk,
            rst      => rst,
            shift_en => wb_shift,
            pix_in   => s_tdata,
            buf_rows => lb_rd_data,
            tap_out  => wb_tap_out
        );

    -- -----------------------------------------------------------------------
    -- Output connections
    -- -----------------------------------------------------------------------
    m_tdata  <= wb_tap_out;
    m_tvalid <= valid_d1;
    m_tlast  <= tlast_d1;
    m_tuser  <= tuser_d1;

end architecture rtl;
