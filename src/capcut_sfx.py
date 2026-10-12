"""Hiệu ứng âm thanh [Sound Effect: ...] cho project CapCut khi dùng GIỌNG TỰ THU.

Giọng máy (edge-tts) đã được chèn SFX thật ngay trong file audio (src/tts.py). Giọng tự thu (Google Vids,
ElevenLabs...) thì không -> khi xuất CapCut, tool đặt SFX trên 1 track âm thanh riêng, đúng câu có thẻ trong
04_script.txt (mốc thời gian từng câu lấy từ 06_media_manifest.json do bước nhập giọng tự thu tính ra).
Track riêng nên kéo / xoá / chỉnh âm lượng từng tiếng trong CapCut rất dễ.
"""
from __future__ import annotations

import re
from pathlib import Path

from .script_parser import parse_segments
from .sfx import sfx_from_tag

_TAG_RE = re.compile(r"(?<!\[)\[([^\[\]]+)\](?!\])")
_IMG_RE = re.compile(r"\[\[IMG:\d+\]\]")
_SENT_RE = re.compile(r"(?<=[.!?…])\s+")


def recorded_voice(run_dir: Path) -> bool:
    """Audio hiện tại là giọng tự thu (không có SFX bên trong) — file gộp của bước nhập giọng mới nhất."""
    run_dir = Path(run_dir)
    full = run_dir / "audio" / "_giong_tu_thu_full.mp3"
    if not full.exists():
        return False
    others = [p for p in (run_dir / "audio").glob("*.mp3") if p.name != full.name]
    return all(p.stat().st_mtime <= full.stat().st_mtime + 120 for p in others)


def _sentences_before(raw: str) -> int:
    text = " ".join(_TAG_RE.sub(" ", _IMG_RE.sub(" ", raw)).split())
    return len([s for s in _SENT_RE.split(text) if s.strip()]) if text else 0


def segment_sfx(raw_text: str, beats: list[dict]) -> list[tuple[float, str]]:
    """[(giây tính từ đầu đoạn, tên SFX)] — SFX đặt ngay trước câu đứng sau thẻ."""
    out = []
    for m in _TAG_RE.finditer(raw_text or ""):
        key = sfx_from_tag(m.group(1))
        if not key:
            continue
        n = _sentences_before(raw_text[:m.start()])
        if not beats:
            t = 0.0
        elif n < len(beats):
            t = float(beats[n]["start"])
        else:
            t = float(beats[-1]["end"])
        out.append((t, key))
    return out


def sfx_events(run_dir: Path, manifest: list[dict]) -> dict:
    """{order của đoạn trong manifest: [(giây trong đoạn, tên SFX)]} theo 04_script.txt hiện tại."""
    script = Path(run_dir) / "04_script.txt"
    if not script.exists():
        return {}
    try:
        segs = parse_segments(script.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    by_index = {s.index: s for s in segs}
    out = {}
    for entry in manifest:
        seg = by_index.get(entry.get("order"))
        if seg is None:
            continue
        raw = "\n".join(b["raw_text"] for b in seg.image_blocks) if seg.image_blocks else seg.raw_text
        ev = segment_sfx(raw, entry.get("beats") or [])
        if ev:
            out[entry.get("order")] = ev
    return out
