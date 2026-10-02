#pragma once
#include <Adafruit_NeoPixel.h>

// The AVR driver writes the full port using pinMask for every bit. Extending
// that mask mirrors the same waveform on two pins without changing its timing.
// Uses the installed Adafruit AVR transmission routine, not custom bit timing.
class MirroredNeoPixel : public Adafruit_NeoPixel {
public:
  MirroredNeoPixel(uint16_t count, int16_t pin, neoPixelType type)
    : Adafruit_NeoPixel(count,pin,type) {}

  bool selectPair(uint8_t first, uint8_t second) {
    if (digitalPinToPort(first)!=digitalPinToPort(second)) return false;
    int16_t previous=getPin();
    setPin(first);
    // setPin releases the previous primary pin. All inactive lines stay low.
    pinMode(previous,OUTPUT); digitalWrite(previous,LOW);
    pinMode(first,OUTPUT); pinMode(second,OUTPUT);
    digitalWrite(first,LOW); digitalWrite(second,LOW);
    pinMask=digitalPinToBitMask(first)|digitalPinToBitMask(second);
    return true;
  }
};
