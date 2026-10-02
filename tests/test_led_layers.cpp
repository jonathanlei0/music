#include "../arduino/led_layers.h"
#include <cassert>

int main() {
  using namespace Layers;
  // Fast AVR blending must be exactly equivalent to the former division.
  for (uint32_t value=0;value<=65025;value++)
    assert(divide255(value)==value/255);
  for (uint32_t value=0;value<256;value++)
    assert(ease8(value)==value*value*(765-2*value)/65025UL);
  for (uint16_t radius=1280;radius<=2048;radius++) {
    uint16_t reciprocal=(255UL<<16)/(radius-512);
    for (uint16_t distance=0;distance<=radius;distance++) {
      int error=(int)wideSparkleAlpha(distance,radius,255)
        -wideSparkleAlphaFast(distance,radius,255,reciprocal);
      assert(error>=0 && error<=2);
    }
  }
  uint16_t beatPhase=0;
  int ticks=0;
  for (int i=0;i<20;i++) {
    uint16_t next=advanceBeatPhase(beatPhase,120,50);
    if (next<beatPhase) ticks++;
    beatPhase=next;
  }
  assert(ticks==2 && beatPhase==0); // Exactly twice per second at 120 BPM.
  assert(advanceBeatPhase(0,60,500)==30000);
  for (int fraction=0;fraction<256;fraction++) {
    int core=0;
    for (int pixel=-8;pixel<=9;pixel++) {
      int distance=pixel*256-fraction;
      if (distance<0) distance=-distance;
      core+=wideSparkleAlpha(distance,5*256,200)==200;
    }
    assert(core>=4);
  }
  assert(bassBeatStrength(0)==180 && bassBeatStrength(255)==255);
  assert(bassBeatStrength(220)>bassBeatStrength(100));
  assert(beatFade(255,0,850)==0);
  assert(beatFade(255,40,850)>120 && beatFade(255,40,850)<135);
  assert(beatFade(255,80,850)==255 && beatFade(255,140,850)==255);
  assert(beatFade(255,400,850)>170);
  assert(beatFade(255,850,850)==0);
  assert(beatFade(255,0,850,170)==170);
  assert(beatFade(255,20,850,170)>170);
  for (int age=141;age<=850;age++)
    assert(beatFade(255,age,850)<=beatFade(255,age-1,850));
  for (int width=1281;width<=2048;width++) {
    int delta=wideSparkleAlpha(900,width,200)-wideSparkleAlpha(900,width-1,200);
    assert(delta>=0 && delta<=2);
  }
  assert(clubOpacity(0)==0 && clubOpacity(160)==0);
  assert(clubOpacity(255)==90);
  for (int activity=1;activity<256;activity++) {
    assert(clubOpacity(activity)>=clubOpacity(activity-1));
    assert(clubOpacity(activity)-clubOpacity(activity-1)<=2);
  }
  Pixel layered=over({10,0,0},{{0,255,0},clubOpacity(255)});
  Pixel foreground=over(layered,{{0,0,255},255});
  assert(layered.g>0 && foreground.g==0 && foreground.b==255);
  assert(activityDarkness(65,0)==65);
  assert(activityDarkness(65,255)==16);
  assert(activityLevel(160,0)==160);
  assert(activityLevel(160,255)>190);
  assert(activityLevel(255,255)==255);
  assert(activityLevel(0,255)==0);
  int calmLit=0, busyLit=0;
  for (int value=0;value<256;value++) {
    uint8_t calm=darkMask(value,activityDarkness(65,0));
    uint8_t busy=darkMask(value,activityDarkness(65,255));
    assert(busy>=calm);
    calmLit+=calm>0; busyLit+=busy>0;
  }
  assert(busyLit>calmLit);
  uint32_t remainder=0, position=0;
  for (int i=0;i<10;i++) position=advanceTravel(position,120,50,remainder,300);
  assert(position==12*256UL); // One beat at 120 BPM moves 12 LEDs.
  uint32_t fastRemainder=0;
  assert(advanceTravel(0,120,250,fastRemainder,300)==6*256UL);
  uint32_t slowRemainder=0;
  assert(advanceTravel(0,60,250,slowRemainder,300)==3*256UL);
  uint32_t edge=299*256UL;
  assert(reflectedPosition(edge-1,300)==edge-1);
  assert(reflectedPosition(edge+1,300)==edge-1);
  assert(reflectedPosition(edge*2,300)==0);
  uint32_t coarseRemainder=0;
  uint32_t coarse=advanceTravel(0,97,200,coarseRemainder,300);
  uint32_t fine=0, fineRemainder=0;
  for (int i=0;i<20;i++) fine=advanceTravel(fine,97,10,fineRemainder,300);
  assert(fine==coarse && fineRemainder==coarseRemainder);
  uint32_t calmRemainder=0, calmPosition=0;
  for (int i=0;i<100;i++)
    calmPosition=advanceTravel(calmPosition,90,100,calmRemainder,300,0);
  assert(calmPosition==480); // 1.875 LEDs in ten seconds at minimum activity.
  uint32_t busyRemainder=0;
  assert(advanceTravel(0,90,100,busyRemainder,300,255)>calmPosition/100);
  Pixel back={255,0,0}, front={0,0,255};
  Pixel opaque=over(back,{front,255});
  assert(opaque.r==0 && opaque.b==255);
  Pixel transparent=over(back,{front,0});
  assert(transparent.r==255 && transparent.b==0);
  Pixel partial=over(back,{front,128});
  assert(partial.r==127 && partial.b==128);
  int darkLow=0, darkHigh=0;
  for (int value=0;value<256;value++) {
    darkLow+=darkMask(value,20)==0;
    darkHigh+=darkMask(value,80)==0;
    assert(darkMask(value,80)<=darkMask(value,20));
  }
  assert(darkHigh>darkLow);
  assert(darkMask(255,100)==255 && darkMask(0,65)==0);
  assert(envelope(0,700)==0 && envelope(350,700)==255 && envelope(700,700)==0);
}
