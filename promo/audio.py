"""Synthesize the promo soundtrack -> ad_audio.wav.

120 BPM in score time (x STRETCH in output) in A minor; every hit sits on the 16th-note grid and scene cuts in
index.html land on beats. Structure:
  0.0-2.0   intro: pad + riser
  2.0-12.5  full groove, progression Am F C G | Am G
  12.5-14.0 half-time breakdown (same pad/hats, sparser drums), fill into
  14.0-15.5 resolve on Am: impact, light groove continues, fade out
"""
import os
import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi
from scipy.io import wavfile

SR = 44100
STRETCH = 1.1          # whole piece 10% slower (120 -> ~109 BPM); keep in sync with render.py
SCORE_DUR = 15.5       # length in score time; every time below is score time
DUR = SCORE_DUR * STRETCH
N = int(SR * DUR)
BEAT = 0.5
L = np.zeros(N)
R = np.zeros(N)
rng = np.random.default_rng(3)


def T(n):
    return np.arange(n) / SR


def add(sig, t0, gain=1.0, pan=0.0):
    i = int(round(t0 * STRETCH * SR))
    j = min(N, i + len(sig))
    if j <= i:
        return
    s = sig[:j - i] * gain
    L[i:j] += s * np.sqrt((1 - pan) / 2) * 1.414
    R[i:j] += s * np.sqrt((1 + pan) / 2) * 1.414


def filt(x, kind, f, order=2):
    return sosfilt(butter(order, f, btype=kind, fs=SR, output='sos'), x)


def kick(level=1.0):
    n = int(.42 * SR); t = T(n)
    f = 45 + 110 * np.exp(-t * 28)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 7.5)
    click = filt(rng.standard_normal(n), 'highpass', 2500) * np.exp(-t * 250) * .25
    return np.tanh((body + click) * 1.6) * level


def snare():
    n = int(.3 * SR); t = T(n)
    nz = filt(rng.standard_normal(n), 'bandpass', [1200, 7000]) * np.exp(-t * 16)
    return (nz * .8 + np.sin(2 * np.pi * 190 * t) * np.exp(-t * 30) * .5) * .55


def hat(open_=False):
    n = int((.18 if open_ else .05) * SR); t = T(n)
    return filt(rng.standard_normal(n), 'highpass', 8000) * np.exp(-t * (22 if open_ else 90)) * .28


def whoosh(dur):
    n = int(dur * STRETCH * SR); t = T(n)
    x = filt(rng.standard_normal(n), 'bandpass', [500, 9000])
    return filt(x, 'highpass', 300) * (t / dur) ** 2.2 * .3


# chord timeline (start, root Hz, pad voicing); one continuous pad crossfades between them
CHORDS = [
    (0.0, 55.00, [220.0, 261.6, 329.6]),    # Am (intro)
    (2.0, 55.00, [220.0, 261.6, 329.6]),    # Am
    (4.0, 43.65, [220.0, 261.6, 349.2]),    # F
    (6.0, 65.41, [196.0, 261.6, 329.6]),    # C
    (8.0, 49.00, [196.0, 246.9, 293.7]),    # G
    (10.0, 55.00, [220.0, 261.6, 329.6]),   # Am
    (12.0, 43.65, [220.0, 261.6, 349.2]),   # F  (into breakdown)
    (13.0, 49.00, [196.0, 246.9, 293.7]),   # G  (tension before resolve)
    (14.0, 55.00, [220.0, 261.6, 329.6, 440.0]),  # Am resolve
]


def chord_at(t):
    c = CHORDS[0]
    for ch in CHORDS:
        if t >= ch[0]:
            c = ch
    return c


# ---- pad: detuned saws per chord segment, 60 ms crossfades, smooth filter automation
t_all = T(N)
pad = np.zeros(N)
bounds = [c[0] for c in CHORDS] + [SCORE_DUR]
for k, (t0, _root, notes) in enumerate(CHORDS):
    a, b = int(t0 * STRETCH * SR), int(bounds[k + 1] * STRETCH * SR)
    xf = int(.06 * SR)
    lo, hi = max(0, a - xf), min(N, b + xf)
    tt = t_all[lo:hi]
    seg = np.zeros(hi - lo)
    for f in notes:
        for d in (-.1, 0, .1):
            seg += 2 * ((f * 2 ** (d / 12) * tt) % 1) - 1
    seg /= len(notes) * 3
    env = np.ones(hi - lo)
    ramp = np.linspace(0, 1, xf)
    if lo < a:
        env[:a - lo] = ramp[-(a - lo):]
    if hi > b:
        env[-(hi - b):] = ramp[::-1][:hi - b]
    pad[lo:hi] += seg * env
# cutoff automation: opens through the groove, dips in breakdown, opens on resolve
ts = t_all / STRETCH  # score time per sample
cut = np.interp(ts, [0, 2, 12.5, 13.2, 14.0, 15.5], [700, 1100, 2600, 2100, 3200, 1800])
out = np.zeros(N)
blk = 1024
state = None
for i in range(0, N, blk):
    sos = butter(2, cut[i], btype='lowpass', fs=SR, output='sos')
    if state is None:
        state = sosfilt_zi(sos) * 0
    out[i:i + blk], state = sosfilt(sos, pad[i:i + blk], zi=state)
pad_env = np.interp(ts, [0, .5, 12.5, 12.8, 13.9, 14.0, 15.5], [0, 1, 1, 1.35, 1.35, 1.25, 1.0])
pad = out * pad_env * .10
L += pad; R += pad

