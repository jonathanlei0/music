#include <Adafruit_NeoPixel.h>
#include "led_layers.h"
#include "mirrored_neopixel.h"

// Each strip's DIN connects to its own Arduino digital pin.
constexpr uint8_t LED_PINS[] = {6, 7, 8, 9};
const uint8_t NUM_STRIPS = sizeof(LED_PINS) / sizeof(LED_PINS[0]);
const uint8_t NUM_OUTPUT_PAIRS=NUM_STRIPS/2;
#if !defined(__AVR_ATmega328P__)
#error "Parallel pin pairs and Timer1 timing require the ATmega328P Arduino Uno."
#endif
static_assert(LED_PINS[0]==6 && LED_PINS[1]==7 && LED_PINS[2]==8 && LED_PINS[3]==9,
              "Output pairs must be D6/D7 (PORTD) and D8/D9 (PORTB).");
// LED count per strip; all strips display the same complete frame.
#define NUM_LEDS 300
// Total current budget across all four strips, not a per-strip budget.
#define MAX_MILLIAMPS 60000UL
#define MAX_BRIGHTNESS 255
#define IDLE_MA_PER_LED 1
#define FULL_MA_PER_CHANNEL 20
#define FRAME_MS 50  // Fixed 20 FPS publication target, not an animation delay.
#define MAX_PULSES 8
#define SPARKLES_PER_STRIP 12
#define TEMPO_SPARKLES 8
// Foreground particles use 12% of their previous color intensity. The final
// user brightness cap still applies to the complete composition afterward.
#define FOREGROUND_PARTICLE_GAIN 31
uint16_t tempoBeatPhase=0;
bool tempoRunning=false, tempoAligned=false;
// Persistent motion, advanced once per shared four-strip frame.
uint32_t sparkleTravel=0, sparkleRemainder=0;
uint32_t middleTravel=0, middleRemainder=0;
float motionBpm=90.0f;
float backgroundPhase=0;

// User cap is independent of the electrical current budget.
uint8_t userBrightness=MAX_BRIGHTNESS;
uint8_t backgroundDarkness=65;
bool sectionPalettes=false;
bool activityControl=false;
bool perfEnabled=false;
uint16_t perfFrames=0, perfRenderMs=0, perfOutputMs=0;
uint16_t perfLateFrames=0, perfMaxRenderMs=0;
unsigned long perfSince=0;
float musicActivity=0;
uint8_t paletteTarget=0;
uint16_t paletteElapsed=4000, paletteDuration=4000;
Layers::Pixel paletteLeft={110,0,190}, paletteRight={190,0,35};
Layers::Pixel paletteFromLeft=paletteLeft, paletteFromRight=paletteRight;
Layers::Pixel paletteToLeft=paletteLeft, paletteToRight=paletteRight;

void selectPalette(uint8_t target, bool fast) {
  if (target==paletteTarget) return;  // Repeated frames must not restart a fade.
  paletteTarget=target;
  paletteFromLeft=paletteLeft; paletteFromRight=paletteRight;
  switch (target) {
    case 1: paletteToLeft={220,0,25}; paletteToRight={255,70,0}; break;
    case 2: paletteToLeft={230,0,100}; paletteToRight={255,130,10}; break;
    case 3: paletteToLeft={15,0,65}; paletteToRight={65,0,110}; break;
    default: paletteToLeft={110,0,190}; paletteToRight={190,0,35}; break;
  }
  paletteElapsed=0; paletteDuration=fast ? 400 : 4000;
}
// Reuse one pixel buffer: four 300-pixel buffers exceed the Uno's SRAM.
MirroredNeoPixel strip(NUM_LEDS, LED_PINS[0], NEO_GRB + NEO_KHZ800);

