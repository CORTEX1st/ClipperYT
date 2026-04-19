"""
Video Editor — Multi-track: Video + Music + Subtitle + Text
All edits combined into one output pass
"""
import os, json, subprocess, tempfile, shutil, time
from pathlib import Path
from ffmpeg_helper import run_ffmpeg, run_ffprobe
from utils import logger


class VideoEditor:
    def __init__(self):
        self.base      = Path(__file__).parent
        self.out_dir   = self.base / "edited_videos"
        self.thumb_dir = self.base / "thumbnails"
        self.music_dir = self.base / "music_library"
        for d in [self.out_dir, self.thumb_dir, self.music_dir]:
            d.mkdir(exist_ok=True)

    def get_info(self, video_path: str) -> dict:
        r = run_ffprobe([
            "-v","error","-show_entries",
            "format=duration,size:stream=width,height,r_frame_rate,codec_name",
            "-of","json", video_path
        ], capture_output=True, text=True)
        if not r.stdout.strip():
            return {"duration":0,"width":1920,"height":1080,"fps":25,"size_mb":0}
        info = json.loads(r.stdout)
        dur  = float(info.get("format",{}).get("duration", 0))
        s    = info.get("streams",[{}])
        fps_raw = s[0].get("r_frame_rate","25/1") if s else "25/1"
        try:
            num, den = fps_raw.split("/")
            fps = round(int(num)/max(1,int(den)), 2)
        except:
            fps = 25
        return {
            "duration":     round(dur, 3),
            "duration_str": f"{int(dur//60)}:{int(dur%60):02d}",
            "width":  s[0].get("width",  1920) if s else 1920,
            "height": s[0].get("height", 1080) if s else 1080,
            "fps":    fps,
            "codec":  s[0].get("codec_name","h264") if s else "h264",
            "size_mb":round(int(info.get("format",{}).get("size",0))/1024/1024,1)
        }

    def get_audio_duration(self, path: str) -> float:
        r = run_ffprobe(["-v","error","-show_entries","format=duration","-of","json",path],
                        capture_output=True, text=True)
        try:
            return float(json.loads(r.stdout)["format"]["duration"])
        except:
            return 0

    def extract_thumbnails(self, video_path: str, count: int = 24) -> list:
        info     = self.get_info(video_path)
        dur      = info["duration"]
        vid_hash = abs(hash(video_path)) % 99999
        out_dir  = self.thumb_dir / str(vid_hash)
        out_dir.mkdir(exist_ok=True)
        paths = []
        for i in range(count):
            t   = dur * i / count
            out = str(out_dir / f"thumb_{i:03d}.jpg")
            if not Path(out).exists():
                run_ffmpeg(["-ss",str(t),"-i",video_path,
                            "-vframes","1","-vf","scale=160:90",
                            "-q:v","5","-y",out], capture_output=True)
            if Path(out).exists():
                paths.append({"index":i,"time":round(t,2),"path":out})
        return paths

    def apply_edits(self, video_path: str, edits: dict, output_name: str) -> str:
        """
        Apply ALL edits in one pipeline:
        edits = {
          "cuts": [{"start":0,"end":5},...],   # segmen DIAMBIL
          "format": "vertical"|"horizontal"|None,
          "texts": [{"text","start","end","position","fontsize","color"}],
          "subtitles": [{"text","start","end"}],
          "music": {"path":"...","volume":0.3,"start_offset":0,"fade_in":2,"fade_out":2},
        }
        """
        info   = self.get_info(video_path)
        dur    = info["duration"]
        w, h   = info["width"], info["height"]
        out    = str(self.out_dir / f"{output_name}.mp4")
        cuts   = edits.get("cuts", [])
        fmt    = edits.get("format")
        texts  = edits.get("texts", [])
        subs   = edits.get("subtitles", [])
        music  = edits.get("music")

        # ── Step 1: Cut & concat segments ───────────────────────
        if cuts:
            tmp_dir  = Path(tempfile.mkdtemp())
            segments = []
            cumtime  = 0.0
            time_map = []

            for idx, cut in enumerate(cuts):
                s  = max(0, float(cut["start"]))
                e  = min(dur, float(cut["end"]))
                if e - s < 0.05: continue
                seg = str(tmp_dir / f"seg_{idx:03d}.mp4")
                r   = run_ffmpeg([
                    "-ss",str(s),"-i",video_path,
                    "-t",str(e-s),
                    "-c:v","libx264","-preset","fast","-crf","22",
                    "-c:a","aac","-b:a","128k",
                    "-avoid_negative_ts","make_zero","-y",seg
                ], capture_output=True, text=True)
                if r.returncode==0 and Path(seg).exists() and Path(seg).stat().st_size>1000:
                    segments.append(seg)
                    time_map.append((s, e, cumtime))
                    cumtime += (e - s)

            if not segments:
                raise RuntimeError("Tidak ada segmen valid!")

            concat_txt = str(tmp_dir/"concat.txt")
            with open(concat_txt,"w") as f:
                for seg in segments: f.write(f"file '{seg.replace(chr(92),'/')}'\n")
            joined = str(tmp_dir/"joined.mp4")
            run_ffmpeg(["-f","concat","-safe","0","-i",concat_txt,"-c","copy","-y",joined], capture_output=True)

            # Remap subtitle timing
            if subs and time_map:
                new_subs = []
                for sub in subs:
                    for (old_s, old_e, new_s_offset) in time_map:
                        if old_s <= sub["start"] < old_e:
                            offset = sub["start"] - old_s
                            new_subs.append({"text":sub["text"],
                                             "start":new_s_offset+offset,
                                             "end":new_s_offset+offset+(sub["end"]-sub["start"])})
                            break
                subs = new_subs

            working = joined
        else:
            tmp_dir = None
            working = video_path

        new_info = self.get_info(working)
        nw, nh   = new_info["width"], new_info["height"]
        new_dur  = new_info["duration"]

        # ── Step 2: Build video filter ───────────────────────────
        vf_parts = []

        if fmt == "vertical":
            tw, th = 1080, 1920
            vf_parts.append(f"crop=ih*9/16:ih,scale={tw}:{th}:flags=lanczos,setsar=1")
            nw, nh = tw, th
        elif fmt == "horizontal":
            tw, th = 1920, 1080
            vf_parts.append(f"scale={tw}:{th}:force_original_aspect_ratio=decrease,pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:black,setsar=1")
            nw, nh = tw, th

        for t in texts:
            txt = t.get("text","").strip()
            if not txt: continue
            txt  = txt.replace("'","").replace(":","")[:100]
            ts   = float(t.get("start",0))
            te   = float(t.get("end", new_dur))
            fs   = int(t.get("fontsize", max(28,int(nh*0.032))))
            col  = t.get("color","white")
            pos  = t.get("position","bottom")
            y    = f"h-text_h-{int(nh*0.07)}" if pos=="bottom" else (f"{int(nh*0.06)}" if pos=="top" else "(h-text_h)/2")
            vf_parts.append(
                f"drawtext=text='{txt}':fontsize={fs}:fontcolor={col}"
                f":box=1:boxcolor=black@0.6:boxborderw=10"
                f":x=(w-text_w)/2:y={y}:enable='between(t,{ts},{te})'"
            )

        for sub in subs:
            txt = sub.get("text","").strip()
            if not txt: continue
            txt  = txt.replace("'","").replace(":","").replace("\n"," ")[:100]
            ts   = float(sub.get("start",0))
            te   = float(sub.get("end", ts+2))
            fs   = max(22, int(nh*0.026))
            vf_parts.append(
                f"drawtext=text='{txt}':fontsize={fs}:fontcolor=white"
                f":box=1:boxcolor=black@0.7:boxborderw=8"
                f":x=(w-text_w)/2:y=h-text_h-{int(nh*0.05)}"
                f":enable='between(t,{ts},{te})'"
            )

        vf = ",".join(vf_parts) if vf_parts else "null"

        # ── Step 3: Handle music mixing ──────────────────────────
        has_music = music and music.get("path") and Path(music["path"]).exists()
        tmp_music = None

        if has_music:
            music_path   = music["path"]
            music_vol    = float(music.get("volume", 0.3))
            music_offset = float(music.get("start_offset", 0))
            fade_in      = float(music.get("fade_in", 2))
            fade_out     = float(music.get("fade_out", 2))
            music_start  = float(music.get("music_start", 0))  # where in music file to start
            music_end_t  = music.get("music_end")

            # Prepare music: trim, loop/pad to video duration, volume
            tmp_music = str(Path(tempfile.mkdtemp()) / "music_processed.aac")
            music_dur = self.get_audio_duration(music_path)

            af_parts = []
            # trim music if start/end specified
            music_trim_cmd = ["-i", music_path]
            if music_start > 0:
                music_trim_cmd = ["-ss", str(music_start)] + music_trim_cmd
            if music_end_t:
                music_trim_cmd += ["-t", str(float(music_end_t) - music_start)]

            # Fade + volume
            af_parts.append(f"volume={music_vol}")
            if fade_in > 0:
                af_parts.append(f"afade=t=in:st=0:d={fade_in}")
            if fade_out > 0:
                fade_start = max(0, new_dur - music_offset - fade_out)
                af_parts.append(f"afade=t=out:st={fade_start}:d={fade_out}")
            af_parts.append(f"apad=pad_dur={new_dur + 5}")
            af_parts.append(f"atrim=end={new_dur + music_offset + 1}")

            af = ",".join(af_parts)
            run_ffmpeg(music_trim_cmd + [
                "-af", af, "-c:a","aac","-b:a","128k","-y", tmp_music
            ], capture_output=True)

        # ── Step 4: Final encode ─────────────────────────────────
        if has_music and tmp_music and Path(tmp_music).exists():
            # Mix original audio + music
            cmd = ["-i", working, "-i", tmp_music,
                   "-filter_complex",
                   f"[0:v]{vf}[vout];"
                   f"[0:a]volume=1.0[orig];"
                   f"[orig][1:a]amix=inputs=2:duration=first:dropout_transition=2[aout]",
                   "-map","[vout]","-map","[aout]",
                   "-c:v","libx264","-preset","fast","-crf","20",
                   "-c:a","aac","-b:a","192k",
                   "-movflags","+faststart","-y",out]
        else:
            cmd = ["-i", working,
                   "-vf", vf,
                   "-c:v","libx264","-preset","fast","-crf","20",
                   "-c:a","aac","-b:a","128k",
                   "-movflags","+faststart","-y",out]

        result = run_ffmpeg(cmd, capture_output=True, text=True)

        # Cleanup
        if tmp_dir:   shutil.rmtree(str(tmp_dir), ignore_errors=True)
        if tmp_music: shutil.rmtree(str(Path(tmp_music).parent), ignore_errors=True)

        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg gagal: {result.stderr[-500:]}")

        size = Path(out).stat().st_size/1024/1024
        logger.info(f"  Edit selesai: {out} ({size:.1f}MB)")
        return out

    def generate_subtitles_groq(self, video_path: str, progress_cb=None) -> list:
        from groq import Groq
        client = Groq(api_key=os.environ["GROQ_API_KEY"])
        if progress_cb: progress_cb(10, "Ekstrak audio...")
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            audio_path = f.name
        run_ffmpeg(["-i",video_path,"-vn","-acodec","mp3","-ar","16000",
                    "-ac","1","-b:a","64k","-y",audio_path], capture_output=True)
        if progress_cb: progress_cb(40, "Transcribing dengan Groq Whisper...")
        try:
            with open(audio_path,"rb") as af:
                transcription = client.audio.transcriptions.create(
                    file=(Path(audio_path).name, af.read()),
                    model="whisper-large-v3",
                    response_format="verbose_json",
                    language="id"
                )
            subs = []
            for seg in getattr(transcription,"segments",None) or []:
                text = (seg.get("text") if isinstance(seg,dict) else getattr(seg,"text","")).strip()
                st   = seg.get("start",0) if isinstance(seg,dict) else getattr(seg,"start",0)
                en   = seg.get("end",0)   if isinstance(seg,dict) else getattr(seg,"end",0)
                if text: subs.append({"text":text,"start":round(st,2),"end":round(en,2)})
            if progress_cb: progress_cb(90, f"{len(subs)} subtitle ditemukan")
            return subs
        finally:
            Path(audio_path).unlink(missing_ok=True)