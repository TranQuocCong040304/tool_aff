"""Chuyển động ảnh — KIỂU 2 cho project CapCut: "Combo Animation 90% + Keyframe điểm nhấn 10%".

- 90% ảnh: hiệu ứng có sẵn của CapCut (Animation → Combo: Zoom 1, Zoom 2, Pendulum...) — nhịp nhanh, đều tay,
  hợp kênh săn deal ra video số lượng lớn.
- 10% ảnh quan trọng (ảnh đầu của phần mở đầu + ảnh đầu mỗi Top): keyframe có gia tốc (ease) trượt + phóng to
  VÀO ĐÚNG VÙNG bạn chọn (vd góc có giá tiền) — việc Combo không làm được vì Combo chỉ zoom ở tâm ảnh.

CapCut không có API: hiệu ứng Combo trong draft là 1 mục trong materials.material_animations, trỏ tới file hiệu
ứng CapCut đã tải về máy (resource_id + path). Tool KHÔNG tự bịa được mục này -> HỌC từ 1 project bạn đã gắn
Combo bằng tay trong CapCut (learn_combos), lưu ở data/capcut_combos.json, rồi chép sang các project sau.
"""
from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from typing import Optional

LIBRARY_FILE = Path(__file__).resolve().parent.parent / "data" / "capcut_combos.json"
_US = 1_000_000

# Điểm nhấn: (u, v) trên ảnh, -1..1 (u: trái → phải, v: trên → dưới).
FOCUS_POINTS = {
    "Giữa": (0.0, 0.0), "Trên": (0.0, -0.6), "Dưới": (0.0, 0.6), "Trái": (-0.6, 0.0), "Phải": (0.6, 0.0),
    "Trên-trái": (-0.6, -0.6), "Trên-phải": (0.6, -0.6), "Dưới-trái": (-0.6, 0.6), "Dưới-phải": (0.6, 0.6),
}
DEFAULT_KEY_ZOOM = 1.25


def _new_id() -> str:
    return str(uuid.uuid4()).upper()


# ------------------------------------------------------------- thư viện Combo --

def learn_combos(content: dict) -> list[dict]:
    """Các hiệu ứng ảnh (ưu tiên Combo) đã gắn trong 1 draft CapCut -> danh sách mẫu, mỗi tên 1 mẫu."""
    used = {r for t in content.get("tracks", []) if t.get("type") == "video"
            for s in t.get("segments", []) for r in s.get("extra_material_refs", [])}
    out, seen = [], set()
    for mat in content.get("materials", {}).get("material_animations", []):
        if mat.get("id") not in used:
            continue
        for anim in mat.get("animations") or []:
            key = anim.get("resource_id") or anim.get("id") or anim.get("name")
            if not key or key in seen or anim.get("material_type", "video") not in ("video", "photo", ""):
                continue
            seen.add(key)
            out.append({"name": anim.get("name") or key, "type": anim.get("type", ""),
                        "category": anim.get("category_name", ""), "anim": copy.deepcopy(anim)})
    # Combo ("group") lên trước; hiệu ứng vào/ra vẫn giữ để dùng nếu bạn chỉ gắn loại đó.
    out.sort(key=lambda c: c["type"] != "group")
    return out