// Binary protocol: A5 5A, 16-byte payload, one-byte rolling checksum.
// Payload: level,sub,bass,body,mid,high,air,hue_lo,hue_hi,event,phrase,
// bpm,width/on-enabled-palette,onset,harmonic,percussive.
// Event 1=beat, 2=section, 3=drop.
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
bool haveBeatPulse=false;
unsigned long beatPulseAt=0;
uint8_t beatPulseStrength=0;
uint8_t beatPulseStarting=0;
uint16_t beatPulseDuration=550;
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
  // Beat accents belong to half the moving foreground; room-wide accents
  // remain exclusive to massive drops. Guard repeated event packets.
  unsigned long now=animationMillis();
  // Anchor the steady train to the first detected beat, then let BPM drive it.
  // Quiet or missed onsets do not suppress later pulses or restart the clock.
  if (event && !tempoAligned) {
    tempoBeatPhase=0; tempoAligned=true;
  }
  if (event && (!haveBeatPulse || now-beatPulseAt>=160UL)) {
    beatPulseStarting=haveBeatPulse ? Layers::beatFade(
      beatPulseStrength,now-beatPulseAt,beatPulseDuration,beatPulseStarting) : 0;
    haveBeatPulse=true;
    beatPulseAt=now;
    beatPulseStrength=Layers::bassBeatStrength(frameBass);
    beatPulseDuration=550+(uint32_t)frameBass*300/255;
  }
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
  // Live-only extension in the former nominal stereo-width byte. Offline
  // senders retain legacy semantics unless PALETTES was explicitly enabled.
  if (sectionPalettes && (packet[12]&0xF0)==0xA0)
    selectPalette(packet[12]&3,(packet[12]&8)!=0);
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
      if (strcmp(rxBuffer,"PALETTES,1")==0) {
        sectionPalettes=true;
        Serial.println("ACK_PALETTES,1");
      }
      if (strcmp(rxBuffer,"ACTIVITY,1")==0) {
        activityControl=true;
        Serial.println("ACK_ACTIVITY,1");
      }
      if (strcmp(rxBuffer,"PERF,1")==0) {
        perfEnabled=true; perfSince=animationMillis();
        perfFrames=perfRenderMs=perfOutputMs=0;
        Serial.println("ACK_PERF,1");
      }
      rxLength=0;
    }
    else if (c!='\r' && rxLength<sizeof(rxBuffer)-1) rxBuffer[rxLength++]=c;
  }
}

void advanceAnimation(uint16_t elapsedMs) {
  if (frameLevel>0) {
    if (tempoRunning)
      tempoBeatPhase=Layers::advanceBeatPhase(tempoBeatPhase,frameBpm,elapsedMs);
    else tempoBeatPhase=0;
    tempoRunning=true;
  } else {
    tempoRunning=false; tempoAligned=false; tempoBeatPhase=0;
  }
  musicActivity+=(framePercussive-musicActivity)*elapsedMs/(600.0f+elapsedMs);
  uint8_t activity=activityControl ? constrain((int)musicActivity,0,255) : 255;
  paletteElapsed=min((uint32_t)paletteDuration,(uint32_t)paletteElapsed+elapsedMs);
  uint8_t paletteMix=(uint32_t)paletteElapsed*255/paletteDuration;
  paletteLeft=Layers::over(paletteFromLeft,{paletteToLeft,paletteMix});
  paletteRight=Layers::over(paletteFromRight,{paletteToRight,paletteMix});
  motionBpm+=(frameBpm-motionBpm)*elapsedMs/(500.0f+elapsedMs);
  uint32_t middleStep=(uint32_t)(motionBpm+0.5f)*elapsedMs*6UL*256UL+middleRemainder;
  middleTravel=(middleTravel+middleStep/60000UL)%(NUM_LEDS*256UL);
  middleRemainder=middleStep%60000UL;
  sparkleTravel=Layers::advanceTravel(sparkleTravel,(uint16_t)(motionBpm+0.5f),
                                      elapsedMs,sparkleRemainder,NUM_LEDS,activity);
  // Sparse sections drift more slowly, without changing position abruptly.
  float backgroundCycleMs=36000.0f-24000.0f*activity/255;
  backgroundPhase+=elapsedMs*256.0f/backgroundCycleMs;
  if (backgroundPhase>=256) backgroundPhase-=256;
  // Follow rises quickly, then fade out a little more gently. These are time
  // constants, not blocking delays; update on every rendered strip.
  float backgroundResponseMs=frameLevel>backgroundLevel ? 180.0f : 300.0f;
  backgroundLevel+=(frameLevel-backgroundLevel)*elapsedMs/(backgroundResponseMs+elapsedMs);
  if (!haveForegroundHue) { foregroundHue=frameHue; haveForegroundHue=true; }
  float hueDelta=frameHue-foregroundHue;
  if (hueDelta>32768) hueDelta-=65536;
  if (hueDelta<-32768) hueDelta+=65536;
  // Follow melody notes promptly even when activity keeps particle motion slow.
  float colorResponseMs=160.0f;
  foregroundHue+=hueDelta*elapsedMs/(colorResponseMs+elapsedMs);
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
  // Move the brightness pools continuously through the fixed color palette.
  uint8_t phase=(uint32_t)i*512/NUM_LEDS-(uint8_t)backgroundPhase;
  uint8_t activity=activityControl ? constrain((int)musicActivity,0,255) : 0;
  uint8_t darkness=Layers::activityDarkness(backgroundDarkness,activity);
  uint8_t mask=Layers::darkMask(strip.sine8(phase),darkness);
  uint8_t level=constrain((int)backgroundLevel,0,255);
  level=Layers::activityLevel(level,activity);
  // Section palettes crossfade independently of particle and background motion.
  // Individual notes still color only the foreground.
  uint8_t position=(uint32_t)i*255/NUM_LEDS;
  Layers::Pixel color=Layers::scale(Layers::over(paletteLeft,{paletteRight,position}),level);
  Layers::Pixel background=Layers::scale(color,mask);
  // Preserve a faint trace of color in selected dark areas.
  // Using the music-level color preserves silence; foreground is composed later.
  return Layers::faintGlow(background,color,
    Layers::staticPattern(i,NUM_LEDS,1));
}

