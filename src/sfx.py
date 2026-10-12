"""Hiệu ứng âm thanh (SFX) cho chỉ dẫn [Sound Effect: ...] trong script.

edge-tts KHÔNG tự phát hiệu ứng âm thanh — chỉ dẫn [Sound Effect: X] được src/tts.py đổi thành 1 đoạn âm
thanh thật chèn vào audio ngay vị trí đó (giữa 2 câu).

Âm thanh lấy theo thứ tự:
  1. File của bạn: assets/sfx/<tên>.mp3 / .wav (vd assets/sfx/swoosh.mp3) — muốn đổi tiếng nào thì thả file
     đúng tên vào đây.
  2. Không có -> tool tự TỔNG HỢP (numpy, không tải gì từ mạng, không lo bản quyền), lưu cache ở
     assets/sfx/_auto/<tên>.wav.

Tên trong chỉ dẫn được khớp gần đúng (tiếng Anh hoặc tiếng Việt): "Tiếng vút chuyển cảnh" -> swoosh,
"nhạc chiến thắng" -> win, "tích tắc" -> ticktock... Không nhận ra -> pop.
"""
from __future__ import annotations

import re
import unicodedata
import wave
from pathlib import Path
from typing import Optional

import numpy as np

SFX_DIR = Path(__file__).resolve().parent.parent / "assets" / "sfx"
AUTO_DIR = SFX_DIR / "_auto"
SR = 44100
PEAK = 0.32          # ~ -10 dBFS: nghe rõ nhưng không át giọng đọc

SFX_NAMES = {
    "pop": "Pop — tiếng bụp ngắn (mở ý, nhấn 1 chi tiết)",
    "ding": "Ding — tiếng ting (nhắc link / con số đáng chú ý)",
    "chime": "Chime — chuông ngân (mở đầu / kết)",
    "swoosh": "Swoosh — tiếng vút (chuyển cảnh, sang sản phẩm mới)",
    "tension": "Tension — nốt trầm căng dần (trước cảnh báo / nhược điểm)",
    "heartbeat": "Heartbeat — nhịp tim (hồi hộp trước khi công bố)",
    "ticktock": "Tick Tock — đồng hồ tích tắc (nhắc giá đổi theo ngày)",
    "win": "Win — hợp âm đi lên (công bố Top 1)",
}

# Khớp theo thứ tự (cụ thể trước, chung chung sau) — so trên chữ thường KHÔNG dấu.
_RULES = [
    ("ticktock", ("tick", "tich tac", "dong ho")),
    ("heartbeat", ("heart", "nhip tim", "tim dap")),
    ("tension", ("tension", "hoi hop", "cang thang", "kich tinh", "suspense")),
    ("win", ("win", "chien thang", "fanfare", "ta da", "tada", "chuc mung", "vinh quang", "quan quan")),
    ("swoosh", ("swoosh", "whoosh", "vut", "chuyen canh", "luot", "transition")),
    ("chime", ("chime", "chuong", "leng keng")),
    ("ding", ("ding", "ting", "thong bao", "bell", "notification")),
    ("pop", ("pop", "bup", "bong", "click")),
]

SFX_TAG_RE = re.compile(r"^\s*(?:sound\s*effect|sfx|hiệu\s*ứng(?:\s*âm\s*thanh)?|âm\s*thanh)\s*:\s*(.+)$",
                        re.IGNORECASE)


def _ascii(text: str) -> str:
    t = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


# Thẻ trần [Heartbeat], [Pop]... (AI đôi khi viết thiếu "Sound Effect:") — vẫn nhận là hiệu ứng âm thanh.
_BARE_SFX_RE = re.compile(r"^\s*(pop|ding|chime|swoosh|tension|heart\s*beat|tick\s*tock|win)\s*$", re.IGNORECASE)


def sfx_from_tag(tag_inner: str) -> Optional[str]:
    """Nội dung trong [...] -> tên SFX, hoặc None nếu đây không phải chỉ dẫn âm thanh."""
    m = SFX_TAG_RE.match(tag_inner or "") or _BARE_SFX_RE.match(tag_inner or "")
    if not m:
        return None
    return sfx_key(m.group(1))


def sfx_key(desc: str) -> str:
    a = _ascii(desc)
    for key, words in _RULES:
        if any(re.search(rf"(?<![a-z]){re.escape(w)}", a) for w in words):
            return key
    return "pop"


