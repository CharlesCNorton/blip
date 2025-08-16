#!/usr/bin/env python3
# BLIP — ultra‑minified, menu + per‑variation exporter (PY/WAV)

import io,math,os,platform,random,shutil,struct,subprocess,sys,tempfile,time,wave
WIN=platform.system()=="Windows"; MAC=platform.system()=="Darwin"; PUNCT=set(".!?;:")

# ===== tiny DSP =====
def H(n,t): return 1.0 if t<=1 else 0.5*(1.0-math.cos(2*math.pi*(n/(t-1))))
def C(x): return -1.0 if x<-1.0 else 1.0 if x>1.0 else x
def PCM16(a): return b"".join(struct.pack("<h",int(C(s)*32767)) for s in a)
def WAVBYTES(a,sr):
    b=io.BytesIO()
    with wave.open(b,"wb") as w: w.setnchannels(1);w.setsampwidth(2);w.setframerate(sr);w.writeframes(PCM16(a))
    return b.getvalue()
def LIM(a,p=0.98,dc=True):
    if not a: return a
    if dc: m=sum(a)/len(a); a=[s-m for s in a]
    pk=max(1e-12,max(abs(s) for s in a))
    if pk>p: sc=p/pk; a=[s*sc for s in a]
    return a
def FX(f,ms,wf,du,vrt,vrd,amp,sr):
    n=int(sr*ms/1000); o=[0.0]*n; ph=0.0
    for i in range(n):
        t=i/sr; ff=f*(1.0+vrd*math.sin(2*math.pi*vrt*t)) if vrt>0 else f
        ph+=2*math.pi*ff/sr; p=(ph/(2*math.pi))%1.0
        if wf=="sine": v=math.sin(ph)
        elif wf=="tri":  v=2.0*abs(2.0*p-1.0)-1.0
        elif wf=="saw":  v=2.0*p-1.0
        else:            v=1.0 if p<du else -1.0
        o[i]=v*H(i,n)*amp
    return o
def SW(f0,f1,ms,wf,amp,sr):
    n=int(sr*ms/1000); o=[0.0]*n; ph=0.0
    for i in range(n):
        u=i/max(1,n-1); f=f0+(f1-f0)*u; ph+=2*math.pi*f/sr; p=(ph/(2*math.pi))%1.0
        if wf=="sine": v=math.sin(ph)
        elif wf=="tri": v=2.0*abs(2.0*p-1.0)-1.0
        elif wf=="saw": v=2.0*p-1.0
        else:           v=math.sin(ph)
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
        t=i/sr; m=math.sin(2*math.pi*(fc*rat)*t); v=math.sin(2*math.pi*fc*t+idx*m); o[i]=v*H(i,n)*amp
    return o

