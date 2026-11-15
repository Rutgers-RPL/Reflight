#include <stdint.h>

#define REG32(address) (*(volatile uint32_t *)(address))

/* STM32F407: reset HSI is 16 MHz; no PLL or clock tree setup is needed. */
#define RCC_AHB1ENR REG32(0x40023830u)
#define RCC_APB1ENR REG32(0x40023840u)
#define GPIOA_MODER REG32(0x40020000u)
#define GPIOA_AFRL REG32(0x40020020u)
#define GPIOA_BSRR REG32(0x40020018u)
#define USART2_SR REG32(0x40004400u)
#define USART2_DR REG32(0x40004404u)
#define USART2_BRR REG32(0x40004408u)
#define USART2_CR1 REG32(0x4000440cu)

extern uint32_t _estack;
void Reset_Handler(void);
void Default_Handler(void);

__attribute__((section(".isr_vector"), used))
const uintptr_t vectors[] = {
    (uintptr_t)&_estack,
    (uintptr_t)Reset_Handler,
    (uintptr_t)Default_Handler,
    (uintptr_t)Default_Handler,
};

void Default_Handler(void) {
    for (;;) { }
}

void Reset_Handler(void) {
    /* Enable GPIOA and USART2 clocks. PA2 is USART2 TX (AF7); PA5 is output. */
    RCC_AHB1ENR |= 1u;
    RCC_APB1ENR |= 1u << 17;
    GPIOA_MODER = (GPIOA_MODER & ~((3u << 4) | (3u << 10)))
                  | (2u << 4) | (1u << 10);
    GPIOA_AFRL = (GPIOA_AFRL & ~(15u << 8)) | (7u << 8);

    USART2_BRR = 0x008bu; /* 16 MHz / 115200, oversampling by 16. */
    USART2_CR1 = (1u << 13) | (1u << 3); /* UE and TE. */

    static const char message[] = "REFLIGHT A\n";
    for (unsigned i = 0; i < sizeof(message) - 1u; ++i) {
        while ((USART2_SR & (1u << 7)) == 0u) { } /* TXE */
        USART2_DR = (uint8_t)message[i];
    }

    GPIOA_BSRR = 1u << 5;
    GPIOA_BSRR = 1u << (5 + 16);
    for (;;) { }
}
