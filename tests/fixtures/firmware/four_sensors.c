/* Direct-register STM32F407 SPI1 polling driver subset for Phase 3.
 * Every transmitted USART byte is read from SPI1 DR by this ARM firmware.
 * CS: ICM PA4, MS PA5, BMI accel PA6, BMI gyro PA7, BMP PB0.
 */
#include <stdint.h>
#define REG32(a) (*(volatile uint32_t *)(a))
#define REG8(a) (*(volatile uint8_t *)(a))
#define RCC_AHB1ENR REG32(0x40023830u)
#define RCC_APB1ENR REG32(0x40023840u)
#define RCC_APB2ENR REG32(0x40023844u)
#define GPIOA_MODER REG32(0x40020000u)
#define GPIOA_BSRR REG32(0x40020018u)
#define GPIOB_MODER REG32(0x40020400u)
#define GPIOB_BSRR REG32(0x40020418u)
#define USART2_SR REG32(0x40004400u)
#define USART2_DR REG32(0x40004404u)
#define USART2_BRR REG32(0x40004408u)
#define USART2_CR1 REG32(0x4000440cu)
#define SPI1_CR1 REG32(0x40013000u)
#define SPI1_SR REG32(0x40013008u)
#define SPI1_DR REG8(0x4001300cu)
extern uint32_t _estack;
void Reset_Handler(void);
__attribute__((section(".isr_vector"), used)) const uintptr_t vectors[] = {
    (uintptr_t)&_estack, (uintptr_t)Reset_Handler, 0, 0
};
static void uart(uint8_t value) {
    while (!(USART2_SR & 0x80u)) {}
    USART2_DR = value;
}
static uint8_t spi(uint8_t value) {
    while (!(SPI1_SR & 2u)) {}
    SPI1_DR = value;
    while (!(SPI1_SR & 1u)) {}
    return SPI1_DR;
}
static void cs(unsigned port_b, unsigned pin, unsigned high) {
    if (port_b) GPIOB_BSRR = high ? (1u << pin) : (1u << (pin + 16u));
    else GPIOA_BSRR = high ? (1u << pin) : (1u << (pin + 16u));
}
static uint8_t reg_read(unsigned port_b, unsigned pin, uint8_t addr, unsigned dummy) {
    cs(port_b, pin, 0);
    (void)spi((uint8_t)(0x80u | addr));
    if (dummy) (void)spi(0);
    uint8_t result = spi(0);
    cs(port_b, pin, 1);
    return result;
}
void Reset_Handler(void) {
    RCC_AHB1ENR |= 3u;
    RCC_APB1ENR |= 1u << 17;
    RCC_APB2ENR |= 1u << 12;
    GPIOA_BSRR = 0xf0u;
    GPIOB_BSRR = 1u;
    GPIOA_MODER = (GPIOA_MODER & ~0xff00u) | 0x5500u;
    GPIOB_MODER = (GPIOB_MODER & ~3u) | 1u;
    USART2_BRR = 0x8bu;
    USART2_CR1 = 0x2008u;
    SPI1_CR1 = 0x344u;

    /* TDK DS-000577 §§16–17: WHO_AM_I, big-endian accel X. */
    uart(reg_read(0, 4, 0x72, 0));
    uart(reg_read(0, 4, 0x00, 0));
    uart(reg_read(0, 4, 0x01, 0));

    /* MS5611 pp10–11: PROM C1 and D1 conversion/24-bit ADC. */
    cs(0, 5, 0); (void)spi(0xA2); uart(spi(0)); uart(spi(0)); cs(0, 5, 1);
    cs(0, 5, 0); (void)spi(0x48); cs(0, 5, 1);
    cs(0, 5, 0); (void)spi(0x00);
    uart(spi(0)); uart(spi(0)); uart(spi(0)); cs(0, 5, 1);

    /* BMI088 §§3,6.1: accel first CS read switches to SPI; reads need dummy. */
    (void)reg_read(0, 6, 0x00, 1);
    uart(reg_read(0, 6, 0x00, 1));
    uart(reg_read(0, 6, 0x12, 1));
    uart(reg_read(0, 6, 0x13, 1));
    /* BMI088 gyro §6.1.1: no extra dummy. */
    uart(reg_read(0, 7, 0x00, 0));
    uart(reg_read(0, 7, 0x02, 0));
    uart(reg_read(0, 7, 0x03, 0));

    /* BMP581 §§5.1,5.5: first 16-clock read switches to SPI on CS rise. */
    (void)reg_read(1, 0, 0x01, 0);
    uart(reg_read(1, 0, 0x01, 0));
    uart(reg_read(1, 0, 0x1D, 0));
    uart(reg_read(1, 0, 0x1E, 0));
    uart(reg_read(1, 0, 0x1F, 0));
    uart(reg_read(1, 0, 0x20, 0));
    uart(reg_read(1, 0, 0x21, 0));
    uart(reg_read(1, 0, 0x22, 0));
    for (;;) {}
}
