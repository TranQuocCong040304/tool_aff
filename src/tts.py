"""Sinh audio bằng Edge-TTS (miễn phí, không chính thức — xem rủi ro trong bản
kế hoạch: có thể ngừng hoạt động bất kỳ lúc nào không báo trước).

Mỗi KHỐI (block — ứng với 1 ảnh cụ thể, hoặc cả đoạn cho hook/CTA) được
synthesize bằng ĐÚNG 1 lệnh gọi API liên tục để giữ giọng đọc tự nhiên, liền
mạch. Phản hồi thật từ người dùng: tách nhiều lệnh gọi riêng cho từng thẻ cảm
xúc (thiết kế trước đây) dù cùng tên giọng vẫn nghe như đổi sang người khác ở
mỗi câu — mỗi lệnh gọi edge-tts là 1 lượt suy luận độc lập, không có gì đảm
bảo 2 lượt liên tiếp giữ đúng y hệt tông giọng/nhịp điệu.

Đã kiểm tra source code thư viện `edge_tts`: `Communicate` luôn bọc TOÀN BỘ
text trong 1 thẻ SSML `<prosody>` duy nhất (mkssml trong communicate.py), text
truyền vào bị escape trước khi chèn — nên KHÔNG có cách nào chèn nhiều mức
prosody khác nhau trong 1 request qua API công khai của thư viện.

Chỉ dẫn [Sound Effect: ...] được thay bằng ÂM THANH THẬT chèn vào đúng chỗ (src/sfx.py).

Vì vậy: thẻ cảm xúc [cười nhẹ], [thở dài]... không còn đổi pitch/tốc độ nữa,
mà chỉ tạo KHOẢNG LẶNG gần đúng vị trí thẻ — bằng cách cắt audio đã
synthesize liền mạch (1 lệnh gọi cho cả block) tại ranh giới câu
(SentenceBoundary) gần nhất rồi chèn silence. Audio gốc không bị synthesize
lại nên giọng vẫn nhất quán 100% trong cùng 1 block.
"""
import asyncio
import re
import shutil
import time
import wave
from pathlib import Path
from typing import List, Optional, Tuple

import edge_tts
from moviepy import AudioClip, AudioFileClip, concatenate_audioclips

from .sfx import sfx_from_tag, sfx_path

DEFAULT_VOICE = "vi-VN-HoaiMyNeural"  # giọng nữ. Giọng nam: vi-VN-NamMinhNeural

# Gemini TTS: dùng THAY edge-tts cho hook/CTA (không cần đồng bộ ảnh-theo-câu)
# vì model không trả timing từng câu — xem GeminiClient.generate_speech và
# src/media_step.py. Giọng có cảm xúc thật (đọc trực tiếp thẻ [hào hứng]...)
# thay vì chỉ tạo khoảng lặng như edge-tts.
DEFAULT_GEMINI_TTS_VOICE = "Kore"
_GEMINI_TTS_SAMPLE_RATE = 24000
_GEMINI_TTS_SAMPLE_WIDTH = 2  # 16-bit PCM

# Khoảng lặng (ms) chèn gần vị trí thẻ cảm xúc — khớp gần đúng theo từ khóa vì
# LLM có thể viết biến thể khác nhau (vd "cười tươi" thay vì "cười nhẹ").
_PAUSE_MS_RULES = [
    (("cười", "haha", "cười lớn", "cười tươi"), 150),
    (("nhấn mạnh", "khẳng định"), 90),
    (("thở dài", "buồn"), 320),
    (("ngập ngừng", "lưỡng lự", "băn khoăn"), 260),
    (("hào hứng", "phấn khích", "wow"), 110),
    (("gật gù", "đồng ý"), 140),
    (("thì thầm", "nhỏ nhẹ"), 130),
]
DEFAULT_PAUSE_MS = 90  # thẻ lạ: vẫn ngắt nhẹ dù không nhận diện được

_TAG_RE = re.compile(r"\[([^\]]*)\]")


def _match_pause_ms(tag_text: str) -> int:
    tag_text = tag_text.strip().lower()
    for keywords, ms in _PAUSE_MS_RULES:
        if any(kw in tag_text for kw in keywords):
            return ms
    return DEFAULT_PAUSE_MS


_SCREEN_TAG_RE = re.compile(r"^\s*text\s*on\s*screen\s*:", re.IGNORECASE)


