"""Synthesize the promo soundtrack (120 BPM, cuts synced to index.html) -> ad_audio.wav."""
import os
import numpy as np
from scipy.signal import butter, sosfilt
from scipy.io import wavfile
SR=44100; DUR=15.5; N=int(SR*DUR)
L=np.zeros(N); R=np.zeros(N)
rng=np.random.default_rng(3)
def T(n): return np.arange(n)/SR
def add(sig, t0, gain=1.0, pan=0.0):
    i=int(t0*SR); j=min(N,i+len(sig));
    if j<=i: return
    s=sig[:j-i]*gain; L[i:j]+=s*np.sqrt((1-pan)/2)*1.414; R[i:j]+=s*np.sqrt((1+pan)/2)*1.414
def filt(x, kind, f, order=2):
    sos=butter(order, f, btype=kind, fs=SR, output='sos'); return sosfilt(sos,x)
def kick(level=1.0):
    n=int(.42*SR); t=T(n); f=45+110*np.exp(-t*28); ph=2*np.pi*np.cumsum(f)/SR
    body=np.sin(ph)*np.exp(-t*7.5); click=filt(rng.standard_normal(n),'highpass',2500)*np.exp(-t*250)*.25
    return np.tanh((body+click)*1.6)*level
def snare():
    n=int(.3*SR); t=T(n); nz=filt(rng.standard_normal(n),'bandpass',[1200,7000])*np.exp(-t*16)
    tone=np.sin(2*np.pi*190*t)*np.exp(-t*30)*.5; return (nz*.8+tone)*.55
def hat(open_=False):
    n=int((.18 if open_ else .05)*SR); t=T(n); return filt(rng.standard_normal(n),'highpass',8000)*np.exp(-t*(22 if open_ else 90))*.28
def saw(f,t): return 2*((f*t)%1)-1
def pad(freqs, dur, cutoff=1400, level=.08):
    n=int(dur*SR); t=T(n); x=np.zeros(n)
    for f in freqs:
        for d in (-.12,0,.12): x+=saw(f*2**(d/12),t)
    x=filt(x,'lowpass',cutoff); env=np.minimum(1,t/.4)*np.minimum(1,(dur-t)/.5); return x*env*level/len(freqs)
def whoosh(dur=.55, up=True):
    n=int(dur*SR); t=T(n); nz=rng.standard_normal(n); out=np.zeros(n)
    seg=2048
    for k in range(0,n,seg):
        p=k/n; fc=(400+6000*p**2) if up else (6000-5500*p); out[k:k+seg]=filt(nz[k:k+seg+0],'bandpass',[fc*.6,min(fc*1.6,20000)])[:len(out[k:k+seg])]
    env=np.sin(np.pi*np.clip(t/dur,0,1))**2 if not up else (t/dur)**2.2
    return out*env*.35
def impact():
    n=int(1.6*SR); t=T(n); f=30+90*np.exp(-t*9); boom=np.sin(2*np.pi*np.cumsum(f)/SR)*np.exp(-t*2.2)
    crash=filt(rng.standard_normal(n),'highpass',3000)*np.exp(-t*2.8)*.35
    return np.tanh(boom*1.4)*.9+crash
# ---- intro 0-2: pad + riser
add(pad([110,164.8,261.6],2.2,900,.10),0.0)
n=int(1.9*SR); t=T(n); sweep=np.sin(2*np.pi*np.cumsum(180+700*(t/1.9)**2)/SR)*(t/1.9)**2*.10
add(sweep,0.1); add(whoosh(1.9,True)*.9,0.1,pan=-.2); add(whoosh(1.9,True)*.9,0.1,pan=.2)
# soft intro ticks on beats
for b in np.arange(0.5,2.0,.5): add(hat()*1.5,b)
# ---- groove 2.0-13.0
prog=[(55,[220,261.6,329.6]),(43.65,[174.6,220,261.6]),(65.41,[196,261.6,329.6]),(49,[196,246.9,293.7])]
kicks=[]
for bar in range(6):  # 2s bars from 2.0 to 14.0 (stop at 13.0)
    t0=2.0+bar*2
    if t0>=12.5: break
    root,ch=prog[bar%4]
    add(pad(ch,2.0,1800+bar*300,.075),t0,pan=0)
    # bass: 8th-note pulses with sidechain
    for k in range(8):
        tt=t0+k*.25
        if tt>=12.5: break
        n=int(.24*SR); tb=T(n); f=root*(2 if k%4==3 else 1)
        b=np.tanh(2.2*(np.sin(2*np.pi*f*tb)+.3*np.sin(4*np.pi*f*tb)))*np.minimum(1,tb/.03)*np.exp(-tb*4)*.22
        add(filt(b,'lowpass',500),tt)
    for bt in range(4):
        tt=t0+bt*.5
        if tt>=12.5: break
        add(kick(),tt); kicks.append(tt)
        if bt%2==1: add(snare(),tt,pan=.05)
        add(hat(),tt+.25,pan=.3); add(hat()*.6,tt+.125,pan=-.3); add(hat()*.6,tt+.375,pan=-.3)
