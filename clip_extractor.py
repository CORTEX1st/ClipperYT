"""
Clip Extractor — Groq AI (pengganti Anthropic)
Deteksi momen viral, potong video, konversi ke 9:16 vertikal
"""
import os, base64, json, tempfile, re, shutil
from pathlib import Path
from groq import Groq
from utils import logger, CLIPS_DIR
from ffmpeg_helper import run_ffmpeg, run_ffprobe


class ClipExtractor:
    def __init__(self):
        self.client     = Groq(api_key=os.environ["GROQ_API_KEY"])
        self.output_dir = CLIPS_DIR
        self.output_dir.mkdir(exist_ok=True)
        self.target_w   = 1080
        self.target_h   = 1920

    def _get_duration(self, video_path: str) -> float:
        r = run_ffprobe(["-v","error","-show_entries","format=duration",
                         "-of","json", video_path], capture_output=True, text=True)
        return float(json.loads(r.stdout)["format"]["duration"])

    def _frame_at(self, video_path: str, timestamp: float, tmpdir: str):
        out = f"{tmpdir}/thumb_{int(timestamp)}.jpg"
        run_ffmpeg(["-ss",str(timestamp),"-i",video_path,
                    "-vframes","1","-q:v","4","-y",out], capture_output=True)
        if Path(out).exists():
            return base64.standard_b64encode(open(out,"rb").read()).decode()
        return None

    def _has_audio_stream(self, video_path: str) -> bool:
        try:
            r = run_ffprobe(
                ["-v","error","-select_streams","a:0","-show_entries","stream=codec_type","-of","json",video_path],
                capture_output=True, text=True
            )
            data = json.loads(r.stdout or "{}")
            return bool(data.get("streams"))
        except Exception:
            return False

    def _valid_audio_file(self, audio_path: str) -> bool:
        p = Path(audio_path)
        if not p.exists() or p.stat().st_size < 800:
            return False
        try:
            r = run_ffprobe(
                [
                    "-v", "error",
                    "-show_entries", "stream=codec_type:format=duration",
                    "-of", "json",
                    audio_path,
                ],
                capture_output=True, text=True
            )
            data = json.loads(r.stdout or "{}")
            has_audio = any((s.get("codec_type") == "audio") for s in data.get("streams", []))
            dur = float((data.get("format") or {}).get("duration") or 0)
            return has_audio and dur > 0.15
        except Exception:
            return False

    def _fmt_srt_time(self, sec: float) -> str:
        s = max(0.0, float(sec))
        ms = int(round((s - int(s)) * 1000))
        total = int(s)
        hh = total // 3600
        mm = (total % 3600) // 60
        ss = total % 60
        return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"

    def _sanitize_title_for_tts(self, text: str) -> str:
        t = (text or "").strip()
        t = re.sub(r"#\w+", "", t)
        t = re.sub(r"\s+", " ", t).strip()
        return t[:180]

    def _wrap_title_lines(self, text: str, max_chars: int = 22) -> str:
        words = (text or "").strip().split()
        if not words:
            return "HIGHLIGHT VIDEO"
        lines, cur = [], []
        for w in words:
            cand = " ".join(cur + [w])
            if len(cand) <= max_chars:
                cur.append(w)
            else:
                if cur:
                    lines.append(" ".join(cur))
                cur = [w]
        if cur:
            lines.append(" ".join(cur))
        return "\n".join(lines[:3])

    def _style_intro_frame(self, frame_path: str, title_text: str):
        try:
            from PIL import Image, ImageDraw, ImageFont
        except Exception:
            return
        try:
            img = Image.open(frame_path).convert("RGBA")
            w, h = img.size
            draw = ImageDraw.Draw(img, "RGBA")
            panel_h = int(h * 0.42)
            for y in range(h - panel_h, h):
                a = (y - (h - panel_h)) / max(1, panel_h)
                alpha = int(45 + 160 * a)
                draw.line([(0, y), (w, y)], fill=(0, 0, 0, alpha))

            fs = max(36, int(h * 0.06))
            try:
                f_main = ImageFont.truetype("arial.ttf", fs)
                f_top = ImageFont.truetype("arial.ttf", max(22, int(fs * 0.55)))
            except Exception:
                f_main = ImageFont.load_default()
                f_top = ImageFont.load_default()

            top = "HOT MOMENT"
            lines = self._wrap_title_lines(title_text or "HIGHLIGHT VIDEO", max_chars=24).split("\n")
            y = h - panel_h + int(panel_h * 0.16)
            draw.text((int(w * 0.06), y), top, fill=(255, 215, 0, 255), font=f_top,
                      stroke_width=2, stroke_fill=(0, 0, 0, 255))
            y += int(fs * 0.9)
            for ln in lines[:3]:
                draw.text((int(w * 0.06), y), ln, fill=(255, 255, 255, 255), font=f_main,
                          stroke_width=max(3, int(fs * 0.1)), stroke_fill=(0, 0, 0, 255))
                y += int(fs * 1.05)
            img.convert("RGB").save(frame_path, "JPEG", quality=92)
        except Exception:
            return

    def _escape_drawtext(self, text: str) -> str:
        t = (text or "")
        t = t.replace("\\", "\\\\")
        t = t.replace(":", "\\:")
        t = t.replace("'", "\\'")
        t = t.replace("%", "\\%")
        t = t.replace("\n", "\\n")
        return t

    def _generate_title_tts(self, text: str, out_path: str) -> str | None:
        title_text = self._sanitize_title_for_tts(text)
        if not title_text:
            return None
        try:
            import asyncio, edge_tts
            async def _tts():
                await edge_tts.Communicate(title_text, "id-ID-ArdiNeural").save(out_path)
            asyncio.run(_tts())
            if Path(out_path).exists() and Path(out_path).stat().st_size > 500:
                return out_path
        except Exception as e:
            logger.warning(f"  Intro TTS edge-tts gagal: {e}")
        try:
            from gtts import gTTS
            gTTS(text=title_text, lang="id").save(out_path)
            if Path(out_path).exists() and Path(out_path).stat().st_size > 500:
                return out_path
        except Exception as e:
            logger.warning(f"  Intro TTS gTTS gagal: {e}")
        return None

    def _prepend_freeze_intro(self, clip_path: str, title_text: str) -> str:
        src = Path(clip_path)
        if not src.exists():
            return clip_path
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            frame = tmp / "intro_frame.jpg"
            intro_video = tmp / "intro_video.mp4"
            intro = tmp / "intro.mp4"
            intro_audio = tmp / "intro_tts.mp3"
            merged = tmp / "merged_intro.mp4"

            # Grab first visual frame of clip
            rr = run_ffmpeg(
                ["-ss","0.04","-i",str(src),"-vframes","1","-q:v","3","-y",str(frame)],
                capture_output=True, text=True
            )
            if rr.returncode != 0 or not frame.exists():
                logger.warning("  Intro skip: gagal ambil frame freeze.")
                return clip_path

            # Intro duration follows TTS length (clamped), fallback fixed
            tts_path = self._generate_title_tts(title_text, str(intro_audio))
            intro_dur = 2.2
            if tts_path and self._valid_audio_file(str(intro_audio)):
                try:
                    pr = run_ffprobe(
                        ["-v","error","-show_entries","format=duration","-of","json",str(intro_audio)],
                        capture_output=True, text=True
                    )
                    intro_dur = min(3.8, max(1.6, float(json.loads(pr.stdout)["format"]["duration"]) + 0.25))
                except Exception:
                    intro_dur = 2.6
            else:
                tts_path = None

            # Style text directly on frame with PIL (stable across ffmpeg builds)
            self._style_intro_frame(str(frame), title_text)
            # 1) Render intro video-only first (robust/simple)
            r_intro_v = run_ffmpeg(
                [
                    "-loop","1","-framerate","25","-i",str(frame),
                    "-t",str(intro_dur),
                    "-vf","scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1",
                    "-c:v","libx264","-preset","fast","-crf","22",
                    "-pix_fmt","yuv420p","-an","-y",str(intro_video)
                ],
                capture_output=True, text=True
            )
            if r_intro_v.returncode != 0 or not intro_video.exists():
                logger.warning(f"  Intro skip: gagal render intro ({(r_intro_v.stderr or '')[-220:]})")
                return clip_path

            # 2) Add audio to intro video
            if tts_path and self._valid_audio_file(str(tts_path)):
                r_intro = run_ffmpeg(
                    [
                        "-i",str(intro_video),"-i",str(intro_audio),
                        "-c:v","copy","-c:a","aac","-b:a","128k","-shortest","-y",str(intro)
                    ],
                    capture_output=True, text=True
                )
            else:
                logger.info("  Intro TTS tidak valid, fallback ke silent intro.")
                r_intro = run_ffmpeg(
                    [
                        "-i",str(intro_video),
                        "-f","lavfi","-i","anullsrc=channel_layout=mono:sample_rate=44100",
                        "-t",str(intro_dur),
                        "-c:v","copy","-c:a","aac","-b:a","96k","-shortest","-y",str(intro)
                    ],
                    capture_output=True, text=True
                )
            if r_intro.returncode != 0 or not intro.exists():
                logger.warning(f"  Intro skip: gagal mux intro ({(r_intro.stderr or '')[-180:]})")
                return clip_path

            # Concat intro + original clip
            if self._has_audio_stream(str(src)):
                merge_cmd = [
                    "-i",str(intro),"-i",str(src),
                    "-filter_complex","[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]",
                    "-map","[v]","-map","[a]",
                    "-c:v","libx264","-preset","fast","-crf","22",
                    "-c:a","aac","-b:a","128k","-movflags","+faststart","-y",str(merged)
                ]
            else:
                # If source has no audio stream, keep intro audio then video-only source
                merge_cmd = [
                    "-i",str(intro),"-i",str(src),
                    "-filter_complex","[0:v][1:v]concat=n=2:v=1:a=0[v];[0:a]anull[a]",
                    "-map","[v]","-map","[a]",
                    "-c:v","libx264","-preset","fast","-crf","22",
                    "-c:a","aac","-b:a","128k","-movflags","+faststart","-y",str(merged)
                ]
            r_merge = run_ffmpeg(merge_cmd, capture_output=True, text=True)
            if r_merge.returncode != 0 or not merged.exists():
                logger.warning(f"  Intro skip: gagal merge intro ({(r_merge.stderr or '')[-180:]})")
                return clip_path
            # Move merged output out of temp folder so it persists
            out_final = self.output_dir / f"{src.stem}_intro.mp4"
            shutil.copy2(str(merged), str(out_final))
            return str(out_final)

    def _collect_word_timestamps(self, clip_path: str) -> list:
        """
        Build per-word timestamps for whole clip.
        Primary: Groq Whisper words.
        Fallback: split each segment text into evenly-spaced words.
        """
        clip_dur = self._get_duration(clip_path)
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            audio_path = f.name
        try:
            run_ffmpeg([
                "-i", clip_path, "-vn", "-acodec", "mp3",
                "-ar", "16000", "-ac", "1", "-b:a", "64k", "-y", audio_path
            ], capture_output=True)
            with open(audio_path, "rb") as af:
                raw = af.read()

            transcription = self.client.audio.transcriptions.create(
                file=(Path(audio_path).name, raw),
                model="whisper-large-v3",
                response_format="verbose_json",
                language="id",
                temperature=0,
                timestamp_granularities=["word", "segment"],
            )

            words = []
            full_text = (getattr(transcription, "text", None) or "").strip()

            def _uniform_from_text(text: str) -> list:
                toks = [t for t in re.split(r"\s+", (text or "").strip()) if t]
                if not toks:
                    return []
                step = max(0.16, clip_dur / len(toks))
                out = []
                cur = 0.0
                for tok in toks:
                    st = cur
                    en = min(clip_dur, cur + step)
                    out.append({"text": tok, "start": st, "end": en})
                    cur += step
                return out
            t_words = getattr(transcription, "words", None)
            if isinstance(t_words, list) and t_words:
                for w in t_words:
                    wt = (w.get("word") if isinstance(w, dict) else getattr(w, "word", "")).strip()
                    ws = w.get("start", 0) if isinstance(w, dict) else getattr(w, "start", 0)
                    we = w.get("end", 0) if isinstance(w, dict) else getattr(w, "end", 0)
                    if wt:
                        words.append({"text": wt, "start": float(ws), "end": float(we)})
                if words:
                    cov_end = max(float(x.get("end", 0)) for x in words)
                    # If Groq word timings don't reach the tail, switch to full-duration uniform fallback.
                    if clip_dur > 1 and cov_end < clip_dur * 0.88 and full_text:
                        fallback_words = _uniform_from_text(full_text)
                        if fallback_words:
                            return fallback_words
                    return words

            segments = getattr(transcription, "segments", None) or []
            for seg in segments:
                text = (seg.get("text") if isinstance(seg, dict) else getattr(seg, "text", "")).strip()
                st = seg.get("start", 0) if isinstance(seg, dict) else getattr(seg, "start", 0)
                en = seg.get("end", 0) if isinstance(seg, dict) else getattr(seg, "end", 0)
                if not text:
                    continue
                toks = [t for t in re.split(r"\s+", text) if t]
                if not toks:
                    continue
                span = max(0.25, float(en) - float(st))
                step = span / len(toks)
                cur = float(st)
                for tok in toks:
                    nst = cur
                    nen = min(float(en), cur + step)
                    words.append({"text": tok, "start": nst, "end": nen})
                    cur += step
            if words:
                cov_end = max(float(x.get("end", 0)) for x in words)
                if clip_dur > 1 and cov_end < clip_dur * 0.88 and full_text:
                    fallback_words = _uniform_from_text(full_text)
                    if fallback_words:
                        return fallback_words
                return words
            if full_text:
                return _uniform_from_text(full_text)
            return []
        except Exception as e:
            logger.warning(f"  Caption transcribe gagal: {e}")
            return []
        finally:
            Path(audio_path).unlink(missing_ok=True)

    def _write_word_srt(self, words: list, srt_path: str, clip_dur: float = 0):
        out = []
        idx = 1
        last_i = len(words) - 1
        for i, w in enumerate(words):
            txt = (w.get("text") or "").strip()
            if not txt:
                continue
            st = float(w.get("start", 0))
            en = float(w.get("end", st + 0.22))
            if en <= st:
                en = st + 0.22
            if clip_dur > 0 and i == last_i:
                en = max(en, clip_dur)
            out.append(f"{idx}\n{self._fmt_srt_time(st)} --> {self._fmt_srt_time(en)}\n{txt}\n")
            idx += 1
        Path(srt_path).write_text("\n".join(out), encoding="utf-8")

    def _burn_word_captions(self, src_clip: str, dst_clip: str) -> str:
        clip_dur = self._get_duration(src_clip)
        words = self._collect_word_timestamps(src_clip)
        if not words:
            return src_clip
        with tempfile.NamedTemporaryFile(suffix=".srt", delete=False) as sf:
            srt_path = sf.name
        try:
            self._write_word_srt(words, srt_path, clip_dur=clip_dur)
            srt_ff = srt_path.replace("\\", "/").replace(":", "\\:")
            vf = (
                f"subtitles='{srt_ff}':"
                "force_style='FontName=Arial,FontSize=20,PrimaryColour=&H00FFFFFF,"
                "OutlineColour=&H00000000,Outline=3,BorderStyle=1,Shadow=0,Alignment=2,MarginV=70'"
            )
            r = run_ffmpeg([
                "-i", src_clip, "-vf", vf,
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-c:a", "copy", "-movflags", "+faststart", "-y", dst_clip
            ], capture_output=True, text=True)
            if r.returncode == 0 and Path(dst_clip).exists() and Path(dst_clip).stat().st_size > 1000:
                return dst_clip
            logger.warning(f"  Burn caption gagal, pakai clip tanpa caption: {r.stderr[-180:] if r.stderr else ''}")
            return src_clip
        finally:
            Path(srt_path).unlink(missing_ok=True)

    def _add_source_watermark(self, src_clip: str, dst_clip: str, source_channel: str = "") -> str:
        label = (source_channel or "").strip()
        if not label:
            return src_clip
        txt = f"SC/Source video: {label}"
        txt = self._escape_drawtext(txt[:110])
        vf = (
            "drawtext="
            f"text='{txt}':"
            "x=w-tw-26:y=h-th-26:"
            "font=Arial:fontcolor=white:fontsize=24:"
            "box=1:boxcolor=black@0.45:boxborderw=10:"
            "shadowcolor=black@0.55:shadowx=1:shadowy=1"
        )
        r = run_ffmpeg([
            "-i", src_clip, "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-c:a", "copy", "-movflags", "+faststart", "-y", dst_clip
        ], capture_output=True, text=True)
        if r.returncode == 0 and Path(dst_clip).exists() and Path(dst_clip).stat().st_size > 1000:
            return dst_clip
        logger.warning(f"  Watermark source gagal, lanjut tanpa watermark: {r.stderr[-160:] if r.stderr else ''}")
        return src_clip

    def _detect_sound_segments(self, video_path: str, start: float, duration: float) -> list:
        """
        Detect non-silent segments using ffmpeg silencedetect.
        Returns list of (start, end) relative to clip start.
        """
        if duration <= 0.2:
            return [(0.0, max(0.1, duration))]
        cmd = [
            "-ss", str(start), "-t", str(duration),
            "-i", video_path,
            "-vn",
            "-af", "silencedetect=noise=-32dB:d=0.25",
            "-f", "null", "-"
        ]
        r = run_ffmpeg(cmd, capture_output=True, text=True)
        stderr = r.stderr or ""
        silences = []
        cur_start = None
        for line in stderr.splitlines():
            line = line.strip()
            if "silence_start" in line:
                try:
                    cur_start = float(line.split("silence_start:")[-1].strip())
                except Exception:
                    cur_start = None
            elif "silence_end" in line:
                try:
                    end_part = line.split("silence_end:")[-1].strip()
                    end_val = float(end_part.split("|")[0].strip())
                except Exception:
                    end_val = None
                if cur_start is not None and end_val is not None:
                    silences.append((max(0.0, cur_start), max(0.0, end_val)))
                    cur_start = None

        silences.sort(key=lambda x: x[0])
        segments = []
        cursor = 0.0
        for s, e in silences:
            if s > cursor + 0.05:
                segments.append((cursor, min(s, duration)))
            cursor = max(cursor, e)
        if cursor < duration - 0.05:
            segments.append((cursor, duration))

        # Filter very short segments
        segments = [(s, e) for s, e in segments if (e - s) >= 0.4]
        if not segments:
            return [(0.0, duration)]
        return segments

    def _build_audio_follow_filter(self, duration: float, segments: list) -> str:
        """
        Build a smoother camera filter:
        - Idle: very slow drift (no shake)
        - During speaking segments: mostly static anchor positions
        """
        # Limit to first 6 segments to keep expression short
        segments = sorted(segments, key=lambda x: x[0])[:6]

        base = "(iw-ih*9/16)/2"
        amp_idle = "(iw-ih*9/16)*0.06"
        rng = "(iw-ih*9/16)"

        def clamp(expr: str) -> str:
            return f"max(min({expr},iw-ih*9/16),0)"

        # Default idle movement: very slow to avoid shaky look
        expr = clamp(f"{base}+{amp_idle}*sin(2*PI*t*0.045)")

        # Static anchor while speech is active (alternating left/center/right)
        anchors = [-0.16, -0.06, 0.0, 0.06, 0.16, 0.0]
        for idx, (s, e) in enumerate(reversed(segments)):
            offset = anchors[idx % len(anchors)]
            seg_expr = clamp(f"{base}+({rng}*{offset:.3f})")
            expr = f"if(between(t,{s:.3f},{e:.3f}),{seg_expr},{expr})"

        vf = (
            "scale=iw*1.06:ih*1.06:flags=lanczos,"
            f"crop=ih*9/16:ih:x='{expr}':y=0,"
            f"scale={self.target_w}:{self.target_h}:flags=lanczos,"
            "setsar=1"
        )
        return vf

    def _detect_viral_moments(self, video_path: str) -> list:
        duration    = self._get_duration(video_path)
        filename    = Path(video_path).stem
        analysis_start = 120 if duration > 120 else 0
        sample_every = max(30, (duration - analysis_start) // 10 if duration > analysis_start else 30)
        # Groq vision model limit: max 5 images
        timestamps  = list(range(int(analysis_start), int(duration), int(sample_every)))[:5]

        logger.info(f"  Sampling {len(timestamps)} frame dari {round(duration/60,1)} mnt...")

        # Build content dengan frame
        messages_content = []
        with tempfile.TemporaryDirectory() as tmpdir:
            for ts in timestamps:
                b64 = self._frame_at(video_path, ts, tmpdir)
                if b64:
                    messages_content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
                    })
                    messages_content.append({"type":"text","text":f"[Frame detik ke-{ts}]"})

        prompt = f"""Kamu ahli viral content YouTube Shorts.

Video: "{filename}" | Durasi: {round(duration/60,1)} menit
Timestamps: {timestamps}

Identifikasi TEPAT 3 segmen terbaik untuk YouTube Shorts dari frame di atas.
Setiap clip HARUS 30-75 detik (pendek, padat, langsung ke momen). Timestamp valid: {int(analysis_start)}-{int(duration)}.
Jangan pilih momen dari 0-{int(min(120,duration))} detik awal.
Wajib pilih start clip yang langsung kuat di 1-3 detik awal (hook langsung),
dan hindari intro panjang.

Kriteria viral: emosi tinggi, aksi dramatis, dialog menarik, visual unik, momen shareable.
Output harus SEO-friendly dan profesional.

Balas HANYA JSON array (tanpa teks lain):
[
  {{
    "start": 45,
    "end": 90,
    "title": "SEO title profesional #shorts",
    "description": "Paragraf pertama wajib hook kuat, lalu penjelasan ringkas.",
    "tags": ["shorts","keyword1","keyword2","trending"],
    "hook_line": "Kalimat hook paling pecah untuk 3 detik awal",
    "viral_score": 92,
    "viral_reason": "Alasan singkat mengapa viral",
    "viral_prediction": "SANGAT TINGGI"
  }}
]"""

        messages_content.append({"type":"text","text":prompt})

        try:
            resp = self.client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[{"role":"user","content":messages_content}],
                max_tokens=1500, temperature=0.4
            )
        except Exception as e:
            logger.warning(f"  Vision gagal ({e}), fallback text only...")
            resp = self.client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[{"role":"user","content":[{"type":"text","text":prompt}]}],
                max_tokens=1500, temperature=0.4
            )

        raw = resp.choices[0].message.content.strip()
        raw = raw.replace("```json","").replace("```","").strip()
        if "[" in raw: raw = raw[raw.index("["):raw.rindex("]")+1]

        try:
            moments = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("  JSON parse error, pakai fallback moments...")
            moments = self._generate_fallback_moments(duration)

        # Validasi & sort
        valid = []
        for m in moments:
            s, e = float(m.get("start",0)), float(m.get("end",0))
            seg_len = e - s
            min_start = 120 if duration > 120 else 0
            if e > s and seg_len >= 30 and seg_len <= 90 and s >= min_start and e <= duration:
                if "hook_line" not in m:
                    m["hook_line"] = ""
                valid.append(m)

        if not valid:
            logger.warning("  Tidak ada momen valid, pakai fallback...")
            valid = self._generate_fallback_moments(duration)

        valid.sort(key=lambda x: x.get("viral_score",0), reverse=True)
        return valid[:3]

    def recommend_viral_moments(self, video_path: str) -> list:
        """Return top moments metadata only (without cutting clips)."""
        return self._detect_viral_moments(video_path)

    def _generate_fallback_moments(self, duration: float) -> list:
        """Fallback 3 clip default jika AI gagal"""
        start0 = 120 if duration > 120 else 0
        usable = max(1, duration - start0)
        seg = max(30, min(70, int(usable / 4)))
        return [
            {"start":int(start0 + usable*0.10),"end":min(duration, int(start0 + usable*0.10)+seg),"title":"Hook Kuat: Momen Terbaik #1 #shorts",
             "description":"Hook: momen paling kuat langsung di awal, lalu penjelasan singkat inti video.","tags":["shorts","viral","trending"],
             "hook_line":"Dengar ini dulu, ini bagian paling ngena dari videonya.",
             "viral_score":75,"viral_reason":"Bagian awal dengan potensi hook tinggi","viral_prediction":"TINGGI"},
            {"start":int(start0 + usable*0.40),"end":min(duration, int(start0 + usable*0.40)+seg),"title":"Hook Kuat: Momen Terbaik #2 #shorts",
             "description":"Hook: bagian penting untuk bikin orang lanjut nonton.","tags":["shorts","viral","fyp"],
             "hook_line":"Bagian ini yang bikin banyak orang berhenti scroll.",
             "viral_score":70,"viral_reason":"Bagian tengah dengan konteks kuat","viral_prediction":"SEDANG"},
            {"start":int(start0 + usable*0.68),"end":min(duration, int(start0 + usable*0.68)+seg),"title":"Hook Kuat: Momen Terbaik #3 #shorts",
             "description":"Hook: penutup emosional dengan alur penjelasan ringkas.","tags":["shorts","viral","youtube"],
             "hook_line":"Akhirnya ini yang jadi inti pesannya.",
             "viral_score":65,"viral_reason":"Bagian akhir dengan payoff cerita","viral_prediction":"SEDANG"},
        ]

    def _cut_and_convert(self, video_path: str, start: float, end: float,
                          output_name: str, hook_line: str = "", intro_text: str = "",
                          source_channel: str = "") -> str:
        output_path = str(self.output_dir / f"{output_name}.mp4")
        raw_path    = str(self.output_dir / f"{output_name}_raw.mp4")
        wm_path     = str(self.output_dir / f"{output_name}_wm.mp4")
        duration    = end - start
        # Audio-follow moving camera for clip mode
        try:
            segs = self._detect_sound_segments(video_path, start, duration)
            vf_filter = self._build_audio_follow_filter(duration, segs)
        except Exception as e:
            logger.warning(f"  Audio-follow gagal: {e}")
            vf_filter = (f"crop=ih*9/16:ih,"
                         f"scale={self.target_w}:{self.target_h}:flags=lanczos,"
                         f"setsar=1")
        result = run_ffmpeg([
            "-ss", str(start), "-i", video_path,
            "-t", str(duration),
            "-vf", vf_filter,
            "-c:v","libx264","-preset","fast","-crf","22",
            "-c:a","aac","-b:a","128k",
            "-movflags","+faststart","-y", raw_path
        ], capture_output=True, text=True)

        if result.returncode != 0:
            logger.error(f"  FFmpeg error: {result.stderr[-300:]}")
            return None
        final_path = self._burn_word_captions(raw_path, output_path)
        final_path = self._prepend_freeze_intro(final_path, intro_text or hook_line or "Highlight Video")
        final_path = self._add_source_watermark(final_path, wm_path, source_channel=source_channel)
        Path(raw_path).unlink(missing_ok=True)
        size = Path(final_path).stat().st_size/1024/1024
        logger.info(f"  Clip: {final_path} ({size:.1f}MB)")
        return final_path

    def extract_viral_clips(self, video_path: str, source_channel: str = "") -> list:
        moments  = self._detect_viral_moments(video_path)
        base     = Path(video_path).stem
        clips    = []
        for i, m in enumerate(moments, 1):
            logger.info(f"  Clip {i}: {m['title']} | Score: {m.get('viral_score','?')}")
            path = self._cut_and_convert(
                video_path, m["start"], m["end"], f"clip_{i:02d}",
                hook_line=m.get("hook_line",""),
                intro_text=m.get("title",""),
                source_channel=source_channel
            )
            if path:
                clips.append({
                    "path":             path,
                    "title":            m["title"],
                    "description":      m["description"],
                    "tags":             m.get("tags",["shorts","viral"]),
                    "hook_line":        m.get("hook_line",""),
                    "viral_score":      m.get("viral_score", 0),
                    "viral_reason":     m.get("viral_reason",""),
                    "viral_prediction": m.get("viral_prediction","SEDANG"),
                    "start":            m["start"],
                    "end":              m["end"],
                })
        return clips

    def create_clip_from_range(self, video_path: str, start: float, end: float,
                               output_name: str = "clip_manual", hook_line: str = "",
                               intro_text: str = "", source_channel: str = "") -> dict:
        path = self._cut_and_convert(
            video_path, float(start), float(end), output_name,
            hook_line=hook_line, intro_text=intro_text, source_channel=source_channel
        )
        if not path:
            return {}
        return {
            "path": path,
            "start": float(start),
            "end": float(end),
        }
