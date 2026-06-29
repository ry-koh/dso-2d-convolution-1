-- Stores NUM_ROWS complete pixel rows, one BRAM36 per row (SDP mode).
-- All NUM_ROWS rows are read simultaneously (same rd_col address).
-- Write address and read address are independent ports.
--
-- Read latency: 1 clock. rd_data valid the cycle after rd_col is presented.
-- UG901-compliant SDP BRAM template; ram_style="block" as belt-and-suspenders.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity line_buf is
    generic (
        DATA_WIDTH : positive := 8;
        LINE_WIDTH : positive := 1920;
        NUM_ROWS   : positive := 2        -- kernel_rows - 1
    );
    port (
        clk     : in  std_logic;

        wr_en   : in  std_logic;
        wr_row  : in  natural range 0 to NUM_ROWS - 1;
        wr_col  : in  natural range 0 to LINE_WIDTH - 1;
        wr_data : in  std_logic_vector(DATA_WIDTH - 1 downto 0);

        rd_col  : in  natural range 0 to LINE_WIDTH - 1;
        -- Row 0 occupies bits DATA_WIDTH-1..0; row 1 next slice; etc.
        rd_data : out std_logic_vector(DATA_WIDTH * NUM_ROWS - 1 downto 0)
    );
end entity line_buf;

architecture rtl of line_buf is

    type ram_t       is array (0 to LINE_WIDTH - 1) of std_logic_vector(DATA_WIDTH - 1 downto 0);
    type bram_array_t is array (0 to NUM_ROWS - 1)  of ram_t;

    signal mem : bram_array_t;

    attribute ram_style            : string;
    attribute ram_style of mem     : signal is "block";

begin

    gen_brams : for i in 0 to NUM_ROWS - 1 generate
        p_bram : process (clk)
        begin
            if rising_edge(clk) then
                if wr_en = '1' and wr_row = i then
                    mem(i)(wr_col) <= wr_data;
                end if;
                rd_data((i + 1) * DATA_WIDTH - 1 downto i * DATA_WIDTH)
                    <= mem(i)(rd_col);
            end if;
        end process p_bram;
    end generate gen_brams;

end architecture rtl;
