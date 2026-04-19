"""
YouTube Uploader - OAuth2 from environment variables (.env.json)
No client_secret.json required.
"""
import os
import pickle
import json
from pathlib import Path

import google_auth_oauthlib.flow
import googleapiclient.discovery
import googleapiclient.http
from googleapiclient.errors import HttpError
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request

from utils import logger
from ffmpeg_helper import run_ffmpeg, run_ffprobe

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except Exception:
    HAS_PIL = False

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_FILE = Path(__file__).parent / "youtube_token.pkl"
THUMB_DIR = Path(__file__).parent / "thumbnails"
THUMB_DIR.mkdir(exist_ok=True)


class YouTubeUploader:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self._youtube = None
        self._retried_auth = False
        self._thumbnail_forbidden = False

    def _video_duration(self, video_path: Path) -> float:
        try:
            r = run_ffprobe(
                ["-v", "error", "-show_entries", "format=duration", "-of", "json", str(video_path)],
                capture_output=True, text=True, timeout=30
            )
            return float(json.loads(r.stdout)["format"]["duration"])
        except Exception:
            return 0.0

    def _extract_thumbnail_frame(self, video_path: Path, out_img: Path) -> bool:
        dur = self._video_duration(video_path)
        # Pick a frame away from intro/outro
        ts = 8.0
        if dur > 20:
            ts = min(max(dur * 0.28, 8.0), dur - 6.0)
        r = run_ffmpeg(
            ["-ss", str(ts), "-i", str(video_path), "-vframes", "1", "-q:v", "3", "-y", str(out_img)],
            capture_output=True, text=True
        )
        return r.returncode == 0 and out_img.exists() and out_img.stat().st_size > 1000

    def _wrap_text(self, text: str, max_chars: int = 24) -> list:
        words = (text or "").strip().split()
        if not words:
            return []
        lines = []
        cur = []
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
        return lines[:3]

    def _draw_thumbnail_text(self, img_path: Path, title: str):
        if not HAS_PIL:
            return
        img = Image.open(img_path).convert("RGBA")
        w, h = img.size
        draw = ImageDraw.Draw(img, "RGBA")

        # Dark gradient panel at bottom
        panel_h = int(h * 0.42)
        for y in range(h - panel_h, h):
            a = (y - (h - panel_h)) / max(1, panel_h)
            alpha = int(45 + 165 * a)
            draw.line([(0, y), (w, y)], fill=(0, 0, 0, alpha))

        # Font
        fsize = max(44, int(h * 0.07))
        try:
            font = ImageFont.truetype("arial.ttf", fsize)
            font_small = ImageFont.truetype("arial.ttf", max(22, int(fsize * 0.45)))
        except Exception:
            font = ImageFont.load_default()
            font_small = ImageFont.load_default()

        clean_title = (title or "").replace("#shorts", "").strip()
        lines = self._wrap_text(clean_title, max_chars=22 if w <= 1280 else 28)
        if not lines:
            lines = ["VIDEO TERBARU"]
        y = h - panel_h + int(panel_h * 0.18)
        for ln in lines:
            draw.text(
                (int(w * 0.06), y),
                ln,
                fill=(255, 255, 255),
                font=font,
                stroke_width=max(3, int(fsize * 0.09)),
                stroke_fill=(0, 0, 0),
            )
            y += int(fsize * 1.08)

        badge = "WATCH NOW"
        bx = int(w * 0.06)
        by = h - int(panel_h * 0.18)
        draw.text(
            (bx, by),
            badge,
            fill=(255, 220, 0),
            font=font_small,
            stroke_width=2,
            stroke_fill=(0, 0, 0),
        )
        img.convert("RGB").save(img_path, "JPEG", quality=92)

    def _generate_thumbnail(self, video_path: Path, title: str) -> Path | None:
        out_img = THUMB_DIR / f"thumb_{video_path.stem}.jpg"
        ok = self._extract_thumbnail_frame(video_path, out_img)
        if not ok:
            return None
        self._draw_thumbnail_text(out_img, title)
        return out_img

    def _set_youtube_thumbnail(self, video_id: str, thumb_path: Path):
        if self._thumbnail_forbidden:
            return
        youtube = self._get_authenticated_service()
        media = googleapiclient.http.MediaFileUpload(str(thumb_path), mimetype="image/jpeg", resumable=False)
        try:
            youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
        except HttpError as e:
            msg = str(e).lower()
            if "youtube.thumbnail" in msg or "custom video thumbnails" in msg or "forbidden" in msg:
                self._thumbnail_forbidden = True
                raise PermissionError(
                    "Channel belum punya izin Custom Thumbnail (403 forbidden). Upload video tetap sukses."
                )
            raise

    def _client_config(self):
        client_id = os.environ.get("YOUTUBE_CLIENT_ID", "")
        client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET", "")
        redirect_port = int(os.environ.get("YOUTUBE_REDIRECT_PORT", "8090"))
        if not client_id or not client_secret:
            raise ValueError(
                "YOUTUBE_CLIENT_ID dan YOUTUBE_CLIENT_SECRET belum diset. "
                "Masukkan di dashboard API Keys."
            )
        cfg = {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uris": [f"http://localhost:{redirect_port}"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        return cfg, redirect_port

    def _oauth_login(self):
        client_config, redirect_port = self._client_config()
        logger.info("  Membuka browser untuk login YouTube...")
        flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_config(client_config, SCOPES)
        return flow.run_local_server(port=redirect_port)

    def _get_authenticated_service(self):
        if self._youtube:
            return self._youtube

        creds = None
        if TOKEN_FILE.exists():
            try:
                with open(TOKEN_FILE, "rb") as f:
                    creds = pickle.load(f)
            except Exception:
                logger.warning("  Token YouTube rusak, menghapus token lama...")
                TOKEN_FILE.unlink(missing_ok=True)
                creds = None

        if creds and creds.expired and creds.refresh_token:
            logger.info("  Memperbarui token YouTube...")
            try:
                creds.refresh(Request())
            except RefreshError as e:
                msg = str(e).lower()
                if "invalid_grant" in msg:
                    logger.warning("  Refresh token invalid_grant. Re-login YouTube diperlukan...")
                    TOKEN_FILE.unlink(missing_ok=True)
                    creds = self._oauth_login()
                else:
                    raise
        elif not creds or not creds.valid:
            creds = self._oauth_login()

        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)

        self._youtube = googleapiclient.discovery.build(
            "youtube", "v3", credentials=creds, cache_discovery=False
        )
        return self._youtube

    def upload(
        self,
        video_path: str,
        title: str,
        description: str,
        tags: list,
        category_id: str = "24",
        privacy: str = "public",
        shorts: bool = False,
    ) -> dict:
        if self.dry_run:
            logger.info(f"  [DRY-RUN] Upload: {title} | {video_path}")
            return {"id": "DRY-RUN-ID", "url": "#dry-run", "title": title}

        path = Path(video_path)
        if not path.exists():
            raise FileNotFoundError(f"Video tidak ditemukan: {path}")

        size_mb = path.stat().st_size / 1024 / 1024
        logger.info(f"  File: {path.name} ({size_mb:.1f} MB)")

        if shorts and "#shorts" not in title.lower():
            title = f"{title} #shorts"
        if shorts and "shorts" not in [t.lower() for t in tags]:
            tags = ["shorts"] + tags

        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": tags[:500],
                "categoryId": category_id,
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
        }

        def _send_once():
            youtube = self._get_authenticated_service()
            media = googleapiclient.http.MediaFileUpload(
                str(path), mimetype="video/*", resumable=True, chunksize=1024 * 1024 * 10
            )
            request = youtube.videos().insert(part=",".join(body.keys()), body=body, media_body=media)
            response = None
            logger.info("  Mengupload ke YouTube...")
            while response is None:
                status, response = request.next_chunk()
                if status:
                    pct = int(status.progress() * 100)
                    done = max(0, min(20, pct // 5))
                    bar = "#" * done + "-" * (20 - done)
                    print(f"\r  [{bar}] {pct}%", end="", flush=True)
            print()
            return response

        try:
            response = _send_once()
        except (RefreshError, HttpError) as e:
            msg = str(e).lower()
            if (not self._retried_auth) and ("invalid_grant" in msg or "unauthorized" in msg or "401" in msg):
                logger.warning("  Kredensial YouTube tidak valid, mencoba login ulang...")
                self._retried_auth = True
                self._youtube = None
                TOKEN_FILE.unlink(missing_ok=True)
                response = _send_once()
            else:
                raise

        video_id = response["id"]
        url = f"https://youtu.be/{video_id}"
        logger.info(f"  Upload berhasil! {url}")

        # Auto thumbnail: frame + title text
        try:
            thumb = self._generate_thumbnail(path, title)
            if thumb:
                self._set_youtube_thumbnail(video_id, thumb)
                logger.info(f"  Thumbnail otomatis terpasang: {thumb.name}")
            else:
                logger.warning("  Thumbnail otomatis gagal dibuat (skip).")
        except Exception as e:
            logger.warning(f"  Set thumbnail gagal (skip): {e}")

        return {"id": video_id, "url": url, "title": title}
