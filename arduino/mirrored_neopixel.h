#pragma once
#include <Adafruit_NeoPixel.h>

// The AVR driver writes the full port using pinMask for every bit. Extending
// that mask mirrors the same waveform on multiple pins without changing its timing.
// Uses the installed Adafruit AVR transmission routine, not custom bit timing.
class MirroredNeoPixel : public Adafruit_NeoPixel {
public:
  MirroredNeoPixel(uint16_t count, int16_t pin, neoPixelType type)
    : Adafruit_NeoPixel(count,pin,type) {}

  bool selectGroup(const uint8_t *pins, uint8_t count) {
    if (count==0) return false;
    const uint8_t port=digitalPinToPort(pins[0]);
    if (port==NOT_A_PIN) return false;
    uint8_t mask=0;
    for (uint8_t i=0;i<count;i++) {
      if (digitalPinToPort(pins[i])!=port) return false;
      mask|=digitalPinToBitMask(pins[i]);
    }
    int16_t previous=getPin();
    setPin(pins[0]);
    // setPin releases the previous primary pin. All inactive lines stay low.
    if (previous>=0) {
      pinMode(previous,OUTPUT); digitalWrite(previous,LOW);
    }
    for (uint8_t i=0;i<count;i++) {
      pinMode(pins[i],OUTPUT); digitalWrite(pins[i],LOW);
    }
    pinMask=mask;
    return true;
  }
};