# ------------------------------------------------------------- tổng hợp âm --

def _t(sec: float) -> np.ndarray:
    return np.arange(int(SR * sec)) / SR


def _env(n: int, attack: float, decay_rate: float) -> np.ndarray:
    t = np.arange(n) / SR
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    return a * np.exp(-t * decay_rate)


def _bell(freq: float, sec: float, decay: float, partials=((1, 1.0), (2.76, 0.4), (5.4, 0.15))) -> np.ndarray:
    t = _t(sec)
    sig = sum(amp * np.sin(2 * np.pi * freq * mult * t) for mult, amp in partials)
    return sig * _env(len(t), 0.004, decay)


def _synth(key: str) -> np.ndarray:
    rng = np.random.default_rng(7)
    if key == "pop":
        t = _t(0.14)
        f = 520 * np.exp(-t * 18) + 140
        sig = np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(len(t), 0.002, 30)
    elif key == "ding":
        sig = _bell(1318.5, 0.7, 6.0)
    elif key == "chime":
        sig = sum(_bell(f, 1.1, 3.5) * (0.6 if i else 1.0) for i, f in enumerate((880.0, 1108.7, 1318.5)))
    elif key == "swoosh":
        n = int(SR * 0.5)
        noise = rng.standard_normal(n)
        # lọc thông thấp có tần số cắt tăng rồi giảm (quét) -> tiếng "vút"
        cut = np.sin(np.linspace(0, np.pi, n)) * 0.25 + 0.02
        out, y = np.zeros(n), 0.0
        for i in range(n):
            y += cut[i] * (noise[i] - y)
            out[i] = y
        sig = out * np.sin(np.linspace(0, np.pi, n)) ** 2
    elif key == "tension":
        t = _t(1.3)
        sig = (np.sin(2 * np.pi * 110 * t) + 0.8 * np.sin(2 * np.pi * 116.5 * t)
               + 0.3 * np.sin(2 * np.pi * 220 * t)) * np.clip(t / 1.0, 0, 1) ** 1.5
        sig *= np.where(t > 1.15, np.clip((1.3 - t) / 0.15, 0, 1), 1)
    elif key == "heartbeat":
        def thump(f=55.0):
            t = _t(0.18)
            return np.sin(2 * np.pi * f * t) * _env(len(t), 0.003, 22)
        sig = np.zeros(int(SR * 1.25))
        for start, amp in ((0.0, 1.0), (0.22, 0.75), (0.68, 1.0), (0.9, 0.75)):
            b = thump() * amp
            i = int(start * SR)
            sig[i:i + len(b)] += b[: len(sig) - i]
    elif key == "ticktock":
        sig = np.zeros(int(SR * 1.05))
        for k in range(4):
            t = _t(0.03)
            click = np.sin(2 * np.pi * (2400 if k % 2 == 0 else 1700) * t) * _env(len(t), 0.0005, 160)
            i = int(k * 0.26 * SR)
            sig[i:i + len(click)] += click
    elif key == "win":
        notes, sig = (523.25, 659.25, 783.99, 1046.5), np.zeros(0)
        for i, f in enumerate(notes):
            dur = 0.13 if i < len(notes) - 1 else 0.7
            sig = np.concatenate([sig, _bell(f, dur, 4.0 if i == len(notes) - 1 else 9.0,
                                             partials=((1, 1.0), (2, 0.35), (3, 0.12)))])
    else:
        return _synth("pop")
    sig = sig / (np.max(np.abs(sig)) or 1.0) * PEAK
    pad = np.zeros(int(SR * 0.08))           # nghỉ ngắn trước/sau để không dính vào giọng đọc
    return np.concatenate([pad, sig, pad])


def _write_wav(path: Path, sig: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(sig, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def sfx_path(key: str) -> Path:
    """File âm thanh cho 1 SFX: file của bạn trong assets/sfx/ (nếu có) -> bản tự tổng hợp."""
    key = key if key in SFX_NAMES else "pop"
    for ext in (".mp3", ".wav", ".m4a", ".ogg"):
        p = SFX_DIR / f"{key}{ext}"
        if p.exists():
            return p
    p = AUTO_DIR / f"{key}.wav"
    if not p.exists():
        _write_wav(p, _synth(key))
    return p
