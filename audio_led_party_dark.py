#include <Adafruit_NeoPixel.h>
#include "led_layers.h"

// Each strip's DIN connects to its own Arduino digital pin.
const uint8_t LED_PINS[] = {6, 7, 8, 9};
const uint8_t NUM_STRIPS = sizeof(LED_PINS) / sizeof(LED_PINS[0]);
// LED count per strip; all strips share the same static background layout.
#define NUM_LEDS 300
// Total current budget across all four strips, not a per-strip budget.
#define MAX_MILLIAMPS 60000UL
#define MAX_BRIGHTNESS 255
#define IDLE_MA_PER_LED 1
#define FULL_MA_PER_CHANNEL 20
#define FRAME_MS 20
#define MAX_PULSES 8
#define SPARKLES_PER_STRIP 12
#define SPARKLE_LIFETIME_MS 140

// User cap is independent of the electrical current budget.
uint8_t userBrightness=MAX_BRIGHTNESS;
uint8_t backgroundDarkness=65;
// Reuse one pixel buffer: four 300-pixel buffers exceed the Uno's SRAM.
Adafruit_NeoPixel strip(NUM_LEDS, LED_PINS[0], NEO_GRB + NEO_KHZ800);

// Binary protocol: A5 5A, 16-byte payload, one-byte rolling checksum.
// Payload: level,sub,bass,body,mid,high,air,hue_lo,hue_hi,event,phrase,
// bpm,width,onset,harmonic,percussive. Event 1=beat, 2=section, 3=drop.
uint8_t frameLevel=0, frameSub=0, frameBass=0, frameBody=0, frameMid=0;
uint8_t frameHigh=0, frameAir=0, frameWidth=0, frameOnset=0;
uint8_t frameHarmonic=0, framePercussive=0;
uint16_t frameHue=0, frameBpm=90;
float smoothLevel=0, smoothSub=0, smoothBody=0, smoothMid=0;
float smoothHigh=0, smoothAir=0, smoothWidth=0, smoothActivity=0;
float phraseEnvelope=0, sectionEnvelope=0;
float backgroundLevel=0, foregroundHue=0;
bool haveForegroundHue=false, haveDrop=false;
unsigned long lastDropAt=0;
uint16_t flashRemainingMs=0;
uint16_t spatialPhase=0;
// Timer1 runs through NeoPixel interrupt blackouts (4 us/tick on the Uno).
// Reserved for timing; do not also use Servo or Timer1 PWM in this sketch.
unsigned long lostClockMs=0;
uint16_t lostClockRemainderUs=0;
unsigned long animationMillis() { return millis()+lostClockMs; }
unsigned long lastFrame=0;
unsigned long lastValidPacket=0;
char rxBuffer[128];
uint8_t rxLength=0;
uint8_t packet[17];
uint8_t lastPacket[16];
bool haveLastPacket=false;
uint8_t packetIndex=0, packetState=0;

struct Pulse {
  uint16_t distance, hue;
  uint8_t energy, width;
  bool active;
};
Pulse pulses[MAX_PULSES];

void spawnPulse(uint8_t energy, uint8_t width, uint16_t hue) {
  uint8_t slot=0, weakest=255;
  for (uint8_t i=0; i<MAX_PULSES; i++) {
    if (!pulses[i].active) { slot=i; weakest=0; break; }
    if (pulses[i].energy < weakest) { weakest=pulses[i].energy; slot=i; }
  }
  pulses[slot] = {0, hue, energy, width, true};
}

void applyEvent(uint8_t event, bool phrase) {
  // Ordinary beats/downbeats and vocal entrances do not launch big accents.
  // Keep fast local sparkles; reserve pulses and flashes for a massive drop.
  unsigned long now=animationMillis();
  if (event==3 && (!haveDrop || now-lastDropAt>=12000UL)) {
    haveDrop=true; lastDropAt=now;
    foregroundHue=frameHue;
    flashRemainingMs=500;
    Serial.println("ACK_DROP");
    spawnPulse(255,28,frameHue);
    spawnPulse(225,20,frameHue+9000);
    spawnPulse(195,14,frameHue+18000);
  }
}

uint8_t packetChecksum(const uint8_t *data) {
  uint8_t checksum=0;
  for (uint8_t i=0; i<16; i++) {
    checksum=(uint8_t)((checksum<<1)|(checksum>>7));
    checksum^=data[i];
  }
  return checksum;
}

