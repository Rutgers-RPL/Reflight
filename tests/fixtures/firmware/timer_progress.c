/* SysTick interrupts must progress through Qiling's normal hardware loop. */
#include <stdint.h>
#define REG(a) (*(volatile uint32_t *)(a))
extern uint32_t _estack;
void Reset_Handler(void);
void SysTick_Handler(void);
__attribute__((section(".isr_vector"), used)) const uintptr_t vectors[16] = {
    [0] = (uintptr_t)&_estack, [1] = (uintptr_t)Reset_Handler,
    [15] = (uintptr_t)SysTick_Handler
};
static volatile uint32_t ticks;
void SysTick_Handler(void) { ++ticks; }
void Reset_Handler(void) {
    ticks = 0;
    REG(0x40023840u) = 1u << 17;
    REG(0x4000440cu) = 0x2008u;
    REG(0xe000e014u) = 199u;
    REG(0xe000e018u) = 0u;
    REG(0xe000e010u) = 7u;
    while (ticks < 3u) {}
    REG(0x40004404u) = 'T';
    REG(0xe000e010u) = 0u;
    for (;;) {}
}
