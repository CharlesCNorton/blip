#!/usr/bin/env python3
# blip_ollama_live.py — Stream from Ollama and play RPG-style text blips with model picker
# Lists installed models via /api/tags and lets you select one. No external deps.

import io, json, math, os, platform, queue, random, shutil, struct, subprocess, sys, tempfile, threading, time, wave, http.client
from urllib.parse import urlparse

WIN = platform.system()=="Windows"
MAC = platform.system()=="Darwin"
PUNCT = set(".!?;:")

# ---- Tunables ----
DEFAULT_SR      = 44100
DEFAULT_AMP     = 0.24
DEFAULT_CPS     = 26.0
DEFAULT_PUNCT   = 2.2
COALESCE_MS     = 180   # group tokens (~180ms) per audio render
FLUSH_MS        = 220   # silence flush for device stability
POST_GAP        = 0.10  # gap between chunks
FILTER_THINK    = True  # strip <think>...</think> from AUDIO (still prints)

# ---------- tiny DSP ----------
def H(n,t): return 1.0 if t<=1 else 0.5*(1.0-math.cos(2.0*math.pi*(n/(t-1))))
def clamp(x): return -1.0 if x<-1.0 else 1.0 if x>1.0 else x
def PCM16(a): return b"".join(struct.pack("<h", int(clamp(s)*32767)) for s in a)
def LIM(a,p=0.98,dc=True):
    if not a: return a
    if dc: m=sum(a)/len(a); a=[s-m for s in a]
    pk=max(1e-12, max(abs(s) for s in a))
    if pk>p:
        sc=p/pk; a=[s*sc for s in a]
    return a
def FX(f,ms,wf,du,vrt,vrd,amp,sr):
    n=int(sr*ms/1000); o=[0.0]*n; ph=0.0
    for i in range(n):
        t=i/sr; ff=f*(1.0+vrd*math.sin(2.0*math.pi*vrt*t)) if vrt>0 else f
        ph+=2.0*math.pi*ff/sr; p=(ph/(2.0*math.pi))%1.0
        if   wf=="sine": v=math.sin(ph)
        elif wf=="tri":  v=2.0*abs(2.0*p-1.0)-1.0
        elif wf=="saw":  v=2.0*p-1.0
        else:            v=1.0 if p<du else -1.0
        o[i]=v*H(i,n)*amp
    return o
def SW(f0,f1,ms,wf,amp,sr):
    n=int(sr*ms/1000); o=[0.0]*n; ph=0.0
    for i in range(n):
        u=i/max(1,n-1); f=f0+(f1-f0)*u; ph+=2.0*math.pi*f/sr; p=(ph/(2.0*math.pi))%1.0
        v=math.sin(ph) if wf=="sine" else (2.0*abs(2.0*p-1.0)-1.0 if wf=="tri" else 2.0*p-1.0)
        o[i]=v*H(i,n)*amp
    return o
def NZ(ms,a,amp,sr):
    n=int(sr*ms/1000); o=[0.0]*n; y=0.0
    for i in range(n):
        x=random.random()*2-1; y=a*x+(1.0-a)*y; o[i]=y*H(i,n)*amp
    return o
def FM(fc,rat,idx,ms,amp,sr):
    n=int(sr*ms/1000); o=[0.0]*n
    for i in range(n):
        t=i/sr; m=math.sin(2.0*math.pi*(fc*rat)*t); v=math.sin(2.0*math.pi*fc*t+idx*m)
        o[i]=v*H(i,n)*amp
    return o

# ---------- 20 variations ----------
def build_vars(amp, sr):
    V=[]
    def mk(s,n,m): V.append((n, LIM(s), m))
    mk(FX(820,55,"pulse",0.25,0,0,amp,sr),                   "pulse25_mid",55)
    mk(FX(920,55,"pulse",0.125,0,0,amp,sr),                  "pulse12_bright",55)
    mk(FX(600,55,"tri",0.25,0,0,amp,sr),                     "triangle_soft",55)
    mk(FX(700,55,"saw",0.25,0,0,amp,sr),                     "saw_buzzy",55)
    mk(SW(650,900,70,"sine",amp,sr),                         "sine_up_sweep",70)
    mk(SW(950,650,70,"sine",amp,sr),                         "sine_down_sweep",70)
    mk(FX(850,60,"pulse",0.25,8.0,0.06,amp,sr),              "pulse_vibrato",60)
    mk(NZ(40,0.22,amp*0.9,sr),                               "noise_click",40)
    mk(FX(700,26,"sine",0.25,0,0,amp,sr)+FX(950,26,"sine",0,0,0,amp,sr),"two_tone",52)
    mk(FX(500,18,"pulse",0.25,0,0,amp,sr)+FX(650,18,"pulse",0,0,0,amp,sr)+FX(820,18,"pulse",0,0,0,amp,sr),"arp_chiptune",54)
    mk(FX(840,45,"pulse",0.25,0,0,amp*0.95,sr),              "ct_snes_pulse",45)
    mk(FX(680,50,"tri",0,0,0,amp,sr),                        "eb_snes_blip",50)
    mk(FX(610,55,"tri",0,0,0,amp,sr),                        "ff6_snes_tri",55)
    mk(FX(980,60,"pulse",0.125,0,0,amp,sr),                  "pkmn_gb_square12",60)
    mk(FX(440,45,"pulse",0.50,0,0,amp,sr),                   "dq_nes_square50",45)
    mk(NZ(24,0.10,amp*0.80,sr)+FX(800,18,"sine",0,0,0,amp*0.75,sr),"fe_gba_click",42)
    mk(FX(720,55,"saw",0,0,0,amp,sr),                        "gs_gba_saw",55)
    mk(SW(500,860,48,"sine",amp,sr),                         "pm_n64_chirp",48)
    mk(FM(600,2.0,1.2,55,amp,sr),                            "ps4_gen_fm",55)
    mk(FX(760,36,"pulse",0.25,14.0,0.05,amp,sr),             "ut_default_blip",36)
    return V