void applyPacket() {
  if (packetChecksum(packet)!=packet[16]) return;
  lastValidPacket=animationMillis();
  // The host repeats a frame so one copy lands between LED updates.
  // Identical copies must not retrigger beats or drops.
  if (haveLastPacket && memcmp(lastPacket, packet, 16)==0) return;
  memcpy(lastPacket, packet, 16);
  haveLastPacket=true;
  frameLevel=packet[0]; frameSub=packet[1]; frameBass=packet[2];
  frameBody=packet[3]; frameMid=packet[4]; frameHigh=packet[5];
  frameAir=packet[6]; frameHue=(uint16_t)packet[7]|((uint16_t)packet[8]<<8);
  frameBpm=constrain(packet[11],50,200); frameWidth=packet[12];
  frameOnset=packet[13]; frameHarmonic=packet[14]; framePercussive=packet[15];
  applyEvent(constrain(packet[9],0,3),packet[10]!=0);
}

void readSerial() {
  while (Serial.available()) {
    uint8_t c=Serial.read();
    if (packetState==1) {
      if (c==0x5A) { packetState=2; packetIndex=0; }
      else packetState=0;
      continue;
    }
    if (packetState==2) {
      packet[packetIndex++]=c;
      if (packetIndex==17) { applyPacket(); packetState=0; packetIndex=0; }
      continue;
    }
    if (c==0xA5) { packetState=1; continue; }
    if (c=='\n') {
      rxBuffer[rxLength]='\0';
      if (strncmp(rxBuffer,"PING,",5)==0) {
        Serial.print("PONG,");
        Serial.println(rxBuffer+5);
      }
      else if (strncmp(rxBuffer,"BRIGHTNESS,",11)==0) {
        char *end;
        long percent=strtol(rxBuffer+11,&end,10);
        if (end!=rxBuffer+11 && *end=='\0' && percent>=0 && percent<=100) {
          userBrightness=(uint16_t)percent*MAX_BRIGHTNESS/100;
          Serial.print("ACK_BRIGHTNESS,");
          Serial.println(percent);
        }
      }
      else if (strncmp(rxBuffer,"DARKNESS,",9)==0) {
        char *end;
        long percent=strtol(rxBuffer+9,&end,10);
        if (end!=rxBuffer+9 && *end=='\0' && percent>=0 && percent<=100) {
          backgroundDarkness=percent;
          Serial.print("ACK_DARKNESS,"); Serial.println(percent);
        }
      }
      rxLength=0;
    }
    else if (c!='\r' && rxLength<sizeof(rxBuffer)-1) rxBuffer[rxLength++]=c;
  }
}

void advanceAnimation(uint16_t elapsedMs) {
  // Follow rises quickly, then fade out a little more gently. These are time
  // constants, not blocking delays; update on every rendered strip.
  float backgroundResponseMs=frameLevel>backgroundLevel ? 80.0f : 180.0f;
  backgroundLevel+=(frameLevel-backgroundLevel)*elapsedMs/(backgroundResponseMs+elapsedMs);
  if (!haveForegroundHue) { foregroundHue=frameHue; haveForegroundHue=true; }
  float hueDelta=frameHue-foregroundHue;
  if (hueDelta>32768) hueDelta-=65536;
  if (hueDelta<-32768) hueDelta+=65536;
  foregroundHue+=hueDelta*elapsedMs/(800.0f+elapsedMs);
  if (foregroundHue<0) foregroundHue+=65536;
  if (foregroundHue>=65536) foregroundHue-=65536;
  float ticks=elapsedMs/20.0f;
  float smoothing=min(1.0f,ticks);
  smoothLevel+=(frameLevel-smoothLevel)*(frameLevel>smoothLevel?.34:.10)*smoothing;
  smoothSub+=(frameSub-smoothSub)*(frameSub>smoothSub?.48:.17)*smoothing;
  smoothBody+=(frameBody-smoothBody)*.13*smoothing; smoothMid+=(frameMid-smoothMid)*.16*smoothing;
  smoothHigh+=(frameHigh-smoothHigh)*.25*smoothing; smoothAir+=(frameAir-smoothAir)*.20*smoothing;
  smoothWidth+=(frameWidth-smoothWidth)*.08*smoothing;
  uint8_t targetActivity=max(frameOnset,framePercussive);
  smoothActivity+=(targetActivity-smoothActivity)*.12*smoothing;
  phraseEnvelope*=pow(.938,ticks); sectionEnvelope*=pow(.955,ticks);
  if (flashRemainingMs>0) {
    flashRemainingMs=elapsedMs>=flashRemainingMs ? 0 : flashRemainingMs-elapsedMs;
    if (!flashRemainingMs) Serial.println("ACK_FLASH_OFF");
  }
  uint16_t pulseSpeed=30+((uint16_t)frameBpm*10)/12
                      +((uint16_t)smoothActivity*16)/255;
  uint16_t limit=((uint16_t)(NUM_LEDS/2)+30)*16;
  float pulseDecay=pow(243.0/256.0,ticks);
  for (uint8_t i=0; i<MAX_PULSES; i++) {
    if (!pulses[i].active) continue;
    pulses[i].distance+=(uint32_t)pulseSpeed*elapsedMs/20;
    pulses[i].energy=(uint8_t)(pulses[i].energy*pulseDecay);
    if (pulses[i].distance>limit || pulses[i].energy<4) pulses[i].active=false;
  }
  spatialPhase+=12+(uint16_t)(smoothActivity*.10);
}

