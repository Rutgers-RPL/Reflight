ENTRY(Reset_Handler)

MEMORY {
    FLASH (rx)  : ORIGIN = 0x08000000, LENGTH = 1M
    SRAM  (rwx) : ORIGIN = 0x20000000, LENGTH = 128K
}

_estack = ORIGIN(SRAM) + LENGTH(SRAM);

SECTIONS {
    .isr_vector : { KEEP(*(.isr_vector)) } > FLASH
    .text : { *(.text*) *(.rodata*) } > FLASH
    .ARM.exidx : { *(.ARM.exidx*) } > FLASH
    .data : { *(.data*) } > SRAM AT > FLASH
    .bss (NOLOAD) : { *(.bss*) *(COMMON) } > SRAM
}