# ---- intro riser
n = int(1.9 * STRETCH * SR); t = T(n); d = 1.9 * STRETCH
add(np.sin(2 * np.pi * np.cumsum(180 + 700 * (t / d) ** 2) / SR) * (t / d) ** 2 * .08, 0.1)
add(whoosh(1.9) * .8, 0.1, pan=-.2); add(whoosh(1.9) * .8, 0.1, pan=.2)
for b in np.arange(0.5, 2.0, BEAT):
    add(hat() * 1.3, b)

kicks = []


def bass_note(f, dur, lvl=.22):
    n = int(dur * STRETCH * SR); tb = T(n)
    b = np.tanh(2.2 * (np.sin(2 * np.pi * f * tb) + .3 * np.sin(4 * np.pi * f * tb)))
    return filt(b * np.minimum(1, tb / .02) * np.exp(-tb * 4) * lvl, 'lowpass', 500)


# ---- full groove 2.0-12.5
for s16 in range(int((12.5 - 2.0) / .125)):
    tt = 2.0 + s16 * .125
    root = chord_at(tt)[1]
    if s16 % 4 == 0:
        add(kick(), tt); kicks.append(tt)
        if (s16 // 4) % 2 == 1:
            add(snare(), tt, pan=.05)
    if s16 % 2 == 0:
        add(bass_note(root * (2 if s16 % 8 == 6 else 1), .24), tt)
    add(hat() * (1 if s16 % 2 else .55), tt, pan=.3 if s16 % 2 else -.3)

# ---- half-time breakdown 12.5-14.0 (same grid and sounds, half density)
for s16 in range(int((14.0 - 12.5) / .125)):
    tt = 12.5 + s16 * .125
    root = chord_at(tt)[1]
    if s16 in (0, 6, 8):
        add(kick() * (.7 if s16 == 6 else .9), tt); kicks.append(tt)
    if s16 == 4:
        add(snare() * .85, tt, pan=.05)
    if s16 % 4 == 0:
        add(bass_note(root, .48, .20), tt)
    add(hat() * (.8 if s16 % 2 else .45), tt, pan=.3 if s16 % 2 else -.3)
# 16th-note snare fill on the last beat, rising
for k, tt in enumerate(np.arange(13.5, 14.0, .125)):
    add(snare() * (.35 + .18 * k), tt, pan=(-.2, .2)[k % 2])
add(whoosh(1.0) * .7, 13.0, pan=-.3); add(whoosh(1.0) * .7, 13.0, pan=.3)

# ---- whooshes into each scene cut
for c in [4.5, 6.5, 8.5, 10.5, 12.5]:
    add(whoosh(.5), c - .5, pan=-.4); add(whoosh(.5), c - .5, pan=.4)

# ---- stat counter blips on 32nds, in key (A minor pentatonic)
pent = [880, 1046.5, 1174.7, 1318.5, 1568]
for i, a in enumerate([10.75, 11.0, 11.25]):
    for k in range(8):
        n = int(.03 * SR); tb = T(n)
        add(np.sin(2 * np.pi * pent[(k + i) % 5] * tb) * np.exp(-tb * 120) * .07, a + k * .0625, pan=-.5 + i * .5)

# ---- word-change accents in the formats scene (A C E A)
for f, a in zip([880, 1046.5, 1318.5, 1760], [4.5, 5.0, 5.5, 6.0]):
    n = int(.3 * SR); tb = T(n)
    add(np.sin(2 * np.pi * f * tb) * np.exp(-tb * 12) * .06, a)

# ---- resolve 14.0-15.5: impact, then the groove continues lightly on Am
n = int(1.5 * SR); t = T(n)
boom = np.sin(2 * np.pi * np.cumsum(30 + 90 * np.exp(-t * 9)) / SR) * np.exp(-t * 2.4)
crash = filt(rng.standard_normal(n), 'highpass', 3000) * np.exp(-t * 3) * .3
add(np.tanh(boom * 1.4) * .85 + crash, 14.0); kicks.append(14.0)
add(bass_note(55, 1.4, .24), 14.0)
for tt in (14.5, 15.0):
    add(kick() * .55, tt); kicks.append(tt)
for s16 in range(int(1.5 / .125)):
    if s16 % 2 == 1:
        add(hat() * .6, 14.0 + s16 * .125, pan=.3)
# Am arpeggio bell on the beat grid
for f, tt in [(880, 14.0), (1046.5, 14.25), (1318.5, 14.5), (1760, 14.75)]:
    n = int(1.0 * SR); tb = T(n)
    bell = (np.sin(2 * np.pi * f * tb) * .6 + np.sin(2 * np.pi * 2 * f * tb) * .15) * np.exp(-tb * 3.2) * .09
    add(bell, tt, pan=.2 if f > 1000 else -.2)

# ---- sidechain duck on every kick, master, fade
duck = np.ones(N)
for k in kicks:
    i = int(k * STRETCH * SR); n = int(.22 * SR); j = min(N, i + n)
    duck[i:j] = np.minimum(duck[i:j], .72 + .28 * (np.arange(j - i) / n))
mix = np.stack([L * duck, R * duck], 1)
fade = np.clip((DUR - t_all) / .7, 0, 1)[:, None]
mix = np.tanh(mix * 1.2) * fade
mix = mix / np.max(np.abs(mix)) * .89
wavfile.write(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ad_audio.wav'), SR, (mix * 32767).astype(np.int16))
print('ok', mix.shape)
