"""Conservative image-based camera-motion advice, never a rejection gate.

Background consensus is only evidence of apparent image motion, not proof of
physical camera translation (digital crop/zoom and moving backgrounds exist).
Thresholds are pilot heuristics, normalized to image diagonal, not accuracy claims.
"""
from __future__ import annotations

import math
import time
from pathlib import Path


VERSION = 1
LABELS = {
    "FIXED": "高度疑似固定机位",
    "SHAKE": "疑似固定机位伴轻微抖动",
    "ZOOM": "疑似变焦／画面缩放",
    "MOVING": "检测到持续整体运动（非合格结论）",
    "MIXED": "固定、运动或缩放混合，需逐段确认",
    "UNKNOWN": "无法可靠判断",
    "UNTESTED": "尚未检测",
}


def unknown(start: float, end: float, reason: str) -> dict:
    return {"version": VERSION, "status": "UNKNOWN", "label": LABELS["UNKNOWN"],
            "reason": reason, "start": start, "end": end, "intervals": [],
            "advisory_only": True, "sample_fps": 4}


def classify_window(samples: list[dict], start: float, end: float, diagonal: float) -> dict:
    import numpy as np

    base = {"start": round(start, 3), "end": round(end, 3), "status": "UNKNOWN"}
    valid = [s for s in samples if s.get("matrix") is not None]
    absolute = bool(samples) and all(s.get("absolute") for s in samples)
    # Direct-to-anchor estimates can tolerate one isolated failed match without
    # inventing displacement across that gap. Chained estimates cannot.
    sufficient = len(valid) == len(samples) or (absolute and len(valid) / max(1, len(samples)) >= .9)
    if end - start < 1.4 or len(samples) < 5 or not sufficient:
        return base
    transform = np.eye(3)
    centers, angles, scales = [np.zeros(2)], [0.0], [0.0]
    for sample in valid:
        matrix = np.eye(3)
        matrix[:2] = sample["matrix"]
        transform = matrix if absolute else matrix @ transform
        # Pair matrices use coordinates centered on the image, so zoom does not
        # masquerade as translation around an off-origin image center.
        centers.append(transform[:2, 2].copy() / diagonal)
        angles.append(math.degrees(math.atan2(transform[1, 0], transform[0, 0])))
        scales.append(math.log(max(1e-8, math.hypot(transform[0, 0], transform[1, 0]))))
    centers = np.asarray(centers)
    path = float(np.linalg.norm(np.diff(centers, axis=0), axis=1).sum())
    net = float(np.linalg.norm(centers[-1]))
    extent = float(np.linalg.norm(centers, axis=1).max())
    angle_path = float(np.abs(np.diff(angles)).sum())
    angle_net = abs(angles[-1])
    scale_range = max(scales) - min(scales)
    coherent = net / max(path, 1e-8)
    base.update(drift=round(net, 5), excursion=round(extent, 5),
                scale_change=round(math.expm1(scale_range), 5),
                rotation=round(angle_net, 3), coherence=round(coherent, 3))
    # Even translation + scaling is flagged, not counted as valid camera motion.
    if scale_range >= .012:
        base["status"] = "ZOOM"
    elif scale_range > .004:
        pass
    elif (net >= .008 and coherent >= .75) or (angle_net >= .7 and angle_net / max(angle_path, 1e-8) >= .8):
        base["status"] = "MOVING"
    elif extent <= .003 and max(map(abs, angles)) <= .25:
        base["status"] = "FIXED"
    elif extent <= .012 and net < .008 and coherent < .35 and max(map(abs, angles)) < .7:
        base["status"] = "SHAKE"
    return base


def summarize(intervals: list[dict], start: float, end: float) -> dict:
    result = unknown(start, end, "背景不足、运动太缓慢或证据不一致；请人工确认")
    weights = {}
    for item in intervals:
        weights[item["status"]] = weights.get(item["status"], 0) + item["end"] - item["start"]
    duration = max(end - start, .001)
    fixed = (weights.get("FIXED", 0) + weights.get("SHAKE", 0)) / duration
    moving = weights.get("MOVING", 0) / duration
    zoom = weights.get("ZOOM", 0) / duration
    status = "UNKNOWN"
    if zoom > 0:
        status = "ZOOM" if zoom >= .5 else "MIXED"
    elif fixed >= .9:
        status = "SHAKE" if weights.get("SHAKE", 0) else "FIXED"
    elif moving >= .8:
        status = "MOVING"
    elif moving >= .15 and fixed >= .15:
        status = "MIXED"
    reasons = {
        "FIXED": "多数时间背景位置基本不变；局部主体动作不代表运镜。请播放确认。",
        "SHAKE": "背景仅小幅往复晃动，没有持续运镜证据；轻微抖动不算有效运镜。",
        "ZOOM": "检测到持续画面缩放，疑似变焦或数字推近；变焦不符合要求，不能当作有效运镜。也可能是实拍推拉，须人工区分。",
        "MOVING": "背景存在持续整体位移或旋转；仍须人工核对是否真实运镜、锚定主体及无变焦。",
        "MIXED": "片段内存在不同运动状态；请按时间段确认，不自动裁剪或整条淘汰。",
    }
    result.update(status=status, label=LABELS[status], intervals=intervals,
                  reason=reasons.get(status, result["reason"]),
                  coverage=round(sum(v for k, v in weights.items() if k != "UNKNOWN") / duration, 3))
    return result


