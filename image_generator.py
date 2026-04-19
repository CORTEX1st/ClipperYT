"""
Image Generator v5 — SEMUA PROVIDER GRATIS
Urutan: Gemini → Together AI (free 3 bulan) → Cloudflare Workers AI → HuggingFace → Pollinations

SETUP API KEY GRATIS (pilih salah satu atau semua):
1. GEMINI_API_KEY     → https://aistudio.google.com/apikey          (30 detik, 500/hari)
2. TOGETHER_API_KEY   → https://api.together.xyz                     (daftar, free 3 bulan)
3. CF_ACCOUNT_ID +
   CF_API_TOKEN       → https://dash.cloudflare.com (free, 100k req/hari)
4. HF_API_KEY         → https://huggingface.co/settings/tokens       (gratis)
5. Pollinations       → tidak butuh key (backup, kadang diblokir ISP)
"""
import os, json, time, base64, urllib.request, urllib.error
import threading, queue
from pathlib import Path
from utils import logger

try:
    from PIL import Image, ImageDraw
    from io import BytesIO
    from PIL import ImageOps
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


class ImageGenerator:
    def __init__(self):
        self.gemini_key   = os.environ.get("GEMINI_API_KEY", "")
        self.together_key = os.environ.get("TOGETHER_API_KEY", "")
        self.cf_account   = os.environ.get("CF_ACCOUNT_ID", "")
        self.cf_token     = os.environ.get("CF_API_TOKEN", "")
        self.hf_key       = os.environ.get("HF_API_KEY", "")
        self._gemini_models_cache = None
        self._gemini_blocked_reason = ""
        self._hf_warned_no_key = False

    def _save_image_bytes(self, raw: bytes, out: str, w: int, h: int, quality: int = 92) -> bool:
        if len(raw) <= 5000:
            return False
        if not (raw[:2] == b"\xff\xd8" or raw[:4] == b"\x89PNG"):
            return False
        if HAS_PIL:
            img = Image.open(BytesIO(raw)).convert("RGB")
            # Preserve aspect ratio without stretching: fill frame with center crop.
            fitted = ImageOps.fit(img, (w, h), method=Image.LANCZOS, centering=(0.5, 0.5))
            fitted.save(out, "JPEG", quality=quality)
        else:
            open(out, "wb").write(raw)
        return True

    def _gemini_image_models(self) -> list:
        """Resolve active Gemini image models from ListModels to avoid stale hardcoded IDs."""
        if self._gemini_models_cache is not None:
            return self._gemini_models_cache

        preferred = [
            "gemini-2.5-flash-image",
            "gemini-3.1-flash-image-preview",
            "gemini-2.0-flash-exp-image-generation",
        ]
        discovered = []
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={self.gemini_key}"
            with urllib.request.urlopen(url, timeout=20) as r:
                data = json.loads(r.read())
            supported = set()
            for m in data.get("models", []):
                name = (m.get("name") or "").replace("models/", "")
                methods = set(m.get("supportedGenerationMethods") or [])
                if "generateContent" in methods:
                    supported.add(name)
            discovered = [m for m in preferred if m in supported]
        except Exception as e:
            logger.warning(f"    Gemini ListModels gagal: {type(e).__name__}")

        self._gemini_models_cache = discovered or preferred
        return self._gemini_models_cache

    # ════════════════════════════════════════════════════════════
    # PROVIDER 1: GEMINI — 500 gambar/hari GRATIS
    # Daftar: https://aistudio.google.com/apikey (30 detik)
    # ════════════════════════════════════════════════════════════
    def _gemini(self, prompt: str, w: int, h: int, out: str) -> bool:
        if not self.gemini_key:
            return False
        models = self._gemini_image_models()
        for model in models:
            try:
                payload = json.dumps({
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]}
                }).encode()
                url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                       f"{model}:generateContent")
                req = urllib.request.Request(url, data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "x-goog-api-key": self.gemini_key,
                    })
                logger.info(f"    Gemini {model}...")
                with urllib.request.urlopen(req, timeout=60) as r:
                    resp = json.loads(r.read())

                for part in resp.get("candidates",[{}])[0].get("content",{}).get("parts",[]):
                    if "inlineData" in part:
                        raw = base64.b64decode(part["inlineData"]["data"])
                        if self._save_image_bytes(raw, out, w, h):
                            logger.info(f"    Gemini OK: {Path(out).stat().st_size//1024}KB")
                            return True

                err = resp.get("error", {})
                code = err.get("code", 0)
                if code == 429:
                    logger.warning("    Gemini quota habis (500/hari), skip"); return False
                logger.warning(f"    Gemini {model}: {err.get('message','no image')[:60]}")
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="ignore")
                if e.code == 429:
                    self._gemini_blocked_reason = "quota/rate limit"
                    logger.warning("    Gemini HTTP 429 (quota/rate limit)"); return False
                if e.code == 401:
                    self._gemini_blocked_reason = "unauthorized key"
                    logger.warning("    Gemini HTTP 401 (API key tidak valid/terbatas)"); return False
                if e.code == 404:
                    logger.warning(f"    Gemini {model}: HTTP 404 (model tidak tersedia), coba model lain")
                    continue
                if e.code == 400:
                    logger.warning(f"    Gemini {model}: HTTP 400 {body[:120]}")
                    continue
                logger.warning(f"    Gemini HTTP {e.code}: {body[:120]}")
            except Exception as e:
                logger.warning(f"    Gemini: {type(e).__name__}: {e}")
        return False

    # ════════════════════════════════════════════════════════════
    # PROVIDER 2: TOGETHER AI — FLUX.1 schnell (free 3 bulan)
    # Daftar: https://api.together.xyz
    # ════════════════════════════════════════════════════════════
    def _together(self, prompt: str, w: int, h: int, out: str) -> bool:
        if not self.together_key:
            return False
        tw, th = min(w, 1440), min(h, 1440)
        try:
            payload = json.dumps({
                "model": "black-forest-labs/FLUX.1-schnell-Free",
                "prompt": prompt,
                "width": tw, "height": th,
                "steps": 4, "n": 1,
                "response_format": "b64_json",
            }).encode()
            req = urllib.request.Request(
                "https://api.together.xyz/v1/images/generations",
                data=payload,
                headers={"Authorization": f"Bearer {self.together_key}",
                         "Content-Type": "application/json"})
            logger.info("    Together AI FLUX.1...")
            with urllib.request.urlopen(req, timeout=60) as r:
                resp = json.loads(r.read())
            raw = base64.b64decode(resp["data"][0]["b64_json"])
            if self._save_image_bytes(raw, out, w, h):
                logger.info(f"    Together OK: {Path(out).stat().st_size//1024}KB")
                return True
        except urllib.error.HTTPError as e:
            if e.code == 402: logger.warning("    Together: kredit habis")
            else: logger.warning(f"    Together HTTP {e.code}: {e.read()[:80]}")
        except Exception as e:
            logger.warning(f"    Together: {type(e).__name__}: {e}")
        return False

    # ════════════════════════════════════════════════════════════
    # PROVIDER 3: CLOUDFLARE WORKERS AI — FLUX.1 schnell (100k/hari)
    # Daftar: https://dash.cloudflare.com (gratis)
    # Buat API token: My Profile → API Tokens → Create Token
    # Pilih template "Workers AI" atau buat custom dengan izin "Workers AI:Read"
    # CF_ACCOUNT_ID: di halaman utama dashboard (kanan bawah)
    # ════════════════════════════════════════════════════════════
    def _cloudflare(self, prompt: str, w: int, h: int, out: str) -> bool:
        if not self.cf_account or not self.cf_token:
            return False
        # Model FLUX tersedia di Cloudflare Workers AI
        models = [
            "@cf/black-forest-labs/flux-1-schnell",  # Paling cepat
        ]
        for model in models:
            try:
                cw = min(w, 1024); ch = min(h, 1024)
                payload = json.dumps({
                    "prompt": prompt,
                    "num_steps": 4,
                    "width":  cw,
                    "height": ch,
                }).encode()
                url = (f"https://api.cloudflare.com/client/v4/accounts/"
                       f"{self.cf_account}/ai/run/{model}")
                req = urllib.request.Request(url, data=payload, headers={
                    "Authorization": f"Bearer {self.cf_token}",
                    "Content-Type":  "application/json",
                })
                logger.info(f"    Cloudflare FLUX.1...")
                with urllib.request.urlopen(req, timeout=60) as r:
                    resp = json.loads(r.read())

                # Response bisa berupa {"result":{"image":"base64..."}}
                img_b64 = (resp.get("result") or {}).get("image", "")
                if img_b64:
                    raw = base64.b64decode(img_b64)
                    if self._save_image_bytes(raw, out, w, h):
                        logger.info(f"    Cloudflare OK: {Path(out).stat().st_size//1024}KB")
                        return True
                errors = resp.get("errors", [])
                if errors:
                    logger.warning(f"    Cloudflare error: {errors[0].get('message','')[:80]}")
            except urllib.error.HTTPError as e:
                logger.warning(f"    Cloudflare HTTP {e.code}: {e.read()[:80]}")
            except Exception as e:
                logger.warning(f"    Cloudflare: {type(e).__name__}: {e}")
        return False

    # ════════════════════════════════════════════════════════════
    # PROVIDER 4: HUGGING FACE — model aktif (bukan yg 410 Gone)
    # Daftar: https://huggingface.co/settings/tokens (gratis)
    # ════════════════════════════════════════════════════════════
    def _huggingface(self, prompt: str, w: int, h: int, out: str) -> bool:
        if not self.hf_key:
            if not self._hf_warned_no_key:
                logger.info("    HF skip: tidak ada HF_API_KEY (endpoint publik sering 410/disabled)")
                self._hf_warned_no_key = True
            return False

        candidates = []
        candidates += [
            ("black-forest-labs/FLUX.1-schnell", 1024),
            ("black-forest-labs/FLUX.1-dev",     1024),
        ]
        hdrs = {"Content-Type":"application/json","X-Wait-For-Model":"true"}
        hdrs["Authorization"] = f"Bearer {self.hf_key}"

        for model_id, max_px in candidates:
            uw = (min(w, max_px)//8)*8
            uh = (min(h, max_px)//8)*8
            payload = json.dumps({
                "inputs": prompt,
                "parameters": {
                    "width": uw, "height": uh,
                    "num_inference_steps": 25,
                    "guidance_scale": 7.5,
                    "negative_prompt": "blurry, bad quality, watermark, ugly",
                },
                "options": {"wait_for_model": True, "use_cache": False}
            }).encode()
            req = urllib.request.Request(
                f"https://router.huggingface.co/hf-inference/models/{model_id}",
                data=payload, headers=hdrs)
            name = model_id.split("/")[-1]
            try:
                logger.info(f"    HF {name}...")
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = r.read()
                if self._save_image_bytes(data, out, w, h):
                    logger.info(f"    HF {name} OK: {Path(out).stat().st_size//1024}KB")
                    return True
                if data[:1]==b'{':
                    msg = json.loads(data).get("error","")
                    if "loading" in msg.lower():
                        logger.warning(f"    HF {name} loading, tunggu 20s...")
                        time.sleep(20)
                        with urllib.request.urlopen(req, timeout=120) as r2:
                            d2 = r2.read()
                        if self._save_image_bytes(d2, out, w, h):
                            return True
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode("utf-8", errors="ignore")[:120]
                except Exception:
                    pass
                if e.code == 403 and "Inference Providers" in detail:
                    logger.warning("    HF token belum punya izin Inference Providers (403)")
                    return False
                logger.warning(f"    HF {name}: HTTP {e.code}" + (" (dihapus)" if e.code==410 else "") + (f" {detail}" if detail else ""))
            except Exception as e:
                logger.warning(f"    HF {name}: {type(e).__name__}: {e}")
        return False

    # ════════════════════════════════════════════════════════════
    # PROVIDER 5: POLLINATIONS — parallel (backup, tanpa key)
    # Sering diblokir ISP Indonesia, tapi gratis kalau bisa akses
    # ════════════════════════════════════════════════════════════
    def _pollinations(self, prompt: str, w: int, h: int, out: str, scene_id: int) -> bool:
        import urllib.parse
        enc    = urllib.parse.quote(f"{prompt}, highly detailed, 8k"[:500])
        combos = [(scene_id*137+42,"flux"),(scene_id*31+7,"flux-realism"),(scene_id+500,"turbo")]
        result_q = queue.Queue()
        stop_ev  = threading.Event()

        def try_dl(seed, model):
            if stop_ev.is_set(): return
            url = (f"https://image.pollinations.ai/prompt/{enc}"
                   f"?width={w}&height={h}&seed={seed}&nologo=true&model={model}")
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                    "Accept":"image/jpeg,image/png,image/*"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    if stop_ev.is_set(): return
                    data = r.read()
                if len(data)>10000 and (data[:2]==b'\xff\xd8' or data[:4]==b'\x89PNG'):
                    result_q.put(("ok", data, model))
                else:
                    result_q.put(("fail", None, model))
            except urllib.error.HTTPError as e:
                result_q.put(("fail", None, f"{model}:HTTP {e.code}"))
            except Exception as e:
                result_q.put(("fail", None, f"{model}:{type(e).__name__}"))

        threads = []
        for s, m in combos:
            t = threading.Thread(target=try_dl, args=(s,m), daemon=True)
            t.start(); threads.append(t)
            logger.info(f"    Pollinations thread: {m}...")

        deadline = time.time() + 125
        received = 0
        while received < len(combos) and time.time() < deadline:
            try:
                status, data, mname = result_q.get(timeout=5)
                received += 1
                if status=="ok" and data:
                    stop_ev.set()
                    if self._save_image_bytes(data, out, w, h):
                        logger.info(f"    Pollinations OK ({mname}): {len(data)//1024}KB")
                        return True
                logger.warning(f"    Pollinations {mname} gagal")
            except queue.Empty:
                continue
        stop_ev.set()
        return False

    # ════════════════════════════════════════════════════════════
    # MAIN — coba semua provider berurutan sampai ada yang berhasil
    # ════════════════════════════════════════════════════════════
    def generate(self, prompt: str, w: int, h: int, out_path: str, scene_id: int = 1) -> bool:
        enhanced = (f"{prompt}, masterpiece, ultra detailed, sharp focus, "
                    f"professional photography, vivid colors, 8k, cinematic lighting")
        logger.info(f"  Scene {scene_id}: generating image...")

        if self.gemini_key:
            if self._gemini_blocked_reason:
                logger.info(f"    → Gemini skip (sudah gagal: {self._gemini_blocked_reason})")
            else:
                logger.info("    → Gemini API (500/hari gratis)...")
                if self._gemini(enhanced, w, h, out_path): return True

        if self.together_key:
            logger.info("    → Together AI (free 3 bulan)...")
            if self._together(enhanced, w, h, out_path): return True

        if self.cf_account and self.cf_token:
            logger.info("    → Cloudflare Workers AI (100k/hari gratis)...")
            if self._cloudflare(enhanced, w, h, out_path): return True

        logger.info("    → HuggingFace...")
        if self._huggingface(enhanced, w, h, out_path): return True

        logger.info("    → Pollinations (backup)...")
        if self._pollinations(enhanced, w, h, out_path, scene_id): return True

        logger.warning(f"  Scene {scene_id}: semua provider gagal → placeholder")
        return False

    # ════════════════════════════════════════════════════════════
    # PLACEHOLDER — full frame gradient, dipanggil hanya kalau semua gagal
    # ════════════════════════════════════════════════════════════
    def make_placeholder(self, out_path: str, w: int, h: int,
                          scene_id: int, prompt: str = "") -> str:
        if HAS_PIL:
            try:
                palettes = [
                    ((139,0,0),(255,165,0)),((0,0,139),(0,191,255)),
                    ((75,0,130),(238,130,238)),((0,100,0),(144,238,144)),
                    ((139,69,19),(255,215,0)),((25,25,112),(100,149,237)),
                ]
                c1,c2 = palettes[(scene_id-1)%len(palettes)]
                img   = Image.new("RGB",(w,h))
                draw  = ImageDraw.Draw(img)
                for y in range(h):
                    t=y/h; t=t*t*(3-2*t)
                    draw.line([(0,y),(w,y)],fill=(
                        int(c1[0]+(c2[0]-c1[0])*t),
                        int(c1[1]+(c2[1]-c1[1])*t),
                        int(c1[2]+(c2[2]-c1[2])*t)))
                fs=w//2
                draw.text((w//2+8,h//2-60+8),str(scene_id),fill=(0,0,0),     anchor="mm",font_size=fs)
                draw.text((w//2,  h//2-60),  str(scene_id),fill=(255,255,255),anchor="mm",font_size=fs)
                draw.text((w//2+3,h//2-fs//2-40+3),"SCENE",fill=(0,0,0),     anchor="mm",font_size=80)
                draw.text((w//2,  h//2-fs//2-40),  "SCENE",fill=(255,255,255),anchor="mm",font_size=80)
                if prompt:
                    for y in range(h-300,h):
                        a=min(1.0,(y-(h-300))/150)
                        draw.line([(0,y),(w,y)],fill=(
                            int((c1[0]+(c2[0]-c1[0])*y/h)*(1-a)),
                            int((c1[1]+(c2[1]-c1[1])*y/h)*(1-a)),
                            int((c1[2]+(c2[2]-c1[2])*y/h)*(1-a))))
                    words=prompt[:90].split(); lines,line=[],[]
                    for word in words:
                        if len(" ".join(line+[word]))<=25: line.append(word)
                        else:
                            if line: lines.append(" ".join(line))
                            line=[word]
                    if line: lines.append(" ".join(line))
                    y_t=h-260
                    for ln in lines[:5]:
                        draw.text((w//2,y_t),ln,fill=(220,220,220),anchor="mm",font_size=48)
                        y_t+=56
                img.save(out_path,"JPEG",quality=92)
                return out_path
            except Exception as e:
                logger.warning(f"PIL error: {e}")
        from ffmpeg_helper import run_ffmpeg
        cols=["0x8B0000","0x00008B","0x4B0082","0x006400","0x8B4513","0x191970"]
        run_ffmpeg(["-f","lavfi","-i",f"color=c={cols[(scene_id-1)%6]}:size={w}x{h}:rate=1",
                    "-t","1","-vframes","1",
                    "-vf",f"drawtext=text='SCENE {scene_id}':fontsize=120:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2",
                    "-y",out_path],capture_output=True)
        return out_path