# ---- half-time breakdown 12.5-14.0: sustained chord, slow kick/snare, gentle riser
add(pad([174.6,220,261.6,329.6],1.6,1500,.11),12.5)
n=int(1.5*SR); tb=T(n); add(filt(np.tanh(2*np.sin(2*np.pi*43.65*tb))*np.exp(-tb*1.6)*.25,'lowpass',400),12.5)
for tt in (12.5,13.25): add(kick()*.9,tt); kicks.append(tt)
add(snare()*.8,12.875,pan=.05); add(snare()*.8,13.625,pan=.05)
for tt in np.arange(12.5,13.5,.25): add(hat(open_=True)*.7,tt+.125,pan=.3)
tt=13.62; step=.09
while tt<13.96:
    add(snare()*(0.25+1.2*(tt-13.62)),tt,pan=rng.uniform(-.3,.3)); tt+=step; step=max(.045,step*.88)
n=int(1.5*SR); t=T(n); add(np.sin(2*np.pi*np.cumsum(180+900*(t/1.5)**2)/SR)*(t/1.5)**2*.10,12.5)
add(whoosh(1.3,True)*.6,12.7,pan=-.3); add(whoosh(1.3,True)*.6,12.7,pan=.3)
# ---- whooshes on cuts
for c in [4.5,6.5,8.5,10.5,12.5]:
    add(whoosh(.5,True),c-.45,pan=-.4); add(whoosh(.5,True),c-.45,pan=.4)
# ---- stat counter blips
for i,a in enumerate([10.75,10.89,11.03]):
    for k in range(10):
        n=int(.03*SR); tb=T(n); add(np.sin(2*np.pi*(1800+k*120)*tb)*np.exp(-tb*120)*.08, a+k*.05*(1+k*.08), pan=-.5+i*.5)
# ---- word-change accents in formats scene
for k,a in enumerate([4.5,5.0,5.5,6.0]):
    n=int(.25*SR); tb=T(n); add(np.sin(2*np.pi*(880*2**(k*2/12))*tb)*np.exp(-tb*14)*.07,a)
# ---- final impact + outro
add(impact(),14.0); add(pad([110,164.8,220,261.6,329.6],1.5,2400,.13),14.0)
n=int(1.4*SR); tb=T(n); bell=sum(np.sin(2*np.pi*f*tb)*a for f,a in [(880,.5),(1318.5,.3),(1760,.15)])*np.exp(-tb*2.5)*.12
add(bell,14.12,pan=-.2); add(bell*.6,14.4,pan=.25)
# ---- sidechain pump on pads/bass approximated by global duck after kicks (light)
duck=np.ones(N)
for k in kicks:
    i=int(k*SR); n=int(.22*SR); j=min(N,i+n); duck[i:j]=np.minimum(duck[i:j], 0.75+0.25*(np.arange(j-i)/n))
mix=np.stack([L*duck,R*duck],1)
# master: fade, soft clip, normalize
tt=np.arange(N)/SR; fade=np.clip((DUR-tt)/.45,0,1)[:,None]
mix=np.tanh(mix*1.2)*fade; mix=mix/np.max(np.abs(mix))*.89
wavfile.write(os.path.join(os.path.dirname(os.path.abspath(__file__)),'ad_audio.wav'),SR,(mix*32767).astype(np.int16)); print('ok', mix.shape)