class CameraMotionAnalyzer:
    """No model download; low-resolution proxy + spatially distributed LK/RANSAC."""

    def estimate_pair(self, previous, current):
        import cv2
        import numpy as np

        height, width = previous.shape
        # Prefer the outer field so a large central hand/fruit does not dominate
        # the consensus. Spatial coverage checks still reject sparse edge-only
        # or one-sided evidence; outer pixels are not guaranteed background.
        feature_mask = np.full_like(previous, 255)
        feature_mask[height//4:3*height//4, width//4:3*width//4] = 0
        points = cv2.goodFeaturesToTrack(previous, maxCorners=500, qualityLevel=.008,
                                         minDistance=7, mask=feature_mask)
        if points is None or len(points) < 40:
            return None
        nxt, ok, _ = cv2.calcOpticalFlowPyrLK(previous, current, points, None)
        if nxt is None:
            return None
        back, back_ok, _ = cv2.calcOpticalFlowPyrLK(current, previous, nxt, None)
        if back is None:
            return None
        keep = (ok.ravel() == 1) & (back_ok.ravel() == 1) & (np.linalg.norm(points - back, axis=2).ravel() < 1.0)
        a, b = points.reshape(-1, 2)[keep], nxt.reshape(-1, 2)[keep]
        if len(a) < 35:
            return None
        matrix, mask = cv2.estimateAffinePartial2D(a, b, method=cv2.RANSAC, ransacReprojThreshold=1.0)
        if matrix is None or mask is None or not np.isfinite(matrix).all():
            return None
        inliers = mask.ravel().astype(bool)
        if inliers.sum() < 30 or inliers.mean() < .65:
            return None
        occupied = {(min(3, max(0, int(x * 4 / width))), min(3, max(0, int(y * 4 / height)))) for x, y in a[inliers]}
        quadrants = {(x // 2, y // 2) for x, y in occupied}
        if len(occupied) < 9 or len(quadrants) < 4 or sum(x in (0, 3) or y in (0, 3) for x, y in occupied) < 8:
            return None
        center = np.array([width / 2, height / 2])
        matrix[:, 2] += matrix[:, :2] @ center - center
        if abs(math.log(max(1e-8, math.hypot(matrix[0, 0], matrix[1, 0])))) > .15:
            return None  # a cut, failed match, or extreme scale change
        return matrix.tolist()

    def analyze(self, path: Path, start: float, end: float, *, time_budget: float = 20) -> dict:
        begin = time.monotonic()
        fallback = unknown(start, end, "检测不可用；保留人工审核，不影响候选状态")
        cap = None
        try:
            import cv2

            if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end) or end - start < 1.5:
                return fallback
            cap = cv2.VideoCapture(str(path))
            fps = cap.get(cv2.CAP_PROP_FPS)
            if not cap.isOpened() or not math.isfinite(fps) or fps <= 0:
                return fallback
            if end - start > 600:
                return unknown(start, end, "片段过长，未执行运镜预筛；请人工确认")
            cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
            stride = max(1, round(fps / 4))
            previous, previous_at = None, start
            window_start, window, intervals = start, [], []
            diagonal = 1.0
            frames = math.ceil((end - start) * fps)
            for frame_index in range(frames):
                if time.monotonic() - begin > time_budget:
                    return unknown(start, end, "检测达到时间预算，未覆盖完整片段；请人工确认")
                if not cap.grab():
                    return unknown(start, end, "代理解码提前结束，证据不完整；请人工确认")
                if frame_index % stride:
                    continue
                ok, frame = cap.retrieve()
                if not ok:
                    return fallback
                at = start + frame_index / fps
                h, w = frame.shape[:2]
                size = (min(480, w), max(2, round(h * min(480, w) / w)))
                gray = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), size)
                diagonal = math.hypot(*size)
                if previous is not None:
                    window.append({"start": window_start, "end": at, "absolute": True,
                                   "matrix": self.estimate_pair(previous, gray)})
                else:
                    previous = gray
                previous_at = at
                if at - window_start >= 3:
                    intervals.append(classify_window(window, window_start, at, diagonal))
                    window, window_start = [], at
                    previous = gray
            if window:
                intervals.append(classify_window(window, window_start, previous_at, diagonal))
            result = summarize(intervals, start, end)
            result["elapsed_seconds"] = round(time.monotonic() - begin, 3)
            result["method"] = "distributed_lk_ransac_v1"
            return result
        except (ImportError, OSError, ValueError, RuntimeError):
            return fallback
        except Exception:
            # OpenCV failures must not remove material or fail shot analysis.
            return fallback
        finally:
            if cap is not None:
                cap.release()
