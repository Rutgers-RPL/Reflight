/* Synthetic Phase 4 telemetry from actual SPI1 reads, with a firmware decision.
 * Packet: A5 5A, ICM accel X i16le, BMI accel X i16le, MS D1 u32le.
 * PB1 is high iff the ICM reading is at least 50 raw counts.
 */
#include <stdint.h>
#define REG32(a) (*(volatile uint32_t *)(a))
#define REG8(a) (*(volatile uint8_t *)(a))
#define GPIOA_BSRR REG32(0x40020018u)
#define GPIOB_BSRR REG32(0x40020418u)
#define SPI1_SR REG32(0x40013008u)
#define SPI1_DR REG8(0x4001300cu)
extern uint32_t _estack;
void Reset_Handler(void);
__attribute__((section(".isr_vector"), used)) const uintptr_t vectors[] = {
    (uintptr_t)&_estack, (uintptr_t)Reset_Handler, 0, 0
};
static uint8_t spi(uint8_t value) {
    while (!(SPI1_SR & 2u)) {}
    SPI1_DR = value;
    while (!(SPI1_SR & 1u)) {}
    return SPI1_DR;
}
static void cs(unsigned pin, unsigned high) {
    GPIOA_BSRR = 1u << (high ? pin : pin + 16u);
}
static void uart(uint8_t value) {
    while (!(REG32(0x40004400u) & 0x80u)) {}
    REG32(0x40004404u) = value;
}
static void word(uint16_t value) { uart((uint8_t)value); uart((uint8_t)(value >> 8)); }
static int16_t icm(void) {
    cs(4, 0); (void)spi(0x80);
    uint16_t high = spi(0), low = spi(0);
    cs(4, 1);
    return (int16_t)((high << 8) | low);
}
static int16_t bmi(void) {
    cs(6, 0); (void)spi(0x92); (void)spi(0);
    uint16_t low = spi(0), high = spi(0);
    cs(6, 1);
    return (int16_t)((high << 8) | low);
}
static uint32_t ms(void) {
    cs(5, 0); (void)spi(0x48); cs(5, 1);
    cs(5, 0); (void)spi(0);
    uint32_t high = spi(0), middle = spi(0), low = spi(0);
    cs(5, 1);
    return (high << 16) | (middle << 8) | low;
}
void Reset_Handler(void) {
    REG32(0x40023830u) |= 3u;
    REG32(0x40023840u) |= 1u << 17;
    REG32(0x40023844u) |= 1u << 12;
    GPIOA_BSRR = 0x70u;
    REG32(0x40020000u) = (REG32(0x40020000u) & ~0x3f00u) | 0x1500u;
    REG32(0x40020400u) = (REG32(0x40020400u) & ~12u) | 4u;
    REG32(0x40004408u) = 0x8bu;
    REG32(0x4000440cu) = 0x2008u;
    REG32(0x40013000u) = 0x344u;
    (void)bmi(); /* Initial CS read selects BMI088 SPI mode. */
    for (;;) {
        int16_t a = icm(), b = bmi();
        uint32_t pressure = ms();
        GPIOB_BSRR = 1u << (a >= 50 ? 1u : 17u);
        uart(0xa5); uart(0x5a); word((uint16_t)a); word((uint16_t)b);
        word((uint16_t)pressure); word((uint16_t)(pressure >> 16));
    }
}
