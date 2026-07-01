-- Top-level 2D convolution window extractor — centred-window design.
--
-- Output convention: for each input pixel P at image position (row r, col c),
-- the design produces one output window where:
--   tap[tr][tc] = pixel at image position (r + tr - HALF_R, c + tc - HALF_C)
--   tap[0][0]           = top-left  neighbour of P
--   tap[HALF_R][HALF_C] = P itself
--   tap[KR-1][KC-1]     = bottom-right neighbour of P
-- HALF_R = (KERN_ROWS-1)/2, HALF_C = (KERN_COLS-1)/2  (integer division).
--
-- All four frame edges produce OOB taps; EDGE_MODE handles them:
--   "ZERO"      -- OOB positions output 0.
--   "REPLICATE" -- OOB positions clamp to the nearest frame edge pixel.
--   "TOROIDAL"  -- left/top OOB wraps mod frame dims (causal approximation);
--                  right/bottom OOB falls back to 0 (future data unavailable).
--
-- Effective per-row push count = LINE_WIDTH + HALF_C (EFF_WIDTH):
--   cols 0..LINE_WIDTH-1   : real pixels from s_tdata
--   cols LINE_WIDTH..EFF_WIDTH-1 : dummy zero pixels injected internally
-- This produces the right-edge pixel outputs without extra buffering.
--
-- FLUSH=true: after last real row, injects HALF_R dummy zero-rows (each
-- EFF_WIDTH events wide) so the bottom HALF_R rows also complete their
-- windows within the same frame.
--
-- BRAM count: KERN_ROWS-1 (same as bottom-right design).
-- Pipeline depth: 3 stages (accept -> delay -> output).
-- Back-pressure: all m_tready AND-gated; BRAM read gated by all_ready.

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
        EDGE_MODE    : string   := "ZERO";
        FLUSH        : boolean  := false
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
    constant HALF_R       : natural  := (KERN_ROWS - 1) / 2;
    constant HALF_C       : natural  := (KERN_COLS - 1) / 2;
    -- Effective columns per row: LINE_WIDTH real + HALF_C dummy column pixels.
    constant EFF_WIDTH    : positive := LINE_WIDTH + HALF_C;

    signal col_cnt    : natural range 0 to EFF_WIDTH    - 1;
    signal row_cnt    : natural range 0 to FRAME_HEIGHT - 1;
    signal buf_wr_row : natural range 0 to NUM_BUF_ROWS - 1;

    signal all_ready      : std_logic;
    signal pixel_accepted : std_logic;
    signal col_pad_push   : std_logic;
    signal push           : std_logic;
    signal push_data      : std_logic_vector(DATA_WIDTH - 1 downto 0);

    -- FLUSH FSM
    signal flushing      : boolean   := false;
    signal flush_push    : std_logic := '0';
    signal flush_row_cnt : natural range 0 to KERN_ROWS - 1 := 0;

    -- line_buf signals
    signal lb_wr_en   : std_logic;
    signal lb_wr_col  : natural range 0 to LINE_WIDTH - 1;
    signal lb_rd_col  : natural range 0 to LINE_WIDTH - 1;
    signal lb_rd_data : std_logic_vector(DATA_WIDTH * NUM_BUF_ROWS - 1 downto 0);

    -- win_buf zero-extend control
    signal new_row    : std_logic;
    signal row_valid  : std_logic_vector(NUM_BUF_ROWS - 1 downto 0);
    signal wb_new_row   : std_logic;
    signal wb_row_valid : std_logic_vector(NUM_BUF_ROWS - 1 downto 0);

    signal toroidal_armed : std_logic := '0';
    signal wb_tap_out     : std_logic_vector(DATA_WIDTH * NUM_TAPS - 1 downto 0);

    -- 1-cycle delay stage
    signal push_d1      : std_logic;
    signal valid_out_d1 : std_logic;
    signal tlast_d1     : std_logic;
    signal tuser_d1     : std_logic;
    signal col_cnt_d1   : natural range 0 to EFF_WIDTH    - 1;
    -- row_cnt_d1 extended to cover flush virtual rows FRAME_HEIGHT..FRAME_HEIGHT+KERN_ROWS-2
    signal row_cnt_d1   : natural range 0 to FRAME_HEIGHT + KERN_ROWS - 2;

    -- Registered output (coordinates valid only when m_tvalid_r='1')
    signal m_tdata_r  : std_logic_vector(DATA_WIDTH * NUM_TAPS - 1 downto 0);
    signal m_tvalid_r : std_logic;
    signal m_tlast_r  : std_logic;
    signal m_tuser_r  : std_logic;
    signal m_col_r    : natural range 0 to LINE_WIDTH   - 1;
    signal m_row_r    : natural range 0 to FRAME_HEIGHT - 1;