def load_library() -> list[dict]:
    try:
        return json.loads(LIBRARY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save_library(combos: list[dict], merge: bool = True) -> list[dict]:
    lib = load_library() if merge else []
    keys = {c["anim"].get("resource_id") or c["name"] for c in lib}
    for c in combos:
        k = c["anim"].get("resource_id") or c["name"]
        if k not in keys:
            lib.append(c)
            keys.add(k)
    LIBRARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    LIBRARY_FILE.write_text(json.dumps(lib, ensure_ascii=False, indent=1), encoding="utf-8")
    return lib


# ------------------------------------------------------------- áp vào draft --

def _smooth(t: float) -> float:
    return t * t * (3 - 2 * t)


def _kf_group(prop: str, dur_us: int, v0: float, v1: float, points: int = 2) -> dict:
    """Keyframe nướng sẵn đường cong ease (smoothstep) — cùng cách Auto Keyframe Pro."""
    pts = max(2, points)
    return {
        "id": _new_id(), "material_id": "", "property_type": prop,
        "keyframe_list": [
            {"id": _new_id(), "curveType": "Line", "graphID": "",
             "time_offset": int(round(max(dur_us - 1, 0) * i / (pts - 1))),
             "left_control": {"x": 0.0, "y": 0.0}, "right_control": {"x": 0.0, "y": 0.0},
             "values": [float(v0 + (v1 - v0) * _smooth(i / (pts - 1)))]}
            for i in range(pts)
        ],
    }


def focus_keyframes(segment: dict, focus: str = "Giữa", zoom: float = DEFAULT_KEY_ZOOM, points: int = 2) -> None:
    """Ảnh điểm nhấn: phóng to dần + trượt sao cho vùng `focus` về giữa khung (không lộ mép ảnh)."""
    u, v = FOCUS_POINTS.get(focus, (0.0, 0.0))
    dur = int(segment["target_timerange"]["duration"])
    clip = segment["clip"]
    s0 = float(clip["scale"]["x"]) or 1.0
    s1 = s0 * zoom
    # CapCut: transform 1.0 = nửa khung. Ảnh phóng s lần -> trượt tối đa (s-1) mà vẫn phủ kín; đưa điểm (u,v) về
    # giữa = dịch ngược chiều u,v.
    x1, y1 = -u * (s1 - s0), v * (s1 - s0)   # trục y của CapCut hướng lên
    clip["scale"] = {"x": s0, "y": s0}
    clip["transform"] = {"x": 0.0, "y": 0.0}
    segment["uniform_scale"] = {"on": False, "value": s0}   # giống Auto Keyframe Pro
    segment["keyframe_refs"] = []
    segment["common_keyframes"] = [
        _kf_group("KFTypeScaleX", dur, s0, s1, points), _kf_group("KFTypeScaleY", dur, s0, s1, points),
        _kf_group("KFTypePositionX", dur, 0.0, x1, points), _kf_group("KFTypePositionY", dur, 0.0, y1, points),
    ]


# --------------------------------------------- Combo GIẢ LẬP bằng keyframe (khi chưa học được Combo thật) --
# Chỉ dùng thuộc tính chắc chắn CapCut đọc được (ScaleX/Y, PositionX/Y) — mô phỏng nhịp của Zoom 1 / Zoom 2 /
# Pendulum. Combo thật (học từ CapCut) vẫn được ưu tiên khi có.
import math

def _fn_kf(prop: str, dur_us: int, fn, points: int = 13) -> dict:
    return {
        "id": _new_id(), "material_id": "", "property_type": prop,
        "keyframe_list": [
            {"id": _new_id(), "curveType": "Line", "graphID": "",
             "time_offset": int(round(max(dur_us - 1, 0) * i / (points - 1))),
             "left_control": {"x": 0.0, "y": 0.0}, "right_control": {"x": 0.0, "y": 0.0},
             "values": [float(fn(i / (points - 1)))]}
            for i in range(points)
        ],
    }


def _ease_out(t: float) -> float:
    return 1 - (1 - t) ** 3


FAKE_COMBOS = {
    # tên: (scale(t), posX(t), posY(t))
    "Zoom nhịp (giống Zoom 1)": (lambda t: 1.0 + 0.10 * math.sin(math.pi * t), lambda t: 0.0, lambda t: 0.0),
    "Phóng nhanh (giống Zoom 2)": (lambda t: 1.0 + 0.15 * _ease_out(min(t / 0.3, 1.0)) + 0.03 * max(t - 0.3, 0) / 0.7,
                                    lambda t: 0.0, lambda t: 0.0),
    "Lắc nhẹ (giống Pendulum)": (lambda t: 1.08, lambda t: 0.035 * math.sin(2 * math.pi * 1.5 * t),
                                 lambda t: 0.012 * math.sin(2 * math.pi * 3 * t)),
    "Thu nhỏ dần": (lambda t: 1.15 - 0.15 * _smooth(t), lambda t: 0.0, lambda t: 0.0),
}


def fake_combo(segment: dict, name: str) -> None:
    sc, px, py = FAKE_COMBOS[name]
    dur = int(segment["target_timerange"]["duration"])
    base = 1.0   # ảnh chính luôn "vừa khung" = 1.0; không lấy scale hiện tại để chạy lại không bị nhân dồn
    segment["clip"]["scale"] = {"x": base * sc(0), "y": base * sc(0)}
    segment["clip"]["transform"] = {"x": px(0), "y": py(0)}
    segment["uniform_scale"] = {"on": False, "value": base * sc(0)}
    segment["keyframe_refs"] = []
    segment["common_keyframes"] = [
        _fn_kf("KFTypeScaleX", dur, lambda t: base * sc(t)), _fn_kf("KFTypeScaleY", dur, lambda t: base * sc(t)),
        _fn_kf("KFTypePositionX", dur, px), _fn_kf("KFTypePositionY", dur, py),
    ]


def set_combo(content: dict, segment: dict, combo: dict) -> None:
    """Gắn 1 hiệu ứng (Combo) vào clip: ghi vào material_animations mà clip đã tham chiếu."""
    mats = {m["id"]: m for m in content["materials"].setdefault("material_animations", [])}
    target = next((mats[r] for r in segment.get("extra_material_refs", []) if r in mats), None)
    if target is None:
        target = {"id": _new_id(), "type": "sticker_animation", "animations": [], "multi_language_current": "none"}
        content["materials"]["material_animations"].append(target)
        segment.setdefault("extra_material_refs", []).append(target["id"])
    dur = int(segment["target_timerange"]["duration"])
    anim = copy.deepcopy(combo["anim"])
    kind = anim.get("type", "group")
    if kind in ("group", "loop"):
        anim["start"], anim["duration"] = 0, dur                      # Combo phủ cả clip
    else:
        d = min(int(anim.get("duration") or 500_000), dur)
        anim["duration"] = d
        anim["start"] = 0 if kind == "in" else max(dur - d, 0)
    target["animations"] = [anim]
    segment["common_keyframes"] = []                                  # không chồng keyframe lên Combo
    segment["keyframe_refs"] = []


def apply_combo_mode(content: dict, track_index: int, combos: list[dict], key_ids: dict,
                     zoom: float = DEFAULT_KEY_ZOOM) -> dict:
    """Áp Kiểu 2 cho track ảnh chính. key_ids = {segment_id: điểm nhấn}. Trả về thống kê."""
    videos = {m["id"]: m for m in content["materials"]["videos"]}
    stats = {"combo": 0, "key": 0, "video": 0}
    i = 0
    for seg in content["tracks"][track_index]["segments"]:
        mat = videos.get(seg.get("material_id"), {})
        if mat.get("type") != "photo":
            stats["video"] += 1
            continue
        if seg["id"] in key_ids:
            focus_keyframes(seg, key_ids[seg["id"]], zoom)
            stats["key"] += 1
        elif combos:
            set_combo(content, seg, combos[i % len(combos)])
            i += 1
            stats["combo"] += 1
        else:   # chưa học Combo thật -> Combo giả lập bằng keyframe (luân phiên), ảnh không đứng yên
            names = list(FAKE_COMBOS)
            fake_combo(seg, names[i % len(names)])
            i += 1
            stats["fake"] = stats.get("fake", 0) + 1
    return stats
