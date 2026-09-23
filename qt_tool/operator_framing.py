"""Operator framing advice. Customer scope: small operator or hands-only view.

No automatic acceptance/rejection; body/hand visibility is probabilistic.
The size cutoffs below are pilot warning thresholds, not a customer specification.
An isolated worker bounds inference time and keeps optional ML dependencies local.
"""
from __future__ import annotations

import json
import math
import subprocess
import time
from pathlib import Path
from threading import BoundedSemaphore


LABELS = {
    "SMALL": "操作者疑似过小", "HANDS_ONLY": "疑似仅见手部",
    "PARTIAL": "操作者身体可见不完整", "VISIBLE": "检测到人物头肩及部分身体",
    "MIXED": "部分时段存在人物构图风险", "UNKNOWN": "无法可靠判断",
    "NOT_APPLICABLE": "当前单元未启用操作者检测",
}
RISK = {"SMALL", "HANDS_ONLY", "PARTIAL"}
AREA_FLOOR, HEIGHT_FLOOR = .035, .30


def report_base(start, end, status="UNKNOWN", reason="检测证据不足，请人工确认"):
    return {"version": 1, "status": status, "label": LABELS[status], "reason": reason,
            "start": start, "end": end, "intervals": [], "advisory_only": True,
            "size_thresholds": {"area": AREA_FLOOR, "height": HEIGHT_FLOOR, "customer_confirmed": False}}


def supported(unit):
    return bool(unit and (unit.startswith(("T3.", "T4.")) or unit in {"T1.1", "T7.1", "T7.2", "T7.3", "T7.8"}))


def classify_frame(poses, hand_count):
    """Use visible, in-frame landmarks only. Never treat a missing pose as small."""
    if len(poses) > 1:
        return {"status": "UNKNOWN", "reason": "存在多个人物，需人工确认哪个是操作者"}
    if not poses:
        return {"status": "HANDS_ONLY" if hand_count else "UNKNOWN",
                "reason": "检测到手部，但未可靠识别到人物身体" if hand_count else "未可靠识别到人物或手部"}
    landmarks = poses[0]
    def visible(i):
        p = landmarks[i]
        return (0 <= p.x <= 1 and 0 <= p.y <= 1 and (p.visibility or 0) >= .6 and (p.presence or 0) >= .6)
    head = sum(visible(i) for i in range(11)) >= 2
    shoulders = all(visible(i) for i in (11, 12))
    arms = any(visible(i) for i in (13, 14))
    torso = shoulders and any(visible(i) for i in (23, 24))
    # Reject anatomically implausible body claims on close-ups of hands/tools.
    # Unusual postures should remain uncertain, not become a compliance failure.
    shoulder_y = (landmarks[11].y + landmarks[12].y) / 2
    head_y = sum(landmarks[i].y for i in range(11)) / 11
    hip_y = (landmarks[23].y + landmarks[24].y) / 2
    upright = shoulders and head_y < shoulder_y - .015 and (not torso or shoulder_y < hip_y - .03)
    head = head and upright
    points = [p for i, p in enumerate(landmarks) if visible(i)]
    bbox = None
    if len(points) >= 4:
        bbox = [min(p.x for p in points), min(p.y for p in points), max(p.x for p in points), max(p.y for p in points)]
    evidence = {"head_visible": head, "shoulders_visible": shoulders, "torso_visible": torso,
                "hand_count": hand_count, "bbox": bbox}
    # A visible head/upper body does not require feet or a full standing body.
    if head and shoulders and (arms or torso) and bbox:
        area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
        height = bbox[3] - bbox[1]
        small = area < AREA_FLOOR and height < HEIGHT_FLOOR
        return {**evidence, "status": "SMALL" if small else "VISIBLE", "area_ratio": round(area, 4),
                "height_ratio": round(height, 4), "reason": "人物可见范围偏小（提示阈值，须人工确认）" if small else "检测到人物头肩及部分身体；仍需确认主体与动作"}
    partial_body = shoulders and arms and torso and shoulder_y < hip_y - .05
    if hand_count or partial_body:
        return {**evidence, "status": "PARTIAL" if partial_body else "HANDS_ONLY",
                "reason": "仅可靠识别手部／部分身体，人物头肩或躯干证据不足；可能有遮挡，请人工确认"}
    return {**evidence, "status": "UNKNOWN", "reason": "人物关键部位证据不足，不能推断大小或完整度"}