begin

    -- -----------------------------------------------------------------------
    -- Back-pressure
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

    -- Accept real pixels only when in real-column range and not flushing.
    pixel_accepted <= s_tvalid and all_ready and not flush_push
                      when col_cnt < LINE_WIDTH else '0';

    -- Dummy column pixels injected when col_cnt is in the padding range.
    col_pad_push   <= all_ready and not flush_push
                      when (HALF_C > 0 and col_cnt >= LINE_WIDTH) else '0';

    s_tready  <= all_ready and not flush_push when col_cnt < LINE_WIDTH else '0';

    push      <= pixel_accepted or col_pad_push or (flush_push and all_ready);
    push_data <= s_tdata when pixel_accepted = '1' else (others => '0');

    -- -----------------------------------------------------------------------
    -- TOROIDAL armed flag
    -- -----------------------------------------------------------------------
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

    -- For TOROIDAL: disable new_row clear (retains row tail for column wrap)
    -- and disable row_valid masking once BRAM has been fully written once.
    -- During dummy-column pushes force row_valid=0 regardless of TOROIDAL so
    -- that OOB right-side taps in win_buf are zero (p_edge_out remaps them).
    wb_new_row <= '0' when EDGE_MODE = "TOROIDAL" else new_row;

    wb_row_valid <= (others => '1')
                    when EDGE_MODE = "TOROIDAL"
                         and toroidal_armed = '1'
                         and col_cnt < LINE_WIDTH
                    else row_valid;

    -- -----------------------------------------------------------------------
    -- FLUSH FSM — injects HALF_R dummy rows after the last real row.
    -- -----------------------------------------------------------------------
    p_flush : process (clk)
    begin
        if rising_edge(clk) then
            if rst = '1' then
                flushing      <= false;
                flush_push    <= '0';
                flush_row_cnt <= 0;
            else
                if not flushing then
                    if FLUSH and HALF_R > 0
                       and push = '1'
                       and row_cnt    = FRAME_HEIGHT - 1
                       and col_cnt    = EFF_WIDTH - 1 then
                        flushing      <= true;
                        flush_push    <= '1';
                        flush_row_cnt <= 0;
                    end if;
                else
                    if all_ready = '1' and col_cnt = EFF_WIDTH - 1 then
                        if flush_row_cnt = HALF_R - 1 then
                            flushing   <= false;
                            flush_push <= '0';
                        else
                            flush_row_cnt <= flush_row_cnt + 1;
                        end if;
                    end if;
                end if;
            end if;
        end if;
    end process p_flush;

    -- -----------------------------------------------------------------------
    -- Column / row / BRAM-row counters.
    -- col_cnt cycles 0..EFF_WIDTH-1 for every row (real and flush).
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
                    col_cnt <= (col_cnt + 1) mod EFF_WIDTH;
                    row_cnt <= 0;
                elsif col_cnt = EFF_WIDTH - 1 then
                    col_cnt <= 0;
                    if row_cnt = FRAME_HEIGHT - 1 then
                        row_cnt <= 0;
                    else
                        row_cnt <= row_cnt + 1;
                    end if;
                    if buf_wr_row = NUM_BUF_ROWS - 1 then
                        buf_wr_row <= 0;
                    else
                        buf_wr_row <= buf_wr_row + 1;
                    end if;
                else
                    col_cnt <= col_cnt + 1;
                end if;
            end if;
        end if;
    end process p_counters;

    -- -----------------------------------------------------------------------
    -- BRAM addressing.
    -- Write suppressed for dummy column positions (col_cnt >= LINE_WIDTH).
    -- Read one column ahead (synchronous-read latency compensation).
    -- During dummy-col or flush, lb_rd_col=0; row_valid=0 zeroes older rows.
    -- -----------------------------------------------------------------------
    lb_wr_en  <= push when col_cnt < LINE_WIDTH else '0';
    lb_wr_col <= col_cnt when col_cnt < LINE_WIDTH else 0;
    lb_rd_col <= col_cnt + 1 when col_cnt < LINE_WIDTH - 1 else 0;

    -- -----------------------------------------------------------------------
    -- row_valid / new_row for win_buf zero-extend.
    -- During dummy-column pushes, force row_valid=0 so win_buf inserts zeros
    -- for older rows (right-OOB handled combinationally by p_edge_out).
    -- -----------------------------------------------------------------------
    new_row <= '1' when col_cnt = 0 and push = '1' else '0';

    p_row_valid : process (row_cnt, flushing, flush_row_cnt, col_cnt)
    begin
        for r in 0 to NUM_BUF_ROWS - 1 loop
            if col_cnt >= LINE_WIDTH then
                row_valid(r) <= '0';
            elsif flushing then
                if FRAME_HEIGHT + flush_row_cnt >= KERN_ROWS - 1 - r then
                    row_valid(r) <= '1';
                else
                    row_valid(r) <= '0';
                end if;
            elsif row_cnt >= KERN_ROWS - 1 - r then
                row_valid(r) <= '1';
            else
                row_valid(r) <= '0';
            end if;
        end loop;
    end process p_row_valid;

    -- -----------------------------------------------------------------------
    -- 1-cycle delay stage.
    -- Computes whether this push produces a valid in-frame output pixel
    -- and what its coordinates are; results registered for p_out_reg.
    -- -----------------------------------------------------------------------
    p_delay : process (clk)
        variable ocv : integer;   -- output column = col_cnt - HALF_C
        variable orv : integer;   -- output row    = virtual_row - HALF_R
        variable vrv : integer;   -- virtual row (accounts for flush offset)
    begin
        if rising_edge(clk) then
            if rst = '1' then
                push_d1      <= '0';
                valid_out_d1 <= '0';
                tlast_d1     <= '0';
                tuser_d1     <= '0';
                col_cnt_d1   <= 0;
                row_cnt_d1   <= 0;
            elsif all_ready = '1' then
                push_d1    <= push;
                col_cnt_d1 <= col_cnt;

                -- Virtual row coordinate for coordinate tracking.
                -- Flush uses FRAME_HEIGHT + flush_row_cnt as the virtual row.
                -- SOF resets output row to 0 (col_cnt_d1 will reflect col 0).
                if flushing then
                    vrv := FRAME_HEIGHT + flush_row_cnt;
                elsif pixel_accepted = '1' and s_tuser = '1' then
                    vrv := 0;
                else
                    vrv := row_cnt;
                end if;
                row_cnt_d1 <= vrv;

                -- Output pixel coordinate offsets.
                ocv := integer(col_cnt) - integer(HALF_C);
                orv := vrv - integer(HALF_R);

                -- Output pixel is in-frame when both offsets are in [0, dim-1].
                if push = '1'
                   and ocv >= 0 and ocv < LINE_WIDTH
                   and orv >= 0 and orv < FRAME_HEIGHT
                then
                    valid_out_d1 <= '1';
                else
                    valid_out_d1 <= '0';
                end if;

                -- TLAST: last valid output column of this row = col_cnt = EFF_WIDTH-1.
                if push = '1' and col_cnt = EFF_WIDTH - 1 then
                    tlast_d1 <= '1';
                else
                    tlast_d1 <= '0';
                end if;

                -- TUSER (SOF): output pixel (row=0, col=0) — ocv=0 and orv=0.
                if push = '1' and ocv = 0 and orv = 0 then
                    tuser_d1 <= '1';
                else
                    tuser_d1 <= '0';
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
                m_tvalid_r <= valid_out_d1;
                m_tlast_r  <= tlast_d1 and valid_out_d1;
                m_tuser_r  <= tuser_d1;
                -- Output coordinates (safe to read only when m_tvalid_r='1').
                if col_cnt_d1 >= HALF_C then
                    m_col_r <= col_cnt_d1 - HALF_C;
                else
                    m_col_r <= 0;
                end if;
                if row_cnt_d1 >= HALF_R then
                    m_row_r <= row_cnt_d1 - HALF_R;
                else
                    m_row_r <= 0;
                end if;
            end if;
        end if;
    end process p_out_reg;

    m_tvalid <= m_tvalid_r;
    m_tlast  <= m_tlast_r;
    m_tuser  <= m_tuser_r;

    -- -----------------------------------------------------------------------
    -- Edge mode output mux (combinational, centred-window coordinates).
    --
    -- Centred coordinate of tap[tr][tc]:
    --   coord_x = m_col_r + (tc - HALF_C)
    --   coord_y = m_row_r + (tr - HALF_R)
    --
    -- OOB on all four sides (left, right, top, bottom).
    -- ZERO: win_buf already stores 0 for all OOB positions — pass through.
    -- REPLICATE: clamp both coords to [0,dim-1]; derive source tap indices.
    -- TOROIDAL: causal; right/bottom OOB falls back to win_buf content (0).
    -- -----------------------------------------------------------------------
    p_edge_out : process (m_tdata_r, m_col_r, m_row_r)
        variable cx   : integer;
        variable cy   : integer;
        variable cxc  : integer;
        variable cyc  : integer;
        variable tc_s : integer;
        variable tr_s : integer;
        variable pix  : std_logic_vector(DATA_WIDTH - 1 downto 0);
    begin
        for tr in 0 to KERN_ROWS - 1 loop
            for tc in 0 to KERN_COLS - 1 loop
                cx := integer(m_col_r) + tc - integer(HALF_C);
                cy := integer(m_row_r) + tr - integer(HALF_R);

                if EDGE_MODE = "REPLICATE"
                   and (cx < 0 or cx >= LINE_WIDTH or cy < 0 or cy >= FRAME_HEIGHT)
                then
                    -- Clamp each axis independently.
                    if    cx < 0          then cxc := 0;
                    elsif cx >= LINE_WIDTH then cxc := LINE_WIDTH   - 1;
                    else                       cxc := cx;
                    end if;

                    if    cy < 0            then cyc := 0;
                    elsif cy >= FRAME_HEIGHT then cyc := FRAME_HEIGHT - 1;
                    else                         cyc := cy;
                    end if;

                    -- Convert clamped image coord back to tap index.
                    tc_s := cxc - integer(m_col_r) + integer(HALF_C);
                    tr_s := cyc - integer(m_row_r) + integer(HALF_R);

                    pix := m_tdata_r((tr_s * KERN_COLS + tc_s + 1) * DATA_WIDTH - 1
                                      downto (tr_s * KERN_COLS + tc_s) * DATA_WIDTH);
                else
                    pix := m_tdata_r((tr * KERN_COLS + tc + 1) * DATA_WIDTH - 1
                                      downto (tr * KERN_COLS + tc) * DATA_WIDTH);
                end if;

                m_tdata((tr * KERN_COLS + tc + 1) * DATA_WIDTH - 1
                         downto (tr * KERN_COLS + tc) * DATA_WIDTH) <= pix;
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
            wr_en    => lb_wr_en,
            wr_row   => buf_wr_row,
            wr_col   => lb_wr_col,
            wr_data  => push_data,
            row_base => buf_wr_row,
            rd_en    => all_ready,
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
