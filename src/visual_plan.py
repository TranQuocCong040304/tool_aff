"""Chia hình cho từng đoạn (mở đầu / từng Top / kết) theo kiểu ƯU TIÊN VIDEO SẢN PHẨM.

Người xem thích video hơn ảnh tĩnh, nên:
- Mở đầu & kết: montage các video sản phẩm, mỗi cảnh CỐ ĐỊNH 5 giây; sản phẩm không có video thì chèn ảnh chính
  của nó vào đúng lượt.
- Mỗi Top có video: dùng video của chính sản phẩm đó TRƯỚC (liền mạch từ đầu video, phủ tối đa `max_ratio` thời
  lượng đoạn), phần còn lại mới dùng ảnh (ảnh chính → mô tả → biến thể), mỗi ảnh 5 giây, xoay vòng ảnh khác nhau.
- Top không có video: chỉ ảnh, mỗi ảnh 5 giây.
Điểm cắt luôn rơi vào ĐẦU CÂU khi được (mốc câu = beats của 06_media_manifest.json).

Hàm thuần (không đụng CapCut) -> dùng được cho cả xuất CapCut lẫn ghép MP4.
Trả về list shot: {"duration", "image": Path|None, "video": Path|None, "video_start": float}.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Optional

VIDEO_START = 0.5      # bỏ 0.5s đầu video shop (thường là màn hình đen / logo)
MIN_SHOT = 1.8         # cảnh ngắn hơn -> gộp với cảnh kế
SHOT_LEN = 5.0         # mở đầu / kết / ảnh: mỗi cảnh CỐ ĐỊNH 5 giây (ngắn hơn -> chớp nháy khó chịu)
MONTAGE_MAX = SHOT_LEN
MAX_PHOTO = SHOT_LEN


def _grid(bounds: list[float], total: float, max_len: float, min_len: float = MIN_SHOT) -> list[float]:
    """Mốc cắt: đầu câu (bounds) -> gộp khoảng quá ngắn, chia đều khoảng quá dài. Trả [0, ..., total]."""
    pts = [0.0] + sorted(b for b in set(round(x, 3) for x in bounds) if min_len <= b <= total - min_len) + [total]
    merged = [0.0]
    for p in pts[1:]:
        if p - merged[-1] < min_len and p != total:
            continue
        if p == total and p - merged[-1] < min_len and len(merged) > 1:
            merged[-1] = total
            continue
        merged.append(p)
    out = [0.0]
    for a, b in zip(merged, merged[1:]):
        n = max(1, int(-(-(b - a) // max_len)))
        out += [a + (b - a) * k / n for k in range(1, n + 1)]
    return out


def _fixed(total: float, step: float = SHOT_LEN) -> list[float]:
    """Mốc cắt đều mỗi `step` giây; đoạn cuối ngắn hơn nửa bước thì gộp vào cảnh trước."""
    pts = [0.0]
    while total - pts[-1] > step:
        pts.append(pts[-1] + step)
    if len(pts) > 1 and total - pts[-1] < step / 2:
        pts.pop()
    return pts + [total]


def _shot(duration: float, image: Optional[Path] = None, video: Optional[Path] = None, video_start: float = 0.0):
    return {"duration": duration, "image": image, "video": video, "video_start": video_start}


# Câu đang TẢ 1 tấm ảnh cụ thể ("Ảnh thứ hai ghi…", "ảnh cuối là…", "trong hình…") -> phải hiện đúng ảnh đó
# (kịch bản viết khi AI nhìn ảnh; ảnh gắn theo [[IMG:n]]), không được thay bằng video.
_IMG_REF_RE = re.compile(r"\b(anh|hinh|tam anh|buc anh)\s+(thu|dau|cuoi|chi tiet|bia|nay|tren|so)|\btrong (anh|hinh)\b|"
                         r"\b(anh|hinh) (ghi|cho thay|chup|quang cao|minh hoa)|^\W*(anh|hinh|tam anh|buc anh)\b")


def _ascii(text: str) -> str:
    t = unicodedata.normalize("NFD", (text or "").lower()).replace("đ", "d")
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def describes_image(text: str) -> bool:
    t = re.sub(r"\banh (em|chi|ay|ta)\b", " ", _ascii(text))   # "anh em", "anh chị"... không phải "ảnh"
    return bool(_IMG_REF_RE.search(t))


def plan_product(total: float, bounds: list[float], video: Optional[tuple[Path, float]], images: list[Path],
                 max_ratio: float = 0.85, max_photo: float = MAX_PHOTO, beats: Optional[list[dict]] = None) -> list[dict]:
    """1 Top: video sản phẩm trước (liền mạch), ảnh sau.
    `beats` = [{"start", "end", "text", "image"}]: câu nào đang TẢ 1 ảnh cụ thể thì hiện ĐÚNG ảnh đó lúc đọc câu
    đó (giọng khớp hình); các câu còn lại chạy video (nối tiếp, không lặp), hết video mới tới ảnh."""
    locked = []
    for bt in beats or []:
        img = bt.get("image")
        if isinstance(img, Path) and describes_image(bt.get("text", "")):
            if locked and locked[-1][2] == img and abs(locked[-1][1] - bt["start"]) < 0.05:
                locked[-1] = (locked[-1][0], bt["end"], img)
            else:
                locked.append((bt["start"], bt["end"], img))
    if not locked:
        return _plan_product_simple(total, bounds, video, images, max_ratio, max_photo)
    # Khoảng tự do (không có câu tả ảnh) -> video nối tiếp / ảnh xoay vòng
    shots, t, vpos, img_i = [], 0.0, VIDEO_START, 0
    budget = (min(total * max_ratio, video[1] - VIDEO_START - 0.1) if video else 0.0)
    used_video = 0.0

    def fill(a: float, b: float):
        nonlocal vpos, used_video, img_i
        span = b - a
        while span > 0.05:
            if video and used_video < budget and video[1] - vpos > MIN_SHOT:
                d = min(span, budget - used_video, video[1] - 0.1 - vpos)
                if d < MIN_SHOT and span - d >= MIN_SHOT:
                    d = 0
                if d > 0:
                    shots.append(_shot(d, video=video[0], video_start=vpos))
                    vpos += d
                    used_video += d
                    span -= d
                    continue
            pool = images or ([video[0]] if video else [])
            if not pool:
                if shots:
                    shots[-1]["duration"] += span
                return
            d = min(span, max_photo) if span - max_photo >= max_photo / 2 else span
            src = pool[img_i % len(pool)]
            img_i += 1
            shots.append(_shot(d, image=src) if src.suffix.lower() not in (".mp4", ".mov", ".webm", ".mkv", ".avi")
                         else _shot(d, video=src, video_start=VIDEO_START))
            span -= d

    for a, b, img in locked:
        if a - t > 0.05:
            fill(t, a)
        shots.append(_shot(b - a, image=img))
        t = b
    if total - t > 0.05:
        fill(t, total)
    # gộp cảnh video liền mạch (cùng file, nối tiếp) -> 1 clip
    merged = []
    for sh in shots:
        p = merged[-1] if merged else None
        if p and sh["video"] and p["video"] == sh["video"] and abs(p["video_start"] + p["duration"] - sh["video_start"]) < 0.05:
            p["duration"] += sh["duration"]
        else:
            merged.append(sh)
    return merged


def _plan_product_simple(total: float, bounds: list[float], video: Optional[tuple[Path, float]], images: list[Path],
                         max_ratio: float = 0.85, max_photo: float = MAX_PHOTO) -> list[dict]:
    """1 Top: video sản phẩm trước (liền mạch), ảnh sau."""
    shots, t = [], 0.0
    if video and video[1] > VIDEO_START + MIN_SHOT:
        vpath, vdur = video
        budget = min(total * max_ratio, vdur - VIDEO_START - 0.1)
        # kết thúc video đúng 1 đầu câu (gần budget nhất, không vượt); không có thì cắt đúng budget
        ends = [b for b in sorted(bounds) if MIN_SHOT <= b <= budget]
        end = ends[-1] if ends and budget - ends[-1] < 6 else budget
        if total - end < MIN_SHOT:          # phần ảnh còn lại quá ngắn -> video phủ luôn nếu đủ dài
            end = total if vdur - VIDEO_START >= total else end
        shots.append(_shot(end, video=vpath, video_start=VIDEO_START))
        t = end
    rest = total - t
    if rest > 0.05:
        pool = images or ([] if not shots else [])
        if not pool and shots:              # không có ảnh -> kéo dài video (nếu đủ) hoặc giữ video
            shots[-1]["duration"] += rest
            return shots
        cuts = _fixed(rest, max_photo)
        for i, (a, b) in enumerate(zip(cuts, cuts[1:])):
            shots.append(_shot(b - a, image=pool[i % len(pool)]))
    return shots


def plan_montage(total: float, bounds: list[float], items: list[dict], max_len: float = MONTAGE_MAX,
                 offset_ratio: float = 0.4) -> list[dict]:
    """Mở đầu / kết: xoay vòng qua các sản phẩm. items = [{"video": (Path, dur)|None, "image": Path|None}].
    Video lấy từ ~40% thời lượng (khác đoạn đầu video đã dùng ở phần Top của sản phẩm đó)."""
    items = [it for it in items if it.get("video") or it.get("image")]
    if not items:
        return []
    cuts = _fixed(total, max_len)
    used = {}                               # vị trí đã dùng trong từng video (montage dài -> không lặp cảnh)
    shots = []
    for i, (a, b) in enumerate(zip(cuts, cuts[1:])):
        it = items[i % len(items)]
        d = b - a
        if it.get("video") and it["video"][1] > d + 1:
            vpath, vdur = it["video"]
            pos = used.get(vpath, max(VIDEO_START, vdur * offset_ratio))
            if pos + d > vdur - 0.1:
                pos = VIDEO_START
            shots.append(_shot(d, video=vpath, video_start=pos))
            used[vpath] = pos + d
        elif it.get("image"):
            shots.append(_shot(d, image=it["image"]))
        else:                                # video quá ngắn cho cảnh này
            shots.append(_shot(d, video=it["video"][0], video_start=VIDEO_START))
    return shots
