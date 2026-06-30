-- Top-level 2D convolution window extractor — Phase 4.
-- Adds EDGE_MODE and FLUSH generics to the Phase 3 base.
--
-- EDGE_MODE:
--   "ZERO"      — out-of-bounds taps read as 0 (Phase 3 behaviour).
--                 Implemented via win_buf's new_row / row_valid mechanism;
--                 the output mux passes m_tdata_r through unchanged.
--   "REPLICATE" — out-of-bounds taps clamp to the nearest in-bounds tap.
--                 Only top (coord_y < 0) and left (coord_x < 0) boundaries
--                 are ever OOB in a causal streaming pipeline; right and
--                 bottom coordinates are always in-bounds for valid col/row.
--                 Implemented in combinational p_edge_out using delayed
--                 pixel coordinates (m_col_r, m_row_r).
--   "TOROIDAL"  — wrap coordinates mod frame dimensions.  Wrapped pixels at
--                 the far right or bottom of a previous frame are not present
--                 in the causal streaming window; those taps fall back to the
--                 zero already stored by win_buf for OOB positions.
--
-- FLUSH (requires KERN_ROWS >= 2):
--   false — no action after the last real frame pixel (Phase 3 behaviour).
--   true  — after the last pixel of each frame, injects KERN_ROWS-1 dummy
--           zero-rows so that the final KERN_ROWS-1 real rows each produce
--           a fully shifted output window.  Real input is stalled (s_tready
--           deasserted) until flush completes.  SOF (s_tuser='1') resets
--           row_cnt and buf_wr_row on the first pixel of the next frame to
--           re-arm row_valid correctly after flush has left row_cnt non-zero.
--
-- Coordinate metasystem:
--   col_cnt_d1 / row_cnt_d1 — 1-cycle-delayed coordinates registered in
--   p_delay; captured into m_col_r / m_row_r in p_out_reg alongside the
--   registered tap data.  p_edge_out computes each tap's image coordinate
--   combinatorially from these to apply EDGE_MODE remapping.
--
-- Pipeline (3 stages, unchanged from Phase 3):
--   Stage 1: pixel or flush accepted; win_buf shifts; BRAM write issued.
--   Stage 2: p_delay registers push / coords / flags.
--   Stage 3: p_out_reg captures wb_tap_out and coords; p_edge_out applies EDGE_MODE.
--
-- All other architecture (BRAM row ordering, delta-cycle fix, row_valid,
-- back-pressure) is identical to Phase 3.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity conv2d is
    generic (
        DATA_WIDTH   : positive := 8;
        KERN_ROWS    : positive := 3;
        KERN_COLS    : positive := 3;
        LINE_WIDTH   : positive := 1920;
        FRAME_HEIGHT : positive := 1080;
        EDGE_MODE    : string   := "ZERO";   -- "ZERO" | "REPLICATE" | "TOROIDAL"
        FLUSH        : boolean  := false     -- inject KERN_ROWS-1 dummy rows after frame
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

    -- FLUSH FSM (only activated when FLUSH=true and KERN_ROWS >= 2)
    signal flushing      : boolean   := false;
    signal flush_push    : std_logic := '0';
    signal flush_col_cnt : natural range 0 to LINE_WIDTH  - 1 := 0;
    signal flush_row_cnt : natural range 0 to KERN_ROWS   - 1 := 0;

    -- Unified push signal: real pixel accepted OR flush pixel being injected
    signal push      : std_logic;
    signal push_data : std_logic_vector(DATA_WIDTH - 1 downto 0);

    -- line_buf ports
    signal lb_wr_col  : natural range 0 to LINE_WIDTH - 1;
    signal lb_rd_col  : natural range 0 to LINE_WIDTH - 1;
    signal lb_rd_data : std_logic_vector(DATA_WIDTH * NUM_BUF_ROWS - 1 downto 0);

    -- win_buf zero-extend control
    signal new_row   : std_logic;
    signal row_valid : std_logic_vector(NUM_BUF_ROWS - 1 downto 0);

    -- win_buf inputs after EDGE_MODE gating:
    --   TOROIDAL passes '0' / all-ones so the shift register and BRAM
    --   data flow through unmasked — causal wrap falls out naturally.
    signal wb_new_row   : std_logic;
    signal wb_row_valid : std_logic_vector(NUM_BUF_ROWS - 1 downto 0);

    -- TOROIDAL armed flag: latches '1' once BRAM has been fully written for
    -- the first time (row_cnt >= KERN_ROWS-1).  Before that, xsim BRAM is
    -- uninitialised ('U'); normal row_valid masking (same as ZERO) keeps the
    -- unwritten rows zeroed, matching the golden model's push_history returning
    -- 0 for indices before the first push.  Once armed it never resets, so
    -- subsequent frames read real BRAM data (previous-frame wrap = causal TOROIDAL).
    signal toroidal_armed : std_logic := '0';

    -- win_buf output
    signal wb_tap_out : std_logic_vector(DATA_WIDTH * NUM_TAPS - 1 downto 0);

    -- 1-cycle delay stage: Bug 2 fix + Phase 4 coordinate delay
    signal pixel_accepted_d1 : std_logic;
    signal tlast_d1          : std_logic;
    signal tuser_d1          : std_logic;
    signal col_cnt_d1        : natural range 0 to LINE_WIDTH               - 1;
    signal row_cnt_d1        : natural range 0 to FRAME_HEIGHT + KERN_ROWS - 2;

    -- Registered output stage
    signal m_tdata_r  : std_logic_vector(DATA_WIDTH * NUM_TAPS - 1 downto 0);
    signal m_tvalid_r : std_logic;
    signal m_tlast_r  : std_logic;
    signal m_tuser_r  : std_logic;
    signal m_col_r    : natural range 0 to LINE_WIDTH               - 1;
    signal m_row_r    : natural range 0 to FRAME_HEIGHT + KERN_ROWS - 2;

begin

    -- TOROIDAL: disable the new_row zero-clear and row_valid masking once armed
    -- so the shift register retains previous-row tail pixels (causal col wrap)
    -- and BRAM retains previous-frame row data (causal row wrap).
    -- Before armed (first KERN_ROWS-1 rows of frame 0), use normal row_valid
    -- masking so unwritten BRAM ('U' in xsim) does not propagate to output.
    wb_new_row   <= '0'             when EDGE_MODE = "TOROIDAL"
                    else new_row;
    wb_row_valid <= (others => '1') when EDGE_MODE = "TOROIDAL" and toroidal_armed = '1'
                    else row_valid;

    p_tor_arm : process (clk)
    begin
        if rising_edge(clk) then
            if rst = '1' then
                toroidal_armed <= '0';
            elsif EDGE_MODE = "TOROIDAL" and row_cnt >= KERN_ROWS - 1 then
                toroidal_armed <= '1';
            end if;
        end if;
    end process p_tor_arm;

    -- -----------------------------------------------------------------------
    -- Back-pressure: stall unless all downstream ports are ready.
    -- During flush, also deassert s_tready to hold real input.
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

    s_tready      <= all_ready and not flush_push;
    pixel_accepted <= s_tvalid and all_ready and not flush_push;
    push           <= pixel_accepted or (flush_push and all_ready);
    push_data      <= s_tdata when pixel_accepted = '1' else (others => '0');

    -- -----------------------------------------------------------------------
    -- FLUSH FSM
    -- Triggers after the last pixel of a frame when FLUSH=true and KERN_ROWS>=2.
    -- Injects KERN_ROWS-1 zero-filled dummy rows into the pipeline.
    -- Stalls when all_ready='0' (honours back-pressure during flush).
    -- -----------------------------------------------------------------------
    p_flush : process (clk)
    begin
        if rising_edge(clk) then
            if rst = '1' then
                flushing      <= false;
                flush_push    <= '0';
                flush_col_cnt <= 0;
                flush_row_cnt <= 0;
            else
                if not flushing then
                    if FLUSH and KERN_ROWS > 1
                       and pixel_accepted  = '1'
                       and row_cnt         = FRAME_HEIGHT - 1
                       and col_cnt         = LINE_WIDTH   - 1 then
                        flushing      <= true;
                        flush_push    <= '1';
                        flush_col_cnt <= 0;
                        flush_row_cnt <= 0;
                    end if;
                else
                    if all_ready = '1' then
                        if flush_col_cnt = LINE_WIDTH - 1 then
                            flush_col_cnt <= 0;
                            if flush_row_cnt = KERN_ROWS - 2 then
                                flushing   <= false;
                                flush_push <= '0';
                            else
                                flush_row_cnt <= flush_row_cnt + 1;
                            end if;
                        else
                            flush_col_cnt <= flush_col_cnt + 1;
                        end if;
                    end if;
                end if;
            end if;
        end if;
    end process p_flush;

    -- -----------------------------------------------------------------------
    -- Column / row / BRAM-row counters.
    -- Advance on push (real or flush pixel).
    -- SOF (s_tuser='1') resets row_cnt and buf_wr_row to re-arm row_valid
    -- correctly after FLUSH=true has left them at a non-zero value.
    -- -----------------------------------------------------------------------
    p_counters : process (clk)
    begin
        if rising_edge(clk) then
            if rst = '1' then
                col_cnt    <= 0;
                row_cnt    <= 0;
                buf_wr_row <= 0;
            elsif push = '1' then
                if pixel_accepted = '1' and s_tuser = '1' then
                    col_cnt    <= (col_cnt + 1) mod LINE_WIDTH;
                    row_cnt    <= 0;
                    buf_wr_row <= 0;
                elsif col_cnt = LINE_WIDTH - 1 then
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
    -- -----------------------------------------------------------------------
    lb_wr_col <= col_cnt;
    lb_rd_col <= 0 when col_cnt = LINE_WIDTH - 1 else col_cnt + 1;

    -- -----------------------------------------------------------------------
    -- Zero-extend control for win_buf
    -- -----------------------------------------------------------------------
    new_row <= '1' when col_cnt = 0 and push = '1' else '0';

    -- During flush, force all row_valid bits high so win_buf reads real BRAM
    -- data (last KERN_ROWS-1 rows of the frame) rather than zeroing them out.
    -- row_cnt wraps to 0 when flush starts, which would otherwise re-arm
    -- row_valid from scratch and incorrectly blank the older row taps.
    p_row_valid : process (row_cnt, flushing)
    begin
        for r in 0 to NUM_BUF_ROWS - 1 loop
            if flushing or row_cnt >= KERN_ROWS - 1 - r then
                row_valid(r) <= '1';
            else
                row_valid(r) <= '0';
            end if;
        end loop;
    end process p_row_valid;

    -- -----------------------------------------------------------------------
    -- 1-cycle delay stage
    -- Delays push, flags, and pixel coordinates so that p_out_reg reads
    -- wb_tap_out one cycle after win_buf's p_shift has committed the new
    -- window (Bug 2 fix).  Also delays col_cnt / row_cnt so that the
    -- registered coordinates in m_col_r / m_row_r align with m_tdata_r.
    -- For flush pixels, tlast is derived from flush_col_cnt position.
    -- -----------------------------------------------------------------------
    p_delay : process (clk)
    begin
        if rising_edge(clk) then
            if rst = '1' then
                pixel_accepted_d1 <= '0';
                tlast_d1          <= '0';
                tuser_d1          <= '0';
                col_cnt_d1        <= 0;
                row_cnt_d1        <= 0;
            elsif all_ready = '1' then
                pixel_accepted_d1 <= push;
                col_cnt_d1        <= col_cnt;
                -- During flush, row_cnt has wrapped to 0; supply the virtual
                -- row (FRAME_HEIGHT + flush_row_cnt) so p_edge_out does not
                -- mistake flush outputs for top-of-frame and wrongly clamp.
                -- At SOF, p_counters resets row_cnt to 0 in this same delta,
                -- but VHDL processes read pre-update values, so row_cnt still
                -- holds the post-flush residual (KERN_ROWS-1).  Detect SOF
                -- explicitly and supply 0 so p_edge_out uses the correct
                -- coordinate for the first real output of each new frame.
                if flushing then
                    row_cnt_d1 <= FRAME_HEIGHT + flush_row_cnt;
                elsif pixel_accepted = '1' and s_tuser = '1' then
                    row_cnt_d1 <= 0;
                else
                    row_cnt_d1 <= row_cnt;
                end if;
                if flushing then
                    tuser_d1 <= '0';
                    if flush_col_cnt = LINE_WIDTH - 1 then
                        tlast_d1 <= '1';
                    else
                        tlast_d1 <= '0';
                    end if;
                else
                    tlast_d1 <= s_tlast and pixel_accepted;
                    tuser_d1 <= s_tuser and pixel_accepted;
                end if;
            end if;
        end if;
    end process p_delay;

    -- -----------------------------------------------------------------------
    -- Registered output stage
    -- -----------------------------------------------------------------------
    p_out_reg : process (clk)
    begin
        if rising_edge(clk) then
            if rst = '1' then
                m_tdata_r  <= (others => '0');
                m_tvalid_r <= '0';
                m_tlast_r  <= '0';
                m_tuser_r  <= '0';
                m_col_r    <= 0;
                m_row_r    <= 0;
            elsif all_ready = '1' then
                m_tdata_r  <= wb_tap_out;
                m_tvalid_r <= pixel_accepted_d1;
                m_tlast_r  <= tlast_d1;
                m_tuser_r  <= tuser_d1;
                m_col_r    <= col_cnt_d1;
                m_row_r    <= row_cnt_d1;
            end if;
        end if;
    end process p_out_reg;

    m_tvalid <= m_tvalid_r;
    m_tlast  <= m_tlast_r;
    m_tuser  <= m_tuser_r;

    -- -----------------------------------------------------------------------
    -- Edge mode output mux (combinational)
    --
    -- For each tap (r, c) in the registered window m_tdata_r, computes the
    -- tap's original image coordinate:
    --   coord_x = m_col_r - c          (c=0 is most-recent pixel)
    --   coord_y = m_row_r - (KERN_ROWS-1-r) (r=KERN_ROWS-1 is current row)
    --
    -- ZERO:      pass through; win_buf already zeroed OOB positions via
    --            new_row / row_valid.
    -- REPLICATE: when OOB, clamp coord to nearest valid axis and derive the
    --            source tap indices:
    --              coord_x < 0  ->  c_src = m_col_r  (leftmost valid col in window)
    --              coord_y < 0  ->  r_src = KERN_ROWS-1 - m_row_r  (top edge row)
    -- TOROIDAL:  OOB-wrapped pixels at right/bottom are not in the causal
    --            window; fall back to what win_buf stored (zero for OOB).
    -- -----------------------------------------------------------------------
    p_edge_out : process (m_tdata_r, m_col_r, m_row_r)
        variable cx  : integer;
        variable cy  : integer;
        variable rs  : integer;
        variable cs  : integer;
        variable pix : std_logic_vector(DATA_WIDTH - 1 downto 0);
    begin
        for r in 0 to KERN_ROWS - 1 loop
            for c in 0 to KERN_COLS - 1 loop
                cx := m_col_r - c;
                cy := m_row_r - (KERN_ROWS - 1 - r);

                if EDGE_MODE = "REPLICATE" and (cx < 0 or cy < 0) then
                    if cx < 0 then cs := m_col_r; else cs := c; end if;
                    if cy < 0 then rs := KERN_ROWS - 1 - m_row_r; else rs := r; end if;
                    pix := m_tdata_r((rs * KERN_COLS + cs + 1) * DATA_WIDTH - 1
                                      downto (rs * KERN_COLS + cs) * DATA_WIDTH);
                else
                    pix := m_tdata_r((r * KERN_COLS + c + 1) * DATA_WIDTH - 1
                                      downto (r * KERN_COLS + c) * DATA_WIDTH);
                end if;

                m_tdata((r * KERN_COLS + c + 1) * DATA_WIDTH - 1
                         downto (r * KERN_COLS + c) * DATA_WIDTH) <= pix;
            end loop;
        end loop;
    end process p_edge_out;

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
            wr_en    => push,
            wr_row   => buf_wr_row,
            wr_col   => lb_wr_col,
            wr_data  => push_data,
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
            shift_en  => push,
            new_row   => wb_new_row,
            row_valid => wb_row_valid,
            pix_in    => push_data,
            buf_rows  => lb_rd_data,
            tap_out   => wb_tap_out
        );

end architecture rtl;
