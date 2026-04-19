"""
AI Video Generator v4 — Multi-provider image generation
"""
import os, json, time, tempfile, uuid, re
from pathlib import Path
from groq import Groq
from utils import logger
from ffmpeg_helper import run_ffmpeg, run_ffprobe
from image_generator import ImageGenerator


class AIVideoGenerator:
    def __init__(self):
        self.client    = Groq(api_key=os.environ["GROQ_API_KEY"])
        self.imggen    = ImageGenerator()
        self.base      = Path(__file__).parent
        self.out_dir   = self.base / "generated_videos"
        self.img_dir   = self.base / "generated_images"
        self.audio_dir = self.base / "generated_audio"
        for d in [self.out_dir, self.img_dir, self.audio_dir]:
            d.mkdir(exist_ok=True)

    # ── 1. Concepts ──────────────────────────────────────────────
    def generate_concepts(self, prompt: str) -> list:
        logger.info(f"  Generating konsep: {prompt[:60]}...")
        resp = self.client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{"role":"user","content":
                f'Kamu creative director YouTube. User ingin: "{prompt}"\n'
                'Buat 5 konsep video berbeda. Balas JSON array saja:\n'
                '[{"id":1,"title":"Judul","hook":"Hook max 15 kata","description":"2-3 kalimat",'
                '"style":"cinematic","duration_est":"45 detik","viral_potential":88}]'
            }],
            max_tokens=1500, temperature=0.8
        )
        raw = resp.choices[0].message.content.strip().replace("```json","").replace("```","").strip()
        if "[" in raw: raw = raw[raw.index("["):raw.rindex("]")+1]
        return json.loads(raw)

    # ── 2. Storyboard ────────────────────────────────────────────
    def generate_storyboard(self, concept: dict, fmt: str = "vertical") -> dict:
        w, h  = (1080,1920) if fmt=="vertical" else (1920,1080)
        ratio = "9:16 vertikal" if fmt=="vertical" else "16:9 horizontal"
        logger.info(f"  Storyboard: {concept['title']}...")
        resp = self.client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{"role":"user","content":
                f"Buat storyboard video YouTube:\n"
                f"Judul: {concept['title']}\nHook: {concept['hook']}\n"
                f"Style: {concept['style']}\nFormat: {ratio}\n\n"
                "Buat TEPAT 6 scene, masing-masing 5-7 detik.\n"
                "Setiap scene harus terasa seperti video bergerak, bukan slideshow.\n"
                "WAJIB: Tentukan karakter utama yang konsisten di semua scene (nama + ciri).\n"
                "WAJIB untuk image_prompt:\n"
                "- Bahasa Inggris\n"
                "- Detail seperti Stable Diffusion prompt\n"
                "- Wajib ada angle kamera, subject, ekspresi, dan environment\n\n"
                "WAJIB untuk speech:\n"
                "- Hanya kalimat yang benar-benar diucapkan (1-2 kalimat pendek)\n"
                "- Jangan masukkan deskripsi visual ke speech\n"
                "- Boleh monolog atau dialog 2 orang\n\n"
                "WAJIB untuk camera_motion, pilih salah satu:\n"
                "- slow_zoom_in, slow_zoom_out, pan_left, pan_right, pan_up, pan_down\n\n"
                "Jika konsep menyebut tokoh spesifik (mis. kakek motivasi),\n"
                "image_prompt harus menampilkan tokoh itu secara jelas.\n"
                f'Balas JSON:\n{{"title":"Judul","description":"Desc","tags":["t1"],'
                f'"total_duration":42,"format":"{fmt}","scenes":['
                f'{{"id":1,"duration":7,"text_overlay":"teks overlay singkat","text_position":"bottom",'
                f'"speech":"kalimat yang diucapkan","dialogue":[{{"speaker":"Suami","line":"..."}},'
                f'{{"speaker":"Istri","line":"..."}}],"camera_motion":"slow_zoom_in",'
                f'"image_prompt":"detailed english prompt"}}]}}'
            }],
            max_tokens=2500, temperature=0.7
        )
        raw = resp.choices[0].message.content.strip().replace("```json","").replace("```","").strip()
        if "{" in raw: raw = raw[raw.index("{"):raw.rindex("}")+1]
        sb = json.loads(raw)
        sb["width"]  = w
        sb["height"] = h
        return sb

    def _clean_speech_text(self, text: str) -> str:
        t = (text or "").strip()
        if not t:
            return ""
        t = re.sub(r"\[(.*?)\]|\((.*?)\)", "", t)
        t = re.sub(r"\b(scene|kamera|camera|visual|prompt)\b[:\-]?", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s+", " ", t).strip()
        if len(t) > 280:
            t = t[:280].rsplit(" ", 1)[0].strip() + "."
        return t

    def _extract_scene_speech(self, scene: dict) -> str:
        dialogue = scene.get("dialogue")
        if isinstance(dialogue, list) and dialogue:
            lines = []
            for turn in dialogue[:4]:
                if not isinstance(turn, dict):
                    continue
                line = self._clean_speech_text(str(turn.get("line", "")))
                spk = str(turn.get("speaker", "")).strip()
                if line:
                    lines.append(f"{spk}: {line}" if spk else line)
            if lines:
                return " ".join(lines)

        for key in ("speech", "narration_id", "narration"):
            v = self._clean_speech_text(str(scene.get(key, "")))
            if v:
                return v
        return ""

    def _wrap_overlay_text(self, text: str, max_chars: int = 26) -> str:
        words = (text or "").strip().split()
        if not words:
            return ""
        lines, line = [], []
        for w in words:
            candidate = " ".join(line + [w])
            if len(candidate) <= max_chars:
                line.append(w)
            else:
                if line:
                    lines.append(" ".join(line))
                line = [w]
        if line:
            lines.append(" ".join(line))
        return "\n".join(lines[:3])

    def _escape_drawtext(self, text: str) -> str:
        t = (text or "")
        t = t.replace("\\", "\\\\")
        t = t.replace(":", "\\:")
        t = t.replace("'", "\\'")
        t = t.replace("%", "\\%")
        t = t.replace("\n", "\\n")
        return t

    def _motion_filter(self, w: int, h: int, frames: int, scene_id: int, motion: str = "") -> str:
        m = (motion or "").strip().lower()
        if not m:
            m = "pan_left" if scene_id % 2 == 0 else "slow_zoom_in"

        z = "min(zoom+0.0009,1.12)"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
        if m == "slow_zoom_out":
            z = "if(lte(on,1),1.12,max(1.00,zoom-0.0009))"
        elif m == "pan_left":
            z = "1.06"; x = "max(iw-iw/zoom-on*1.3,0)"
        elif m == "pan_right":
            z = "1.06"; x = "min(on*1.3,iw-iw/zoom)"
        elif m == "pan_up":
            z = "1.06"; y = "max(ih-ih/zoom-on*1.3,0)"
        elif m == "pan_down":
            z = "1.06"; y = "min(on*1.3,ih-ih/zoom)"

        return (
            "scale=iw*1.12:ih*1.12:flags=lanczos,"
            f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps=25,"
            "setsar=1"
        )

    # ── 3. Dubbing ───────────────────────────────────────────────
    def generate_dubbing(self, text: str, out_path: str, lang: str = "id") -> str:
        text = self._clean_speech_text(text)
        if not text:
            return None
        voices = {"id":"id-ID-ArdiNeural","en":"en-US-GuyNeural"}
        voice  = voices.get(lang, "id-ID-ArdiNeural")
        try:
            import asyncio, edge_tts
            async def _tts():
                await edge_tts.Communicate(text, voice).save(out_path)
            asyncio.run(_tts())
            if Path(out_path).exists() and Path(out_path).stat().st_size > 500:
                return out_path
        except Exception as e:
            logger.warning(f"  edge-tts: {e}")
        try:
            from gtts import gTTS
            gTTS(text=text, lang=lang).save(out_path)
            if Path(out_path).exists(): return out_path
        except Exception as e:
            logger.warning(f"  gTTS: {e}")
        return None

    # ── 4. Image → Video Clip ────────────────────────────────────
    def image_to_clip(self, img_path: str, dur: int, w: int, h: int,
                       text: str, text_pos: str, clip_path: str,
                       audio_path: str = None, scene_id: int = 1,
                       motion: str = "") -> bool:
        use_dur = float(dur)
        cmd = ["-loop","1","-i",img_path]
        if audio_path and Path(audio_path).exists():
            ar = run_ffprobe(["-v","error","-show_entries","format=duration",
                              "-of","json",audio_path], capture_output=True, text=True)
            try:
                aud_dur = float(json.loads(ar.stdout)["format"]["duration"])
            except Exception:
                aud_dur = dur
            use_dur = max(dur, aud_dur + 0.3)

        fps = 25
        frames = max(1, int(round(use_dur * fps)))
        vf = self._motion_filter(w, h, frames, scene_id, motion)

        if text and text.strip():
            wrapped = self._wrap_overlay_text(text.strip()[:120], max_chars=30 if h >= w else 42)
            t = self._escape_drawtext(wrapped)
            y = f"h-text_h-{int(h*0.12)}" if text_pos=="bottom" else f"{int(h*0.10)}"
            fs = max(30, int(h*0.034))
            vf += (f",drawtext=text='{t}':fontsize={fs}:fontcolor=white"
                   f":line_spacing={max(6, fs//5)}:box=1:boxcolor=black@0.62:boxborderw=16"
                   f":x=(w-text_w)/2:y={y}:fix_bounds=1")

        if audio_path and Path(audio_path).exists():
            cmd += ["-i",audio_path,"-t",str(use_dur),"-vf",vf,
                    "-c:v","libx264","-preset","fast","-crf","22",
                    "-c:a","aac","-b:a","128k","-pix_fmt","yuv420p",
                    "-r",str(fps),"-shortest","-y",clip_path]
        else:
            cmd += ["-t",str(use_dur),"-vf",vf,
                    "-c:v","libx264","-preset","fast","-crf","22",
                    "-pix_fmt","yuv420p","-r",str(fps),"-an","-y",clip_path]

        r  = run_ffmpeg(cmd, capture_output=True, text=True)
        ok = r.returncode==0 and Path(clip_path).exists() and Path(clip_path).stat().st_size>1000
        if ok:
            logger.info(f"    Clip OK: {Path(clip_path).stat().st_size//1024}KB")
        else:
            logger.error(f"    Clip error: {r.stderr[-150:]}")
        return ok

    # ── 5. Render All Scenes ─────────────────────────────────────
    def render_video(self, storyboard: dict, session_id: str,
                     with_dubbing: bool = True, dub_lang: str = "id",
                     progress_cb=None, one_image_mode: bool = False,
                     image_path: str = None) -> str:
        scenes  = storyboard.get("scenes",[])
        w       = storyboard.get("width",1080)
        h       = storyboard.get("height",1920)
        out_dir = self.img_dir / session_id
        out_dir.mkdir(exist_ok=True)

        if not scenes: raise RuntimeError("Storyboard kosong!")

        total     = len(scenes)
        img_clips = []

        shared_img_path = None
        user_img_path = None
        if image_path:
            try:
                p = Path(str(image_path))
                if p.exists():
                    user_img_path = str(p)
            except Exception:
                user_img_path = None

        for i, scene in enumerate(scenes, 1):
            pct        = int(10 + i/total*68)
            img_path   = str(out_dir / f"scene_{i:02d}.jpg")
            audio_path = str(out_dir / f"audio_{i:02d}.mp3") if with_dubbing else None
            clip_path  = str(out_dir / f"clip_{i:02d}.mp4")
            dur        = max(4, int(scene.get("duration",6)))

            # Download gambar (multi-provider) or use user image
            if user_img_path and Path(user_img_path).exists():
                if progress_cb: progress_cb(pct, f"Scene {i}/{total}: gunakan gambar upload...")
                import shutil
                shutil.copy2(user_img_path, img_path)
            elif one_image_mode and shared_img_path and Path(shared_img_path).exists():
                if progress_cb: progress_cb(pct, f"Scene {i}/{total}: reuse 1 gambar utama...")
                import shutil
                shutil.copy2(shared_img_path, img_path)
            else:
                if progress_cb: progress_cb(pct, f"Scene {i}/{total}: generating image...")
                prompt_for_scene = scenes[0].get("image_prompt","cinematic landscape, beautiful lighting") if one_image_mode else scene.get("image_prompt","cinematic landscape, beautiful lighting")
                success = self.imggen.generate(
                    prompt   = prompt_for_scene,
                    w=w, h=h, out_path=img_path, scene_id=i
                )
                if not success:
                    self.imggen.make_placeholder(img_path, w, h, i, prompt_for_scene)
                if one_image_mode and Path(img_path).exists():
                    shared_img_path = img_path

            # Dubbing
            used_audio = None
            if with_dubbing and audio_path:
                if progress_cb: progress_cb(pct, f"Scene {i}/{total}: dubbing...")
                speech_text = self._extract_scene_speech(scene)
                if speech_text:
                    res = self.generate_dubbing(speech_text, audio_path, dub_lang)
                    if res: used_audio = res

            # Image → clip
            ok = self.image_to_clip(img_path, dur, w, h,
                                     scene.get("text_overlay",""),
                                     scene.get("text_position","bottom"),
                                     clip_path, used_audio,
                                     scene_id=i,
                                     motion=scene.get("camera_motion",""))
            if ok:
                img_clips.append(clip_path)
            else:
                # Fallback solid color
                cols = ["0x8B0000","0x00008B","0x4B0082","0x006400","0x8B4513","0x191970"]
                run_ffmpeg(["-f","lavfi","-i",
                    f"color=c={cols[(i-1)%6]}:size={w}x{h}:rate=25",
                    "-t",str(dur),"-c:v","libx264","-preset","ultrafast",
                    "-crf","23","-pix_fmt","yuv420p","-an","-y",clip_path],
                    capture_output=True)
                if Path(clip_path).exists() and Path(clip_path).stat().st_size>500:
                    img_clips.append(clip_path)

        if not img_clips: raise RuntimeError("Semua scene gagal!")

        if progress_cb: progress_cb(82, f"Menggabungkan {len(img_clips)} scene...")
        concat_f = str(out_dir/"concat.txt")
        with open(concat_f,"w") as f:
            for c in img_clips:
                f.write(f"file '{c.replace(chr(92),'/') }'\n")
        merged = str(out_dir/"merged.mp4")
        r = run_ffmpeg(["-f","concat","-safe","0","-i",concat_f,
                        "-c","copy","-y",merged], capture_output=True, text=True)
        if r.returncode!=0 or not Path(merged).exists():
            merged = img_clips[0]

        if progress_cb: progress_cb(90,"Encoding final...")
        final = str(self.out_dir/f"generated_{session_id}.mp4")
        r2 = run_ffmpeg(["-i",merged,"-c:v","libx264","-preset","medium",
                         "-crf","20","-movflags","+faststart","-y",final],
                        capture_output=True, text=True)
        if r2.returncode!=0 or not Path(final).exists():
            import shutil; shutil.copy2(merged,final)

        size = Path(final).stat().st_size/1024/1024
        if progress_cb: progress_cb(98,f"Selesai! ({size:.1f}MB)")
        return final

    # ── 6. Full Pipeline ─────────────────────────────────────────
    def full_pipeline(self, user_prompt: str, concept_idx: int,
                      fmt: str="vertical", with_dubbing: bool=True,
                      dub_lang: str="id", progress_cb=None,
                      one_image_mode: bool = False,
                      image_path: str = None) -> dict:
        if progress_cb: progress_cb(5,"Generating konsep...")
        concepts   = self.generate_concepts(user_prompt)
        concept    = concepts[min(concept_idx, len(concepts)-1)]
        if progress_cb: progress_cb(12,f"Storyboard: {concept['title']}...")
        storyboard = self.generate_storyboard(concept, fmt)
        sid        = uuid.uuid4().hex[:8]
        vpath      = self.render_video(
            storyboard, sid, with_dubbing, dub_lang, progress_cb,
            one_image_mode=one_image_mode, image_path=image_path
        )
        if progress_cb: progress_cb(100,"Selesai!")
        return {"video_path":vpath,"storyboard":storyboard,"concept":concept,
                "session_id":sid,"title":storyboard.get("title",""),
                "description":storyboard.get("description",""),
                "tags":storyboard.get("tags",[])}
