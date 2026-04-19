"""
YouTube AI Agent — Flask Backend
Includes: Upload, Editor, AI Video Generator
"""
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os, json, threading, uuid, time
from pathlib import Path
from datetime import datetime
import subprocess
from urllib.parse import urlparse

BASE_DIR = Path(__file__).parent.resolve()
UI_DIR   = BASE_DIR / "ui"
UI_DIR.mkdir(exist_ok=True)

app = Flask(__name__, static_folder=str(UI_DIR), static_url_path="")
CORS(app)

UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "output_clips"
JOBS_FILE  = BASE_DIR / "jobs.json"
ENV_FILE   = BASE_DIR / ".env.json"

for d in [UPLOAD_DIR, OUTPUT_DIR, BASE_DIR/"edited_videos", BASE_DIR/"generated_videos", BASE_DIR/"generated_images", BASE_DIR/"logs"]:
    d.mkdir(exist_ok=True)

def load_env():
    if ENV_FILE.exists():
        return json.loads(ENV_FILE.read_text())
    return {}

def save_env(data):
    ENV_FILE.write_text(json.dumps(data, indent=2))

def load_jobs():
    if JOBS_FILE.exists():
        return json.loads(JOBS_FILE.read_text())
    return []

def save_jobs(jobs):
    JOBS_FILE.write_text(json.dumps(jobs, indent=2))

def update_job(job_id, **kwargs):
    jobs = load_jobs()
    for job in jobs:
        if job["id"] == job_id:
            job.update(kwargs)
    save_jobs(jobs)

def log_job(job_id, msg, progress=None):
    jobs = load_jobs()
    for job in jobs:
        if job["id"] == job_id:
            job["log"].append({"time": datetime.now().strftime("%H:%M:%S"), "msg": msg})
            if progress is not None:
                job["progress"] = progress
    save_jobs(jobs)

def probe_video_meta(video_path: Path):
    try:
        from ffmpeg_helper import run_ffprobe
        r = run_ffprobe(
            ["-v","error","-show_entries","format=duration","-of","json",str(video_path)],
            capture_output=True, text=True, timeout=60
        )
        dur = float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        dur = 0
    return {
        "ok": True,
        "filename": video_path.name,
        "path": str(video_path),
        "duration": dur,
        "duration_str": f"{int(dur//60)}m {int(dur%60)}s",
        "size_mb": round(video_path.stat().st_size/1024/1024, 1),
    }

def is_valid_youtube_url(url: str) -> bool:
    try:
        p = urlparse(url)
        host = (p.netloc or "").lower()
        return any(h in host for h in ["youtube.com", "youtu.be", "m.youtube.com"])
    except Exception:
        return False

def score_from_youtube_meta(meta: dict) -> int:
    views = float(meta.get("view_count") or 0)
    likes = float(meta.get("like_count") or 0)
    comments = float(meta.get("comment_count") or 0)
    duration = float(meta.get("duration") or 0)
    engagement = ((likes * 2.0) + (comments * 3.5)) / max(1.0, views)
    norm_views = min(1.0, views / 300000.0)
    norm_eng = min(1.0, engagement * 10.0)
    duration_fit = 1.0 if 25 <= duration <= 60 else 0.6 if duration <= 120 else 0.4
    score = int(45 + (norm_views * 25) + (norm_eng * 25) + (duration_fit * 10))
    return max(1, min(score, 99))

def fallback_recommendations(video_path: str, mode: str = "clip") -> list:
    try:
        from ffmpeg_helper import run_ffprobe
        r = run_ffprobe(["-v","error","-show_entries","format=duration","-of","json",str(video_path)],
                        capture_output=True, text=True, timeout=30)
        duration = float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        duration = 120.0
    if mode == "film":
        return [{
            "title": "Upload Video Utuh",
            "description": "Rekomendasi upload full video.",
            "tags": ["youtube", "video"],
            "viral_score": 65,
            "viral_prediction": "SEDANG",
            "viral_reason": "Skor default tanpa analisis AI",
            "start": 0,
            "end": duration,
        }]
    start0 = 120 if duration > 120 else 0
    usable = max(1, duration - start0)
    seg = max(30, min(70, int(usable / 4)))
    return [
        {"start": max(start0, int(start0 + usable*0.15)), "end": min(duration, int(start0 + usable*0.15)+seg),
         "title":"Hook Kuat: Rekomendasi Viral #1 #shorts", "description":"Hook kuat di awal lalu penjelasan inti konten.",
         "tags":["shorts","viral","trending"], "hook_line":"Ini bagian yang paling bikin orang berhenti scroll.",
         "viral_score":74, "viral_reason":"Pacing awal video", "viral_prediction":"TINGGI"},
        {"start": max(start0, int(start0 + usable*0.40)), "end": min(duration, int(start0 + usable*0.40)+seg),
         "title":"Hook Kuat: Rekomendasi Viral #2 #shorts", "description":"Hook cepat lalu konteks yang lebih lengkap.",
         "tags":["shorts","viral","fyp"], "hook_line":"Bagian ini biasanya jadi momen paling diingat.",
         "viral_score":69, "viral_reason":"Highlight tengah", "viral_prediction":"SEDANG"},
        {"start": max(start0, int(start0 + usable*0.68)), "end": min(duration, int(start0 + usable*0.68)+seg),
         "title":"Hook Kuat: Rekomendasi Viral #3 #shorts", "description":"Hook emosional dan penjelasan penutup yang jelas.",
         "tags":["shorts","viral","youtube"], "hook_line":"Penutup ini biasanya bikin orang replay.",
         "viral_score":64, "viral_reason":"Momentum akhir", "viral_prediction":"SEDANG"},
    ]

