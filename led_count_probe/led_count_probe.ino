#include <Adafruit_NeoPixel.h>

// Temporary low-power firmware for finding the physical end of a strip.
// Every LED costs three bytes of the Uno's 2 KB SRAM, hence the 400 cap.
#define LED_PIN 6
#define MAX_PROBE_LEDS 400
#define PROBE_BRIGHTNESS 24

Adafruit_NeoPixel strip(MAX_PROBE_LEDS, LED_PIN, NEO_GRB + NEO_KHZ800);

char rxBuffer[16];
uint8_t rxLength = 0;


void setup() {
  Serial.begin(115200);
  strip.begin();
  strip.setBrightness(PROBE_BRIGHTNESS);
  strip.clear();
  strip.show();
}


void loop() {
  while (Serial.available()) {
    char c = Serial.read();

    if (c == '\n') {
      rxBuffer[rxLength] = '\0';
      handleCommand(rxBuffer);
      rxLength = 0;
    }
    else if (c != '\r' && rxLength < sizeof(rxBuffer) - 1) {
      rxBuffer[rxLength++] = c;
    }
  }
}


void handleCommand(char *line) {
  if (line[0] != 'T' || line[1] != ',') {
    return;
  }

  long index = atol(line + 2);

  strip.clear();

  if (index >= 0 && index < MAX_PROBE_LEDS) {
    strip.setPixelColor((uint16_t)index, strip.Color(255, 255, 255));
  }

  strip.show();

  Serial.print("PROBE,");
  Serial.println(index);
}
