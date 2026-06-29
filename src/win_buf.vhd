-- Assembles the current M x N pixel neighbourhood from:
--   - the live input pixel (top row, newest column)
--   - M-1 rows read from line_buf (older rows, accounting for 1-clock read latency)
--
-- Internal storage: M shift-register rows, each N stages deep (plain FFs).
-- M x N is small (e.g. 3x3 = 9 bytes) — BRAM would be wasteful here.
--
-- On each valid clock:
--   row[KERN_ROWS-1] shifts in the live pixel from the input stream.
--   row[k] (k<KERN_ROWS-1) shifts in line_buf output for buffer row k.
--
-- Zero-extend — two mechanisms:
--   new_row  : '1' on the first pixel of every row.  Forces positions
--              1..KERN_COLS-1 to zero instead of shifting old values in,
--              so out-of-bounds column taps are 0 rather than the previous
--              row's trailing pixels.
--   row_valid: bit r = '1' when buf_rows(r) contains a valid older row
--              (i.e. enough rows have been processed since frame start).
--              When '0', zero is loaded into position 0 of that row, so
--              out-of-frame row taps are 0 rather than stale BRAM data.
--
-- tap_out is a flattened M x N x DATA_WIDTH vector:
--   tap[row][col] at bits ((row*N + col + 1)*DATA_WIDTH - 1) downto (row*N + col)*DATA_WIDTH
--   row 0 = oldest row (top of kernel); row M-1 = newest row (bottom).
--   col 0 = most recent pixel in that row; col N-1 = oldest.

library ieee;
use ieee.std_logic_1164.all;

entity win_buf is
    generic (
        DATA_WIDTH : positive := 8;
        KERN_ROWS  : positive := 3;   -- M
        KERN_COLS  : positive := 3    -- N
    );
    port (
        clk       : in  std_logic;
        rst       : in  std_logic;

        -- Advance window by one pixel when shift_en = '1'
        shift_en  : in  std_logic;

        -- '1' on the first pixel of each new row; zeroes columns 1..KERN_COLS-1
        new_row   : in  std_logic;

        -- Bit r = '1' when buf_rows(r) is valid (enough rows seen since frame start)
        row_valid : in  std_logic_vector(KERN_ROWS - 2 downto 0);

        -- Live pixel enters the newest row
        pix_in    : in  std_logic_vector(DATA_WIDTH - 1 downto 0);

        -- Older rows from line_buf (row 0 of buf = row 1 of window, etc.)
        -- Packed: buf_rows((k+1)*DATA_WIDTH-1 downto k*DATA_WIDTH) = buf row k
        buf_rows  : in  std_logic_vector(DATA_WIDTH * (KERN_ROWS - 1) - 1 downto 0);

        -- Flattened M x N tap array
        tap_out   : out std_logic_vector(DATA_WIDTH * KERN_ROWS * KERN_COLS - 1 downto 0)
    );
end entity win_buf;

architecture rtl of win_buf is

    type row_t    is array (0 to KERN_COLS - 1) of std_logic_vector(DATA_WIDTH - 1 downto 0);
    type window_t is array (0 to KERN_ROWS - 1) of row_t;

    signal win : window_t;

begin

    p_shift : process (clk)
    begin
        if rising_edge(clk) then
            if rst = '1' then
                for r in 0 to KERN_ROWS - 1 loop
                    for c in 0 to KERN_COLS - 1 loop
                        win(r)(c) <= (others => '0');
                    end loop;
                end loop;
            elsif shift_en = '1' then
                -- Newest row: shift in live pixel at col 0.
                -- On new_row, zero positions 1..KERN_COLS-1 (col zero-extend).
                win(KERN_ROWS - 1)(0) <= pix_in;
                for c in KERN_COLS - 1 downto 1 loop
                    if new_row = '1' then
                        win(KERN_ROWS - 1)(c) <= (others => '0');
                    else
                        win(KERN_ROWS - 1)(c) <= win(KERN_ROWS - 1)(c - 1);
                    end if;
                end loop;

                -- Older rows: shift in line_buf output at col 0, or 0 if
                -- this row slot is not yet valid (inter-frame zero-extend).
                -- On new_row, zero positions 1..KERN_COLS-1 (col zero-extend).
                for r in 0 to KERN_ROWS - 2 loop
                    if row_valid(r) = '1' then
                        win(r)(0) <=
                            buf_rows((r + 1) * DATA_WIDTH - 1 downto r * DATA_WIDTH);
                    else
                        win(r)(0) <= (others => '0');
                    end if;
                    for c in KERN_COLS - 1 downto 1 loop
                        if new_row = '1' then
                            win(r)(c) <= (others => '0');
                        else
                            win(r)(c) <= win(r)(c - 1);
                        end if;
                    end loop;
                end loop;
            end if;
        end if;
    end process p_shift;

    -- Flatten window to tap_out
    gen_taps : for r in 0 to KERN_ROWS - 1 generate
        gen_cols : for c in 0 to KERN_COLS - 1 generate
            tap_out((r * KERN_COLS + c + 1) * DATA_WIDTH - 1
                     downto (r * KERN_COLS + c) * DATA_WIDTH)
                <= win(r)(c);
        end generate gen_cols;
    end generate gen_taps;

end architecture rtl;