def _parse_tags(raw_text_with_tags: str) -> Tuple[str, List[Tuple[int, int]], List[Tuple[int, str]]]:
    """Bỏ mọi thẻ [...] khỏi text, trả về (text sạch để đọc,
    [(vị trí ký tự trong text sạch, pause_ms)] cho thẻ cảm xúc,
    [(vị trí ký tự, tên SFX)] cho chỉ dẫn [Sound Effect: ...] — chèn âm thanh thật, xem src/sfx.py).
    [Text on screen: ...] là chỉ dẫn dựng video -> bỏ hẳn, không ngắt giọng."""
    clean_parts: List[str] = []
    pauses: List[Tuple[int, int]] = []
    sfx: List[Tuple[int, str]] = []
    last_end = 0
    lead = len(raw_text_with_tags) - len(raw_text_with_tags.lstrip())   # text sạch bị strip() đầu
    for m in _TAG_RE.finditer(raw_text_with_tags):
        clean_parts.append(raw_text_with_tags[last_end:m.start()])
        char_pos = max(0, sum(len(p) for p in clean_parts) - lead)
        inner = m.group(1)
        key = sfx_from_tag(inner)
        if key:
            sfx.append((char_pos, key))
        elif not _SCREEN_TAG_RE.match(inner):
            pauses.append((char_pos, _match_pause_ms(inner)))
        last_end = m.end()
    clean_parts.append(raw_text_with_tags[last_end:])
    clean_text = "".join(clean_parts).strip()
    return clean_text, pauses, sfx


def _strip_tags_track_pauses(raw_text_with_tags: str) -> Tuple[str, List[Tuple[int, int]]]:
    """(text sạch, khoảng lặng) — giữ cho code cũ; SFX xem _parse_tags."""
    clean_text, pauses, _ = _parse_tags(raw_text_with_tags)
    return clean_text, pauses


async def _synthesize_block_raw(text: str, out_path: Path, voice: str, rate: str) -> List[dict]:
    """1 lệnh gọi API duy nhất cho toàn bộ text của 1 block — audio liên tục,
    kèm timing từng câu (SentenceBoundary) để cắt/chèn khoảng lặng sau này."""
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
    audio_bytes = bytearray()
    sentences: List[dict] = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_bytes.extend(chunk["data"])
        elif chunk["type"] == "SentenceBoundary":
            sentences.append(
                {
                    "text": chunk["text"],
                    "start": chunk["offset"] / 10_000_000,
                    "end": (chunk["offset"] + chunk["duration"]) / 10_000_000,
                }
            )
    if not audio_bytes:
        raise RuntimeError("edge-tts trả về audio rỗng.")
    out_path.write_bytes(bytes(audio_bytes))
    return sentences


def _synthesize_block_with_retry(
    text: str, out_path: Path, voice: str, rate: str, retries: int, retry_delay: float
) -> List[dict]:
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            return asyncio.run(_synthesize_block_raw(text, out_path, voice, rate))
        except Exception as e:
            last_error = e
            if attempt < retries:
                # Lỗi "No audio was received" thường là edge-tts chặn tạm vì gọi
                # dồn dập (cùng 1 câu lúc lỗi lúc được) — chờ tăng dần cho server nguôi.
                time.sleep(min(retry_delay * 2 ** (attempt - 1), 30.0))
    raise RuntimeError(f"edge-tts thất bại sau {retries} lần thử: {last_error}") from last_error


def _synthesize_block_by_sentence(
    text: str, out_path: Path, voice: str, rate: str, retries: int, retry_delay: float
) -> List[dict]:
    """Phương án dự phòng khi cả block bị edge-tts từ chối mãi (đã gặp: 1 block
    lỗi liền 9 lần, trong khi từng câu của nó gọi riêng vẫn được). Mỗi câu 1
    lệnh gọi — giọng có thể lệch nhẹ giữa các câu (xem đầu file), nhưng còn
    hơn mất trắng cả đoạn sản phẩm trong video."""
    parts = [p for p in re.split(r"(?<=[.!?…])\s+", text.strip()) if p.strip()]
    clips, sentences, cursor = [], [], 0.0
    try:
        for i, part in enumerate(parts):
            part_path = out_path.with_name(f"{out_path.stem}_s{i:02d}.mp3")
            _synthesize_block_with_retry(part, part_path, voice, rate, retries, retry_delay)
            clip = AudioFileClip(str(part_path))
            clips.append(clip)
            sentences.append({"text": part, "start": cursor, "end": cursor + clip.duration})
            cursor += clip.duration
        merged = concatenate_audioclips(clips)
        merged.write_audiofile(str(out_path), logger=None)
        merged.close()
    finally:
        for c in clips:
            c.close()
    return sentences


