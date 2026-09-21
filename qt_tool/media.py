from __future__ import annotations

import hashlib
import csv
import json
import math
import re
import shutil
import subprocess
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .db import Database
from .rules import RuleEngine, RuleStatus
from .subject import SubjectContinuityAnalyzer


class ToolMissing(RuntimeError):
    pass


def _command_exists(command: str) -> bool:
    path = Path(command)
    return path.exists() if path.parent != Path(".") else shutil.which(command) is not None


def _run(args: list[str], timeout: int = 3600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout, check=False)


def merge_facts(base_json: str, probe: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    facts = dict(json.loads(base_json or "{}"))
    facts.update(probe)
    facts.update(overrides)
    return facts


def delivery_description(title: str, limit: int = 60) -> str:
    """Return a Windows-safe short description while preserving Chinese text."""
    value = unicodedata.normalize("NFKC", str(title or ""))
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"_+", "_", value).strip(" ._")
    return (value[:limit].rstrip(" ._") or "视频片段")


def tool_status(settings: Settings) -> dict[str, bool]:
    subject = SubjectContinuityAnalyzer(settings.root / "tools" / "models")
    return {
        "ffmpeg": _command_exists(settings.ffmpeg_bin),
        "ffprobe": _command_exists(settings.ffprobe_bin),
        "yt_dlp": _command_exists(settings.ytdlp_bin),
        "youtube_js": bool(settings.ytdlp_js_runtime),
        "youtube_auth": bool(settings.ytdlp_cookies_from_browser),
        "subject_ai": subject.available,
    }


def source_score(metadata: dict[str, Any], target_unit: str | None, query: str, gap_boost: float = 0) -> float:
    title = str(metadata.get("title", "")).lower()
    description = str(metadata.get("description", "")).lower()
    text = f"{title} {description}"
    positive = [w for w in re.split(r"\W+", query.lower()) if len(w) > 2]
    relevance = min(35.0, sum(6.0 for word in positive if word in text))
    height = int(metadata.get("height") or 0)
    quality = 20.0 if height >= 2160 else 14.0 if height >= 1440 else 7.0 if height >= 1080 else 0.0
    duration = float(metadata.get("duration") or 0)
    duration_score = 15.0 if duration >= 30 else 10.0 if duration >= 15 else 4.0 if duration >= 5 else -40.0
    negatives = ("montage", "compilation", "gameplay", "shorts", "reaction")
    penalty = sum(18.0 for word in negatives if word in text)
    unit_bonus = 8.0 if target_unit else 0.0
    return max(0.0, min(100.0, 20 + relevance + quality + duration_score + unit_bonus + gap_boost - penalty))


