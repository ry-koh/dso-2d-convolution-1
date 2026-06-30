-- Stores NUM_ROWS pixel rows, one BRAM18/36 per row (SDP mode).
-- All rows are read simultaneously at rd_col.
--
-- row_base: index of the physical BRAM that currently holds the OLDEST row.
-- rd_data is returned in age order: slot 0 = BRAM[row_base] (oldest),
-- slot 1 = BRAM[(row_base+1) mod NUM_ROWS], etc.
-- The caller (conv2d) passes buf_wr_row as row_base; the BRAM being written
-- is the one about to be overwritten, i.e. the one holding the oldest data.
--
-- Read latency: 1 clock (synchronous read).
-- UG901-compliant SDP BRAM template; ram_style="block" belt-and-suspenders.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity line_buf is
    generic (
        DATA_WIDTH : positive := 8;
        LINE_WIDTH : positive := 1920;
        NUM_ROWS   : positive := 2
    );
    port (
        clk      : in  std_logic;

        wr_en    : in  std_logic;
        wr_row   : in  natural range 0 to NUM_ROWS - 1;
        wr_col   : in  natural range 0 to LINE_WIDTH - 1;
        wr_data  : in  std_logic_vector(DATA_WIDTH - 1 downto 0);

        row_base : in  natural range 0 to NUM_ROWS - 1;
        rd_en    : in  std_logic;
        rd_col   : in  natural range 0 to LINE_WIDTH - 1;
        -- Slot 0 = oldest row; slot NUM_ROWS-1 = most recently completed row.
        rd_data  : out std_logic_vector(DATA_WIDTH * NUM_ROWS - 1 downto 0)
    );
end entity line_buf;

architecture rtl of line_buf is

    type ram_t        is array (0 to LINE_WIDTH - 1) of std_logic_vector(DATA_WIDTH - 1 downto 0);
    type bram_array_t is array (0 to NUM_ROWS - 1)  of ram_t;

    signal mem     : bram_array_t;
    signal rd_raw  : std_logic_vector(DATA_WIDTH * NUM_ROWS - 1 downto 0);

    attribute ram_style        : string;
    attribute ram_style of mem : signal is "block";

begin

    -- BRAM read/write: one process per physical BRAM row so each infers
    -- independently as a Simple Dual Port BRAM.
    gen_brams : for i in 0 to NUM_ROWS - 1 generate
        p_bram : process (clk)
        begin
            if rising_edge(clk) then
                if wr_en = '1' and wr_row = i then
                    mem(i)(wr_col) <= wr_data;
                end if;
                -- Gated synchronous read: rd_en holds rd_raw stable during
                -- back-pressure stalls so win_buf sees the correct column value.
                if rd_en = '1' then
                    rd_raw((i + 1) * DATA_WIDTH - 1 downto i * DATA_WIDTH)
                        <= mem(i)(rd_col);
                end if;
            end if;
        end process p_bram;
    end generate gen_brams;

    -- Rotate rd_raw so that output slot 0 is always the oldest row.
    -- row_base is the physical index of the oldest BRAM (the one currently
    -- being written, before its old contents are overwritten).
    p_permute : process (rd_raw, row_base)
        variable phys : natural range 0 to NUM_ROWS - 1;
    begin
        for slot in 0 to NUM_ROWS - 1 loop
            phys := (row_base + slot) mod NUM_ROWS;
            rd_data((slot + 1) * DATA_WIDTH - 1 downto slot * DATA_WIDTH) <=
                rd_raw((phys + 1) * DATA_WIDTH - 1 downto phys * DATA_WIDTH);
        end loop;
    end process p_permute;

end architecture rtl;
