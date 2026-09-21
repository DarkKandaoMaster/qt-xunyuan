from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _as_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _as_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    root: Path
    data_dir: Path
    rules_path: Path
    conflicts_path: Path
    search_templates_path: Path
    db_path: Path
    proxy_max_height: int
    source_max_duration_seconds: int
    max_download_concurrency: int
    daily_traffic_warning_gb: float
    cache_limit_gb: float
    single_file_warning_gb: float
    ffmpeg_bin: str
    ffprobe_bin: str
    ytdlp_bin: str
    ytdlp_js_runtime: str
    ytdlp_cookies_from_browser: str
    ytdlp_sleep_interval: int
    ytdlp_max_sleep_interval: int
    ai_api_key: str
    ai_api_url: str
    ai_model: str
    host: str
    port: int


def _detect_ytdlp_js_runtime(root: Path) -> str:
    configured = os.environ.get("YTDLP_JS_RUNTIME", "").strip()
    if configured:
        return configured

    candidates = (
        ("deno", root / "tools" / "bin" / "deno.exe"),
        ("node", root / "tools" / "bin" / "node.exe"),
        ("deno", Path(shutil.which("deno") or "")),
        ("node", Path(shutil.which("node") or "")),
        ("node", Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" /
         "dependencies" / "node" / "bin" / "node.exe"),
    )
    for name, path in candidates:
        if str(path) not in {"", "."} and path.is_file():
            return f"{name}:{path}"
    return ""


def load_settings() -> Settings:
    _load_dotenv(ROOT / ".env")
    data_dir = Path(os.environ.get("DATA_DIR", "./data"))
    if not data_dir.is_absolute():
        data_dir = (ROOT / data_dir).resolve()
    for name in ("metadata", "proxy", "original", "clips", "rejected", "deliverable", "contact_sheets"):
        (data_dir / name).mkdir(parents=True, exist_ok=True)
    bundled_ffmpeg = next((ROOT / "tools" / "ffmpeg").glob("*/bin/ffmpeg.exe"), Path("ffmpeg"))
    bundled_ffprobe = next((ROOT / "tools" / "ffmpeg").glob("*/bin/ffprobe.exe"), Path("ffprobe"))
    bundled_ytdlp = ROOT / "tools" / "bin" / "yt-dlp.exe"
    return Settings(
        root=ROOT,
        data_dir=data_dir,
        rules_path=ROOT / "rules" / "qt_rules_v4.yaml",
        conflicts_path=ROOT / "rules" / "conflicts.yaml",
        search_templates_path=ROOT / "rules" / "search_templates.yaml",
        db_path=data_dir / "qt_tool.sqlite3",
        proxy_max_height=_as_int("PROXY_MAX_HEIGHT", 480),
        source_max_duration_seconds=max(60, _as_int("SOURCE_MAX_DURATION_SECONDS", 600)),
        max_download_concurrency=_as_int("MAX_DOWNLOAD_CONCURRENCY", 2),
        daily_traffic_warning_gb=_as_float("DAILY_TRAFFIC_WARNING_GB", 20.0),
        cache_limit_gb=_as_float("CACHE_LIMIT_GB", 50.0),
        single_file_warning_gb=_as_float("SINGLE_FILE_WARNING_GB", 4.0),
        ffmpeg_bin=os.environ.get("FFMPEG_BIN", str(bundled_ffmpeg)),
        ffprobe_bin=os.environ.get("FFPROBE_BIN", str(bundled_ffprobe)),
        ytdlp_bin=os.environ.get("YTDLP_BIN", str(bundled_ytdlp) if bundled_ytdlp.exists() else "yt-dlp"),
        ytdlp_js_runtime=_detect_ytdlp_js_runtime(ROOT),
        ytdlp_cookies_from_browser=os.environ.get("YTDLP_COOKIES_FROM_BROWSER", "").strip(),
        ytdlp_sleep_interval=max(0, _as_int("YTDLP_SLEEP_INTERVAL", 5)),
        ytdlp_max_sleep_interval=max(0, _as_int("YTDLP_MAX_SLEEP_INTERVAL", 10)),
        ai_api_key=os.environ.get("AI_API_KEY", ""),
        ai_api_url=os.environ.get("AI_API_URL", ""),
        ai_model=os.environ.get("AI_MODEL", ""),
        host=os.environ.get("HOST", "127.0.0.1"),
        port=_as_int("PORT", 8765),
    )
