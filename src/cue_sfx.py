"""Hiệu ứng âm thanh "theo lời thoại" — học từ project mẫu AutoVideo 20261010_224231 (2) (bạn tự chèn 00:00-01:05):

  - Giọng bảo bấm link ("bấm vào link số 1", "link ghim dưới bình luận"...)  -> Mouse click  (ngay lúc nói "bấm")
  - Sang 1 Top mới (đầu đoạn Top 5, Top 4, ...)                                -> Woosh        (đúng đầu đoạn)
  - Hô giá tiền ("681.000đ", "370k", "1 triệu 2"...)                            -> Drop coin    (lúc nói con số)

Khác thẻ [Sound Effect: ...] trong kịch bản (src/capcut_sfx.py): cái này KHÔNG cần AI viết thẻ, tool tự dò theo
nội dung câu, nên giọng máy hay giọng tự thu đều dùng được. Mốc giây của từ khoá trong câu ước lượng theo vị trí
ký tự (câu nói đều tốc độ -> lệch ~0.1-0.3 s).

Âm thanh lấy theo thứ tự: assets/sfx/<tên>.mp3|.wav (thả file của bạn vào đây để đổi tiếng) -> đúng file CapCut
trong cache máy bạn (3 tiếng bạn đã chọn trong thư viện CapCut) -> tiếng tự tổng hợp (src/sfx.py).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from .sfx import SFX_DIR, sfx_path

# tên -> (file trong cache CapCut, tên hiển thị trong CapCut, tiếng dự phòng tự tổng hợp, âm lượng)
CUES = {
    "mouse_click": ("d91f21d1c2b6ec21cf77a8d7185bdb47.mp3", "Mouse click sound", "pop", 1.0),
    "woosh": ("7785b04dd58d6f7a816e7d9744448ae5.mp3", "Woosh", "swoosh", 1.0),
    "coin": ("d329163b7d5445d12e38bb1c09b3b209.mp3", "coin! Get shop item 6", "ding", 1.0),
}
CUE_LABELS = {"mouse_click": "Mouse click (bảo bấm link)", "woosh": "Woosh (sang Top mới)", "coin": "Drop coin (hô giá)"}

# "bấm vào link", "nhấn link", "click vào đường link", "link ghim (dưới) bình luận", "link (ở) dưới bình luận"
_CLICK_RE = re.compile(
    r"\b(bấm|nhấn|ấn|click|chạm|tap)\s+(ngay\s+)?(vào\s+)?(cái\s+)?(đường\s+)?(link|liên kết|giỏ hàng)"
    r"|\b(link|liên kết)\s+(mình\s+|tui\s+|tôi\s+|em\s+)?(đã\s+)?(ghim|để|gắn)\b"
    r"|\b(link|liên kết)\s+(ở\s+)?(ngay\s+)?dưới\s+(phần\s+)?(bình luận|comment|mô tả)",
    re.IGNORECASE)
# 681.000đ · 370.050 đồng · 1.250.000 VNĐ · 370k · 99 nghìn · 1 triệu 2 · 1,2 triệu
_PRICE_RE = re.compile(
    r"\b\d{1,3}(?:[.,]\d{3})+\s*(?:đ\b|đồng|vnđ|vnd|₫)?"
    r"|\b\d+(?:[.,]\d+)?\s*(?:k|K|nghìn|ngàn|triệu|tr)\b"
    r"|\b\d+\s*(?:đ|₫)(?!\w)",
    re.IGNORECASE)
_NOT_PRICE_RE = re.compile(r"^\s*(?:lượt|chiếc|sao|đánh giá|người|đơn|cái|w\b|ml|g\b|tháng|năm|%)", re.IGNORECASE)

LEAD = 0.12          # đặt tiếng sớm hơn từ khoá 1 chút (bạn đặt click ~0.2 s trước chữ "bấm")
MIN_GAP = 2.5        # 2 tiếng cùng loại cách nhau tối thiểu (giây)


def _capcut_cache_dir() -> Optional[Path]:
    env = os.environ.get("CAPCUT_CACHE_DIR", "").strip()
    cands = [Path(env)] if env else []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        cands.append(Path(local) / "CapCut" / "User Data" / "Cache" / "music")
    cands.append(Path.home() / "AppData" / "Local" / "CapCut" / "User Data" / "Cache" / "music")
    return next((c for c in cands if c.exists()), None)


def cue_path(name: str) -> Path:
    cache_file, _, fallback, _ = CUES[name]
    for ext in (".mp3", ".wav", ".m4a"):
        p = SFX_DIR / f"{name}{ext}"
        if p.exists():
            return p
    cache = _capcut_cache_dir()
    if cache and (cache / cache_file).exists():
        return cache / cache_file
    return sfx_path(fallback)


def cue_volume(name: str) -> float:
    return CUES[name][3]


def _at(beat: dict, char_pos: int) -> float:
    text = beat.get("text") or ""
    start, end = float(beat.get("start", 0)), float(beat.get("end", 0))
    frac = char_pos / max(len(text), 1)
    return max(start, start + (end - start) * frac - LEAD)


def _first_price(text: str) -> Optional[int]:
    for m in _PRICE_RE.finditer(text or ""):
        tail = text[m.end():m.end() + 12]
        raw = m.group(0)
        if _NOT_PRICE_RE.match(tail) or re.search(r"\b(19|20)\d{2}$", raw.strip()):
            continue
        # số trần không có đơn vị tiền chỉ nhận khi có dấu chấm nghìn (681.000) — "4.85" không phải giá
        if not re.search(r"[đ₫kK]|đồng|vn|nghìn|ngàn|triệu|tr", raw) and not re.search(r"\d[.,]\d{3}", raw):
            continue
        return m.start()
    return None


def segment_cues(seg: dict) -> list[tuple[float, str]]:
    """[(giây tính từ đầu đoạn, tên cue)] cho 1 đoạn của 06_media_manifest.json."""
    out = []
    if seg.get("kind") == "product":
        out.append((0.0, "woosh"))
    for b in seg.get("beats") or []:
        text = b.get("text") or ""
        m = _CLICK_RE.search(text)
        if m:
            out.append((_at(b, m.start()), "mouse_click"))
        p = _first_price(text)
        if p is not None:
            out.append((_at(b, p), "coin"))
    out.sort()
    kept, last = [], {}
    for t, name in out:
        if t - last.get(name, -99) >= MIN_GAP:
            kept.append((round(t, 3), name))
            last[name] = t
    return kept


def cue_events(manifest: list[dict], enabled: Optional[set] = None) -> dict:
    """{order của đoạn: [(giây trong đoạn, tên cue)]}. `enabled` = tập tên cue được bật (None = tất cả)."""
    out = {}
    for seg in manifest:
        ev = [(t, n) for t, n in segment_cues(seg) if enabled is None or n in enabled]
        if ev:
            out[seg.get("order")] = ev
    return out