// Called only for pixels actually covered by a foreground effect.
void overlayPixel(int16_t position, Layers::Pixel color, uint8_t opacity) {
  if (position<0 || position>=NUM_LEDS || opacity==0) return;
  // Buffer is unscaled GRB during rendering. Avoid color packing/unpacking
  // and library brightness conversions for every foreground pixel.
  uint8_t *pixel=strip.getPixels()+position*3;
  pixel[0]=Layers::mix(pixel[0],color.g,opacity);
  pixel[1]=Layers::mix(pixel[1],color.r,opacity);
  pixel[2]=Layers::mix(pixel[2],color.b,opacity);
}

void renderMiddleGround() {
  if (!activityControl) return;
  uint8_t opacity=Layers::clubOpacity(constrain((int)musicActivity,0,255));
  opacity=(uint16_t)opacity*constrain((int)(smoothLevel*4),0,255)/255;
  if (!opacity) return;
  // Eight moving color bands, separated by dark space. Fractional positions
  // taper each band smoothly as it crosses LEDs. Render before foreground.
  for (uint8_t band=0;band<8;band++) {
    uint32_t center=(middleTravel+(uint32_t)band*NUM_LEDS*256UL/8)%(NUM_LEDS*256UL);
    int16_t pixel=center>>8;
    int16_t fraction=center&255;
    Layers::Pixel color=Layers::unpack(colorAt(
      (uint16_t)foregroundHue+(uint16_t)band*8192U,220,255));
    for (int8_t offset=-5;offset<=6;offset++) {
      uint16_t distance=abs((int16_t)offset*256-fraction);
      if (distance>=6*256) continue;
      uint8_t alpha=(uint32_t)(6*256-distance)*opacity/(6*256);
      int16_t position=(pixel+offset+NUM_LEDS)%NUM_LEDS;
      overlayPixel(position,color,alpha);
    }
  }
}