def build_shorts_seo_metadata(base_title: str, base_desc: str, base_tags, hook_line: str = "", viral_reason: str = ""):
    import re
    title = (base_title or "Shorts Viral").strip()
    desc = (base_desc or "").strip()
    hook = (hook_line or "").strip()

    # Clean title: keep readable, no spammy punctuation
    title = re.sub(r"\s+", " ", title)
    title = re.sub(r"[^\w\s#\-\|]", "", title, flags=re.UNICODE).strip()
    if "#shorts" not in title.lower():
        title = f"{title} #shorts"
    title = title[:100]

    tags = base_tags or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    clean_tags = []
    for t in tags:
        x = str(t).lower().replace("#", "").strip()
        x = re.sub(r"[^a-z0-9_-]", "", x)
        if x and x not in clean_tags:
            clean_tags.append(x)
    for t in ["shorts", "viral", "trending", "youtube"]:
        if t not in clean_tags:
            clean_tags.append(t)
    clean_tags = clean_tags[:10]

    # Description: short, hook-first, not crowded
    lines = []
    if hook:
        lines.append(hook[:140])
    if desc:
        lines.append(desc[:220])
    elif viral_reason:
        lines.append(f"Inti momen: {viral_reason[:180]}")
    lines.append("Follow untuk momen viral berikutnya.")
    hashtags = " ".join([f"#{t}" for t in clean_tags[:5]])
    lines.append(hashtags)
    final_desc = "\n\n".join([ln for ln in lines if ln]).strip()[:5000]
    return title, final_desc, clean_tags

# ── Static ───────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(str(UI_DIR), "index.html")

# ── Settings ─────────────────────────────────────────────────────
@app.route("/api/settings", methods=["GET"])
def get_settings():
    env = load_env()
    masked = {}
    for k, v in env.items():
        if any(x in k.lower() for x in ["key","secret","token"]):
            masked[k] = ("*"*20 + v[-4:]) if len(v) > 4 else "****"
        else:
            masked[k] = v
    masked["_saved"] = bool(env)
    return jsonify(masked)

@app.route("/api/settings", methods=["POST"])
def save_settings():
    data = request.json
    if not data:
        return jsonify({"error": "No data"}), 400
    env  = load_env()
    for k, v in data.items():
        if v and not str(v).startswith("*"):
            env[k] = v
    save_env(env)
    for k, v in env.items():
        os.environ[k] = str(v)
    return jsonify({"ok": True})

@app.route("/api/settings/key", methods=["POST"])
def save_single_key():
    """Simpan satu API key — tidak perlu GET dulu"""
    data = request.json
    if not data or "key" not in data or "value" not in data:
        return jsonify({"error": "Butuh field 'key' dan 'value'"}), 400
    key   = data["key"].strip()
    value = data["value"].strip()
    if not key or not value:
        return jsonify({"error": "Key/value tidak boleh kosong"}), 400
    env       = load_env()
    env[key]  = value
    save_env(env)
    os.environ[key] = value
    return jsonify({"ok": True, "key": key})

