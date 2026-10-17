#pragma once
#include <stdint.h>
#include "ease_table.h"

namespace Layers {
// Exact floor(value/255) for 0..65025, using only 16-bit addition and shifts.
inline uint8_t divide255(uint16_t value) {
  return (value+1+(value>>8))>>8;
}
// One cycle per quarter-note beat, regardless of detected onset strength.
inline uint16_t advanceBeatPhase(uint16_t phase, uint16_t bpm, uint16_t elapsedMs) {
  return (phase+(uint32_t)bpm*elapsedMs)%60000UL;
}
inline uint8_t ease8(uint8_t x) {
  return lookupEase(x);
}
inline uint8_t wideSparkleAlpha(uint16_t distanceQ8, uint16_t radiusQ8, uint8_t opacity) {
  if (distanceQ8<=512) return opacity; // Four-LED minimum full-bright core.
  if (radiusQ8<=512 || distanceQ8>=radiusQ8) return 0;
  uint8_t shape=(uint32_t)(radiusQ8-distanceQ8)*255/(radiusQ8-512);
  return (uint16_t)ease8(shape)*opacity/255;
}
// Precompute edgeScale=(255<<16)/(radiusQ8-512) once for the whole frame.
// At most two alpha levels differ from exact division, before the dim color gain.
inline uint8_t wideSparkleAlphaFast(uint16_t distanceQ8, uint16_t radiusQ8,
                                   uint8_t opacity, uint16_t edgeScale) {
  if (distanceQ8<=512) return opacity;
  if (distanceQ8>=radiusQ8) return 0;
  uint8_t shape=((uint32_t)(radiusQ8-distanceQ8)*edgeScale)>>16;
  return divide255((uint16_t)ease8(shape)*opacity);
}
inline uint8_t bassBeatStrength(uint8_t bass) {
  return 180+(uint16_t)bass*75/255;
}
inline uint8_t beatFade(uint8_t strength, uint32_t age, uint16_t duration,
                        uint8_t starting=0) {
  if (!duration || age>=duration) return 0;
  // Short fade-in, visible peak, then a long rounded release. Retriggers rise
  // from the previous pulse's current level instead of briefly going black.
  if (age<80) {
    uint8_t rise=ease8(age*255/80);
    return ((uint32_t)starting*(255-rise)+(uint32_t)strength*rise)/255;
  }
  if (age<=140 || duration<=140) return strength;
  uint16_t x=(age-140)*255UL/(duration-140);
  uint16_t eased=ease8(x);
  return (uint16_t)strength*(255-eased)/255;
}
// Club layer fades in gradually above the shared club threshold (160/255).
inline uint8_t clubOpacity(uint8_t activity) {
  if (activity<=160) return 0;
  uint16_t x=(uint16_t)(activity-160)*255/95;
  uint16_t eased=ease8(x);
  return eased*90/255;  // Keep room for the foreground to remain dominant.
}
// Retain the user's darkness setting at rest; busy music fills most gaps.
inline uint8_t activityDarkness(uint8_t darkness, uint8_t activity) {
  return (uint16_t)darkness*(255-(uint16_t)activity*3/4)/255;
}
// Up to roughly 25% more light in already-lit pixels, with no byte overflow.
inline uint8_t activityLevel(uint8_t level, uint8_t activity) {
  uint16_t boosted=(uint32_t)level*(256+activity/4)/256;
  return boosted>255 ? 255 : boosted;
}
// Fixed-scale activity controls 0.125–12 LEDs/beat; quadratic mapping keeps
// sparse music extremely slow. Carry fractional progress across frames.
inline uint32_t advanceTravel(uint32_t position, uint16_t bpm, uint16_t elapsedMs,
                              uint32_t &remainder, uint16_t count, uint8_t activity=255) {
  uint16_t perBeat=32+(uint32_t)activity*activity*(3072-32)/65025UL;
  uint32_t amount=(uint32_t)bpm*elapsedMs*perBeat+remainder;
  remainder=amount%60000UL;
  return (position+amount/60000UL) % ((count-1)*512UL);
}
inline uint32_t reflectedPosition(uint32_t travel, uint16_t count) {
  uint32_t end=(count-1)*256UL;
  uint32_t phase=travel%(end*2);
  return phase<=end ? phase : end*2-phase;
}
struct Pixel { uint8_t r, g, b; };
struct Foreground { Pixel color; uint8_t opacity; };

inline uint8_t mix(uint8_t back, uint8_t front, uint8_t alpha) {
  return divide255((uint16_t)back*(255-alpha)+(uint16_t)front*alpha);
}
inline Pixel over(Pixel background, Foreground foreground) {
  return {mix(background.r,foreground.color.r,foreground.opacity),
          mix(background.g,foreground.color.g,foreground.opacity),
          mix(background.b,foreground.color.b,foreground.opacity)};
}
inline Pixel scale(Pixel p, uint8_t gain) {
  return {divide255((uint16_t)p.r*gain),
          divide255((uint16_t)p.g*gain),
          divide255((uint16_t)p.b*gain)};
}
inline Pixel unpack(uint32_t color) {
  return {(uint8_t)(color>>16),(uint8_t)(color>>8),(uint8_t)color};
}
// Lift only selected dark regions to a very faint version of their own color.
// The brightest glow channel is at most 6/255 before the user's brightness cap.
inline Pixel faintGlow(Pixel background, Pixel color, uint8_t wave) {
  uint8_t gain=wave>128 ? (uint16_t)(wave-128)*6/127 : 0;
  Pixel floor=scale(color,gain);
  return {background.r>floor.r ? background.r : floor.r,
          background.g>floor.g ? background.g : floor.g,
          background.b>floor.b ? background.b : floor.b};
}
// Fixed triangular light pools. No clock or sine calculation is needed.
inline uint8_t staticPattern(uint16_t position, uint16_t count, uint8_t pools) {
  uint8_t phase=(uint32_t)position*256*pools/count;
  return phase<128 ? phase*2 : (255-phase)*2;
}
// Shape a static brightness mask; higher darkness widens its dark gaps.
inline uint8_t darkMask(uint8_t sine, uint8_t darkness) {
  uint8_t threshold=(uint16_t)darkness*200/100;
  if (sine<=threshold) return 0;
  uint16_t x=(uint16_t)(sine-threshold)*255/(255-threshold);
  return ease8(x);
}
inline uint8_t envelope(uint32_t age, uint16_t lifetime) {
  if (!lifetime || age>=lifetime) return 0;
  uint16_t phase=age*510UL/lifetime;
  return phase<=255 ? phase : 510-phase;
}
}
