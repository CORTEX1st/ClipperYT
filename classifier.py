"""
Video Classifier — Groq AI (pengganti Anthropic)
"""
import os, base64, json, tempfile
from pathlib import Path
from groq import Groq
from utils import logger
from ffmpeg_helper import run_ffmpeg, run_ffprobe


class VideoClassifier:
    def __init__(self):
        self.client = Groq(api_key=os.environ["GROQ_API_KEY"])

    def _get_info(self, video_path: str) -> dict:
        r = run_ffprobe([
            "-v","error","-show_entries","format=duration,size:stream=width,height",
            "-of","json", video_path
        ], capture_output=True, text=True)
        info = json.loads(r.stdout)
        dur  = float(info["format"]["duration"])
        s    = info.get("streams",[{}])
        return {
            "duration_seconds":  dur,
            "duration_minutes":  round(dur/60, 1),
            "width":  s[0].get("width",  0) if s else 0,
            "height": s[0].get("height", 0) if s else 0,
            "filename": Path(video_path).name
        }

    def _extract_frames(self, video_path: str, num_frames: int = 4) -> list:
        frames = []
        info   = self._get_info(video_path)
        dur    = info["duration_seconds"]
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(num_frames):
                t  = dur * (i + 1) / (num_frames + 1)
                fp = f"{tmp}/f{i}.jpg"
                run_ffmpeg(["-ss",str(t),"-i",video_path,
                            "-vframes","1","-q:v","4","-y",fp], capture_output=True)
                if Path(fp).exists():
                    frames.append(base64.standard_b64encode(open(fp,"rb").read()).decode())
        return frames

    def classify(self, video_path: str) -> dict:
        info = self._get_info(video_path)
        # Video > 60 menit langsung film
        if info["duration_minutes"] >= 60:
            result = self._ask_groq_text(info, forced="film")
            result["category"] = "film"
            return result
        frames = self._extract_frames(video_path, 4)
        return self._ask_groq_text(info, frames=frames)

    def classify_metadata_only(self, video_path: str) -> dict:
        info   = self._get_info(video_path)
        frames = self._extract_frames(video_path, 3)
        return self._ask_groq_text(info, frames=frames, metadata_only=True)

    def _ask_groq_text(self, info: dict, forced: str = None,
                       frames: list = None, metadata_only: bool = False) -> dict:
        """
        Groq vision model: kirim frame sebagai base64 image
        """
        dur_min  = info["duration_minutes"]
        filename = info["filename"]
        w, h     = info["width"], info["height"]

        if metadata_only:
            task = "Buat judul, deskripsi, dan tags YouTube yang menarik. Jangan tentukan category."
        elif forced == "film":
            task = "Video ini adalah film/vlog panjang. Buat metadata YouTube yang sesuai."
        else:
            task = ("Tentukan apakah video ini lebih cocok dijadikan:\n"
                    "- 'film': upload langsung sebagai video panjang\n"
                    "- 'clip': dipotong jadi beberapa YouTube Shorts viral\n\n"
                    "Pilih berdasarkan konten, durasi, dan potensi viral.")

        prompt = f"""Kamu adalah analis konten YouTube profesional.

Video: {filename}
Durasi: {dur_min} menit
Resolusi: {w}x{h}

{task}

Balas HANYA JSON (tanpa teks lain):
{{"category":"film","confidence":90,"title":"Judul YouTube yang menarik","description":"Deskripsi singkat menarik untuk YouTube","tags":["tag1","tag2","tag3","tag4","tag5"]}}"""

        messages_content = []

        # Tambah frame sebagai gambar jika ada (Groq vision)
        if frames:
            for i, b64 in enumerate(frames[:4]):
                messages_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
                })

        messages_content.append({"type": "text", "text": prompt})

        try:
            resp = self.client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[{"role": "user", "content": messages_content}],
                max_tokens=600,
                temperature=0.3
            )
            raw = resp.choices[0].message.content.strip()
            raw = raw.replace("```json","").replace("```","").strip()
            if "{" in raw: raw = raw[raw.index("{"):raw.rindex("}")+1]
            result = json.loads(raw)
        except Exception as e:
            logger.warning(f"  Groq vision gagal ({e}), fallback ke text only...")
            # Fallback tanpa gambar
            resp = self.client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[{"role":"user","content":[{"type":"text","text":prompt}]}],
                max_tokens=600, temperature=0.3
            )
            raw = resp.choices[0].message.content.strip()
            raw = raw.replace("```json","").replace("```","").strip()
            if "{" in raw: raw = raw[raw.index("{"):raw.rindex("}")+1]
            result = json.loads(raw)

        if forced:
            result["category"] = forced
        if metadata_only:
            result.pop("category", None)

        return result