uint8_t add8(uint8_t a, uint16_t b) {
  uint16_t total=(uint16_t)a+b;
  return total>255 ? 255 : total;
}

uint32_t colorAt(uint16_t hue, uint8_t saturation, uint8_t value) {
  return strip.gamma32(strip.ColorHSV(hue,saturation,value));
}

uint8_t hash8(uint16_t x) {
  x^=x>>7; x*=40503U; return (uint8_t)(x^(x>>8));
}

// Calm standalone palette used whenever no valid music packet is arriving.
// The three colors blend continuously so there are no sudden palette changes.
uint8_t blendByte(uint8_t a, uint8_t b, uint8_t amount) {
  return ((uint16_t)a*(255-amount)+(uint16_t)b*amount)/255;
}

uint32_t renderStandalone(unsigned long now) {
  // Fixed warm palette: red -> purple -> orange -> red.
  const uint8_t redR=255,    redG=0,   redB=18;
  const uint8_t purpleR=145, purpleG=0, purpleB=210;
  const uint8_t orangeR=255, orangeG=72, orangeB=0;

  uint32_t rawSum=0;
  for (uint16_t i=0; i<NUM_LEDS; i++) {
    uint8_t pos=(uint8_t)(((uint32_t)i*256UL)/NUM_LEDS);
    uint8_t r, g, b;

    if (pos < 85) {
      // Red -> purple.
      uint8_t amount=((uint16_t)pos*255)/84;
      r=blendByte(redR,purpleR,amount);
      g=blendByte(redG,purpleG,amount);
      b=blendByte(redB,purpleB,amount);
    } else if (pos < 170) {
      // Purple -> orange.
      uint8_t amount=((uint16_t)(pos-85)*255)/84;
      r=blendByte(purpleR,orangeR,amount);
      g=blendByte(purpleG,orangeG,amount);
      b=blendByte(purpleB,orangeB,amount);
    } else {
      // Orange -> red, closing the loop smoothly at the end of the strip.
      uint8_t amount=((uint16_t)(pos-170)*255)/85;
      r=blendByte(orangeR,redR,amount);
      g=blendByte(orangeG,redG,amount);
      b=blendByte(orangeB,redB,amount);
    }

    uint8_t mask=Layers::darkMask(Layers::staticPattern(i,NUM_LEDS,2),backgroundDarkness);
    Layers::Pixel color={r,g,b};
    Layers::Pixel background=Layers::faintGlow(
      Layers::scale(color,mask),color,
      Layers::staticPattern(i,NUM_LEDS,1));
    r=background.r; g=background.g; b=background.b;
    strip.setPixelColor(i,r,g,b);
    rawSum+=(uint32_t)r+g+b;
  }
  return rawSum;
}

