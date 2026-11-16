# serial_echo_f103c6: UART check

USART1 of the STM32F103C6 (TX = **PA9**, RX = **PA10**), **115200 8N1**.

- sends `Hello from STM32 #N` every second;
- sends back every byte it receives (echo);
- the LED on PC13 blinks once every 2 s.

Flashed through an ST-Link, read with any USB-TTL adapter:

```text
USB-TTL RX ◄── PA9     USB-TTL TX ──► PA10     GND ─── GND   (do NOT connect 3.3V if the ST-Link powers the board)
```

This checks both directions separately: `Hello` arrives → the PA9 → adapter line works;
the echo comes back → the adapter → PA10 line works. The Serial Hello example is based on it.
