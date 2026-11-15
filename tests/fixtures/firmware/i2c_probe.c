#include <stdint.h>
#define REG32(a) (*(volatile uint32_t *)(a))
#define RCC_APB1ENR REG32(0x40023840u)
#define I2C1_CR1 REG32(0x40005400u)
extern uint32_t _estack;
void Reset_Handler(void);
__attribute__((section(".isr_vector"), used)) const uintptr_t vectors[] = {
    (uintptr_t)&_estack, (uintptr_t)Reset_Handler, 0, 0
};
void Reset_Handler(void) {
    RCC_APB1ENR |= 1u << 21;
    I2C1_CR1 = 0x101u; /* PE | START: first real controller operation. */
    for (;;) {}
}