Layers::Pixel backgroundPixel(uint16_t i) {
  // Static pools and dim gaps: no phase clock or animated brightness wave.
  uint8_t mask=Layers::darkMask(Layers::staticPattern(i,NUM_LEDS,2),backgroundDarkness);
  uint8_t level=constrain((int)backgroundLevel,0,255);
  // Fixed purple-to-red palette; notes color the foreground only.
  uint16_t hue=56000UL+(uint32_t)i*10000UL/NUM_LEDS;
  // Apply level linearly after color conversion, avoiding a second nonlinear
  // dimming curve that made lower levels seem stuck near black.
  Layers::Pixel color=Layers::scale(Layers::unpack(colorAt(hue,230,255)),level);
  Layers::Pixel background=Layers::scale(color,mask);
  // Preserve a faint trace of color in selected dark areas.
  // Using the music-level color preserves silence; foreground is composed later.
  return Layers::faintGlow(background,color,
    Layers::staticPattern(i,NUM_LEDS,1));
}

// Called only for pixels actually covered by a foreground effect.
void overlayPixel(int16_t position, Layers::Pixel color, uint8_t opacity) {
  if (position<0 || position>=NUM_LEDS || opacity==0) return;
  Layers::Pixel back=Layers::unpack(strip.getPixelColor(position));
  Layers::Pixel result=Layers::over(back,{color,opacity});
  strip.setPixelColor(position,result.r,result.g,result.b);
}

uint32_t renderFrame(uint8_t stripIndex, unsigned long now) {
  // Evaluate background colors at anchors, interpolate between them.
  // Twelve-pixel segments preserve a smooth gradient without 300 HSV conversions.
  Layers::Pixel left=backgroundPixel(0);
  for (uint16_t start=0;start<NUM_LEDS;start+=12) {
    uint16_t end=min((uint16_t)(start+12),(uint16_t)NUM_LEDS);
    Layers::Pixel right=backgroundPixel(end);
    for (uint16_t i=start;i<end;i++) {
      uint8_t alpha=(uint16_t)(i-start)*255/(end-start);
      Layers::Pixel pixel=Layers::over(left,{right,alpha});
      strip.setPixelColor(i,pixel.r,pixel.g,pixel.b);
    }
    left=right;
    // Drain UART while interrupts are enabled during rendering.
    readSerial();
  }
  uint8_t phrase=constrain((int)phraseEnvelope,0,255);
  if (phrase) {
    Layers::Pixel color=Layers::unpack(colorAt(frameHue+8500,180,255));
    for (int16_t offset=-23;offset<24;offset++)
      overlayPixel(NUM_LEDS/2+offset,color,(uint16_t)phrase*(24-abs(offset))/24);
  }
  for (uint8_t p=0;p<MAX_PULSES;p++) {
    if (!pulses[p].active) continue;
    Layers::Pixel color=Layers::unpack(colorAt(pulses[p].hue,235,255));
    int16_t radius=pulses[p].distance/16;
    int16_t width=pulses[p].width;
    for (int16_t distance=max(0,radius-width+1);distance<radius+width;distance++) {
      uint8_t alpha=(uint16_t)(width-abs(distance-radius))*pulses[p].energy/width;
      overlayPixel(NUM_LEDS/2+distance,color,alpha);
      if (distance) overlayPixel(NUM_LEDS/2-distance,color,alpha);
    }
  }
  uint8_t flash=flashRemainingMs>350 ? 255 : (uint32_t)flashRemainingMs*255/350;
  if (flash) {
    for (uint16_t start=0;start<NUM_LEDS;start+=12) {
      if ((start/12+stripIndex)%4) continue;
      for (uint16_t i=start;i<min((uint16_t)(start+12),(uint16_t)NUM_LEDS);i++)
        overlayPixel(i,Layers::unpack(colorAt(frameHue,180,255)),flash);
    }
  }
  // Draw 3 pixels per sparkle, instead of checking every candidate at every LED.
  // Foreground remains independent of the background's brightness and darkness.
  Layers::Pixel sparkleColor=Layers::unpack(colorAt((uint16_t)foregroundHue,200,255));
  bool active=smoothLevel>8;
  uint8_t high=constrain((int)smoothHigh,0,255);
  uint8_t chance=96+((uint16_t)high*159)/255;
  for (uint8_t k=0;k<SPARKLES_PER_STRIP && active;k++) {
    uint32_t clock=now+k*113UL+stripIndex*97UL;
    uint16_t seed=(uint16_t)(clock/SPARKLE_LIFETIME_MS)*109U+k*997U+stripIndex*3571U;
    if (k>=2 && hash8(seed+31)>chance) continue;
    // Keep the whole sweep on the strip instead of wrapping across its ends.
    int16_t position=12+((uint16_t)hash8(seed)*(NUM_LEDS-24))/256;
    uint16_t age=clock%SPARKLE_LIFETIME_MS;
    // Sweep out and back with a rounded turnaround, fading throughout.
    uint8_t phase=(uint32_t)age*256/SPARKLE_LIFETIME_MS;
    int16_t travel=(uint16_t)strip.sine8((uint8_t)(phase-64))*12/255;
    position+=k%2 ? travel : -travel;
    uint8_t opacity=Layers::envelope(age,SPARKLE_LIFETIME_MS);
    overlayPixel(position-1,sparkleColor,opacity/2);
    overlayPixel(position,sparkleColor,opacity);
    overlayPixel(position+1,sparkleColor,opacity/2);
  }
  uint32_t rawSum=0;
  // Native RGB bytes, independent of GRB ordering; full brightness at this point.
  uint8_t *pixels=strip.getPixels();
  for (uint16_t i=0;i<NUM_LEDS*3;i++) rawSum+=pixels[i];
  return rawSum;
}