def summarize(samples, start, end):
    report = report_base(start, end)
    if not samples:
        return report
    durations = {}
    intervals = []
    for sample in samples:
        status = sample["status"]
        durations[status] = durations.get(status, 0) + sample["end"] - sample["start"]
        if intervals and intervals[-1]["status"] == status and abs(intervals[-1]["end"] - sample["start"]) < .01:
            intervals[-1]["end"] = sample["end"]
            intervals[-1]["sample_count"] += 1
        else:
            intervals.append({"start": sample["start"], "end": sample["end"], "status": status, "reason": sample["reason"], "sample_count": 1})
    duration = max(.001, end - start)
    # A brief bad frame never labels the whole clip. Low-confidence frames remain
    # in the denominator; no renormalization over only successful detections.
    status = "UNKNOWN"
    for risk in ("HANDS_ONLY", "PARTIAL", "SMALL"):
        if durations.get(risk, 0) / duration >= .65 and any(i["status"] == risk and i["end"] - i["start"] >= 2 and i["sample_count"] >= 3 for i in intervals):
            status = risk
            break
    if status == "UNKNOWN":
        if any(i["status"] in RISK and i["end"] - i["start"] >= 2 and i["sample_count"] >= 3 for i in intervals):
            status = "MIXED"
        elif durations.get("VISIBLE", 0) / duration >= .8:
            status = "VISIBLE"
    reasons = {
        "SMALL": "多次抽样显示操作者占画面较小，请确认是否能清楚识别人物和动作；数值门槛为试运行提示值。",
        "HANDS_ONLY": "多次抽样检测到手部，未可靠识别头肩和身体；疑似只露手，不符合客户要求的人物完整度，请复核。",
        "PARTIAL": "多次抽样仅可靠识别部分身体，操作者可见不完整，请复核。",
        "MIXED": "部分抽样区间存在操作者过小或身体不完整风险，请按时间段核对。",
        "VISIBLE": "抽样可见人物头肩及部分身体，仍需确认其为操作者，并检查大小及其他规则。",
        "UNKNOWN": "人物或手部识别证据不足，可能有遮挡、背身或多人，需人工确认。",
    }
    report.update(status=status, label=LABELS[status], reason=reasons[status], intervals=intervals,
                  samples=samples, sample_count=len(samples))
    return report


def guard_identity(observation, previous):
    """A tiny newly detected person may be a bystander, not the operator."""
    if observation["status"] == "SMALL":
        prominent = max((p.get("area_ratio", 0) for p in previous if p["status"] == "VISIBLE"), default=0)
        if prominent > 3 * observation.get("area_ratio", 0):
            return {**observation, "status": "UNKNOWN", "reason": "人物范围明显变化，无法确认仍是同一操作者（可能是旁观者）"}
    return observation


class OperatorFramingAnalyzer:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.python = self.root / "tools/operator_env/Scripts/python.exe"
        self.models = self.root / "tools/models"
        self.slot = BoundedSemaphore(1)

    def analyze(self, path, start, end, unit):
        if not supported(unit):
            return report_base(start, end, "NOT_APPLICABLE", "此检测用于人物操作者；动物、载具或纯物理现象需按对应主体人工判断")
        if not self.python.is_file() or not all((self.models / name).is_file() for name in ("pose_landmarker_lite.task", "hand_landmarker.task")):
            return report_base(start, end, reason="人物／手部检测组件未安装，请人工确认")
        if not self.slot.acquire(blocking=False):
            return report_base(start, end, reason="操作者检测正在使用中，可稍后补检")
        started = time.monotonic()
        try:
            result = subprocess.run([str(self.python), str(Path(__file__).resolve()), str(Path(path).resolve()),
                                     str(start), str(end), str(self.models)], capture_output=True,
                                    text=True, encoding="utf-8", errors="replace", timeout=40)
            if result.returncode:
                return report_base(start, end, reason="人物检测未完成，请稍后重试或人工确认")
            report = json.loads(result.stdout.strip().splitlines()[-1])
            report["elapsed_seconds"] = round(time.monotonic() - started, 2)
            return report
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
            return report_base(start, end, reason="人物检测超时或不可用，请人工确认")
        finally:
            self.slot.release()


def run_worker(path, start, end, model_dir):
    import cv2
    import mediapipe as mp

    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end and end - start <= 600):
        return report_base(start, end, reason="片段范围无效或超过检测长度上限")
    vision = mp.tasks.vision
    pose_options = vision.PoseLandmarkerOptions(base_options=mp.tasks.BaseOptions(model_asset_path=str(model_dir / "pose_landmarker_lite.task")), num_poses=2)
    hand_options = vision.HandLandmarkerOptions(base_options=mp.tasks.BaseOptions(model_asset_path=str(model_dir / "hand_landmarker.task")), num_hands=2)
    cap = cv2.VideoCapture(str(path))
    samples = []
    step = max(1.0, (end - start) / 60)
    begin = time.monotonic()
    try:
        with vision.PoseLandmarker.create_from_options(pose_options) as pose, vision.HandLandmarker.create_from_options(hand_options) as hand:
            at = start
            while at < end - .001:
                if time.monotonic() - begin > 30:
                    return report_base(start, end, reason="人物检测达到时间预算，未覆盖完整片段，请人工确认")
                cap.set(cv2.CAP_PROP_POS_MSEC, min(at + step / 2, end - .05) * 1000)
                ok, frame = cap.read()
                if not ok:
                    observation = {"status": "UNKNOWN", "reason": "该抽样画面读取失败"}
                else:
                    h, w = frame.shape[:2]
                    if max(w, h) > 640:
                        frame = cv2.resize(frame, (round(w * 640 / max(w, h)), round(h * 640 / max(w, h))))
                    image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    poses = pose.detect(image).pose_landmarks
                    # Run hand detection only when upper body evidence is not clear.
                    observation = classify_frame(poses, 0)
                    if observation["status"] not in {"VISIBLE", "SMALL"}:
                        observation = classify_frame(poses, len(hand.detect(image).hand_landmarks))
                observation = guard_identity(observation, samples)
                samples.append({"start": round(at, 3), "end": round(min(end, at + step), 3), **observation})
                at += step
        result = summarize(samples, start, end)
        result["sample_interval"] = round(step, 3)
        result["method"] = "mediapipe_pose_lite_and_hands_v1"
        return result
    finally:
        cap.release()


if __name__ == "__main__":
    import sys
    print(json.dumps(run_worker(Path(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3]), Path(sys.argv[4])), ensure_ascii=True))
