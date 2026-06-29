-- Top-level 2D convolution window extractor.
-- Phase 2 fixed parameters: 3x3 kernel, 8-bit pixels, zero-extend edges,
-- flush off, synchronous active-high reset, single clock domain.
--
-- I/O contract: one AXI4-Stream video input -> KERN_ROWS*KERN_COLS output ports.
-- Each output port carries the pixel at the corresponding window tap position.
-- Tap indexing (matches win_buf): row 0 = oldest row, row KERN_ROWS-1 = current;
-- col 0 = most recent pixel in that row, col KERN_COLS-1 = oldest.
-- Flattened: tap[r][c] at m_tdata bits ((r*KERN_COLS+c+1)*DW-1 downto (r*KERN_COLS+c)*DW).
--
-- Pipeline (2 stages):
--   Stage 1: pixel accepted from input; win_buf shifts; BRAM write issued.
--   Stage 2: m_tdata_r captures win_buf output; m_tvalid_r goes high.
-- The 1-stage output register aligns m_tdata with m_tvalid and gives clean
-- registered AXI4-Stream outputs.
--
-- Zero-extend: Vivado simulation initialises BRAM to 0; win_buf resets to 0.
-- Taps for pixels outside the frame boundary therefore read as 0 naturally.
-- This is correct for the first frame. Phase 4 will add explicit edge modes.
--
-- Back-pressure: s_tready = AND of all m_tready. Pipeline stalls cleanly
-- (no pixel accepted, no shift, output registers held) when any downstream
-- deasserts TREADY.
--
-- BRAM row ordering: the physical BRAM being written (buf_wr_row) holds the
-- OLDEST stored row (it is about to be overwritten). Passing buf_wr_row as
-- row_base to line_buf permutes the read outputs so slot 0 is always oldest.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity conv2d is
    generic (
        DATA_WIDTH   : positive := 8;
        KERN_ROWS    : positive := 3;
        KERN_COLS    : positive := 3;
        LINE_WIDTH   : positive := 1920;
        FRAME_HEIGHT : positive := 1080
    );
    port (
        clk  : in std_logic;
        rst  : in std_logic;

        s_tdata  : in  std_logic_vector(DATA_WIDTH - 1 downto 0);
        s_tvalid : in  std_logic;
        s_tready : out std_logic;
        s_tlast  : in  std_logic;
        s_tuser  : in  std_logic;

        m_tdata  : out std_logic_vector(DATA_WIDTH * KERN_ROWS * KERN_COLS - 1 downto 0);
        m_tvalid : out std_logic;
        m_tready : in  std_logic_vector(KERN_ROWS * KERN_COLS - 1 downto 0);
        m_tlast  : out std_logic;
        m_tuser  : out std_logic
    );
end entity conv2d;

architecture rtl of conv2d is

    constant NUM_TAPS     : positive := KERN_ROWS * KERN_COLS;
    constant NUM_BUF_ROWS : positive := KERN_ROWS - 1;

    signal col_cnt    : natural range 0 to LINE_WIDTH   - 1;
    signal row_cnt    : natural range 0 to FRAME_HEIGHT - 1;
    signal buf_wr_row : natural range 0 to NUM_BUF_ROWS - 1;

    signal all_ready      : std_logic;
    signal pixel_accepted : std_logic;

    -- line_buf ports
    signal lb_wr_col  : natural range 0 to LINE_WIDTH - 1;
    signal lb_rd_col  : natural range 0 to LINE_WIDTH - 1;
    signal lb_rd_data : std_logic_vector(DATA_WIDTH * NUM_BUF_ROWS - 1 downto 0);

    -- win_buf zero-extend control
    signal new_row   : std_logic;
    signal row_valid : std_logic_vector(NUM_BUF_ROWS - 1 downto 0);

    -- win_buf output
    signal wb_tap_out : std_logic_vector(DATA_WIDTH * NUM_TAPS - 1 downto 0);

    -- 1-cycle delay to let win_buf's p_shift settle before p_out_reg samples tap_out
    signal pixel_accepted_d1 : std_logic;
    signal tlast_d1          : std_logic;
    signal tuser_d1          : std_logic;

    -- Registered output stage
    signal m_tdata_r  : std_logic_vector(DATA_WIDTH * NUM_TAPS - 1 downto 0);
    signal m_tvalid_r : std_logic;
    signal m_tlast_r  : std_logic;
    signal m_tuser_r  : std_logic;