class MediaPipeline:
    def __init__(self, settings: Settings, db: Database, rules: RuleEngine):
        self.settings = settings
        self.db = db
        self.rules = rules
        root = getattr(settings, "root", settings.data_dir.parent)
        self.subject_analyzer = SubjectContinuityAnalyzer(Path(root) / "tools" / "models")
        if hasattr(self.db, "delivery_filename_rows"):
            self.repair_delivery_filenames()

    def repair_delivery_filenames(self) -> int:
        """Rename legacy deliverables and update their persisted standard names."""
        root = (self.settings.data_dir / "deliverable").resolve()
        renamed = 0
        for row in self.db.delivery_filename_rows():
            unit = str(row.get("delivery_unit") or row.get("unit") or "UNSET")
            sequence = int(row.get("delivery_sequence") or 0)
            if sequence <= 0:
                continue
            stored_name = str(row.get("delivery_filename") or "")
            compliant = re.fullmatch(rf"{re.escape(unit)}_{sequence:03d}_.+\.mp4", stored_name, re.IGNORECASE)
            filename = stored_name if compliant else f"{unit}_{sequence:03d}_{delivery_description(row.get('source_title') or '')}.mp4"
            old_path = Path(str(row.get("final_path") or ""))
            final_path = old_path
            if old_path.is_file():
                resolved = old_path.resolve()
                if root == resolved.parent or root in resolved.parents:
                    target = old_path.with_name(filename)
                    if target != old_path:
                        if target.exists():
                            raise FileExistsError(f"交付文件名冲突：{target}")
                        shutil.copy2(old_path, target)
                        if target.stat().st_size != old_path.stat().st_size:
                            target.unlink(missing_ok=True)
                            raise OSError(f"交付文件重命名校验失败：{old_path}")
                        old_path.unlink()
                        final_path = target
                        renamed += 1
            self.db.update_final_clip_delivery(int(row["candidate_id"]), str(final_path), filename,
                                               str(row.get("deliverable_status") or "PENDING"))
        return renamed

    def _ytdlp(self, *args: str, use_cookies: bool = False) -> list[str]:
        command = [self.settings.ytdlp_bin]
        if self.settings.ytdlp_js_runtime:
            command.extend(("--js-runtimes", self.settings.ytdlp_js_runtime))
        if _command_exists(self.settings.ffmpeg_bin):
            command.extend(("--ffmpeg-location", str(Path(self.settings.ffmpeg_bin).parent)))
        if use_cookies and self.settings.ytdlp_cookies_from_browser:
            command.extend(("--cookies-from-browser", self.settings.ytdlp_cookies_from_browser,
                            "--sleep-requests", "1",
                            "--sleep-interval", str(self.settings.ytdlp_sleep_interval),
                            "--max-sleep-interval", str(max(self.settings.ytdlp_sleep_interval,
                                                            self.settings.ytdlp_max_sleep_interval))))
        command.extend(args)
        return command

    def discover(self, query: str, target_unit: str | None = None, limit: int = 10) -> dict[str, int]:
        if not _command_exists(self.settings.ytdlp_bin):
            raise ToolMissing("未找到 yt-dlp；安装后才能自动搜索公开来源，也可先手工导入 URL。")
        target = f"ytsearch{max(1, min(limit, 50))}:{query}"
        proc = _run(self._ytdlp("--dump-single-json", "--flat-playlist", "--skip-download",
                                "--ignore-errors", "--no-warnings", target), timeout=300)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "yt-dlp 搜索失败")
        payload = json.loads(proc.stdout)
        self.db.add_traffic("metadata", len(proc.stdout.encode("utf-8")))
        entries = payload.get("entries") or []
        found = created = 0
        for entry in entries:
            if not entry:
                continue
            found += 1
            url = entry.get("webpage_url") or entry.get("url")
            if not url:
                continue
            metadata = self._normalize_ytdlp(entry, query, target_unit)
            _, is_new = self.db.add_source(metadata)
            created += int(is_new)
        return {"found": found, "created": created}

    def import_url(self, url: str, target_unit: str | None = None) -> tuple[int, bool]:
        if _command_exists(self.settings.ytdlp_bin):
            proc = _run(self._ytdlp("--dump-single-json", "--skip-download", "--no-warnings", url), timeout=180)
            if proc.returncode == 0:
                self.db.add_traffic("metadata", len(proc.stdout.encode("utf-8")))
                return self.db.add_source(self._normalize_ytdlp(json.loads(proc.stdout), "manual", target_unit))
        platform = "youtube" if "youtu" in url else "vimeo" if "vimeo" in url else "manual"
        video_id = hashlib.sha256(url.encode()).hexdigest()[:20]
        return self.db.add_source({"platform": platform, "video_id": video_id, "url": url, "title": url,
                                   "target_unit": target_unit, "status": "DISCOVERED"})

    def _normalize_ytdlp(self, item: dict[str, Any], query: str, target_unit: str | None) -> dict[str, Any]:
        extractor = str(item.get("extractor_key") or item.get("extractor") or "unknown").lower()
        platform = "youtube" if "youtube" in extractor else "vimeo" if "vimeo" in extractor else extractor
        formats = [{k: f.get(k) for k in ("format_id", "ext", "width", "height", "fps", "filesize", "vcodec", "acodec")}
                   for f in (item.get("formats") or [])]
        raw_url = item.get("webpage_url") or item.get("original_url") or item.get("url")
        if platform == "youtube" and raw_url and not str(raw_url).startswith(("http://", "https://")):
            raw_url = f"https://www.youtube.com/watch?v={raw_url}"
        result = {
            "platform": platform,
            "video_id": str(item.get("id") or hashlib.sha256(str(item.get("webpage_url", "")).encode()).hexdigest()[:20]),
            "url": raw_url,
            "title": item.get("title") or "",
            "uploader": item.get("uploader") or item.get("channel") or "",
            "description": item.get("description") or "",
            "duration": item.get("duration"),
            "thumbnail": item.get("thumbnail") or "",
            "available_formats": formats,
            "resolution": f"{item.get('width') or 0}x{item.get('height') or 0}",
            "target_unit": target_unit,
            "search_query": query,
            "metadata": item,
            "status": "METADATA_READY",
        }
        result["source_score"] = source_score(item, target_unit, query)
        return result

    def download_proxy(self, source_id: int) -> Path:
        source = self._required_source(source_id)
        if not _command_exists(self.settings.ytdlp_bin):
            raise ToolMissing("未找到 yt-dlp")
        output = self.settings.data_dir / "proxy" / f"{source['platform']}_{source['video_id']}.%(ext)s"
        self.db.update_source(source_id, status="PROXY_QUEUED", error=None)
        before = self._matching_bytes(output.parent, f"{source['platform']}_{source['video_id']}.*")
        fmt = (f"bestvideo[height<={self.settings.proxy_max_height}][vcodec^=avc1]+bestaudio[ext=m4a]/"
               f"best[height<={self.settings.proxy_max_height}][vcodec^=avc1][acodec!=none]/"
               f"bestvideo[height<={self.settings.proxy_max_height}][vcodec!=none]+bestaudio/"
               f"best[height<={self.settings.proxy_max_height}][vcodec!=none]")
        proc = _run(self._ytdlp("-f", fmt, "--merge-output-format", "mp4", "--no-playlist",
                                "-o", str(output), source["url"], use_cookies=True), timeout=3600)
        if proc.returncode != 0:
            self.db.update_source(source_id, status="ERROR", error=proc.stderr[-2000:])
            raise RuntimeError(proc.stderr.strip() or "代理下载失败")
        path = self._find_download(output.parent, f"{source['platform']}_{source['video_id']}.*")
        info = self.probe(path)
        if not info.get("playable"):
            self.db.update_source(source_id, status="ERROR", error="代理文件不包含视频画面，请重新下载")
            raise RuntimeError("代理文件不包含视频画面，请重新下载")
        self.db.add_traffic("proxy", max(0, path.stat().st_size - before), source_id)
        self.db.update_source(source_id, status="PROXY_READY", proxy_path=str(path), error=None)
        self.db.update_candidate_proxies(source_id, str(path))
        return path

    def download_final(self, source_id: int) -> Path:
        source = self._required_source(source_id)
        if not _command_exists(self.settings.ytdlp_bin):
            raise ToolMissing("未找到 yt-dlp")
        output = self.settings.data_dir / "original" / f"{source['platform']}_{source['video_id']}.%(ext)s"
        self.db.update_source(source_id, status="FINAL_DOWNLOADING", error=None)
        before = self._matching_bytes(output.parent, f"{source['platform']}_{source['video_id']}.*")
        proc = _run(self._ytdlp("-f", "bestvideo+bestaudio/best", "--merge-output-format", "mp4",
                                "--no-playlist", "-o", str(output), source["url"],
                                use_cookies=True), timeout=7200)
        if proc.returncode != 0:
            self.db.update_source(source_id, status="ERROR", error=proc.stderr[-2000:])
            raise RuntimeError(proc.stderr.strip() or "最终源下载失败")
        path = self._find_download(output.parent, f"{source['platform']}_{source['video_id']}.*")
        self.db.add_traffic("final", max(0, path.stat().st_size - before), source_id)
        self.db.update_source(source_id, status="FINAL_READY", original_path=str(path), error=None)
        return path

    @staticmethod
    def _matching_bytes(parent: Path, pattern: str) -> int:
        return sum(p.stat().st_size for p in parent.glob(pattern) if p.is_file())

    @staticmethod
    def _find_download(parent: Path, pattern: str) -> Path:
        matches = sorted((p for p in parent.glob(pattern)
                          if p.is_file() and not re.search(r"\.f\d+\.", p.name) and not p.name.endswith(".part")),
                         key=lambda p: p.stat().st_mtime, reverse=True)
        if not matches:
            raise FileNotFoundError("下载完成但未找到已合并的视频文件")
        return matches[0]

    def probe(self, path: Path) -> dict[str, Any]:
        if not _command_exists(self.settings.ffprobe_bin):
            raise ToolMissing("未找到 ffprobe")
        proc = _run([self.settings.ffprobe_bin, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], timeout=120)
        if proc.returncode != 0:
            return {"playable": False, "probe_error": proc.stderr[-1000:]}
        payload = json.loads(proc.stdout)
        video = next((s for s in payload.get("streams", []) if s.get("codec_type") == "video"), {})
        audio = next((s for s in payload.get("streams", []) if s.get("codec_type") == "audio"), None)
        raw_fps = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"
        try:
            a, b = raw_fps.split("/", 1)
            fps = float(a) / float(b)
        except (ValueError, ZeroDivisionError):
            fps = 0.0
        duration = payload.get("format", {}).get("duration") or video.get("duration")
        return {"playable": bool(video), "duration": float(duration or 0), "width": int(video.get("width") or 0),
                "height": int(video.get("height") or 0), "fps": fps, "video_codec": video.get("codec_name"),
                "has_audio": audio is not None, "audio_codec": audio.get("codec_name") if audio else None,
                "format_name": payload.get("format", {}).get("format_name", ""), "file_size": path.stat().st_size}

    def detect_shots(self, path: Path, threshold: float = 0.28, safety_margin: float = 0.12) -> list[tuple[float, float]]:
        info = self.probe(path)
        duration = float(info.get("duration") or 0)
        if duration <= 0:
            return []
        if not _command_exists(self.settings.ffmpeg_bin):
            return [(0.0, duration)] if duration >= 5 else []
        filter_expr = f"select='gt(scene,{threshold})',showinfo"
        proc = _run([self.settings.ffmpeg_bin, "-hide_banner", "-i", str(path), "-vf", filter_expr, "-an", "-f", "null", "-"], timeout=7200)
        cuts = [float(x) for x in re.findall(r"pts_time:([0-9.]+)", proc.stderr)]
        boundaries = [0.0] + sorted({c for c in cuts if 0 < c < duration}) + [duration]
        shots: list[tuple[float, float]] = []
        for idx, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
            safe_start = start + (safety_margin if idx > 0 else 0)
            safe_end = end - (safety_margin if idx < len(boundaries) - 2 else 0)
            if safe_end - safe_start >= 5.0:
                shots.append((round(safe_start, 3), round(safe_end, 3)))
        return shots

    def analyze_source(self, source_id: int) -> list[int]:
        source = self._required_source(source_id)
        if not source.get("proxy_path"):
            raise ValueError("请先下载代理")
        path = Path(source["proxy_path"])
        info = self.probe(path)
        if not info.get("playable"):
            raise ValueError("代理文件不包含视频画面，请返回生产台重新下载代理")
        shots = self.detect_shots(path)
        unit = source.get("target_unit")
        bucket = unit.split(".", 1)[0] if unit else None
        material_type = self.rules.buckets.get(bucket, {}).get("material_type") if bucket else None
        # Re-analysis replaces only machine-created/unreviewed slices. Human
        # decisions and already-produced final clips are deliberately preserved.
        self.db.clear_replaceable_candidates(source_id)
        created_ids: list[int] = []
        for shot_start, shot_end in shots:
            # Stage 1 is always the hard-cut detector. Only a single-shot range
            # reaches stage 2, where sustained subject absence creates new
            # reviewable slices instead of rejecting the whole source.
            subject = self.subject_analyzer.analyze(path, shot_start, shot_end, unit)
            for start, end in subject.segments:
                duration = end - start
                frame_hash = self.representative_frame_hash(path, start + duration / 2)
                if frame_hash and self.db.candidate_hash_exists(frame_hash):
                    continue
                duration_bucket, _, _ = self.rules.duration_bucket(duration)
                facts = dict(info, duration=duration, shot_count=1, unit=unit, bucket=bucket,
                             material_type=material_type, source_type="PROXY", **subject.facts_for((start, end)))
                cid, created = self.db.add_candidate({"source_id": source_id, "start_time": start, "end_time": end,
                    "duration": duration, "proxy_path": str(path), "candidate_bucket": bucket, "candidate_unit": unit,
                    "material_type": material_type, "duration_bucket": duration_bucket, "score": source.get("source_score", 0),
                    "representative_hash": frame_hash, "facts": facts})
                if created:
                    results = self.rules.evaluate(facts)
                    gate = self.rules.unit_gate(unit)
                    if gate:
                        results.append(gate)
                    self.db.save_rule_results(cid, [r.to_dict() for r in results])
                    if self.rules.automatic_reject(results):
                        self.db.review(cid, {"decision": "REJECT", "notes": "程序确定性硬规则自动淘汰"})
                    created_ids.append(cid)
        self.db.update_source(source_id, status="WAITING_REVIEW", analysis_completed=1, error=None)
        return created_ids

    def representative_frame_hash(self, path: Path, at_seconds: float) -> str | None:
        """Representative-frame dHash, robust to resolution and moderate recompression."""
        if not _command_exists(self.settings.ffmpeg_bin):
            return None
        proc = subprocess.run([self.settings.ffmpeg_bin, "-v", "error", "-ss", f"{at_seconds:.3f}", "-i", str(path),
                               "-frames:v", "1", "-vf", "scale=9:8,format=gray", "-f", "rawvideo", "-"],
                              capture_output=True, timeout=120, check=False)
        raw = proc.stdout
        if proc.returncode != 0 or len(raw) < 72:
            return None
        bits = []
        for y in range(8):
            row = raw[y * 9:(y + 1) * 9]
            bits.extend(1 if row[x] > row[x + 1] else 0 for x in range(8))
        value = sum(bit << (63 - idx) for idx, bit in enumerate(bits))
        return f"{value:016x}"

    def silence_ratio(self, path: Path, duration: float) -> float | None:
        if not _command_exists(self.settings.ffmpeg_bin) or duration <= 0:
            return None
        proc = _run([self.settings.ffmpeg_bin, "-hide_banner", "-i", str(path), "-af", "silencedetect=noise=-50dB:d=0.5", "-f", "null", "-"], timeout=7200)
        starts = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", proc.stderr)]
        ends = [(float(a), float(b)) for a, b in re.findall(r"silence_end: ([0-9.]+) \| silence_duration: ([0-9.]+)", proc.stderr)]
        silent = sum(length for _, length in ends)
        if len(starts) > len(ends) and starts:
            silent += max(0.0, duration - starts[-1])
        return min(1.0, silent / duration)

    def black_ratio(self, path: Path, duration: float) -> float | None:
        if not _command_exists(self.settings.ffmpeg_bin) or duration <= 0:
            return None
        proc = _run([self.settings.ffmpeg_bin, "-hide_banner", "-i", str(path), "-vf", "blackdetect=d=0.05:pic_th=0.98", "-an", "-f", "null", "-"], timeout=7200)
        black = sum(float(x) for x in re.findall(r"black_duration:([0-9.]+)", proc.stderr))
        return min(1.0, black / duration)

    def clip_final(self, candidate_id: int) -> Path:
        candidate = self.db.get_candidate(candidate_id)
        if not candidate:
            raise KeyError("候选不存在")
        if candidate["status"] != "ACCEPTED":
            raise ValueError("只有人工接受的候选才能制作最终片段")
        source_path = candidate.get("original_path")
        if not source_path:
            source_path = str(self.download_final(int(candidate["source_id"])))
        original = Path(source_path).resolve()
        proxy_root = (self.settings.data_dir / "proxy").resolve()
        original_root = (self.settings.data_dir / "original").resolve()
        if proxy_root == original or proxy_root in original.parents:
            raise AssertionError("禁止从代理文件制作交付片段")
        if original_root not in original.parents:
            raise AssertionError("最终片段源文件必须来自 original 目录（source_type=FINAL）")
        if not _command_exists(self.settings.ffmpeg_bin):
            raise ToolMissing("未找到 ffmpeg")
        output = self._clip_path(candidate)
        start, duration = float(candidate["start_time"]), float(candidate["duration"])
        # Accurate input seeking + normal encode preserves content and avoids keyframe drift.
        proc = _run([self.settings.ffmpeg_bin, "-hide_banner", "-y", "-ss", f"{start:.3f}", "-i", str(original),
                     "-t", f"{duration:.3f}", "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-preset", "medium",
                     "-crf", "17", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output)], timeout=7200)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr[-2000:] or "最终剪片失败")
        return output

    def final_qa_and_deliver(self, candidate_id: int) -> dict[str, Any]:
        candidate = self.db.get_candidate(candidate_id)
        if not candidate:
            raise KeyError("候选不存在")
        if not candidate.get("candidate_bucket") or not candidate.get("candidate_unit") or candidate.get("candidate_viewpoint") not in {"first_person", "third_person"}:
            raise ValueError("最终处理前必须由人工确认桶、单元和人称")
        clip = self._clip_path(candidate)
        if not clip.exists():
            clip = self.clip_final(candidate_id)
            candidate = self.db.get_candidate(candidate_id) or candidate
        info = self.probe(clip)
        if info.get("has_audio"):
            info["silence_ratio"] = self.silence_ratio(clip, float(info.get("duration") or 0))
        info["black_ratio"] = self.black_ratio(clip, float(info.get("duration") or 0))
        detected = self.detect_shots(clip)
        info["shot_count"] = max(1, len(detected))
        subject_trim_required = False
        if info["shot_count"] == 1:
            subject = self.subject_analyzer.analyze(
                clip, 0.0, float(info.get("duration") or 0), candidate.get("candidate_unit")
            )
            info.update(subject.facts_for((0.0, float(info.get("duration") or 0))))
            info["subject_proposed_segments"] = [list(segment) for segment in subject.segments]
            subject_trim_required = subject.status == "SPLIT"
            info["subject_trim_required"] = subject_trim_required
            if subject_trim_required:
                info["subject_check"] = "TRIM_REQUIRED"
        else:
            info.update({"subject_check": "SKIPPED_SHOT_CHANGE", "subject_trim_required": False})
        facts = merge_facts(candidate.get("facts_json") or "{}", info,
                            unit=candidate.get("candidate_unit"), bucket=candidate.get("candidate_bucket"),
                            material_type=candidate.get("material_type"), duration=info.get("duration"),
                            shot_count=info["shot_count"], source_type="FINAL")
        results = self.rules.evaluate(facts)
        gate = self.rules.unit_gate(candidate.get("candidate_unit"))
        if gate:
            results.append(gate)
        hard_fail = self.rules.automatic_reject(results)
        conflicts = any(r.status == RuleStatus.CONFLICT for r in results)
        qa_status = "FAIL" if hard_fail else "TRIM_REQUIRED" if subject_trim_required else "CONFLICT" if conflicts else "PASS"
        self.db.save_rule_results(candidate_id, [r.to_dict() for r in results], stage="final")
        qa_payload = {"rules": [r.to_dict() for r in results], "probe": info}
        self.db.create_final_clip(candidate_id, original_path=candidate.get("original_path"), final_path=str(clip),
                                  duration=info.get("duration"), width=info.get("width"), height=info.get("height"), fps=info.get("fps"),
                                  has_audio=int(bool(info.get("has_audio"))), qa_status=qa_status,
                                  deliverable_status="PENDING" if qa_status == "PASS" else "BLOCKED", qa=qa_payload)
        final_path = None
        if qa_status == "PASS":
            bucket = candidate.get("candidate_bucket") or "UNSET"
            unit = candidate.get("candidate_unit") or "UNSET"
            bucket_name = self.rules.buckets.get(bucket, {}).get("name", "未分类")
            viewpoint = "第一人称" if candidate.get("candidate_viewpoint") == "first_person" else "第三人称"
            deliver_dir = self.settings.data_dir / "deliverable" / "QT寻源数据" / f"{bucket}_{bucket_name}" / viewpoint
            deliver_dir.mkdir(parents=True, exist_ok=True)
            assignment = self.db.reserve_delivery_filename(
                candidate_id, unit, delivery_description(candidate.get("source_title") or "")
            )
            final_path = deliver_dir / assignment["filename"]
            shutil.copy2(clip, final_path)
            self.db.update_final_clip_delivery(candidate_id, str(final_path), assignment["filename"], "READY")
        if qa_status == "PASS":
            self.db.update_source(int(candidate["source_id"]), status="DELIVERABLE")
            with self.db.connect() as con:
                con.execute("UPDATE candidate_shots SET status='DELIVERABLE' WHERE id=?", (candidate_id,))
        return {"qa_status": qa_status, "final_path": str(final_path) if final_path else None, "probe": info,
                "rules": [r.to_dict() for r in results]}

    def export_delivery_csv(self) -> Path:
        rows = self.db.delivery_rows()
        delivery_time = datetime.now(UTC).isoformat()
        output = self.settings.data_dir / "deliverable" / "QT寻源数据_交付信息表.csv"
        fields = ["人称", "OSS路径", "交付时间", "统合单元", "分辨率", "时长"]
        try:
            stream = output.open("w", encoding="utf-8-sig", newline="")
        except PermissionError:
            # Spreadsheet programs can exclusively lock a CSV on Windows. Keep
            # exporting useful instead of failing the whole operation.
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output = output.with_name(f"QT寻源数据_交付信息表_{timestamp}.csv")
            stream = output.open("w", encoding="utf-8-sig", newline="")
        with stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({
                    "人称": "第一人称" if row["viewpoint"] == "first_person" else "第三人称",
                    "OSS路径": "OSS",
                    "交付时间": row.get("exported_at") or delivery_time,
                    "统合单元": row["unit"],
                    "分辨率": f"{row['width']}x{row['height']}",
                    "时长": round(float(row["duration"] or 0), 3),
                })
        self.db.mark_delivery_exported([int(row["candidate_id"]) for row in rows], delivery_time)
        return output

    def _clip_path(self, candidate: dict[str, Any]) -> Path:
        source_stem = (Path(candidate["original_path"]).stem if candidate.get("original_path")
                       else f"{candidate.get('platform') or 'source'}_{candidate.get('video_id') or candidate['source_id']}")
        safe_stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", source_stem).strip(" .") or "source"
        part_number = self.db.candidate_part_number(int(candidate["id"]))
        return self.settings.data_dir / "clips" / f"{safe_stem}-{part_number}.mp4"

    def _required_source(self, source_id: int) -> dict[str, Any]:
        source = self.db.get_source(source_id)
        if not source:
            raise KeyError("来源不存在")
        return source
