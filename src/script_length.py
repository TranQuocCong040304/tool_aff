"""Độ dài video mục tiêu (phút) -> số từ cho hook / từng sản phẩm / CTA.

Tốc độ đọc (WPM, chỉnh / đo được trong app; tiếng Việt: 1 âm tiết = 1 "từ" khi đếm).
Lưu lựa chọn ở <run>/script_length.json. Dùng cho CẢ script mặc định (prompt 04a/04b/04c) lẫn kịch bản
theo mẫu kênh (src/script_templates.py) — quy tắc độ dài được gắn SAU prompt và ưu tiên hơn số câu /
số từ ghi trong prompt / mẫu.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from .voice_tones import tone_rule

# Tốc độ giọng đọc (từ/phút; tiếng Việt: 1 âm tiết = 1 từ). Mặc định đo trên video thật của bạn: script 1.318 từ
# -> video 5:54 (gồm cả khoảng nghỉ giữa các cảnh) ≈ 225 từ/phút. Mỗi giọng / công cụ đọc một tốc độ khác -> chỉnh / đo lại trong app, lưu chung
# mọi run ở data/voice_speed.json.
WPM = 225
SPEED_FILE = Path(__file__).resolve().parent.parent / "data" / "voice_speed.json"


def load_wpm() -> int:
    try:
        w = int(json.loads(SPEED_FILE.read_text(encoding="utf-8"))["wpm"])
        return w if 60 <= w <= 500 else WPM
    except (OSError, ValueError, KeyError, TypeError):
        return WPM


def save_wpm(wpm: int) -> None:
    SPEED_FILE.parent.mkdir(parents=True, exist_ok=True)
    SPEED_FILE.write_text(json.dumps({"wpm": int(wpm)}), encoding="utf-8")


def measure_wpm(run_dir: Optional[Path]) -> Optional[dict]:
    """Đo tốc độ đọc THẬT từ audio đã sinh của run (06_media_manifest.json: chữ của từng đoạn + mốc thời gian câu)."""
    if run_dir is None:
        return None
    path = Path(run_dir) / "06_media_manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    words = sum(count_words(b.get("text", "")) for seg in manifest for b in seg.get("beats", []))
    secs = sum(max((b.get("end") or 0) - (b.get("start") or 0), 0) for seg in manifest for b in seg.get("beats", []))
    if words < 50 or secs < 20:
        return None
    return {"wpm": int(round(words / secs * 60)), "words": words, "seconds": secs}
LENGTH_FILE = "script_length.json"
DEFAULT_MINUTES = (6, 9)
HOOK_SHARE = 0.08
CTA_SHARE = 0.08


def load_target(run_dir: Optional[Path]) -> tuple[int, int]:
    if run_dir is not None:
        p = Path(run_dir) / LENGTH_FILE
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            lo, hi = int(d["min_minutes"]), int(d["max_minutes"])
            if 1 <= lo <= hi:
                return lo, hi
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return DEFAULT_MINUTES


def _flag(run_dir: Optional[Path], name: str, default: bool = True) -> bool:
    if run_dir is not None:
        try:
            return bool(json.loads((Path(run_dir) / LENGTH_FILE).read_text(encoding="utf-8")).get(name, default))
        except (OSError, ValueError):
            pass
    return default


def load_sfx(run_dir: Optional[Path]) -> bool:
    """Có chèn hiệu ứng âm thanh [Sound Effect: ...] vào script không (mặc định CÓ)."""
    return _flag(run_dir, "sfx")


def load_tones(run_dir: Optional[Path]) -> bool:
    """Có gắn thẻ giọng đọc [Giọng trầm, nghiêm túc]... đầu mỗi đoạn ý không (mặc định CÓ)."""
    return _flag(run_dir, "tones")


def save_target(run_dir: Path, lo: int, hi: int, sfx: bool = True, tones: bool = True) -> None:
    (Path(run_dir) / LENGTH_FILE).write_text(
        json.dumps({"min_minutes": lo, "max_minutes": hi, "sfx": sfx, "tones": tones},
                   ensure_ascii=False), encoding="utf-8")


def budget(lo_min: float, hi_min: float, n_products: int = 5, sfx: bool = False, tones: bool = False,
           wpm: Optional[int] = None) -> dict:
    """Khoảng số từ (min, max) cho từng phần."""
    n = max(n_products, 1)
    wpm = wpm or load_wpm()
    lo_w, hi_w = lo_min * wpm, hi_min * wpm
    prod_share = (1 - HOOK_SHARE - CTA_SHARE) / n
    r = lambda share: (int(round(lo_w * share / 5) * 5), int(round(hi_w * share / 5) * 5))
    return {"minutes": (lo_min, hi_min), "total": (int(lo_w), int(hi_w)), "hook": r(HOOK_SHARE),
            "product": r(prod_share), "cta": r(CTA_SHARE), "n_products": n, "sfx": sfx, "tones": tones,
            "wpm": wpm}


def key(b: Optional[dict]) -> Optional[str]:
    """Chuỗi nhận diện mục tiêu — đổi mục tiêu thì cache tiến độ script cũ không dùng lại."""
    return None if not b else f"{b['minutes'][0]}-{b['minutes'][1]}@{b.get('wpm')}" + ("-sfx" if b.get("sfx") else "") + ("-tones" if b.get("tones") else "")


_SFX_LIST = "Pop, Ding, Chime, Swoosh, Tension, Heartbeat, Tick Tock, Win"
_SFX_RULES = {
    "hook": "[Sound Effect: Heartbeat] ở DÒNG ĐẦU TIÊN (trước câu hỏi nỗi đau); [Sound Effect: Pop] ngay trước câu "
            "\"Dừng lại 3 giây!\" mời mở bình luận ghim.",
    "product": "Dòng ĐẦU TIÊN của lời bình (nếu có [[IMG:1]] thì đặt ngay SAU dòng [[IMG:1]]): tiếng mở Top — đổi luân phiên "
               "giữa các Top cho đỡ nhàm: Top 5 [Sound Effect: Swoosh], Top 4 [Sound Effect: Chime], Top 3 [Sound Effect: "
               "Tension], Top 2 [Sound Effect: Swoosh], Top 1 [Sound Effect: Heartbeat]. Có thể thêm [Sound Effect: Ding] "
               "trước câu \"bấm link số …\".",
    "cta": "[Sound Effect: Tick Tock] ở dòng đầu (câu \"Tóm cái váy lại…\"); [Sound Effect: Ding] trước câu kêu gọi bấm link.",
}


def sfx_rule(part: str) -> str:
    part = {"intro": "hook", "outro": "cta"}.get(part, part)
    return (f"\nHIỆU ỨNG ÂM THANH (hệ thống chèn âm thanh thật): mỗi chỉ dẫn nằm RIÊNG 1 dòng, giữa 2 câu, tên chỉ "
            f"chọn trong: {_SFX_LIST}. {_SFX_RULES[part]} Tối đa 3 hiệu ứng trong đoạn này, không dùng 2 hiệu ứng "
            "liền nhau.\n")


def rule(b: Optional[dict], part: str) -> str:
    """Câu quy định độ dài gắn vào cuối prompt. part: hook | product | cta | intro | outro."""
    if not b:
        return ""
    part = {"intro": "hook", "outro": "cta"}.get(part, part)
    lo, hi = b[part]
    sec = lambda w: int(round(w / (b.get("wpm") or WPM) * 60))
    extra = {
        "hook": "Câu đầu tiên phải vào thẳng vấn đề / lời hứa của tiêu đề.",
        "product": "Nếu có nhiều ảnh, mỗi ảnh chỉ 1 câu ngắn — tổng cả đoạn vẫn phải nằm trong khoảng này.",
        "cta": "",
    }[part]
    return (sfx_rule(part) if b.get("sfx") else "") + (tone_rule(part) if b.get("tones") else "") + (f"\n\nĐỘ DÀI BẮT BUỘC (ưu tiên hơn mọi số câu / số từ ghi ở trên): đoạn này khoảng {lo}–{hi} từ "
            f"(~{sec(lo)}–{sec(hi)} giây đọc; video mục tiêu {b['minutes'][0]}–{b['minutes'][1]} phút). "
            f"Không độn ý, không lặp lại. {extra}").rstrip() + "\n"


_TAG_RE = re.compile(r"\[\[IMG:\d+\]\]|\[[^\[\]]*\]")


def count_words(text: str) -> int:
    """Số từ được ĐỌC (bỏ thẻ [..], [[IMG:n]], chỉ dẫn [Text on screen] / [Sound Effect])."""
    return len(_TAG_RE.sub(" ", text or "").split())


def minutes(words: int, wpm: Optional[int] = None) -> float:
    return words / (wpm or load_wpm())


def fmt_minutes(words: int, wpm: Optional[int] = None) -> str:
    s = int(round(words / (wpm or load_wpm()) * 60))
    return f"{s // 60}:{s % 60:02d}"