# ---------- schedule & render ----------
def schedule(text, cps, punct_mult, ws, glen, sr):
    starts=[]; t=0.0; step=1.0/max(1.0,cps)
    for ch in text:
        if ch.strip() or ws: starts.append(int(t*sr))
        t += step * (punct_mult if ch in PUNCT else 1.0)
    total_sec = t + (glen/sr) + 0.12
    return starts, int(total_sec*sr)+1

def render_line(text, grain, sr, cps, punct_mult, ws):
    glen=len(grain); starts, TL = schedule(text, cps, punct_mult, ws, glen, sr)
    buf=[0.0]*TL
    for s0 in starts:
        j=s0; i=0; e=min(TL,s0+glen)
        while j<e: buf[j]+=grain[i]; j+=1; i+=1
    return LIM(buf, 0.95, True)

# ---------- player ----------
class Player:
    def __init__(self):
        if WIN: self.mode="winsound"
        elif MAC and shutil.which("afplay"): self.mode="afplay"
        elif shutil.which("paplay"): self.mode="paplay"
        elif shutil.which("aplay"):  self.mode="aplay"
        else: self.mode="bell"
        self.p=None; self.path=None
    def play_async(self, samples, sr, tmpdir):
        dur=len(samples)/float(sr)
        if self.mode=="winsound":
            import winsound
            p=os.path.join(tmpdir, f"blip_{int(time.time()*1000)}.wav")
            with wave.open(p,"wb") as w: w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(PCM16(samples))
            self.path=p; winsound.PlaySound(p, winsound.SND_FILENAME|winsound.SND_ASYNC)
        elif self.mode in ("afplay","paplay","aplay"):
            p=os.path.join(tmpdir, f"blip_{int(time.time()*1000)}.wav")
            with wave.open(p,"wb") as w: w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(PCM16(samples))
            cmd=[self.mode, p] if self.mode!="aplay" else ["aplay","-q",p]
            try: self.p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); self.path=p
            except: self.p=None
        else:
            sys.stdout.write("\a"); sys.stdout.flush()
        return dur
    def wait_done(self, timeout):
        if self.mode=="winsound":
            time.sleep(max(0.0, timeout))
            if self.path and os.path.exists(self.path):
                try: os.remove(self.path)
                except: pass
            self.path=None
            return
        if self.p:
            try: self.p.wait(timeout=timeout)
            except subprocess.TimeoutExpired: pass
            if self.path and os.path.exists(self.path):
                try: os.remove(self.path)
                except: pass
            self.path=None
    def flush_silence(self, sr, ms):
        n=int(sr*ms/1000); s=[0.0]*max(1,n)
        self.play_async(s, sr, tempfile.gettempdir()); self.wait_done(ms/1000.0+0.05)

# ---------- Ollama endpoint discovery & model listing ----------
def discover_endpoint():
    """Return (scheme, host, port) honoring OLLAMA_HOST; default http://127.0.0.1:11434"""
    env=os.getenv("OLLAMA_HOST","").strip()
    scheme="http"; host="127.0.0.1"; port=11434
    if env:
        if "://" not in env: env="http://"+env
        u=urlparse(env)
        if u.scheme: scheme=u.scheme
        if u.hostname: host=u.hostname
        if u.port: port=u.port or (443 if scheme=="https" else 11434)
    return scheme, host, port

def _conn(scheme, host, port, timeout=3600):
    return (http.client.HTTPSConnection if scheme=="https" else http.client.HTTPConnection)(host, port, timeout=timeout)

def list_local_models(scheme, host, port):
    """Return a sorted list of model names via GET /api/tags; empty list if none/unavailable."""
    try:
        c=_conn(scheme,host,port,timeout=8)
        c.request("GET","/api/tags")
        r=c.getresponse()
        data=r.read()
        c.close()
        if r.status!=200: return []
        obj=json.loads(data.decode("utf-8"))
        names=[m.get("name","") for m in obj.get("models",[])]
        names=[n for n in names if n]
        names.sort(key=lambda s:s.lower())
        return names
    except Exception:
        # Fallback: try CLI parsing if available
        try:
            out=subprocess.check_output(["ollama","list"], text=True, stderr=subprocess.DEVNULL)
            names=[]
            for line in out.splitlines():
                s=line.strip()
                if not s or s.lower().startswith("name"): continue
                tok=s.split()
                if tok: names.append(tok[0])
            names.sort(key=lambda s:s.lower())
            return names
        except Exception:
            return []

