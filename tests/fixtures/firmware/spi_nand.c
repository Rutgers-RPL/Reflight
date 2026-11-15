/* Synthetic NAND acceptance via STM32F407 SPI1 and GPIO CS; no HAL hooks. */
#include <stdint.h>
#define R(a) (*(volatile uint32_t *)(a))
#define B(a) (*(volatile uint8_t *)(a))
#define CS_LOW R(0x40020018u) = 1u << 23
#define CS_HIGH R(0x40020018u) = 1u << 7
extern uint32_t _estack;
void Reset_Handler(void);
__attribute__((section(".isr_vector"), used)) const uintptr_t vectors[] = {
    (uintptr_t)&_estack, (uintptr_t)Reset_Handler, 0, 0
};
static void uart(uint8_t value) {
    while (!(R(0x40004400u) & 0x80u)) {}
    R(0x40004404u) = value;
}
static uint8_t spi(uint8_t value) {
    while (!(R(0x40013008u) & 2u)) {}
    B(0x4001300cu) = value;
    while (!(R(0x40013008u) & 1u)) {}
    return B(0x4001300cu);
}
static void command(uint8_t op) { CS_LOW; spi(op); CS_HIGH; }
static uint8_t feature(uint8_t addr) {
    CS_LOW; spi(0x0f); spi(addr); uint8_t value = spi(0); CS_HIGH;
    return value;
}
static void set_feature(uint8_t addr, uint8_t value) {
    CS_LOW; spi(0x1f); spi(addr); spi(value); CS_HIGH;
}
static void row_command(uint8_t op, uint16_t row) {
    CS_LOW; spi(op); spi(0); spi(row >> 8); spi(row); CS_HIGH;
}
static unsigned wait_ready(void) {
    for (unsigned i = 0; i < 1000; ++i) {
        if (!(feature(0xc0) & 1u)) return i != 0;
    }
    uart('T'); for (;;) {}
}
static void check(int ok) { if (!ok) { uart('F'); for (;;) {} } }
#ifdef MIXED_SENSOR
static void sensor_sample(void) {
    R(0x40020018u) = 1u << 20;
    spi(0x80); uint8_t high=spi(0), low=spi(0);
    R(0x40020018u) = 1u << 4;
    uart('S'); uart(high); uart(low);
}
#endif
static void read_check(uint16_t row, uint8_t a, uint8_t b, uint8_t c) {
    row_command(0x13, row); check(wait_ready());
    CS_LOW; spi(3); spi(7); spi(0xfe); spi(0);
    check(spi(0) == a); check(spi(0) == b); check(spi(0) == c); CS_HIGH;
}
void Reset_Handler(void) {
    R(0x40023830u) |= 3u; R(0x40023840u) |= 1u << 17; R(0x40023844u) |= 1u << 12;
    CS_HIGH;
    R(0x40020000u) = (R(0x40020000u) & ~((3u<<4)|(3u<<14))) | (2u<<4)|(1u<<14);
    R(0x40020400u) = (R(0x40020400u) & ~(3u<<2)) | (1u<<2);
    R(0x40004408u) = 0x8b; R(0x4000440cu) = 0x2008; R(0x40013000u) = 0x344;
#ifdef MIXED_SENSOR
    R(0x40020018u) = 1u << 4;
    R(0x40020000u) = (R(0x40020000u) & ~(3u<<8)) | (1u<<8);
    R(0x40020018u) = 1u << 20; spi(0x1b); spi(0x06); R(0x40020018u) = 1u << 4;
#endif
    CS_LOW; spi(0x9f); spi(0); uint8_t mid=spi(0), did=spi(0); uart(mid); uart(did);
    if (mid == 0xef) check(spi(0) == 0x21);
    CS_HIGH;
    check((mid==0xef && did==0xae) || (mid==0xc8 && (did==0x31 || did==0x21)));
    check(feature(0xa0) != 0);
    command(6); row_command(0x10, 63); wait_ready(); check(feature(0xc0)&8);
    command(0xff); wait_ready(); check(!(feature(0xc0)&0x0f));
    set_feature(0xa0, 0); check(feature(0xa0) == 0);
    command(6); row_command(0xd8, 63); check(wait_ready()); check(!(feature(0xc0)&0x0c));
    if (mid == 0xef) command(6); /* Winbond needs WREN before load. */
    CS_LOW; spi(2); spi(7); spi(0xfe); spi(0x12); spi(0xa5); spi(0x69); CS_HIGH;
    if (mid == 0xc8) command(6); /* GD documented sequence: load, WREN, execute. */
    row_command(0x10, 63); check(wait_ready()); check(!(feature(0xc0)&0x0a));
    read_check(63, 0x12, 0xa5, 0x69);
    if (mid == 0xef) command(6);
    CS_LOW; spi(2); spi(7); spi(0xfe); spi(255); spi(255); spi(255); CS_HIGH;
    if (mid == 0xc8) command(6);
    row_command(0x10, 63); check(wait_ready()); read_check(63, 0x12, 0xa5, 0x69);
    command(0xff); wait_ready(); read_check(63, 0x12, 0xa5, 0x69);
    row_command(0xd8, 63); /* No WREN: must be ignored. */
    read_check(63, 0x12, 0xa5, 0x69);
    command(6); row_command(0xd8, 63); check(wait_ready()); read_check(63, 255, 255, 255);
    /* Leave a known record in neighboring block for optional image export. */
    if (mid == 0xef) command(6);
    CS_LOW; spi(2); spi(0); spi(1); spi(0x3c); spi(0xc3); CS_HIGH;
    if (mid == 0xc8) command(6);
    row_command(0x10, 64); check(wait_ready());
    uart('P'); R(0x40020418u) = 1u << 1;
#ifdef MIXED_SENSOR
    for (unsigned sample=0; sample<4; ++sample) {
        sensor_sample();
        for (volatile unsigned delay=0; delay<300; ++delay) { __asm volatile("nop"); }
    }
#endif
    for (;;) {}
}
