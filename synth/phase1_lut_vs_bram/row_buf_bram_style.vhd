-- Row buffer coded to reliably infer a True Dual-Port (TDP) BRAM36 on
-- Xilinx 7-series/Zynq-7000 devices under Vivado synthesis.
--
-- Rules applied (each one is necessary; violating any one can prevent BRAM):
--   (1) Read is SYNCHRONOUS — data appears one clock after the address is
--       presented.  This matches the RAMB36E1 read-first or write-first port
--       behaviour.  An asynchronous (combinational) read forces distributed RAM.
--   (2) NO per-element reset of the storage array.  Only the output register
--       pipeline can be reset; the RAM cells themselves cannot.  Resetting the
--       array in HDL forces FF/LUT inference.
--   (3) The array uses a plain integer range for the address dimension and a
--       std_logic_vector element — the canonical UG901 SDP-BRAM template.
--   (4) A `ram_style` attribute is added as an explicit override.  This is a
--       belt-and-suspenders measure; a correctly written template infers BRAM
--       without it, but the attribute makes the intent unambiguous to the tool
--       and produces a critical warning if the template cannot be honoured.
--   (5) The write port and read port use separate addresses (simple dual port
--       semantics), which maps directly onto RAMB36E1 SDP mode.
--
-- For a 3x3 kernel on a 1920-wide image: 2 row buffers x 1920 x 8 bits =
-- 30,720 bits total → fits in 1 BRAM36 (36 Kb capacity) with room to spare.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity row_buf_bram_style is
    generic (
        DATA_WIDTH : positive := 8;
        LINE_WIDTH : positive := 1920
    );
    port (
        clk      : in  std_logic;
        wr_en    : in  std_logic;
        wr_addr  : in  natural range 0 to LINE_WIDTH - 1;
        wr_data  : in  std_logic_vector(DATA_WIDTH - 1 downto 0);
        rd_addr  : in  natural range 0 to LINE_WIDTH - 1;
        rd_data  : out std_logic_vector(DATA_WIDTH - 1 downto 0)
    );
end entity row_buf_bram_style;

architecture rtl of row_buf_bram_style is

    type ram_t is array (0 to LINE_WIDTH - 1)
        of std_logic_vector(DATA_WIDTH - 1 downto 0);

    -- Rule (4): explicit attribute — belt-and-suspenders.
    attribute ram_style        : string;
    attribute ram_style of mem : signal is "block";

    signal mem     : ram_t;
    signal rd_data_r : std_logic_vector(DATA_WIDTH - 1 downto 0);

begin

    p_ram : process (clk)
    begin
        if rising_edge(clk) then
            -- Rule (5): write port
            if wr_en = '1' then
                mem(wr_addr) <= wr_data;
            end if;
            -- Rule (1): synchronous read — registered on clock edge.
            -- Rule (2): NO reset of mem() array.  Only the output register
            --           pipeline stage can be reset if needed.
            rd_data_r <= mem(rd_addr);
        end if;
    end process p_ram;

    rd_data <= rd_data_r;

end architecture rtl;