# ===== variations (20) =====
# Each entry: (name, grain_fn) where grain_fn returns the sample list (uses A,SR)
A_DEFAULT=0.24
def V_RECIPES():
    A,SR="A","SR"
    R={
"pulse25_mid":      f"FX(820,55,'pulse',0.25,0,0,{A},{SR})",
"pulse12_bright":   f"FX(920,55,'pulse',0.125,0,0,{A},{SR})",
"triangle_soft":    f"FX(600,55,'tri',0.25,0,0,{A},{SR})",
"saw_buzzy":        f"FX(700,55,'saw',0.25,0,0,{A},{SR})",
"sine_up_sweep":    f"SW(650,900,70,'sine',{A},{SR})",
"sine_down_sweep":  f"SW(950,650,70,'sine',{A},{SR})",
"pulse_vibrato":    f"FX(850,60,'pulse',0.25,8.0,0.06,{A},{SR})",
"noise_click":      f"NZ(40,0.22,{A}*0.9,{SR})",
"two_tone":         f"FX(700,26,'sine',0.25,0,0,{A},{SR})+FX(950,26,'sine',0.25,0,0,{A},{SR})",
"arp_chiptune":     f"FX(500,18,'pulse',0.25,0,0,{A},{SR})+FX(650,18,'pulse',0.25,0,0,{A},{SR})+FX(820,18,'pulse',0.25,0,0,{A},{SR})",
"ct_snes_pulse":    f"FX(840,45,'pulse',0.25,0,0,{A}*0.95,{SR})",
"eb_snes_blip":     f"FX(680,50,'tri',0,0,0,{A},{SR})",
"ff6_snes_tri":     f"FX(610,55,'tri',0,0,0,{A},{SR})",
"pkmn_gb_square12": f"FX(980,60,'pulse',0.125,0,0,{A},{SR})",
"dq_nes_square50":  f"FX(440,45,'pulse',0.50,0,0,{A},{SR})",
"fe_gba_click":     f"NZ(24,0.10,{A}*0.80,{SR})+FX(800,18,'sine',0,0,0,{A}*0.75,{SR})",
"gs_gba_saw":       f"FX(720,55,'saw',0,0,0,{A},{SR})",
"pm_n64_chirp":     f"SW(500,860,48,'sine',{A},{SR})",
"ps4_gen_fm":       f"FM(600,2.0,1.2,55,{A},{SR})",
"ut_default_blip":  f"FX(760,36,'pulse',0.25,14.0,0.05,{A},{SR})",
    }
    return R

def build_vars(amp,sr):
    R=V_RECIPES(); V=[]
    for n,expr in R.items():
        g=eval(expr,{"FX":FX,"SW":SW,"NZ":NZ,"FM":FM,"A":amp,"SR":sr})
        V.append((n,LIM(g),len(g)*1000/sr))
    return V

# ===== schedule + render (one mix per line) =====
def sched(text,cps,pm,ws,gl,sr):
    st=[]; t=0.0; step=1.0/max(1.0,cps)
    for ch in text:
        if ch.strip() or ws: st.append(int(t*sr))
        t+=step*(pm if ch in PUNCT else 1.0)
    return st,int((t+gl/sr+0.12)*sr)+1
def render_line(text,grain,sr,cps,pm,ws):
    gl=len(grain); st,TL=sched(text,cps,pm,ws,gl,sr); buf=[0.0]*TL
    for s0 in st:
        j=s0; i=0; e=min(TL,s0+gl)
        while j<e: buf[j]+=grain[i]; j+=1; i+=1
    return LIM(buf,0.95,True)

