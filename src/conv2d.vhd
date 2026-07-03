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
--   "EXTEND"    -- alias for "REPLICATE".
--   "TOROIDAL"  -- stream-linear wrap: row suffix/prefix supply horizontal OOB.
--
-- Effective per-row push count = LINE_WIDTH + HALF_C (EFF_WIDTH):
--   cols 0..LINE_WIDTH-1   : real pixels from s_tdata
--   cols LINE_WIDTH..EFF_WIDTH-1 : dummy zero pixels injected internally
-- This produces the right-edge pixel outputs without extra buffering.
--
-- FLUSH=true: after last real row, injects HALF_R dummy
-- zero-rows so the bottom HALF_R rows complete their windows within the frame.
-- FLUSH=false (streaming): bottom HALF_R rows of frame N are triggered by the
-- first HALF_R rows of frame N+1; no dummy rows injected.
-- TOROIDAL + FLUSH=true uses dummy rows, not next-frame real pixels.
--
-- BRAM count: KERN_ROWS-1 (same as bottom-right design).
-- Pipeline depth: 3 stages (accept -> delay -> output).
-- Back-pressure: single m_tready stalls the pipeline; BRAM read gated by all_ready.

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
        m_tready : in  std_logic;
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
    constant FRAME_PIXELS : positive := LINE_WIDTH * FRAME_HEIGHT;
    constant TOR_MAX_POS  : natural  := HALF_R * LINE_WIDTH + HALF_C;
    constant TOR_BUF_DEPTH : positive := 2 * TOR_MAX_POS + 1;
    constant STREAM_DIRECT : boolean := EDGE_MODE = "TOROIDAL"
                                      or (FRAME_HEIGHT <= HALF_R and not FLUSH);

    signal col_cnt    : natural range 0 to EFF_WIDTH    - 1;
    signal row_cnt    : natural range 0 to FRAME_HEIGHT - 1;
    signal buf_wr_row : natural range 0 to NUM_BUF_ROWS - 1;

    signal all_ready      : std_logic;
    signal pixel_accepted : std_logic;
    signal col_pad_push   : std_logic;
    signal push           : std_logic;
    signal push_data      : std_logic_vector(DATA_WIDTH - 1 downto 0);

    type prefix_t is array (0 to HALF_C) of std_logic_vector(DATA_WIDTH - 1 downto 0);
    signal row_prefix : prefix_t;
    signal prefix_pix : std_logic_vector(DATA_WIDTH - 1 downto 0);

    -- FLUSH FSM
    signal flushing      : boolean   := false;
    signal flush_push    : std_logic := '0';
    signal flush_row_cnt : natural range 0 to KERN_ROWS - 1 := 0;
    signal tor_flush_push : std_logic := '0';

    -- Streaming tail (FLUSH=false)
    signal tailing   : boolean := false;
    signal tail_cnt  : natural range 0 to KERN_ROWS - 1 := 0;

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

    signal toroidal_armed     : std_logic := '0';
    signal toroidal_row_count : natural range 0 to NUM_BUF_ROWS := 0;
    signal wb_tap_out     : std_logic_vector(DATA_WIDTH * NUM_TAPS - 1 downto 0);

    -- 1-cycle delay stage
    signal push_d1      : std_logic;
    signal valid_out_d1 : std_logic;
    signal tlast_d1     : std_logic;
    signal tuser_d1     : std_logic;
    signal col_cnt_d1   : natural range 0 to EFF_WIDTH    - 1;
    -- row_cnt_d1 extended to cover flush virtual rows FRAME_HEIGHT..FRAME_HEIGHT+KERN_ROWS-2
    signal row_cnt_d1   : natural range 0 to FRAME_HEIGHT + KERN_ROWS - 2;

    signal valid_out_d2 : std_logic;
    signal tlast_d2     : std_logic;
    signal tuser_d2     : std_logic;
    signal col_cnt_d2   : natural range 0 to EFF_WIDTH    - 1;
    signal row_cnt_d2   : natural range 0 to FRAME_HEIGHT + KERN_ROWS - 2;
    signal wb_tap_d2    : std_logic_vector(DATA_WIDTH * NUM_TAPS - 1 downto 0);

    -- Registered output (coordinates valid only when m_tvalid_r='1')
    signal m_tdata_r  : std_logic_vector(DATA_WIDTH * NUM_TAPS - 1 downto 0);
    signal m_tvalid_r : std_logic;
    signal m_tlast_r  : std_logic;
    signal m_tuser_r  : std_logic;
    signal m_col_r    : natural range 0 to LINE_WIDTH   - 1;
    signal m_row_r    : natural range 0 to FRAME_HEIGHT - 1;

    type tor_buf_t is array (0 to TOR_BUF_DEPTH - 1) of std_logic_vector(DATA_WIDTH - 1 downto 0);

    signal tor_buf      : tor_buf_t;
    signal tor_warm_cnt : natural range 0 to TOR_MAX_POS + 1 := 0;
    signal tor_real_cnt : natural range 0 to FRAME_PIXELS := 0;
    signal tor_out_cnt  : natural range 0 to FRAME_PIXELS := 0;
    signal tor_flush_cnt : natural range 0 to TOR_MAX_POS := 0;
    signal tor_tdata_r  : std_logic_vector(DATA_WIDTH * NUM_TAPS - 1 downto 0);
    signal tor_tvalid_r : std_logic := '0';
    signal tor_tlast_r  : std_logic := '0';
    signal tor_tuser_r  : std_logic := '0';

    signal edge_tdata   : std_logic_vector(DATA_WIDTH * NUM_TAPS - 1 downto 0);
    signal edge_tdata_r : std_logic_vector(DATA_WIDTH * NUM_TAPS - 1 downto 0);
    signal edge_tvalid_r : std_logic := '0';
    signal edge_tlast_r  : std_logic := '0';
    signal edge_tuser_r  : std_logic := '0';