begin

    -- -----------------------------------------------------------------------
    -- Back-pressure: stall unless all downstream ports are ready
    -- -----------------------------------------------------------------------
    p_all_ready : process (m_tready)
        variable v : std_logic;
    begin
        v := '1';
        for i in 0 to NUM_TAPS - 1 loop
            v := v and m_tready(i);
        end loop;
        all_ready <= v;
    end process;

    s_tready      <= all_ready;
    pixel_accepted <= s_tvalid and all_ready;

    -- -----------------------------------------------------------------------
    -- Column / row / BRAM-row counters
    -- -----------------------------------------------------------------------
    p_counters : process (clk)
    begin
        if rising_edge(clk) then
            if rst = '1' then
                col_cnt    <= 0;
                row_cnt    <= 0;
                buf_wr_row <= 0;
            elsif pixel_accepted = '1' then
                if col_cnt = LINE_WIDTH - 1 then
                    col_cnt <= 0;
                    if row_cnt = FRAME_HEIGHT - 1 then
                        row_cnt    <= 0;
                        buf_wr_row <= 0;
                    else
                        row_cnt <= row_cnt + 1;
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
    -- BRAM address generation
    -- Write: current column. Read: next column (pre-fetch compensates for
    -- the 1-clock BRAM read latency, so data for col C arrives the cycle
    -- that col C is presented to win_buf).
    -- -----------------------------------------------------------------------
    lb_wr_col <= col_cnt;
    lb_rd_col <= 0 when col_cnt = LINE_WIDTH - 1 else col_cnt + 1;

    -- -----------------------------------------------------------------------
    -- Zero-extend control for win_buf
    -- new_row: '1' on the first pixel of a new row so win_buf zeroes out the
    --          KERN_COLS-1 stale column positions instead of shifting them in.
    -- row_valid(r): '1' when the BRAM slot r holds a valid older row within
    --               the current frame.  When '0', win_buf inserts 0 instead of
    --               stale BRAM data from the previous frame.
    -- -----------------------------------------------------------------------
    new_row <= '1' when col_cnt = 0 and pixel_accepted = '1' else '0';

    p_row_valid : process (row_cnt)
    begin
        for r in 0 to NUM_BUF_ROWS - 1 loop
            if row_cnt >= KERN_ROWS - 1 - r then
                row_valid(r) <= '1';
            else
                row_valid(r) <= '0';
            end if;
        end loop;
    end process p_row_valid;

    -- -----------------------------------------------------------------------
    -- 1-cycle delay stage
    -- win_buf's p_shift and this process both fire at the same rising edge.
    -- VHDL delta-cycle ordering means wb_tap_out (combinational on win) still
    -- reflects the OLD window at delta 0.  Registering pixel_accepted here
    -- and gating p_out_reg on the delayed version means p_out_reg reads
    -- wb_tap_out one cycle later, after p_shift has updated win.
    -- -----------------------------------------------------------------------
    p_delay : process (clk)
    begin
        if rising_edge(clk) then
            if rst = '1' then
                pixel_accepted_d1 <= '0';
                tlast_d1          <= '0';
                tuser_d1          <= '0';
            elsif all_ready = '1' then
                pixel_accepted_d1 <= pixel_accepted;
                tlast_d1          <= s_tlast and pixel_accepted;
                tuser_d1          <= s_tuser and pixel_accepted;
            end if;
        end if;
    end process p_delay;

    -- -----------------------------------------------------------------------
    -- Registered output stage
    -- Reads wb_tap_out one cycle after acceptance so the value is stable
    -- (win_buf p_shift has already committed the new window in the previous
    -- clock's delta-1).  Held when all_ready='0' (AXI-S: no TVALID retract).
    -- -----------------------------------------------------------------------
    p_out_reg : process (clk)
    begin
        if rising_edge(clk) then
            if rst = '1' then
                m_tdata_r  <= (others => '0');
                m_tvalid_r <= '0';
                m_tlast_r  <= '0';
                m_tuser_r  <= '0';
            elsif all_ready = '1' then
                m_tdata_r  <= wb_tap_out;
                m_tvalid_r <= pixel_accepted_d1;
                m_tlast_r  <= tlast_d1;
                m_tuser_r  <= tuser_d1;
            end if;
        end if;
    end process p_out_reg;

    m_tdata  <= m_tdata_r;
    m_tvalid <= m_tvalid_r;
    m_tlast  <= m_tlast_r;
    m_tuser  <= m_tuser_r;

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
            clk      => clk,
            wr_en    => pixel_accepted,
            wr_row   => buf_wr_row,
            wr_col   => lb_wr_col,
            wr_data  => s_tdata,
            row_base => buf_wr_row,
            rd_col   => lb_rd_col,
            rd_data  => lb_rd_data
        );

    u_win_buf : entity work.win_buf
        generic map (
            DATA_WIDTH => DATA_WIDTH,
            KERN_ROWS  => KERN_ROWS,
            KERN_COLS  => KERN_COLS
        )
        port map (
            clk       => clk,
            rst       => rst,
            shift_en  => pixel_accepted,
            new_row   => new_row,
            row_valid => row_valid,
            pix_in    => s_tdata,
            buf_rows  => lb_rd_data,
            tap_out   => wb_tap_out
        );

end architecture rtl;
