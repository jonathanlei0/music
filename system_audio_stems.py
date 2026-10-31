"""Fast spectral stem estimates for lighting; not learned source separation."""
from collections import deque
import math
import numpy as np
from scipy.ndimage import median_filter
from audio_led import SAMPLE_RATE, MusicalAnalyzer
from virtualdj_melody import NoteTracker


class SpectralStems:
    SIZE = 4096

    def __init__(self):
        self.window = np.hanning(self.SIZE).astype(np.float32)
        self.audio = np.zeros((self.SIZE,2),dtype=np.float32)
        self.filled = 0
        self.history = deque(maxlen=17)
        self.frequencies = np.fft.rfftfreq(self.SIZE,1/SAMPLE_RATE)
        self.tempo = MusicalAnalyzer()
        self.notes = NoteTracker()
        self.beatpos = 0.0
        self.last_time = None
        self.last_grid_anchor = None
        self.counter = 0

    def reset(self):
        self.__init__()

    def rms_spectrum(self, spectrum):
        power = np.abs(spectrum)**2
        # Real FFT endpoints have no negative-frequency partner.
        total = 2*np.sum(power,axis=0)-power[0]-power[-1]
        return float(np.sqrt(max(0,float(np.mean(total)))/(self.SIZE*np.sum(self.window**2))))

    def process(self, stereo, captured):
        count = min(len(stereo),self.SIZE)
        if self.last_time is not None and captured-self.last_time > .12:
            self.reset()  # A missing callback must not become a musical drop.
        dt = len(stereo)/SAMPLE_RATE if self.last_time is None else max(0,captured-self.last_time)
        self.last_time = captured
        self.audio[:-count] = self.audio[count:]
        self.audio[-count:] = stereo[-count:]
        self.filled = min(self.SIZE,self.filled+count)
        mono = stereo[:,int(np.argmax(np.mean(stereo**2,axis=0)))]
        analysis = self.tempo.process(mono,captured)
        bpm = analysis['bpm']
        self.beatpos += dt*bpm/60
        if analysis['event'] and (self.last_grid_anchor is None or
                abs(self.beatpos-round(self.beatpos)) < .25):
            # Small onset corrections; don't replay skipped beats.
            self.beatpos += (round(self.beatpos)-self.beatpos)*.35
            self.last_grid_anchor = captured
        spectrum = np.fft.rfft(self.audio*self.window[:,None],axis=0)
        magnitude = np.sqrt(np.mean(np.abs(spectrum)**2,axis=1))
        self.history.append(magnitude)
        harmonic = np.median(np.asarray(self.history),axis=0)
        percussion = median_filter(magnitude,size=17,mode='nearest')
        harmonic_mask = harmonic**2/(harmonic**2+percussion**2+1e-12)
        percussion_mask = 1-harmonic_mask
        f = self.frequencies
        bass_band = np.clip((f-25)/20,0,1)*np.clip((260-f)/90,0,1)
        vocal_band = np.clip((f-150)/100,0,1)*np.clip((5000-f)/1200,0,1)
        # Center-panned harmonic midrange is a vocal proxy; centered instruments
        # can leak into it. Stereo side energy remains instrumental.
        center = np.abs(np.mean(spectrum,axis=1))
        side = np.abs((spectrum[:,0]-spectrum[:,1])*.5)
        centered = np.clip((center-side)/(center+side+1e-9),0,1)
        bass_mask = harmonic_mask*bass_band
        vocal_mask = harmonic_mask*(1-bass_band)*vocal_band*centered*.75
        other_mask = np.maximum(0,harmonic_mask-bass_mask-vocal_mask)
        bass = spectrum*bass_mask[:,None]
        vocal = spectrum*vocal_mask[:,None]
        other = spectrum*other_mask[:,None]
        drum = spectrum*percussion_mask[:,None]
        kick = drum*((f>=35)&(f<=180))[:,None]
        hat = drum*((f>=3500)&(f<=12000))[:,None]
        level = float(np.sqrt(np.mean(stereo**2)))
        meters = dict(vocal=self.rms_spectrum(vocal),instru=self.rms_spectrum(other),
                      bass=self.rms_spectrum(bass),kick=self.rms_spectrum(kick),
                      hihat=self.rms_spectrum(hat))
        # Spectral RMS differs from VDJ meters; calibrate to the renderer's ranges.
        for name,gain in dict(vocal=4,instru=2,bass=4,kick=5,hihat=3).items():
            meters[name] = min(1,meters[name]*gain) if level>=.001 else 0.0
        deck = dict(id=1,valid=True,playing=int(level>=.001),volume=1.0,
                    level=level,bpm=bpm,beatpos=self.beatpos,**meters)
        note = None
        if self.filled==self.SIZE and self.counter%2==0:
            chosen = vocal if meters['vocal']>.04 else other
            recovered = np.fft.irfft(chosen,n=self.SIZE,axis=0)
            channel = int(np.argmax(np.mean(recovered**2,axis=0)))
            note = self.notes.analyze(recovered[:,channel].astype(np.float32),captured)
        self.counter += 1
        return dict(version=1,master=level,decks=[deck]),note
