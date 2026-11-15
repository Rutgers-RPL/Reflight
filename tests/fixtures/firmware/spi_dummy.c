#include <stdint.h>
#define REG32(a) (*(volatile uint32_t *)(a))
#define REG8(a) (*(volatile uint8_t *)(a))
#define RCC_AHB1ENR REG32(0x40023830u)
#define RCC_APB1ENR REG32(0x40023840u)
#define RCC_APB2ENR REG32(0x40023844u)
#define GPIOA_MODER REG32(0x40020000u)
#define GPIOA_BSRR REG32(0x40020018u)
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
void Reset_Handler(void) {
    RCC_AHB1ENR |= 1u;
    RCC_APB1ENR |= 1u << 17;
    RCC_APB2ENR |= 1u << 12;
    GPIOA_BSRR = 1u << 4;
    GPIOA_MODER = (GPIOA_MODER & ~((3u << 4) | (3u << 8))) | (2u << 4) | (1u << 8);
    USART2_BRR = 0x8bu;
    USART2_CR1 = 0x2008u;
    SPI1_CR1 = 0x344u;
    GPIOA_BSRR = 1u << 20;
    uart(spi(0x12u));
    uart(spi(0xa5u));
    GPIOA_BSRR = 1u << 4;
    for (;;) {}
}
