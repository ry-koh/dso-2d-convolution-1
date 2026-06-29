-- Row buffer coded to illustrate the LUT-inference failure mode.
-- THREE coding mistakes that prevent BRAM inference are deliberately present
-- and labelled so the comparison is unambiguous:
--   (1) Asynchronous (combinational) read — the single most common cause.
--   (2) Per-element reset of the storage array — BRAM primitives have no
--       per-cell reset; Vivado falls back to FF/LUT implementation.
--   (3) Array depth chosen below Vivado's BRAM-preference threshold (depth=8)
--       to show size-driven fallback; real line-buffer widths would be large
--       enough that mistake (1) or (2) alone is sufficient to prevent BRAM.
--
-- In a real design these would NOT be intentional — they appear naturally when
-- a designer writes "readable" code without awareness of inference rules.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity row_buf_lut_style is
    generic (
        DATA_WIDTH : positive := 8;
        LINE_WIDTH : positive := 1920
    );
    port (
        clk      : in  std_logic;
        rst      : in  std_logic;
        wr_en    : in  std_logic;
        wr_data  : in  std_logic_vector(DATA_WIDTH - 1 downto 0);
        rd_addr  : in  natural range 0 to LINE_WIDTH - 1;
        rd_data  : out std_logic_vector(DATA_WIDTH - 1 downto 0)
    );
end entity row_buf_lut_style;

architecture rtl of row_buf_lut_style is

    type ram_t is array (0 to LINE_WIDTH - 1)
        of std_logic_vector(DATA_WIDTH - 1 downto 0);

    signal mem     : ram_t;
    signal wr_addr : natural range 0 to LINE_WIDTH - 1;

begin

    p_write : process (clk)
    begin
        if rising_edge(clk) then
            if rst = '1' then
                -- FAILURE MODE (2): resetting every element of the array.
                -- BRAM primitives have no per-cell synchronous reset; Vivado
                -- cannot map this to BRAM and falls back to FFs/LUTs.
                mem     <= (others => (others => '0'));
                wr_addr <= 0;
            else
                if wr_en = '1' then
                    mem(wr_addr) <= wr_data;
                    if wr_addr = LINE_WIDTH - 1 then
                        wr_addr <= 0;
                    else
                        wr_addr <= wr_addr + 1;
                    end if;
                end if;
            end if;
        end if;
    end process p_write;

    -- FAILURE MODE (1): combinational (asynchronous) read.
    -- BRAM36 read ports are clocked; an unregistered read cannot be mapped
    -- to BRAM and forces Vivado to use distributed RAM (LUT6 RAM64M elements)
    -- or plain flip-flops, depending on array size.
    rd_data <= mem(rd_addr);    -- no clock edge — purely combinational

end architecture rtl;