uint32_t renderFrame(unsigned long now) {
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
  renderMiddleGround();
  uint8_t beatAccent=haveBeatPulse ? Layers::beatFade(
    beatPulseStrength,animationMillis()-beatPulseAt,beatPulseDuration,beatPulseStarting) : 0;
  // All foreground is beat-gated; the background and club middle layer continue.
  if (beatAccent) {
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
      Layers::Pixel flashColor=Layers::unpack(colorAt(frameHue,180,255));
      for (uint16_t start=0;start<NUM_LEDS;start+=12) {
        if ((start/12)%4) continue;
        for (uint16_t i=start;i<min((uint16_t)(start+12),(uint16_t)NUM_LEDS);i++)
          overlayPixel(i,flashColor,flash);
      }
    }
    // Draw only the pixels occupied by each moving foreground sparkle.
    // Foreground remains independent of the background's brightness and darkness.
    Layers::Pixel sparkleColor=Layers::scale(
      Layers::unpack(colorAt((uint16_t)foregroundHue,160,255)),FOREGROUND_PARTICLE_GAIN);
    uint32_t path=(NUM_LEDS-1)*512UL;
    uint16_t radiusQ8=5*256U+(uint32_t)beatAccent*3*256/255;
    uint8_t radius=(radiusQ8+255)/256;
    uint16_t edgeScale=(255UL<<16)/(radiusQ8-512);
    for (uint8_t k=0;k<SPARKLES_PER_STRIP;k++) {
      // Permanent particles with evenly spaced phases; bounce at strip ends.
      // No age, random respawn, or treble probability gate.
      uint32_t phase=(k%2 ? path-sparkleTravel : sparkleTravel)+path*k/SPARKLES_PER_STRIP;
      uint32_t position=Layers::reflectedPosition(phase,NUM_LEDS);
      // Keep the four-LED core inside the strip even at a turnaround.
      position=512+position*(NUM_LEDS-5)/(NUM_LEDS-1);
      int16_t pixel=position>>8;
      uint8_t fraction=position&255;
      // No steady baseline: bass controls beat brightness and footprint.
      // Fractional width avoids whole-LED jumps as the pulse expands/fades.
      for (int8_t offset=-radius;offset<=radius+1;offset++) {
        uint16_t distance=abs((int16_t)offset*256-fraction);
        if (distance>=radiusQ8) continue;
        uint8_t alpha=Layers::wideSparkleAlphaFast(distance,radiusQ8,beatAccent,edgeScale);
        overlayPixel(pixel+offset,sparkleColor,alpha);
      }
    }
  }
  // A separate steady pulse train driven by BPM, not by bass/onset loudness.
  if (tempoRunning) {
    uint16_t period=60000UL/frameBpm;
    uint16_t duration=min(360U,(uint16_t)((uint32_t)period*3/4));
    uint8_t opacity=Layers::beatFade(230,tempoBeatPhase/frameBpm,duration);
    Layers::Pixel color=Layers::scale(
      Layers::unpack(colorAt((uint16_t)foregroundHue,130,255)),FOREGROUND_PARTICLE_GAIN);
    uint32_t path=(NUM_LEDS-1)*512UL;
    for (uint8_t k=0;k<TEMPO_SPARKLES && opacity;k++) {
      uint32_t phase=sparkleTravel+path*(2*k+1)/(TEMPO_SPARKLES*2);
      uint32_t position=Layers::reflectedPosition(phase,NUM_LEDS);
      int16_t pixel=position>>8;
      uint8_t fraction=position&255;
      for (int8_t offset=-2;offset<=3;offset++) {
        uint16_t distance=abs((int16_t)offset*256-fraction);
        if (distance>=768) continue;
        overlayPixel(pixel+offset,color,(uint32_t)(768-distance)*opacity/768);
      }
    }
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

// Two strips latch simultaneously per transmission; the buffer is unchanged
// between pairs. Four physical strips now need only two data transmissions.
void showPair(uint8_t index) {
  if (!strip.selectPair(LED_PINS[index*2],LED_PINS[index*2+1])) return;
  uint16_t timerStart=TCNT1;
  unsigned long microsStart=micros();
  strip.show();
  unsigned long softwareUs=micros()-microsStart;
  uint32_t hardwareUs=(uint16_t)(TCNT1-timerStart)*4UL;
  if (perfEnabled) perfOutputMs+=hardwareUs/1000;
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
  for (uint8_t i=0;i<NUM_OUTPUT_PAIRS;i++) showPair(i);
  Serial.println("READY");
}

void loop() {
  static uint8_t nextPair=0;
  static unsigned long receiveStarted=0, nextPublishAt=0;
  static bool frameReady=false, pacingStarted=false;
  readSerial();
  unsigned long now=animationMillis();
  if (now-receiveStarted<3) return;
  if (nextPair==0 && !frameReady) {
    // Render ahead into the reusable buffer, then publish on the deadline.
    // LED chips keep displaying the preceding frame during this computation.
    unsigned long frameTime=pacingStarted && (int32_t)(nextPublishAt-now)>0 ? nextPublishAt : now;
    uint16_t elapsedMs=min(250UL,frameTime-lastFrame);
    lastFrame=frameTime;
    bool music=lastValidPacket!=0 && now-lastValidPacket<=1500;
    if (music) advanceAnimation(elapsedMs);
    strip.setBrightness(MAX_BRIGHTNESS);
    uint32_t rawSum=music ? renderFrame(frameTime) : renderStandalone(frameTime);
    strip.setBrightness(min(userBrightness,brightnessForBudget(rawSum)));
    unsigned long renderEnd=animationMillis();
    if (perfEnabled) {
      uint16_t renderMs=renderEnd-now;
      perfRenderMs+=renderMs;
      perfMaxRenderMs=max(perfMaxRenderMs,renderMs);
    }
    frameReady=true;
    if (!pacingStarted) { nextPublishAt=renderEnd; pacingStarted=true; }
  }
  now=animationMillis();
  if (nextPair==0) {
    if ((int32_t)(now-nextPublishAt)<0) return; // Continue serving serial.
    unsigned long late=now-nextPublishAt;
    if (perfEnabled && late>2) perfLateFrames++;
    // Reschedule from a late publication; never fire a burst to catch up.
    nextPublishAt=now+FRAME_MS;
    frameReady=false;
  }
  showPair(nextPair);
  nextPair=(nextPair+1)%NUM_OUTPUT_PAIRS;
  receiveStarted=animationMillis();
  if (perfEnabled && nextPair==0) {
    perfFrames++;
    unsigned long elapsed=receiveStarted-perfSince;
    if (elapsed>=2000 && Serial.availableForWrite()>=48) {
      char report[48];
      snprintf(report,sizeof(report),"PERF,%lu,%u,%u,%u,%u\n",
               (unsigned long)perfFrames*10000UL/elapsed,
               perfRenderMs/perfFrames,perfOutputMs/perfFrames,
               perfLateFrames,perfMaxRenderMs);
      Serial.print(report);
      perfFrames=perfRenderMs=perfOutputMs=perfLateFrames=perfMaxRenderMs=0;
      perfSince=receiveStarted;
    }
  }
}