uint8_t brightnessForBudget(uint32_t rawSum) {
  if (rawSum==0) return MAX_BRIGHTNESS;
  int32_t budget=(int32_t)MAX_MILLIAMPS
                 -(int32_t)NUM_LEDS*NUM_STRIPS*IDLE_MA_PER_LED;
  if (budget<=0) return 1;
  uint32_t allowed=((uint32_t)budget*255UL*255UL)
                   /(rawSum*NUM_STRIPS*(uint32_t)FULL_MA_PER_CHANNEL);
  if (allowed>MAX_BRIGHTNESS) return MAX_BRIGHTNESS;
  return allowed<1 ? 1 : allowed;
}

// Output one strip at a time; serial servicing and animation updates occur
// between strips. Background brightness only follows the audio level.
void showStrip(uint8_t index) {
  int16_t previousPin=strip.getPin();
  strip.setPin(LED_PINS[index]);
  pinMode(previousPin,OUTPUT); digitalWrite(previousPin,LOW);
  uint16_t timerStart=TCNT1;
  unsigned long microsStart=micros();
  strip.show();
  unsigned long softwareUs=micros()-microsStart;
  uint32_t hardwareUs=(uint16_t)(TCNT1-timerStart)*4UL;
  if (hardwareUs>softwareUs) {
    uint32_t lost=hardwareUs-softwareUs+lostClockRemainderUs;
    lostClockMs+=lost/1000;
    lostClockRemainderUs=lost%1000;
  }
}

void setup() {
  TCCR1A=0; TCCR1B=_BV(CS11)|_BV(CS10); TCNT1=0;
  for (uint8_t s=0; s<NUM_STRIPS; s++) {
    pinMode(LED_PINS[s], OUTPUT);
    digitalWrite(LED_PINS[s], LOW);
  }
  Serial.begin(115200); strip.begin(); strip.setBrightness(MAX_BRIGHTNESS);
  strip.clear();
  for (uint8_t i=0;i<NUM_STRIPS;i++) showStrip(i);
  Serial.println("READY");
}

void loop() {
  static uint8_t nextStrip=0;
  static unsigned long receiveStarted=0;
  readSerial();
  unsigned long now=animationMillis();
  // Nonblocking receive window: return to serial processing, not busy-wait.
  if (now-receiveStarted<3) return;
  uint16_t elapsedMs=min(250UL,now-lastFrame);
  lastFrame=now;
  bool music=lastValidPacket!=0 && now-lastValidPacket<=1500;
  if (music) {
    advanceAnimation(elapsedMs);
  }
  strip.setBrightness(MAX_BRIGHTNESS);
  uint32_t rawSum=music ? renderFrame(nextStrip,now) : renderStandalone(now);
  strip.setBrightness(min(userBrightness,brightnessForBudget(rawSum)));
  showStrip(nextStrip);
  nextStrip=(nextStrip+1)%NUM_STRIPS;
  receiveStarted=animationMillis();
}