def _sentence_starts(clean_text: str, sentences: List[dict]) -> List[int]:
    """Vị trí ký tự bắt đầu của từng câu (edge-tts SentenceBoundary) trong text sạch — tìm tuần tự theo
    ~12 ký tự đầu của câu; không thấy thì ước lượng bằng độ dài cộng dồn."""
    norm = lambda t: re.sub(r"\s+", " ", t)
    hay = norm(clean_text)
    # ánh xạ vị trí trong chuỗi đã gộp khoảng trắng -> vị trí trong clean_text
    pos_map, j = [], 0
    for i, ch in enumerate(clean_text):
        if ch.isspace() and pos_map and clean_text[i - 1].isspace():
            continue
        pos_map.append(i)
    out, cur = [], 0
    for s in sentences:
        needle = norm(s.get("text", "")).strip()[:12]
        k = hay.find(needle, cur) if needle else -1
        if k < 0:
            k = min(cur, len(hay))
        out.append(pos_map[k] if k < len(pos_map) else len(clean_text))
        cur = k + max(len(needle), 1)
    return out


def _sentence_index_for_char(sentences: List[dict], char_pos: int) -> int:
    """Câu nào (theo thứ tự) chứa gần đúng vị trí ký tự char_pos — xấp xỉ
    bằng độ dài cộng dồn, đủ dùng để chèn khoảng lặng gần đúng chỗ."""
    cum = 0
    for i, s in enumerate(sentences):
        cum += len(s["text"]) + 1
        if char_pos <= cum:
            return i
    return len(sentences) - 1 if sentences else 0


def synthesize_beats(
    blocks: List[dict],
    out_path: Path,
    voice: str = DEFAULT_VOICE,
    base_rate: str = "+0%",
    retries: int = 6,
    retry_delay: float = 3.0,
    inter_block_delay: float = 0.6,
) -> List[dict]:
    """blocks: [{"text_with_tags": str, "image": Optional[str]}] — mỗi block
    ứng với 1 ảnh cụ thể (hoặc image=None nếu không gắn ảnh nào, vd hook/CTA).
    MỖI BLOCK = ĐÚNG 1 lệnh gọi edge-tts (giọng liền mạch trong cả block).

    Trả về beats: [{"text", "start", "end", "image"}] (giây) — mỗi câu trong
    block là 1 beat (để phụ đề tiến triển + đổi ảnh nếu cần), giữ nguyên
    "image" của block cha.
    """
    if not blocks:
        raise ValueError("Không có block nào để sinh audio.")

    tmp_dir = out_path.parent / f".{out_path.stem}_chunks"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    audio_segments = []  # clip đưa vào concatenate_audioclips cuối cùng
    sfx_clips = {}  # tên SFX -> AudioFileClip (dùng lại, đóng ở cuối)

    def _sfx(key: str):
        if key not in sfx_clips:
            sfx_clips[key] = AudioFileClip(str(sfx_path(key)))
        return sfx_clips[key]
    block_audios = []  # AudioFileClip gốc của từng block — phải sống tới lúc export xong
    beats = []
    cursor = 0.0
    block_idx = 0
    try:
        for block in blocks:
            clean_text, pauses, sfx_events = _parse_tags(block.get("text_with_tags", ""))
            if not clean_text.strip():
                continue

            block_path = tmp_dir / f"{block_idx:03d}.mp3"
            if block_idx > 0 and inter_block_delay > 0:
                # Giãn nhẹ giữa các block — gọi liên tiếp không nghỉ dễ bị
                # edge-tts từ chối "No audio was received" (đã gặp thật, kể cả
                # sau 5 lần retry, vì retry cũng dồn dập không nghỉ).
                time.sleep(inter_block_delay)
            block_idx += 1
            try:
                sentences = _synthesize_block_with_retry(clean_text, block_path, voice, base_rate, retries, retry_delay)
            except RuntimeError as e:
                print(f"  [CẢNH BÁO] {e} — thử sinh từng câu riêng cho block này.")
                sentences = _synthesize_block_by_sentence(clean_text, block_path, voice, base_rate, retries, retry_delay)

            block_audio = AudioFileClip(str(block_path))
            block_audios.append(block_audio)

            if not sentences:
                # Text ngắn đôi khi không trả SentenceBoundary -> coi cả block là 1 câu.
                sentences = [{"text": clean_text, "start": 0.0, "end": block_audio.duration}]

            # Khoảng lặng cần chèn SAU câu nào (lấy pause lớn nhất nếu nhiều thẻ rơi vào cùng 1 câu).
            pause_after = [0] * len(sentences)
            for char_pos, pause_ms in pauses:
                idx = _sentence_index_for_char(sentences, char_pos)
                pause_after[idx] = max(pause_after[idx], pause_ms)
            # Hiệu ứng âm thanh: phát NGAY TRƯỚC câu đầu tiên bắt đầu sau vị trí thẻ (thẻ cuối block -> sau câu cuối).
            starts = _sentence_starts(clean_text, sentences)
            sfx_before: List[List[str]] = [[] for _ in range(len(sentences) + 1)]
            for char_pos, key in sfx_events:
                idx = next((j for j, st in enumerate(starts) if st >= char_pos), len(sentences))
                sfx_before[idx].append(key)

            for i, s in enumerate(sentences):
                for key in sfx_before[i]:
                    clip = _sfx(key)
                    audio_segments.append(clip)
                    cursor += clip.duration
                start = max(0.0, min(s["start"], block_audio.duration))
                end = max(start, min(s["end"], block_audio.duration))
                if end <= start:
                    continue
                sub = block_audio.subclipped(start, end)
                audio_segments.append(sub)
                beats.append(
                    {
                        "text": s["text"],
                        "start": cursor,
                        "end": cursor + sub.duration,
                        "image": block.get("image"),
                    }
                )
                cursor += sub.duration

                if pause_after[i] > 0 and not sfx_before[i + 1]:   # có hiệu ứng ngay sau thì khỏi chèn lặng
                    pause_s = pause_after[i] / 1000.0
                    silence = AudioClip(lambda t: 0, duration=pause_s, fps=24000)
                    audio_segments.append(silence)
                    cursor += pause_s

            for key in sfx_before[len(sentences)]:
                clip = _sfx(key)
                audio_segments.append(clip)
                cursor += clip.duration

        if not audio_segments:
            raise ValueError("Không có mảnh text nào để sinh audio (toàn bộ block rỗng).")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        final_audio = concatenate_audioclips(audio_segments)
        final_audio.write_audiofile(str(out_path), logger=None)
        final_audio.close()
    finally:
        for c in audio_segments:
            try:
                c.close()
            except Exception:
                pass
        for c in list(block_audios) + list(sfx_clips.values()):
            try:
                c.close()
            except Exception:
                pass
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return beats