# ===== player =====
class P:
    def __init__(s):
        s.mode="winsound" if WIN else ("afplay" if MAC and shutil.which("afplay") else ("paplay" if shutil.which("paplay") else ("aplay" if shutil.which("aplay") else "bell")))
        s.mem=False; s.p=None; s.path=None; s.hold=None
    def toggle(s):
        if WIN: s.mem=not s.mem
    def reset(s,kill=False):
        if s.p and s.p.poll() is None:
            try: s.p.terminate(); s.p.wait(timeout=0.2)
            except: 
                try: s.p.kill()
                except: pass
        s.p=None
        if WIN:
            try:
                import winsound; winsound.PlaySound(None, winsound.SND_PURGE)
            except: pass
            if s.path and os.path.exists(s.path):
                try: os.remove(s.path)
                except: pass
            s.path=None; s.hold=None
        elif kill:
            for nm in ("afplay","paplay","aplay"):
                if shutil.which("pkill"):
                    try: subprocess.run(["pkill","-x",nm],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                    except: pass
                if shutil.which("killall"):
                    try: subprocess.run(["killall","-q",nm],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                    except: pass
    def play(s,a,sr,tmp):
        dur=len(a)/float(sr)
        if WIN:
            import winsound
            if s.mem:
                d=WAVBYTES(a,sr); s.hold=d; winsound.PlaySound(d, winsound.SND_MEMORY|winsound.SND_ASYNC)
            else:
                p=os.path.join(tmp,f"blip_{int(time.time()*1000)}.wav")
                with wave.open(p,"wb") as w: w.setnchannels(1);w.setsampwidth(2);w.setframerate(sr);w.writeframes(PCM16(a))
                s.path=p; winsound.PlaySound(p, winsound.SND_FILENAME|winsound.SND_ASYNC)
        elif s.mode in ("afplay","paplay","aplay"):
            p=os.path.join(tmp,f"blip_{int(time.time()*1000)}.wav")
            with wave.open(p,"wb") as w: w.setnchannels(1);w.setsampwidth(2);w.setframerate(sr);w.writeframes(PCM16(a))
            try: s.p=subprocess.Popen([s.mode,p] if s.mode!="aplay" else ["aplay","-q",p],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); s.path=p
            except: s.p=None
        else:
            sys.stdout.write("\a"); sys.stdout.flush()
        return dur
    def wait(s,sec):
        if WIN:
            time.sleep(max(0.0,sec))
            if s.path and os.path.exists(s.path):
                try: os.remove(s.path)
                except: pass
            s.path=None; s.hold=None
        elif s.p:
            try: s.p.wait(timeout=sec)
            except subprocess.TimeoutExpired: pass
            if s.path and os.path.exists(s.path):
                try: os.remove(s.path)
                except: pass
            s.path=None
    def beep(s,sr=44100):
        n=int(sr*0.3); t=[math.sin(2*math.pi*440*i/sr)*0.3 for i in range(n)]
        s.play(LIM(t),sr,tempfile.gettempdir()); s.wait(0.35)
    def label(s): return ("winsound-"+("mem" if s.mem else "file")) if WIN else s.mode

# ===== printing =====
def print_sync(text,cps,pm):
    step=1.0/max(1.0,cps); t0=time.monotonic(); t=0.0
    for ch in text:
        target=t0+t
        while True:
            dt=target-time.monotonic()
            if dt<=0: break
            time.sleep(0.001 if dt<0.01 else 0.01)
        sys.stdout.write(ch); sys.stdout.flush()
        t+=step*(pm if ch in PUNCT else 1.0)
    if not text.endswith("\n"): print()

# ===== export helpers =====
def used_funcs(expr): 
    u=set()
    for k in ("FX","SW","NZ","FM"):
        if k in expr: u.add(k)
    return u

def export_py(name,expr,stem,sr,amp,cps,pm,ws):
    f=used_funcs(expr); inc=[]
    if "FX" in f: inc.append("def FX(f,ms,wf,du,vrt,vrd,A,SR):\n n=int(SR*ms/1000);o=[0.0]*n;ph=0.0\n for i in range(n):\n  t=i/SR;ff=f*(1.0+vrd*math.sin(2*math.pi*vrt*t)) if vrt>0 else f\n  ph+=2*math.pi*ff/SR;p=(ph/(2*math.pi))%1.0\n  v=math.sin(ph) if wf=='sine' else (2.0*abs(2.0*p-1.0)-1.0 if wf=='tri' else (2.0*p-1.0 if wf=='saw' else (1.0 if p<du else -1.0)))\n  o[i]=v*(0.5*(1.0-math.cos(2*math.pi*(i/(n-1)))) if n>1 else 1.0)*A\n return o")
    if "SW" in f: inc.append("def SW(f0,f1,ms,wf,A,SR):\n n=int(SR*ms/1000);o=[0.0]*n;ph=0.0\n for i in range(n):\n  u=i/max(1,n-1);f=f0+(f1-f0)*u;ph+=2*math.pi*f/SR;p=(ph/(2*math.pi))%1.0\n  v=math.sin(ph) if wf=='sine' else (2.0*abs(2.0*p-1.0)-1.0 if wf=='tri' else 2.0*p-1.0)\n  o[i]=v*(0.5*(1.0-math.cos(2*math.pi*(i/(n-1)))) if n>1 else 1.0)*A\n return o")
    if "NZ" in f: inc.append("def NZ(ms,a,A,SR):\n n=int(SR*ms/1000);o=[0.0]*n;y=0.0\n for i in range(n):\n  x=random.random()*2-1;y=a*x+(1.0-a)*y\n  o[i]=y*(0.5*(1.0-math.cos(2*math.pi*(i/(n-1)))) if n>1 else 1.0)*A\n return o")
    if "FM" in f: inc.append("def FM(fc,rat,idx,ms,A,SR):\n n=int(SR*ms/1000);o=[0.0]*n\n for i in range(n):\n  t=i/SR;m=math.sin(2*math.pi*(fc*rat)*t);v=math.sin(2*math.pi*fc*t+idx*m)\n  o[i]=v*(0.5*(1.0-math.cos(2*math.pi*(i/(n-1)))) if n>1 else 1.0)*A\n return o")
    code=f"""# blip_export_{name}.py — standalone
import io,math,os,platform,random,struct,subprocess,tempfile,time,wave,sys
WIN=platform.system()=='Windows'
def PCM16(a):\n return b''.join(struct.pack('<h',int(max(-1,min(1,s))*32767)) for s in a)
def LIM(a,p=0.98):\n pk=max(1e-12,max(abs(s) for s in a));\n return a if pk<=p else [s*(p/pk) for s in a]
{"\n".join(inc)}
def make_grain(A,SR):\n return LIM({expr})
def sched(text,C,PM,WS,GL,SR):\n st=[];t=0.0;step=1.0/max(1.0,C)\n for ch in text:\n  import string\n  if (ch.strip()!='') or WS: st.append(int(t*SR))\n  t+=step*(PM if ch in '.!?;:' else 1.0)\n return st,int((t+GL/SR+0.12)*SR)+1
def render(text,g,SR,C,PM,WS):\n GL=len(g);st,TL=sched(text,C,PM,WS,GL,SR);buf=[0.0]*TL\n for s0 in st:\n  j=s0;i=0;e=min(TL,s0+GL)\n  while j<e: buf[j]+=g[i];j+=1;i+=1\n pk=max(1e-12,max(abs(s) for s in buf));\n return buf if pk<=0.95 else [s*(0.95/pk) for s in buf]
def play(a,SR):\n d=len(a)/SR\n if WIN:\n  import winsound,os,wave\n  p=os.path.join(tempfile.gettempdir(),f'blip_{{int(time.time()*1000)}}.wav')\n  with wave.open(p,'wb') as w: w.setnchannels(1);w.setsampwidth(2);w.setframerate(SR);w.writeframes(PCM16(a))\n  winsound.PlaySound(p, winsound.SND_FILENAME)\n  try: os.remove(p)\n  except: pass\n else:\n  p=os.path.join(tempfile.gettempdir(),f'blip_{{int(time.time()*1000)}}.wav')\n  with wave.open(p,'wb') as w: w.setnchannels(1);w.setsampwidth(2);w.setframerate(SR);w.writeframes(PCM16(a))\n  cmd='afplay' if shutil.which('afplay') else ('paplay' if shutil.which('paplay') else 'aplay')\n  try: subprocess.run([cmd,p])\n  except: pass\n  try: os.remove(p)\n  except: pass\n return d
def print_sync(text,C,PM):\n step=1.0/max(1.0,C);t0=time.monotonic();t=0.0\n for ch in text:\n  tgt=t0+t\n  while True:\n   dt=tgt-time.monotonic()\n   if dt<=0: break\n   time.sleep(0.001 if dt<0.01 else 0.01)\n  sys.stdout.write(ch);sys.stdout.flush();t+=step*(PM if ch in '.!?;:' else 1.0)\n if not text.endswith('\\n'): print()
if __name__=='__main__':\n TXT="Link, can you hear me? The forest is whispering..."\n SR={sr};A={amp};C={cps};PM={pm};WS={str(ws)}\n g=make_grain(A,SR); buf=render(TXT,g,SR,C,PM,WS)\n play(buf,SR); print_sync(TXT,C,PM)
"""
    with open(f"{stem}_mini.py","w",encoding="utf-8") as f: f.write(code)

def export_wav(name,grain,stem,sr):
    with wave.open(f"{stem}.wav","wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(PCM16(grain))

# ===== UI =====
B=(
"\n===============================================================\n"
"  BLIP vX — compact RPG text bleeps + EXPORT (PY/WAV)\n"
"---------------------------------------------------------------\n"
"  1-20) Play one   A) Play ALL   V) Verify beep\n"
"  E) Export (py/wav/both)        T) Edit text\n"
"  C) CPS           P) Punct×     W) Whitespace\n"
"  G) Post‑gap      F) Flush ms   S) Amplitude\n"
"  H) Sample rate   K) Deep clear L) List   Q) Quit\n"
+("  D) (Windows) Toggle winsound driver (file/mem)\n" if WIN else "")+
"---------------------------------------------------------------"
)
def cls(): os.system("cls" if os.name=="nt" else "clear")
def menu(V,cfg,pl):
    cls(); print(B)
    for i,(n,_,ms) in enumerate(V,1): print(f"  {i:2d}) {n:16s} ({int(ms)} ms)")
    print(f"\n  Driver: {pl.label()} | SR:{cfg['sr']} | AMP:{cfg['amp']:.2f} | CPS:{cfg['cps']:.1f} | P×{cfg['pm']:.1f} | Gap {cfg['gap']:.2f}s | WS {'ON' if cfg['ws'] else 'OFF'}")
    print("---------------------------------------------------------------")
def edit_lines():
    print("\nEnter text lines (blank ends):"); out=[]
    while True:
        try: s=input("> ")
        except EOFError: break
        if not s.strip(): break
        out.append(s)
    return out or ["Link, can you hear me? The forest is whispering...","A hero rises. A legend returns."]

# ===== main =====
def settle(pl,sr,ms):
    n=int(sr*ms/1000); pl.play([0.0]*max(1,n),sr,tempfile.gettempdir()); pl.wait(ms/1000+0.05)
def play_line(text,grain,cfg,pl,tmp):
    d=pl.play(render_line(text,grain,cfg['sr'],cfg['cps'],cfg['pm'],cfg['ws']),cfg['sr'],tmp)
    print_sync(text,cfg['cps'],cfg['pm']); pl.wait(d+0.05); settle(pl,cfg['sr'],cfg['flush']); 
    if cfg['gap']>0: time.sleep(cfg['gap'])
def play_var(var,cfg,pl,tmp):
    n,g,ms=var; h=f"=== {n} ({int(ms)} ms) ==="; print(h); print("-"*len(h))
    for t in cfg['texts']: play_line(t,g,cfg,pl,tmp); print()
def play_all(V,cfg,pl,tmp):
    print(f"[ALL | driver={pl.label()} | sr={cfg['sr']} | amp={cfg['amp']:.2f} | cps={cfg['cps']:.1f}]")
    for var in V: play_var(var,cfg,pl,tmp)

def main():
    cfg={'texts':["Link, can you hear me? The forest is whispering...","A hero rises. A legend returns."],
         'cps':26.0,'pm':2.2,'ws':False,'gap':0.80,'flush':240,'amp':A_DEFAULT,'sr':44100,'deep':False}
    pl=P(); tmp=tempfile.gettempdir()
    V=build_vars(cfg['amp'],cfg['sr'])
    print("[Audio verify]"); pl.beep(cfg['sr']); settle(pl,cfg['sr'],cfg['flush'])
    R=V_RECIPES()
    while True:
        menu(V,cfg,pl)
        try: ch=input("Choice > ").strip()
        except EOFError: print(); break
        if not ch: continue
        c=ch.lower()
        if c in ("q","quit","exit"): print("Goodbye."); break
        if c in ("l","list"): continue
        if c in ("v","verify","beep"): pl.beep(cfg['sr']); input("\n[Press ENTER]"); continue
        if c in ("a","all"): play_all(V,cfg,pl,tmp); input("\n[Press ENTER]"); continue
        if c in ("t","text","texts"): cfg['texts']=edit_lines(); continue
        if c in ("c","cps"):
            try: v=float(input("CPS: ").strip()); 
            except: v=None
            if v and v>0: cfg['cps']=v; continue
        if c in ("p","punct","punct-mult"):
            try: v=float(input("Punctuation multiplier: ").strip())
            except: v=None
            if v and v>0: cfg['pm']=v; continue
        if c in ("w","whitespace"): cfg['ws']=not cfg['ws']; print("Whitespace:", "ON" if cfg['ws'] else "OFF"); time.sleep(0.4); continue
        if c in ("g","gap","post-gap"):
            try: v=float(input("Post-gap seconds: ").strip())
            except: v=None
            if v is not None and v>=0: cfg['gap']=v; continue
        if c in ("f","flush","silence-flush"):
            try: v=int(input("Silence flush ms: ").strip())
            except: v=None
            if v: cfg['flush']=max(10,min(2000,v)); continue
        if c in ("s","amp","volume"):
            try: v=float(input("Amplitude 0..1: ").strip())
            except: v=None
            if v: cfg['amp']=max(0.01,min(1.0,v)); V=build_vars(cfg['amp'],cfg['sr']); settle(pl,cfg['sr'],cfg['flush']); print(f"AMP {cfg['amp']:.2f}"); time.sleep(0.3); continue
        if c in ("h","sr","samplerate"):
            try: v=int(input("Sample rate (44100/48000): ").strip())
            except: v=None
            if v and 8000<=v<=192000: cfg['sr']=v; V=build_vars(cfg['amp'],cfg['sr']); settle(pl,cfg['sr'],cfg['flush']); print(f"SR {cfg['sr']}"); time.sleep(0.3); continue
        if c in ("k","kill","clear"): print("\nDeep clear…"); pl.reset(kill=True); settle(pl,cfg['sr'],cfg['flush']); print("Done."); time.sleep(0.4); continue
        if c in ("d","driver") and WIN: pl.toggle(); print("Driver:",pl.label()); pl.reset(False); settle(pl,cfg['sr'],cfg['flush']); time.sleep(0.4); continue
        if c in ("e","export"):
            tgt=input("Export which (index or name)? ").strip().lower()
            idx=None
            if tgt.isdigit():
                i=int(tgt); 
                if 1<=i<=len(V): idx=i-1
            if idx is None:
                nm_map={n.lower():k for k,(n,_,_) in enumerate(V)}
                if tgt in nm_map: idx=nm_map[tgt]
            if idx is None: print("Not found."); time.sleep(0.6); continue
            n,g,_=V[idx]; stem=input(f"Base filename [default {n}]: ").strip() or n
            kind=(input("Type: [P]y / [W]av / [B]oth (default B): ").strip().lower() or "b")
            try:
                if kind in ("b","w"): export_wav(n,g,stem,cfg['sr'])
                if kind in ("b","p"):
                    expr=V_RECIPES()[n]
                    export_py(n,expr,stem,cfg['sr'],cfg['amp'],cfg['cps'],cfg['pm'],cfg['ws'])
                print("Exported.")
            except Exception as e:
                print("Export failed:",e)
            time.sleep(0.5); continue
        if ch.isdigit():
            i=int(ch)
            if 1<=i<=len(V): play_var(V[i-1],cfg,pl,tmp); input("\n[Press ENTER]")
            else: print("Invalid."); time.sleep(0.6); 
            continue
        nm={n.lower():(n,g,ms) for n,g,ms in V}
        if c in nm: play_var(nm[c],cfg,pl,tmp); input("\n[Press ENTER]"); continue
        print("Unrecognized."); time.sleep(0.6)

if __name__=="__main__":
    try: main()
    except KeyboardInterrupt: print("\nInterrupted.")