begin

    all_ready <= m_tready;

    -- Accept real pixels only when in real-column range and not flushing.
    pixel_accepted <= s_tvalid and all_ready and not flush_push and not tor_flush_push
                      when (EDGE_MODE = "TOROIDAL" or col_cnt < LINE_WIDTH) else '0';

    -- Dummy column pixels injected when col_cnt is in the padding range.
    col_pad_push   <= all_ready and not flush_push
                      when (EDGE_MODE /= "TOROIDAL" and HALF_C > 0 and col_cnt >= LINE_WIDTH) else '0';

    s_tready  <= all_ready and not tor_flush_push when EDGE_MODE = "TOROIDAL" else
                 all_ready and not flush_push when col_cnt < LINE_WIDTH else '0';

    push      <= pixel_accepted or col_pad_push or (flush_push and all_ready)
                 or (tor_flush_push and all_ready);
    push_data <= s_tdata when pixel_accepted = '1'
                 else prefix_pix when EDGE_MODE = "TOROIDAL" and col_pad_push = '1'
                 else (others => '0');

    p_prefix_mux : process (col_cnt, row_prefix)
    begin
        prefix_pix <= (others => '0');
        if HALF_C > 0 and col_cnt >= LINE_WIDTH then
            prefix_pix <= row_prefix(col_cnt - LINE_WIDTH);
        end if;
    end process p_prefix_mux;

    p_prefix : process (clk)
    begin
        if rising_edge(clk) then
            if rst = '1' then
                for i in 0 to HALF_C loop
                    row_prefix(i) <= (others => '0');
                end loop;
            elsif pixel_accepted = '1' and col_cnt < HALF_C then
                row_prefix(col_cnt) <= s_tdata;
            elsif flush_push = '1' and all_ready = '1' and col_cnt < HALF_C then
                row_prefix(col_cnt) <= (others => '0');
            end if;
        end if;
    end process p_prefix;

    -- -----------------------------------------------------------------------
    -- TOROIDAL stream-row warmup.
    -- row_cnt is frame-local, so short frames may never reach KERN_ROWS-1.
    -- Count completed streamed rows across frame boundaries instead.
    -- -----------------------------------------------------------------------
    p_tor_arm : process (clk)
    begin
        if rising_edge(clk) then
            if rst = '1' then
                toroidal_armed     <= '0';
                toroidal_row_count <= 0;
            elsif EDGE_MODE = "TOROIDAL"
                  and push = '1'
                  and col_cnt = EFF_WIDTH - 1
                  and toroidal_armed = '0'
            then
                if toroidal_row_count = NUM_BUF_ROWS - 1 then
                    toroidal_armed     <= '1';
                    toroidal_row_count <= NUM_BUF_ROWS;
                else
                    toroidal_row_count <= toroidal_row_count + 1;
                end if;
            end if;
        end if;
    end process p_tor_arm;

    -- For TOROIDAL: disable new_row clear so left OOB columns keep the
    -- previous streamed row suffix.  FLUSH=true keeps row_valid masking active
    -- so frame boundaries do not mix.
    wb_new_row <= '0' when EDGE_MODE = "TOROIDAL" else new_row;

    wb_row_valid <= row_valid;

    -- -----------------------------------------------------------------------
    -- FLUSH FSM (FLUSH=true) and streaming tail (FLUSH=false).
    -- Mutually exclusive: at most one is active at a time.
    -- -----------------------------------------------------------------------
    p_flush : process (clk)
    begin
        if rising_edge(clk) then
            if rst = '1' then
                flushing      <= false;
                flush_push    <= '0';
                flush_row_cnt <= 0;
                tailing       <= false;
                tail_cnt      <= 0;
            else
                -- Activation: fire once at the last push of each real frame.
                if not flushing and not tailing then
                    if EDGE_MODE /= "TOROIDAL"
                       and FLUSH and HALF_R > 0
                       and push = '1'
                       and row_cnt = FRAME_HEIGHT - 1
                       and col_cnt = EFF_WIDTH - 1 then
                        flushing      <= true;
                        flush_push    <= '1';
                        flush_row_cnt <= 0;
                    elsif EDGE_MODE /= "TOROIDAL"
                       and not FLUSH and HALF_R > 0
                       and push = '1'
                       and row_cnt = FRAME_HEIGHT - 1
                       and col_cnt = EFF_WIDTH - 1 then
                        tailing  <= true;
                        tail_cnt <= 0;
                    end if;
                end if;
                -- FLUSH FSM: advance through dummy zero rows.
                if flushing then
                    if all_ready = '1' and col_cnt = EFF_WIDTH - 1 then
                        if flush_row_cnt = HALF_R - 1 then
                            flushing   <= false;
                            flush_push <= '0';
                        else
                            flush_row_cnt <= flush_row_cnt + 1;
                        end if;
                    end if;
                end if;
                -- Streaming tail: count real next-frame rows used as tail triggers.
                if tailing then
                    if push = '1' and col_cnt = EFF_WIDTH - 1 then
                        if tail_cnt = HALF_R - 1 then
                            tailing <= false;
                        else
                            tail_cnt <= tail_cnt + 1;
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
    -- During non-TOROIDAL dummy-col or flush, lb_rd_col=0; row_valid=0 zeroes
    -- older rows. TOROIDAL dummy columns walk the row prefix for older rows.
    -- -----------------------------------------------------------------------
    lb_wr_en  <= push when col_cnt < LINE_WIDTH else '0';
    lb_wr_col <= col_cnt when col_cnt < LINE_WIDTH else 0;
    lb_rd_col <= col_cnt + 1 when col_cnt < LINE_WIDTH - 1 else
                 (col_cnt - LINE_WIDTH + 1) mod LINE_WIDTH
                 when EDGE_MODE = "TOROIDAL"
                      and col_cnt >= LINE_WIDTH
                      and col_cnt < EFF_WIDTH - 1 else
                 0;

    -- -----------------------------------------------------------------------
    -- row_valid / new_row for win_buf zero-extend.
    -- During non-TOROIDAL dummy-column pushes, force row_valid=0 so win_buf
    -- inserts zeros. TOROIDAL keeps row validity so BRAM col 0 supplies row
    -- prefixes for right-edge stream-linear wrap.
    -- -----------------------------------------------------------------------
    new_row <= '1' when col_cnt = 0 and push = '1' else '0';

    p_row_valid : process (row_cnt, flushing, flush_row_cnt, col_cnt, tailing, toroidal_row_count)
    begin
        for r in 0 to NUM_BUF_ROWS - 1 loop
            if col_cnt >= LINE_WIDTH and EDGE_MODE /= "TOROIDAL" then
                row_valid(r) <= '0';
            elsif EDGE_MODE = "TOROIDAL" and not FLUSH then
                if toroidal_row_count >= KERN_ROWS - 1 - r then
                    row_valid(r) <= '1';
                else
                    row_valid(r) <= '0';
                end if;
            elsif tailing then
                row_valid(r) <= '1';   -- BRAM holds complete previous-frame data
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
                valid_out_d2 <= '0';
                tlast_d2     <= '0';
                tuser_d2     <= '0';
                col_cnt_d2   <= 0;
                row_cnt_d2   <= 0;
                wb_tap_d2    <= (others => '0');
            elsif all_ready = '1' then
                valid_out_d2 <= valid_out_d1;
                tlast_d2     <= tlast_d1;
                tuser_d2     <= tuser_d1;
                col_cnt_d2   <= col_cnt_d1;
                row_cnt_d2   <= row_cnt_d1;
                wb_tap_d2    <= wb_tap_out;

                push_d1    <= push;
                col_cnt_d1 <= col_cnt;
                -- Virtual row coordinate for coordinate tracking.
                -- Flush uses FRAME_HEIGHT + flush_row_cnt as the virtual row.
                -- SOF resets output row to 0 (col_cnt_d1 will reflect col 0).
                if tailing then
                    vrv := FRAME_HEIGHT + tail_cnt;
                elsif flushing then
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
                m_tdata_r  <= wb_tap_d2;
                m_tvalid_r <= valid_out_d2;
                m_tlast_r  <= tlast_d2 and valid_out_d2;
                m_tuser_r  <= tuser_d2;
                -- Output coordinates (safe to read only when m_tvalid_r='1').
                if col_cnt_d2 >= HALF_C then
                    m_col_r <= col_cnt_d2 - HALF_C;
                else
                    m_col_r <= 0;
                end if;
                if row_cnt_d2 >= HALF_R then
                    m_row_r <= row_cnt_d2 - HALF_R;
                else
                    m_row_r <= 0;
                end if;
            end if;
        end if;
    end process p_out_reg;

    -- -----------------------------------------------------------------------
    -- TOROIDAL stream-linear window path.
    --
    -- The TOROIDAL vectors use flat stream offsets, not independent row/column
    -- wrapping.  Delay output until the furthest positive tap is present, then
    -- fetch each tap by its flat offset from a shift register.  FLUSH=true
    -- appends zero samples after each frame; FLUSH=false lets the next frame
    -- provide the positive tail.
    -- -----------------------------------------------------------------------
    p_toroidal : process (clk)
        variable next_buf    : tor_buf_t;
        variable tap_offset  : integer;
        variable age         : integer;
        variable next_warm   : natural range 0 to TOR_MAX_POS + 1;
        variable next_real   : natural range 0 to FRAME_PIXELS;
        variable next_out    : natural range 0 to FRAME_PIXELS;
        variable next_flush  : natural range 0 to TOR_MAX_POS;
        variable do_push     : boolean;
        variable real_push   : boolean;
        variable flush_push_v : boolean;
        variable out_valid_v : boolean;
        variable center_idx  : integer;
        variable center_base : integer;
        variable center_local : integer;
        variable center_row_i : integer;
        variable center_col_i : integer;
        variable src_row_i   : integer;
        variable src_col_i   : integer;
        variable tap_flat    : integer;
        variable tap_y       : integer;
    begin
        if rising_edge(clk) then
            if rst = '1' then
                for i in 0 to TOR_BUF_DEPTH - 1 loop
                    tor_buf(i) <= (others => '0');
                end loop;
                tor_warm_cnt  <= 0;
                tor_real_cnt  <= 0;
                tor_out_cnt   <= 0;
                tor_flush_cnt <= 0;
                tor_flush_push <= '0';
                tor_tdata_r   <= (others => '0');
                tor_tvalid_r  <= '0';
                tor_tlast_r   <= '0';
                tor_tuser_r   <= '0';
            elsif all_ready = '1' then
                next_buf := tor_buf;
                next_warm := tor_warm_cnt;
                next_real := tor_real_cnt;
                next_out := tor_out_cnt;
                next_flush := tor_flush_cnt;
                real_push := STREAM_DIRECT and pixel_accepted = '1';
                flush_push_v := STREAM_DIRECT and tor_flush_push = '1';
                do_push := real_push or flush_push_v;

                tor_tvalid_r <= '0';
                tor_tlast_r  <= '0';
                tor_tuser_r  <= '0';

                if not STREAM_DIRECT then
                    tor_flush_push <= '0';
                elsif do_push then
                    for i in TOR_BUF_DEPTH - 1 downto 1 loop
                        next_buf(i) := next_buf(i - 1);
                    end loop;

                    if real_push then
                        next_buf(0) := s_tdata;
                    else
                        next_buf(0) := (others => '0');
                    end if;

                    if next_warm < TOR_MAX_POS + 1 then
                        next_warm := next_warm + 1;
                    end if;

                    if real_push then
                        if next_real < FRAME_PIXELS then
                            next_real := next_real + 1;
                        end if;
                        if FLUSH and next_real = FRAME_PIXELS then
                            next_flush := TOR_MAX_POS;
                        end if;
                    elsif next_flush > 0 then
                        next_flush := next_flush - 1;
                    end if;

                    out_valid_v := next_warm = TOR_MAX_POS + 1
                                   and ((not FLUSH) or next_out < FRAME_PIXELS);

                    if out_valid_v then
                        center_idx := integer(next_out);
                        for tr in 0 to KERN_ROWS - 1 loop
                            for tc in 0 to KERN_COLS - 1 loop
                                tap_offset := (tr - integer(HALF_R)) * LINE_WIDTH
                                              + tc - integer(HALF_C);
                                tap_flat := center_idx + tap_offset;
                                tap_y := center_idx / LINE_WIDTH
                                         + tr - integer(HALF_R);
                                age := integer(TOR_MAX_POS) - tap_offset;
                                if EDGE_MODE /= "TOROIDAL" then
                                    center_local := center_idx mod FRAME_PIXELS;
                                    center_base := center_idx - center_local;
                                    center_row_i := center_local / LINE_WIDTH;
                                    center_col_i := center_local mod LINE_WIDTH;
                                    src_row_i := center_row_i + tr - integer(HALF_R);
                                    src_col_i := center_col_i + tc - integer(HALF_C);

                                    if EDGE_MODE = "ZERO"
                                       and (src_row_i < 0
                                            or src_row_i >= FRAME_HEIGHT
                                            or src_col_i < 0
                                            or src_col_i >= LINE_WIDTH)
                                    then
                                        tor_tdata_r((tr * KERN_COLS + tc + 1) * DATA_WIDTH - 1
                                                     downto (tr * KERN_COLS + tc) * DATA_WIDTH)
                                            <= (others => '0');
                                    else
                                        if src_row_i < 0 then
                                            src_row_i := 0;
                                        elsif src_row_i >= FRAME_HEIGHT then
                                            src_row_i := FRAME_HEIGHT - 1;
                                        end if;

                                        if src_col_i < 0 then
                                            src_col_i := 0;
                                        elsif src_col_i >= LINE_WIDTH then
                                            src_col_i := LINE_WIDTH - 1;
                                        end if;

                                        tap_flat := center_base + src_row_i * LINE_WIDTH + src_col_i;
                                        age := integer(TOR_MAX_POS) - (tap_flat - center_idx);
                                        tor_tdata_r((tr * KERN_COLS + tc + 1) * DATA_WIDTH - 1
                                                     downto (tr * KERN_COLS + tc) * DATA_WIDTH)
                                            <= next_buf(age);
                                    end if;
                                elsif EDGE_MODE = "TOROIDAL"
                                      and FLUSH
                                      and (tap_y < 0
                                           or tap_y >= FRAME_HEIGHT
                                           or tap_flat < 0
                                           or tap_flat >= FRAME_PIXELS)
                                then
                                    tor_tdata_r((tr * KERN_COLS + tc + 1) * DATA_WIDTH - 1
                                                 downto (tr * KERN_COLS + tc) * DATA_WIDTH)
                                        <= (others => '0');
                                else
                                    tor_tdata_r((tr * KERN_COLS + tc + 1) * DATA_WIDTH - 1
                                                 downto (tr * KERN_COLS + tc) * DATA_WIDTH)
                                        <= next_buf(age);
                                end if;
                            end loop;
                        end loop;

                        tor_tvalid_r <= '1';
                        if next_out mod LINE_WIDTH = 0 then
                            tor_tuser_r <= '1';
                        end if;
                        if next_out mod LINE_WIDTH = LINE_WIDTH - 1 then
                            tor_tlast_r <= '1';
                        end if;

                        if next_out = FRAME_PIXELS - 1 then
                            next_out := 0;
                            if FLUSH then
                                next_warm := 0;
                                next_real := 0;
                                for i in 0 to TOR_BUF_DEPTH - 1 loop
                                    next_buf(i) := (others => '0');
                                end loop;
                            end if;
                        else
                            next_out := next_out + 1;
                        end if;
                    end if;

                    tor_buf <= next_buf;
                    tor_warm_cnt <= next_warm;
                    tor_real_cnt <= next_real;
                    tor_out_cnt <= next_out;
                    tor_flush_cnt <= next_flush;

                    if (FLUSH or not real_push) and next_flush > 0 then
                        tor_flush_push <= '1';
                    else
                        tor_flush_push <= '0';
                    end if;
                    if not FLUSH and flush_push_v and next_flush = 0 then
                        tor_real_cnt <= 0;
                    end if;
                elsif STREAM_DIRECT
                      and not FLUSH
                      and s_tvalid = '0'
                      and tor_real_cnt > 0
                      and tor_flush_cnt = 0
                      and HALF_C > 0
                then
                    tor_flush_cnt <= HALF_C;
                    tor_flush_push <= '1';
                else
                    tor_flush_push <= '0';
                end if;
            end if;
        end if;
    end process p_toroidal;

    m_tvalid <= tor_tvalid_r  when STREAM_DIRECT else edge_tvalid_r;
    m_tlast  <= tor_tlast_r   when STREAM_DIRECT else edge_tlast_r;
    m_tuser  <= tor_tuser_r   when STREAM_DIRECT else edge_tuser_r;
    m_tdata  <= tor_tdata_r   when STREAM_DIRECT else edge_tdata_r;

    -- -----------------------------------------------------------------------
    -- Edge mode output mux (combinational into edge_tdata; registered by p_edge_reg).
    --
    -- Centred coordinate of tap[tr][tc]:
    --   coord_x = m_col_r + (tc - HALF_C)
    --   coord_y = m_row_r + (tr - HALF_R)
    --
    -- OOB on all four sides (left, right, top, bottom).
    -- ZERO: win_buf already stores 0 for all OOB positions — pass through.
    -- REPLICATE/EXTEND: clamp both coords to [0,dim-1]; derive source tap indices.
    -- TOROIDAL: pass through the stream-linear window built by prefix columns
    -- and, when FLUSH=false, next-frame continuation.
    -- -----------------------------------------------------------------------
    p_edge_out : process (m_tdata_r, m_col_r, m_row_r)
        variable cx   : integer;
        variable cy   : integer;
        variable flat_idx : integer;
        variable src_row  : integer;
        variable src_col  : integer;
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
                flat_idx := integer(m_row_r) * LINE_WIDTH + integer(m_col_r)
                            + (tr - integer(HALF_R)) * LINE_WIDTH
                            + (tc - integer(HALF_C));

                if EDGE_MODE = "TOROIDAL"
                   and (flat_idx < 0
                        or (FLUSH and flat_idx >= FRAME_HEIGHT * LINE_WIDTH))
                then
                    pix := (others => '0');
                elsif EDGE_MODE = "TOROIDAL" and flat_idx < FRAME_HEIGHT * LINE_WIDTH then
                    src_row := flat_idx / LINE_WIDTH;
                    src_col := flat_idx mod LINE_WIDTH;
                    tr_s := src_row - integer(m_row_r) + integer(HALF_R);
                    tc_s := src_col - integer(m_col_r) + integer(HALF_C);

                    if tr_s >= 0 and tr_s < KERN_ROWS
                       and tc_s >= 0 and tc_s < KERN_COLS
                    then
                        pix := m_tdata_r((tr_s * KERN_COLS + tc_s + 1) * DATA_WIDTH - 1
                                          downto (tr_s * KERN_COLS + tc_s) * DATA_WIDTH);
                    else
                        pix := m_tdata_r((tr * KERN_COLS + tc + 1) * DATA_WIDTH - 1
                                          downto (tr * KERN_COLS + tc) * DATA_WIDTH);
                    end if;
                elsif (EDGE_MODE = "REPLICATE" or EDGE_MODE = "EXTEND")
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
                elsif EDGE_MODE = "ZERO"
                   and (cx < 0 or cx >= LINE_WIDTH or cy < 0 or cy >= FRAME_HEIGHT)
                then
                    pix := (others => '0');
                else
                    pix := m_tdata_r((tr * KERN_COLS + tc + 1) * DATA_WIDTH - 1
                                      downto (tr * KERN_COLS + tc) * DATA_WIDTH);
                end if;

                edge_tdata((tr * KERN_COLS + tc + 1) * DATA_WIDTH - 1
                            downto (tr * KERN_COLS + tc) * DATA_WIDTH) <= pix;
            end loop;
        end loop;
    end process p_edge_out;

    -- Register the edge-mux output to cut the combinational path for large kernels.
    p_edge_reg : process (clk)
    begin
        if rising_edge(clk) then
            if rst = '1' then
                edge_tvalid_r <= '0';
                edge_tlast_r  <= '0';
                edge_tuser_r  <= '0';
            elsif all_ready = '1' then
                edge_tdata_r  <= edge_tdata;
                edge_tvalid_r <= m_tvalid_r;
                edge_tlast_r  <= m_tlast_r;
                edge_tuser_r  <= m_tuser_r;
            end if;
        end if;
    end process p_edge_reg;

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