# ── Upload ────────────────────────────────────────────────────────
@app.route("/api/upload", methods=["POST"])
def upload_video():
    if "file" not in request.files:
        return jsonify({"error": "Tidak ada file"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Nama file kosong"}), 400
    safe_name = f.filename.replace(" ", "_")
    save_path = UPLOAD_DIR / safe_name
    f.save(str(save_path))
    return jsonify(probe_video_meta(save_path))

@app.route("/api/upload-youtube", methods=["POST"])
def upload_from_youtube():
    data = request.json or {}
    url = str(data.get("url","")).strip()
    if not url:
        return jsonify({"error":"URL YouTube kosong"}), 400
    if not is_valid_youtube_url(url):
        return jsonify({"error":"URL harus dari YouTube (youtube.com / youtu.be)"}), 400

    yt_meta = {}
    try:
        proc_meta = subprocess.run(
            ["python","-m","yt_dlp","--skip-download","--dump-single-json",url],
            capture_output=True, text=True, cwd=str(BASE_DIR)
        )
        if proc_meta.returncode == 0 and proc_meta.stdout.strip():
            yt_meta = json.loads(proc_meta.stdout)
    except Exception:
        yt_meta = {}

    out_tmpl = str(UPLOAD_DIR / "yt_%(title).80s_%(id)s.%(ext)s")
    cmd = [
        "python", "-m", "yt_dlp",
        "--no-playlist",
        "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--restrict-filenames",
        "-o", out_tmpl,
        url
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(BASE_DIR))
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        if "No module named yt_dlp" in stderr:
            return jsonify({"error":"yt-dlp belum terpasang. Jalankan: pip install yt-dlp"}), 500
        return jsonify({"error":f"Gagal download dari YouTube: {stderr[-220:]}"}), 500

    # Cari file terbaru hasil download
    candidates = sorted(UPLOAD_DIR.glob("yt_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return jsonify({"error":"Video tidak ditemukan setelah download"}), 500
    meta = probe_video_meta(candidates[0])
    if yt_meta:
        meta["source_url"] = url
        meta["yt_meta"] = {
            "title": yt_meta.get("title",""),
            "description": yt_meta.get("description",""),
            "uploader": yt_meta.get("uploader",""),
            "view_count": yt_meta.get("view_count",0),
            "like_count": yt_meta.get("like_count",0),
            "comment_count": yt_meta.get("comment_count",0),
            "tags": yt_meta.get("tags",[])[:10],
            "duration": yt_meta.get("duration",0),
            "upload_date": yt_meta.get("upload_date",""),
            "viral_score": score_from_youtube_meta(yt_meta),
        }
    return jsonify(meta)

@app.route("/api/recommendations", methods=["POST"])
def get_recommendations():
    data = request.json or {}
    fpath = str(data.get("filepath","")).replace("/", os.sep).replace("\\\\", os.sep)
    mode = str(data.get("mode","clip")).strip() or "clip"
    source_url = str(data.get("source_url","")).strip()
    if not fpath or not Path(fpath).exists():
        return jsonify({"error":"File video tidak ditemukan"}), 404

    env = load_env()
    for k, v in env.items():
        os.environ[k] = str(v)

    recs = []
    try:
        if mode == "film":
            recs = fallback_recommendations(fpath, mode="film")
        else:
            import sys; sys.path.insert(0, str(BASE_DIR))
            from clip_extractor import ClipExtractor
            recs = ClipExtractor().recommend_viral_moments(fpath)
    except Exception:
        recs = fallback_recommendations(fpath, mode=mode)

    # Optional scrape metadata from YouTube URL as additional signal
    yt_meta = {}
    if source_url and is_valid_youtube_url(source_url):
        try:
            proc_meta = subprocess.run(
                ["python","-m","yt_dlp","--skip-download","--dump-single-json",source_url],
                capture_output=True, text=True, cwd=str(BASE_DIR)
            )
            if proc_meta.returncode == 0 and proc_meta.stdout.strip():
                raw = json.loads(proc_meta.stdout)
                yt_meta = {
                    "title": raw.get("title",""),
                    "description": raw.get("description",""),
                    "uploader": raw.get("uploader",""),
                    "view_count": raw.get("view_count",0),
                    "like_count": raw.get("like_count",0),
                    "comment_count": raw.get("comment_count",0),
                    "tags": (raw.get("tags") or [])[:10],
                    "viral_score": score_from_youtube_meta(raw),
                }
        except Exception:
            yt_meta = {}

    return jsonify({"ok": True, "recommendations": recs, "yt_meta": yt_meta})

# ── Jobs ──────────────────────────────────────────────────────────
@app.route("/api/jobs", methods=["POST"])
def create_job():
    data   = request.json
    job_id = str(uuid.uuid4())[:8]
    job = {
        "id": job_id, "filename": data["filename"], "filepath": data["filepath"],
        "mode": data["mode"], "title": data.get("title",""), "description": data.get("description",""),
        "tags": data.get("tags",""), "privacy": data.get("privacy","public"),
        "schedule": data.get("schedule"), "schedule_type": data.get("schedule_type","now"),
        "best_slot": data.get("best_slot",""), "status": "queued", "progress": 0,
        "log": [], "created_at": datetime.now().isoformat(), "clips": [],
        "clip_candidates": [], "youtube_url": None,
        "preselected_recommendation": data.get("preselected_recommendation"),
        "preselected_recommendations": data.get("preselected_recommendations", []),
    }
    jobs = load_jobs(); jobs.append(job); save_jobs(jobs)
    threading.Thread(target=run_job, args=(job_id,), daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id})

@app.route("/api/jobs", methods=["GET"])
def list_jobs():
    return jsonify(load_jobs())

@app.route("/api/jobs/<job_id>", methods=["GET"])
def get_job(job_id):
    job = next((j for j in load_jobs() if j["id"] == job_id), None)
    return jsonify(job) if job else (jsonify({"error":"Not found"}), 404)

@app.route("/api/jobs/<job_id>", methods=["DELETE"])
def delete_job_route(job_id):
    save_jobs([j for j in load_jobs() if j["id"] != job_id])
    return jsonify({"ok": True})

@app.route("/api/jobs/<job_id>/upload-clips", methods=["POST"])
def upload_selected_clips(job_id):
    data     = request.json
    selected = data.get("selected_indices", [])
    job = next((j for j in load_jobs() if j["id"] == job_id), None)
    if not job:
        return jsonify({"error": "Job tidak ditemukan"}), 404
    threading.Thread(target=upload_clips_job, args=(job_id, selected), daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/best-times", methods=["GET"])
def best_times():
    return jsonify([
        {"day":"Senin","time":"14:00","score":78,"reason":"Sore hari mulai santai"},
        {"day":"Selasa","time":"14:00","score":82,"reason":"Engagement tertinggi weekday"},
        {"day":"Rabu","time":"15:00","score":85,"reason":"Mid-week peak views"},
        {"day":"Kamis","time":"12:00","score":80,"reason":"Istirahat siang populer"},
        {"day":"Jumat","time":"17:00","score":88,"reason":"Pre-weekend traffic spike"},
        {"day":"Sabtu","time":"10:00","score":92,"reason":"Weekend morning prime time"},
        {"day":"Minggu","time":"11:00","score":90,"reason":"Sunday watch session peak"},
    ])


# ── Video Editor Routes (New Pipeline) ───────────────────────────
@app.route("/api/editor/list", methods=["GET"])
def editor_list():
    files = []
    for d in ["uploads","output_clips","edited_videos","generated_videos"]:
        dp = BASE_DIR / d
        if dp.exists():
            for f in sorted(dp.glob("*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True):
                files.append({"name":f.name,"path":str(f),"folder":d,
                               "size_mb":round(f.stat().st_size/1024/1024,1),
                               "modified":f.stat().st_mtime})
    return jsonify(files)

@app.route("/api/editor/info", methods=["POST"])
def editor_info():
    fpath = request.json.get("filepath","").replace("/", os.sep).replace("\\\\", os.sep)
    if not Path(fpath).exists():
        return jsonify({"error":"File tidak ditemukan"}), 404
    import sys; sys.path.insert(0, str(BASE_DIR))
    from video_editor import VideoEditor
    return jsonify(VideoEditor().get_info(fpath))

@app.route("/api/editor/thumbnails", methods=["POST"])
def editor_thumbnails():
    data  = request.json
    fpath = data.get("filepath","").strip()
    count = int(data.get("count", 20))
    # Normalize path (handle Windows backslash)
    fpath = fpath.replace("/", os.sep).replace("\\", os.sep)
    if not Path(fpath).exists():
        # Try searching in known folders
        fname = Path(fpath).name
        for d in ["uploads","output_clips","edited_videos","generated_videos"]:
            candidate = BASE_DIR / d / fname
            if candidate.exists():
                fpath = str(candidate)
                break
        else:
            return jsonify({"error": f"File tidak ditemukan: {fpath}"}), 404
    import sys; sys.path.insert(0, str(BASE_DIR))
    from video_editor import VideoEditor
    try:
        thumbs = VideoEditor().extract_thumbnails(fpath, count)
        import base64
        result = []
        for t in thumbs:
            if Path(t["path"]).exists():
                b64 = base64.b64encode(open(t["path"],"rb").read()).decode()
                result.append({"index":t["index"],"time":t["time"],"b64":b64})
        return jsonify(result)
    except Exception as e:
        import traceback
        return jsonify({"error":str(e), "detail":traceback.format_exc()[-300:]}), 500

@app.route("/api/editor/apply", methods=["POST"])
def editor_apply():
    """Apply all edits in one pass"""
    data   = request.json
    fpath  = data.get("filepath","").replace("/", os.sep).replace("\\\\", os.sep)
    edits  = data.get("edits", {})
    name   = data.get("output_name", f"edited_{int(time.time())}")
    if not Path(fpath).exists():
        return jsonify({"error":"File tidak ditemukan"}), 404
    try:
        import sys; sys.path.insert(0, str(BASE_DIR))
        from video_editor import VideoEditor
        out  = VideoEditor().apply_edits(fpath, edits, name)
        size = Path(out).stat().st_size/1024/1024
        return jsonify({"ok":True,"path":out,"size_mb":round(size,1)})
    except Exception as e:
        import traceback
        return jsonify({"error":str(e),"detail":traceback.format_exc()[-300:]}), 500

@app.route("/api/editor/transcribe", methods=["POST"])
def editor_transcribe():
    """Generate subtitles using Groq Whisper"""
    data   = request.json
    fpath  = data.get("filepath","")
    session_id = str(uuid.uuid4())[:8]
    env = load_env()
    for k,v in env.items(): os.environ[k] = v
    if not Path(fpath).exists():
        return jsonify({"error":"File tidak ditemukan"}), 404

    TRANSCRIBE_SESSIONS[session_id] = {"status":"running","progress":0,"log":[],"subtitles":[]}

    def run():
        def cb(pct, msg):
            TRANSCRIBE_SESSIONS[session_id]["progress"] = pct
            TRANSCRIBE_SESSIONS[session_id]["log"].append(msg)
        try:
            import sys; sys.path.insert(0, str(BASE_DIR))
            from video_editor import VideoEditor
            subs = VideoEditor().generate_subtitles_groq(fpath, cb)
            TRANSCRIBE_SESSIONS[session_id].update({"status":"done","subtitles":subs,"progress":100})
        except Exception as e:
            TRANSCRIBE_SESSIONS[session_id].update({"status":"error","log":[str(e)]})

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"ok":True,"session_id":session_id})

@app.route("/api/editor/transcribe/<session_id>", methods=["GET"])
def transcribe_status(session_id):
    s = TRANSCRIBE_SESSIONS.get(session_id)
    return jsonify(s) if s else (jsonify({"error":"Not found"}), 404)

# ── Serve edited/generated videos as static ──────────────────────
@app.route("/videos/<path:filename>")
def serve_video(filename):
    from flask import send_from_directory
    for d in ["edited_videos","generated_videos","output_clips","uploads"]:
        dp = BASE_DIR / d
        if (dp / filename).exists():
            return send_from_directory(str(dp), filename)
    return "Not found", 404

# ── AI Video Generator Routes ─────────────────────────────────────
AI_GEN_SESSIONS = {}
TRANSCRIBE_SESSIONS = {}

@app.route("/api/aigen/concepts", methods=["POST"])
def aigen_concepts():
    prompt = request.json.get("prompt","")
    if not prompt:
        return jsonify({"error":"Prompt kosong"}), 400
    env = load_env()
    for k,v in env.items(): os.environ[k] = v
    try:
        import sys; sys.path.insert(0, str(BASE_DIR))
        from ai_video_generator import AIVideoGenerator
        concepts = AIVideoGenerator().generate_concepts(prompt)
        return jsonify({"ok":True,"concepts":concepts})
    except Exception as e:
        return jsonify({"error":str(e)}), 500

@app.route("/api/aigen/storyboard", methods=["POST"])
def aigen_storyboard():
    data    = request.json
    concept = data.get("concept",{})
    fmt     = data.get("format","vertical")
    env = load_env()
    for k,v in env.items(): os.environ[k] = v
    try:
        import sys; sys.path.insert(0, str(BASE_DIR))
        from ai_video_generator import AIVideoGenerator
        sb = AIVideoGenerator().generate_storyboard(concept, fmt)
        return jsonify({"ok":True,"storyboard":sb})
    except Exception as e:
        return jsonify({"error":str(e)}), 500

@app.route("/api/aigen/render", methods=["POST"])
def aigen_render():
    data        = request.json
    prompt      = data.get("prompt","")
    concept_idx = int(data.get("concept_idx",0))
    fmt         = data.get("format","vertical")
    storyboard  = data.get("storyboard",None)
    one_image_mode = bool(data.get("one_image_mode", False))
    image_path_raw = data.get("image_path","")
    image_path = image_path_raw.replace("/", os.sep).replace("\\\\", os.sep) if image_path_raw else None
    session_id  = str(uuid.uuid4())[:8]
    env = load_env()
    for k,v in env.items(): os.environ[k] = v
    AI_GEN_SESSIONS[session_id] = {"status":"running","progress":0,"log":[],"result":None}

    def cb(pct, msg):
        AI_GEN_SESSIONS[session_id]["progress"] = pct
        AI_GEN_SESSIONS[session_id]["log"].append({"time":datetime.now().strftime("%H:%M:%S"),"msg":msg})

    def run():
        try:
            import sys; sys.path.insert(0, str(BASE_DIR))
            import uuid as _u
            from ai_video_generator import AIVideoGenerator
            gen = AIVideoGenerator()
            if storyboard:
                cb(20,"Render dari storyboard...")
                dub_l = data.get("dub_lang","id")
                dub_on= data.get("with_dubbing",False)
                vp = gen.render_video(
                    storyboard, _u.uuid4().hex[:8], dub_on, dub_l, cb,
                    one_image_mode=one_image_mode, image_path=image_path
                )
                result = {"video_path":vp,"storyboard":storyboard,
                          "title":storyboard.get("title","Generated Video"),
                          "description":storyboard.get("description",""),
                          "tags":storyboard.get("tags",[])}
            else:
                dub      = data.get("with_dubbing", True)
                dub_lang = data.get("dub_lang", "id")
                result   = gen.full_pipeline(
                    prompt, concept_idx, fmt, dub, dub_lang, cb,
                    one_image_mode=one_image_mode, image_path=image_path
                )
            AI_GEN_SESSIONS[session_id].update({"status":"done","result":result,"progress":100})
        except Exception as e:
            import traceback
            AI_GEN_SESSIONS[session_id]["status"] = "error"
            AI_GEN_SESSIONS[session_id]["log"].append({"time":"--","msg":f"Error: {e}"})
            AI_GEN_SESSIONS[session_id]["log"].append({"time":"--","msg":traceback.format_exc()[-400:]})

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"ok":True,"session_id":session_id})

@app.route("/api/aigen/status/<session_id>", methods=["GET"])
def aigen_status(session_id):
    s = AI_GEN_SESSIONS.get(session_id)
    return jsonify(s) if s else (jsonify({"error":"Not found"}), 404)

@app.route("/api/aigen/upload-image", methods=["POST"])
def aigen_upload_image():
    if "file" not in request.files:
        return jsonify({"error":"Tidak ada file"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error":"Nama file kosong"}), 400
    safe_name = f.filename.replace(" ", "_")
    img_dir = BASE_DIR / "uploads"
    img_dir.mkdir(exist_ok=True)
    save_path = img_dir / f"img_{int(time.time())}_{safe_name}"
    f.save(str(save_path))
    return jsonify({"ok":True,"filename":save_path.name,"path":str(save_path)})

# ── Job Runner ────────────────────────────────────────────────────
def run_job(job_id):
    job = next((j for j in load_jobs() if j["id"] == job_id), None)
    if not job: return
    env = load_env()
    for k,v in env.items(): os.environ[k] = v
    available = list(env.keys())
    log_job(job_id, f"ENV tersedia: {available}")
    if "GROQ_API_KEY" not in os.environ:
        log_job(job_id, "ERROR: GROQ_API_KEY tidak ditemukan!")
        update_job(job_id, status="error"); return
    try:
        import traceback
        schedule_type = job.get("schedule_type","now")
        if schedule_type in ["best","custom"] and job.get("schedule"):
            target = datetime.fromisoformat(job["schedule"])
            wait   = (target - datetime.now()).total_seconds()
            if wait > 0:
                update_job(job_id, status="scheduled")
                log_job(job_id, f"Dijadwalkan: {target.strftime('%d/%m/%Y %H:%M')}", 0)
                while wait > 0:
                    time.sleep(min(60, wait))
                    wait = (target - datetime.now()).total_seconds()
                    if wait > 0:
                        log_job(job_id, f"Menunggu... {int(wait//60)} menit lagi")
        update_job(job_id, status="running", progress=5)
        log_job(job_id, "AI Agent dimulai...", 5)
        import sys; sys.path.insert(0, str(BASE_DIR))
        from classifier import VideoClassifier
        from clip_extractor import ClipExtractor
        from uploader import YouTubeUploader
        video_path = job["filepath"]; mode = job["mode"]
        log_job(job_id, "Menganalisis video dengan Groq AI...", 10)
        classifier = VideoClassifier()
        if mode == "auto":
            result   = classifier.classify(video_path)
            category = result["category"]
            log_job(job_id, f"Kategori: {category.upper()} ({result.get('confidence',0)}%)", 25)
        else:
            category = mode
            result   = classifier.classify_metadata_only(video_path)
            log_job(job_id, f"Mode manual: {category.upper()}", 25)
        title       = job["title"]       or result.get("title", Path(video_path).stem)
        description = job["description"] or result.get("description","")
        tags_raw    = job["tags"]
        tags        = [t.strip() for t in tags_raw.split(",")] if tags_raw else result.get("tags",[])
        has_yt   = bool(env.get("YOUTUBE_CLIENT_ID") and env.get("YOUTUBE_CLIENT_SECRET"))
        uploader = YouTubeUploader(dry_run=not has_yt)
        if category == "film":
            log_job(job_id, "Upload film ke YouTube...", 40)
            res = uploader.upload(video_path=video_path,title=title,description=description,
                                  tags=tags,category_id="1",privacy=job.get("privacy","public"))
            url = res.get("url","#")
            log_job(job_id, f"Upload selesai! {url}", 100)
            update_job(job_id, status="done", progress=100, youtube_url=url)
        else:
            pre = job.get("preselected_recommendation") or {}
            pres = job.get("preselected_recommendations") or []
            extractor = ClipExtractor()
            if pres:
                log_job(job_id, f"Menggunakan {len(pres)} rekomendasi clip terpilih...", 45)
                clip_results = []
                generated_candidates = []
                fail_reasons = []
                for i, item in enumerate(pres, 1):
                    if item.get("start") is None or item.get("end") is None:
                        continue
                    clip = extractor.create_clip_from_range(
                        video_path,
                        float(item.get("start",0)),
                        float(item.get("end",0)),
                        f"selected_{job_id}_{i:02d}",
                        hook_line=item.get("hook_line",""),
                        intro_text=item.get("title","")
                    )
                    if not clip:
                        msg = f"Gagal membuat clip terpilih #{i}"
                        fail_reasons.append(msg)
                        log_job(job_id, msg)
                        continue
                    ctitle = item.get("title") or title
                    cdesc  = item.get("description") or description
                    ctags  = item.get("tags") or tags or ["shorts","viral"]
                    ctitle, cdesc, ctags = build_shorts_seo_metadata(
                        ctitle, cdesc, ctags,
                        hook_line=item.get("hook_line",""),
                        viral_reason=item.get("viral_reason","")
                    )
                    pct = 55 + int((i / max(1, len(pres))) * 40)
                    log_job(job_id, f"Upload clip terpilih {i}/{len(pres)}...", pct)
                    generated_candidates.append({
                        "path": clip["path"], "title": ctitle, "description": cdesc,
                        "tags": ctags, "viral_score": item.get("viral_score",0),
                        "viral_reason": item.get("viral_reason",""),
                        "hook_line": item.get("hook_line",""),
                        "viral_prediction": item.get("viral_prediction",""),
                        "start": item.get("start",0), "end": item.get("end",0), "selected": True
                    })
                    try:
                        res = uploader.upload(
                            video_path=clip["path"],
                            title=ctitle,
                            description=cdesc,
                            tags=ctags,
                            category_id="24",
                            privacy=job.get("privacy","public"),
                            shorts=True
                        )
                        clip_results.append({
                            "title": ctitle,
                            "url": res.get("url","#"),
                            "viral_score": item.get("viral_score",0),
                            "viral_prediction": item.get("viral_prediction","")
                        })
                    except Exception as ue:
                        msg = str(ue).strip() or repr(ue)
                        fail_reasons.append(f"Clip {i}: {msg}")
                        tb_short = traceback.format_exc()[-320:].replace("\n", " | ")
                        log_job(job_id, f"Gagal upload clip {i}: {msg}")
                        log_job(job_id, f"Detail: {tb_short}")
                update_job(job_id, clips=clip_results)
                if clip_results:
                    log_job(job_id, f"Selesai! {len(clip_results)} clip terupload.", 100)
                    update_job(job_id, status="done", progress=100, youtube_url=clip_results[0].get("url","#"))
                else:
                    if generated_candidates:
                        update_job(job_id, status="review", progress=80, clip_candidates=generated_candidates)
                        log_job(job_id, "Semua upload otomatis gagal. Clip disimpan untuk retry manual via tombol Upload Clip Terpilih.", 80)
                        if fail_reasons:
                            log_job(job_id, "Alasan gagal: " + " || ".join(fail_reasons[:3]))
                    else:
                        raise RuntimeError("Semua clip terpilih gagal diupload")
            elif pre and pre.get("start") is not None and pre.get("end") is not None:
                log_job(job_id, "Menggunakan rekomendasi clip terpilih...", 45)
                clip = extractor.create_clip_from_range(
                    video_path, float(pre.get("start",0)), float(pre.get("end",0)),
                    f"selected_{job_id}", hook_line=pre.get("hook_line",""),
                    intro_text=pre.get("title","")
                )
                if not clip:
                    raise RuntimeError("Gagal membuat clip dari rekomendasi terpilih")
                ctitle = pre.get("title") or title
                cdesc  = pre.get("description") or description
                ctags  = pre.get("tags") or tags or ["shorts","viral"]
                ctitle, cdesc, ctags = build_shorts_seo_metadata(
                    ctitle, cdesc, ctags,
                    hook_line=pre.get("hook_line",""),
                    viral_reason=pre.get("viral_reason","")
                )
                log_job(job_id, "Upload clip terpilih ke YouTube...", 70)
                try:
                    res = uploader.upload(
                        video_path=clip["path"],
                        title=ctitle,
                        description=cdesc,
                        tags=ctags,
                        category_id="24",
                        privacy=job.get("privacy","public"),
                        shorts=True
                    )
                    curl = res.get("url","#")
                    update_job(job_id, clips=[{
                        "title": ctitle, "url": curl,
                        "viral_score": pre.get("viral_score", 0),
                        "viral_prediction": pre.get("viral_prediction","")
                    }])
                    log_job(job_id, f"Upload selesai! {curl}", 100)
                    update_job(job_id, status="done", progress=100, youtube_url=curl)
                except Exception as ue:
                    msg = str(ue).strip() or repr(ue)
                    tb_short = traceback.format_exc()[-320:].replace("\n", " | ")
                    log_job(job_id, f"Gagal upload clip terpilih: {msg}")
                    log_job(job_id, f"Detail: {tb_short}")
                    candidate = {
                        "path": clip["path"], "title": ctitle, "description": cdesc,
                        "tags": ctags, "viral_score": pre.get("viral_score",0),
                        "viral_reason": pre.get("viral_reason",""),
                        "hook_line": pre.get("hook_line",""),
                        "viral_prediction": pre.get("viral_prediction",""),
                        "start": pre.get("start",0), "end": pre.get("end",0), "selected": True
                    }
                    update_job(job_id, status="review", progress=80, clip_candidates=[candidate])
                    log_job(job_id, "Dialihkan ke review agar bisa retry upload manual.", 80)
            else:
                log_job(job_id, "Mencari momen viral...", 40)
                clips     = extractor.extract_viral_clips(video_path)
                log_job(job_id, f"{len(clips)} clip siap untuk direview!", 80)
                candidates = []
                for c in clips:
                    ctitle, cdesc, ctags = build_shorts_seo_metadata(
                        c.get("title",""), c.get("description",""), c.get("tags",[]),
                        hook_line=c.get("hook_line",""),
                        viral_reason=c.get("viral_reason","")
                    )
                    candidates.append({"path":c["path"],"title":ctitle,"description":cdesc,
                               "tags":ctags,"viral_score":c["viral_score"],
                               "viral_reason":c.get("viral_reason",""),
                               "hook_line":c.get("hook_line",""),
                               "viral_prediction":c.get("viral_prediction",""),
                               "start":c.get("start",0),"end":c.get("end",0),"selected":True})
                log_job(job_id, "Menunggu Anda memilih clip untuk diupload...", 80)
                update_job(job_id, status="review", progress=80, clip_candidates=candidates)
    except Exception as e:
        log_job(job_id, f"Error: {str(e)}")
        log_job(job_id, traceback.format_exc()[-400:])
        update_job(job_id, status="error")

def upload_clips_job(job_id, selected_indices):
    job = next((j for j in load_jobs() if j["id"] == job_id), None)
    if not job: return
    env = load_env()
    for k,v in env.items(): os.environ[k] = v
    import sys; sys.path.insert(0, str(BASE_DIR))
    from uploader import YouTubeUploader
    candidates = job.get("clip_candidates",[])
    has_yt     = bool(env.get("YOUTUBE_CLIENT_ID") and env.get("YOUTUBE_CLIENT_SECRET"))
    uploader   = YouTubeUploader(dry_run=not has_yt)
    update_job(job_id, status="uploading", progress=85)
    clip_results = []
    for i, idx in enumerate(selected_indices):
        if idx >= len(candidates): continue
        clip = candidates[idx]
        pct  = 85 + int((i+1)/len(selected_indices)*14)
        log_job(job_id, f"Mengupload clip {i+1}/{len(selected_indices)}: {clip['title']}", pct)
        try:
            res = uploader.upload(video_path=clip["path"],title=clip["title"],
                                  description=clip["description"],tags=clip["tags"],
                                  category_id="24",privacy=job.get("privacy","public"),shorts=True)
            clip_results.append({"title":clip["title"],"url":res.get("url","#"),
                                  "viral_score":clip["viral_score"],
                                  "viral_prediction":clip.get("viral_prediction","")})
            log_job(job_id, f"Upload selesai: {res.get('url','#')}")
        except Exception as e:
            log_job(job_id, f"Gagal upload clip: {e}")
    log_job(job_id, f"Selesai! {len(clip_results)} clip diupload.", 100)
    update_job(job_id, status="done", progress=100, clips=clip_results)


# ── Music Upload Route ────────────────────────────────────────────
@app.route("/api/music/upload", methods=["POST"])
def upload_music():
    if "file" not in request.files:
        return jsonify({"error":"Tidak ada file"}), 400
    f    = request.files["file"]
    name = f.filename.replace(" ","_")
    music_dir = BASE_DIR / "music_library"
    music_dir.mkdir(exist_ok=True)
    save_path = music_dir / name
    f.save(str(save_path))
    # Get duration
    from ffmpeg_helper import run_ffprobe
    import json as _json
    try:
        r   = run_ffprobe(["-v","error","-show_entries","format=duration","-of","json",str(save_path)],capture_output=True,text=True)
        dur = float(_json.loads(r.stdout)["format"]["duration"])
    except:
        dur = 0
    return jsonify({"ok":True,"filename":name,"path":str(save_path),"duration":round(dur,1)})

@app.route("/api/music/list", methods=["GET"])
def list_music():
    music_dir = BASE_DIR / "music_library"
    music_dir.mkdir(exist_ok=True)
    files = []
    for ext in ["*.mp3","*.wav","*.aac","*.ogg","*.m4a"]:
        for f in music_dir.glob(ext):
            files.append({"name":f.name,"path":str(f),"size_mb":round(f.stat().st_size/1024/1024,1)})
    return jsonify(files)

@app.route("/api/music/<filename>")
def serve_music(filename):
    from flask import send_from_directory
    music_dir = BASE_DIR / "music_library"
    if (music_dir/filename).exists():
        return send_from_directory(str(music_dir), filename)
    return "Not found", 404


# ── AI Music Generation (Suno via Pollinations) ───────────────────
MUSIC_GEN_SESSIONS = {}

@app.route("/api/music/generate", methods=["POST"])
def generate_ai_music():
    data     = request.json
    prompt   = data.get("prompt","cinematic background music")
    duration = int(data.get("duration", 60))
    session_id = str(uuid.uuid4())[:8]
    env = load_env()
    for k,v in env.items(): os.environ[k] = v

    MUSIC_GEN_SESSIONS[session_id] = {"status":"running","progress":10,"msg":"Starting...","path":None,"filename":None,"error":None}

    def run():
        import urllib.request, urllib.parse
        try:
            music_dir = BASE_DIR / "music_library"
            music_dir.mkdir(exist_ok=True)

            MUSIC_GEN_SESSIONS[session_id].update({"progress":20,"msg":"Generating dengan Suno AI..."})

            # Try Suno via Pollinations (free)
            enc = urllib.parse.quote(prompt[:200])
            url = f"https://audio.pollinations.ai/{enc}"

            MUSIC_GEN_SESSIONS[session_id].update({"progress":40,"msg":"Downloading audio..."})

            req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120) as r:
                data_bytes = r.read()
                content_type = r.headers.get("Content-Type","")

            if len(data_bytes) < 1000:
                raise ValueError(f"Audio terlalu kecil: {len(data_bytes)} bytes")

            # Detect format
            ext = ".mp3"
            if data_bytes[:4] == b'RIFF': ext = ".wav"
            elif data_bytes[:3] == b'ID3' or data_bytes[:2] == b'\xff\xfb': ext = ".mp3"

            safe_prompt = "".join(c if c.isalnum() or c in ' _-' else '' for c in prompt[:30]).strip().replace(' ','_')
            filename    = f"ai_{safe_prompt}_{session_id}{ext}"
            save_path   = str(music_dir / filename)

            with open(save_path,"wb") as f:
                f.write(data_bytes)

            MUSIC_GEN_SESSIONS[session_id].update({
                "status":"done","progress":100,
                "msg":f"Musik siap! ({len(data_bytes)//1024}KB)",
                "path":save_path,"filename":filename
            })
        except Exception as e:
            import traceback
            MUSIC_GEN_SESSIONS[session_id].update({
                "status":"error","error":str(e),
                "msg":f"Error: {e}"
            })

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"ok":True,"session_id":session_id})

@app.route("/api/music/gen-status/<session_id>", methods=["GET"])
def music_gen_status(session_id):
    s = MUSIC_GEN_SESSIONS.get(session_id)
    return jsonify(s) if s else (jsonify({"error":"Not found"}), 404)


# ── Image Provider Status ─────────────────────────────────────────
@app.route("/api/imgprovider/status", methods=["GET"])
def img_provider_status():
    env = load_env()
    return jsonify({
        "gemini":   bool(env.get("GEMINI_API_KEY","")),
        "together": bool(env.get("TOGETHER_API_KEY","")),
        "cf":       bool(env.get("CF_ACCOUNT_ID","") and env.get("CF_API_TOKEN","")),
        "hf":       bool(env.get("HF_API_KEY","")),
        "pollinations": True,
    })

if __name__ == "__main__":
    env = load_env()
    for k,v in env.items(): os.environ[k] = v
    print("\n" + "="*52)
    print("  YouTube AI Agent")
    print(f"  Folder : {BASE_DIR}")
    print(f"  Buka   : http://localhost:5000")
    print("="*52 + "\n")
    app.run(debug=True, port=5000)