def pick_installed_model(scheme, host, port):
    names=list_local_models(scheme, host, port)
    if not names:
        print("\nNo local models found (or server unreachable). Is Ollama running and do you have models pulled?\n"
              "Try:  ollama serve   and   ollama pull llama3.2")
        return input("Type a model name manually (e.g., llama3.2): ").strip() or "llama3.2"
    print("\nChoose a model installed on this Ollama:")
    for i,n in enumerate(names,1): print(f" {i:2d}) {n}")
    sel=input(f"Select [1-{len(names)}]: ").strip() or "1"
    try: idx=max(1, min(len(names), int(sel)))
    except: idx=1
    return names[idx-1]

# ---------- streaming (NDJSON) ----------
def stream_generate(model, prompt, scheme, host, port, options=None):
    """
    Yields incremental text chunks from /api/generate (NDJSON lines).
    """
    payload={"model": model, "prompt": prompt, "stream": True}
    if options: payload["options"]=options
    body=json.dumps(payload).encode("utf-8")
    c=_conn(scheme, host, port, timeout=3600)
    c.request("POST","/api/generate", body=body, headers={"Content-Type":"application/json"})
    r=c.getresponse()
    for raw in r:
        if not raw.strip(): continue
        try: obj=json.loads(raw.decode("utf-8"))
        except Exception: continue
        if obj.get("done"): break
        chunk = obj.get("response","")
        if chunk: yield chunk
    try: c.close()
    except: pass

# ---------- audio worker ----------
class AudioWorker(threading.Thread):
    def __init__(self, grain, sr, cps, punct_mult, ws):
        super().__init__(daemon=True)
        self.grain=grain; self.sr=sr; self.cps=cps; self.punct=punct_mult; self.ws=ws
        self.q=queue.Queue(); self.stop=False
        self.player=Player(); self.tmp=tempfile.gettempdir()
    def enqueue(self, text):
        self.q.put(text)
    def run(self):
        while not self.stop:
            item=self.q.get()
            if item is None: break
            samples = render_line(item, self.grain, self.sr, self.cps, self.punct, self.ws)
            dur = self.player.play_async(samples, self.sr, self.tmp)
            self.player.wait_done(dur+0.03)
            self.player.flush_silence(self.sr, FLUSH_MS)
            if POST_GAP>0: time.sleep(POST_GAP)
    def close(self):
        self.stop=True; self.q.put(None)

# ---------- small UI ----------
def pick_variation(vars):
    print("\nChoose a blip timbre:")
    for i,(n,_,ms) in enumerate(vars,1):
        print(f" {i:2d}) {n:16s} ({int(ms)} ms)")
    s=input("Number [10=ut_default_blip]: ").strip() or "10"
    try:
        i=int(s); i=max(1,min(len(vars),i))
    except: i=10
    return vars[i-1]

def cls(): os.system("cls" if os.name=="nt" else "clear")

def main():
    # Greet & prompt text
    cls()
    print("BLIP + OLLAMA LIVE  (pick installed model -> stream with sound)\n")

    # Discover Ollama endpoint (honors OLLAMA_HOST)
    scheme, host, port = discover_endpoint()
    print(f"Ollama endpoint: {scheme}://{host}:{port}")

    # Model picker (from /api/tags); fallback to manual if empty
    model = pick_installed_model(scheme, host, port)

    prompt_text = input("\nPrompt: ").strip() or "Describe an ancient forest in one paragraph."

    # Pick blip style
    vars = build_vars(DEFAULT_AMP, DEFAULT_SR)
    name, grain, _ = pick_variation(vars)
    print(f"\nUsing model: {model}\nUsing variation: {name}\n")

    # Set up audio worker
    aw = AudioWorker(grain, DEFAULT_SR, DEFAULT_CPS, DEFAULT_PUNCT, ws=False)
    aw.start()

    print("--- Response ---\n")
    sys.stdout.flush()

    # token coalescer
    last_t = time.monotonic()
    bucket = []
    def flush_bucket():
        nonlocal bucket, last_t
        if not bucket: return
        text="".join(bucket)
        audio_text = text
        if FILTER_THINK:
            audio_text = audio_text.replace("<think>","").replace("</think>","")
        aw.enqueue(audio_text)
        bucket.clear()
        last_t = time.monotonic()

    try:
        for chunk in stream_generate(model, prompt_text, scheme, host, port):
            sys.stdout.write(chunk); sys.stdout.flush()
            bucket.append(chunk)
            now=time.monotonic()
            if (now-last_t)*1000 >= COALESCE_MS or (bucket and any(c in PUNCT for c in bucket[-1])):
                flush_bucket()
        flush_bucket()
    except KeyboardInterrupt:
        pass
    finally:
        aw.close()
        aw.join(timeout=1.5)

    print("\n\n[done]")

if __name__=="__main__":
    main()