def synthesize(
    text_with_tags: str,
    out_path: Path,
    voice: str = DEFAULT_VOICE,
    rate: str = "+0%",
    retries: int = 5,
    retry_delay: float = 3.0,
) -> List[dict]:
    """Tiện ích cho đoạn KHÔNG gắn với ảnh cụ thể (hook/CTA, hoặc sản phẩm
    không có ảnh chi tiết) — 1 block duy nhất, image=None cho mọi beat."""
    if not text_with_tags.strip():
        raise ValueError("Không thể sinh audio từ text rỗng.")
    return synthesize_beats(
        [{"text_with_tags": text_with_tags, "image": None}],
        out_path, voice=voice, base_rate=rate, retries=retries, retry_delay=retry_delay,
    )


def synthesize_gemini(
    text_with_tags: str,
    out_path: Path,
    gemini_client,
    voice_name: str = DEFAULT_GEMINI_TTS_VOICE,
) -> List[dict]:
    """Sinh audio qua Gemini TTS (model gemini-3.1-flash-tts-preview) — CHỈ
    dùng cho đoạn không cần đồng bộ ảnh-theo-câu (hook/CTA, xem
    src/media_step.py). Model đọc trực tiếp thẻ cảm xúc [hào hứng]... như chỉ
    dẫn phong cách thật (không strip/chuyển thành khoảng lặng như edge-tts) —
    nhưng KHÔNG trả về timing từng câu, nên trả về ĐÚNG 1 beat cho cả đoạn."""
    # Gemini TTS đọc thẻ như chỉ dẫn giọng — bỏ chỉ dẫn hiệu ứng / chữ trên màn hình để nó không đọc thành lời.
    text_with_tags = re.sub(r"\[\s*(?:sound\s*effect|sfx|text\s*on\s*screen)\s*:[^\]]*\]", "", text_with_tags,
                            flags=re.IGNORECASE)
    pcm = gemini_client.generate_speech(text_with_tags, voice_name=voice_name)
    if not pcm:
        raise RuntimeError("Gemini TTS trả về audio rỗng.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(_GEMINI_TTS_SAMPLE_WIDTH)
        f.setframerate(_GEMINI_TTS_SAMPLE_RATE)
        f.writeframes(pcm)

    duration = len(pcm) / (_GEMINI_TTS_SAMPLE_RATE * _GEMINI_TTS_SAMPLE_WIDTH)
    clean_text = _TAG_RE.sub("", text_with_tags).strip()
    return [{"text": clean_text, "start": 0.0, "end": duration, "image": None}]
