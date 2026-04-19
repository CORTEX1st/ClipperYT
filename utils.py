"""
Utilities — Logger dan direktori kerja
"""

import logging
from pathlib import Path

# Direktori — relatif terhadap lokasi utils.py
BASE_DIR  = Path(__file__).parent.resolve()
CLIPS_DIR = BASE_DIR / "output_clips"
LOGS_DIR  = BASE_DIR / "logs"

# Buat semua folder otomatis saat module diimport
LOGS_DIR.mkdir(exist_ok=True)
CLIPS_DIR.mkdir(exist_ok=True)

def setup_dirs():
    CLIPS_DIR.mkdir(exist_ok=True)
    LOGS_DIR.mkdir(exist_ok=True)

# Logger
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(LOGS_DIR / "agent.log"), encoding="utf-8")
    ]
)
logger = logging.getLogger("youtube-agent")