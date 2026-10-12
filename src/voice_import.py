"""Giọng TỰ THU thay cho edge-tts (vốn chỉ có 2 giọng tiếng Việt HoaiMy/NamMinh).

Quy trình: recording_script() xuất kịch bản sạch (bỏ thẻ cảm xúc, [[IMG:n]])
-> người dùng thu ở Google Vids / ElevenLabs / tự đọc -> nộp lại 1 file liền
hoặc nhiều file theo đúng thứ tự (mp3/wav/m4a, cả mp4 vì Google Vids chỉ cho
tải video) -> import_recorded_voice() ghi ra 06_media_manifest.json Y HỆT
định dạng của run_media_step, nên ghép video / xuất CapCut không phải sửa gì.

Cái khó là mốc thời gian từng câu (để đổi ảnh đúng lúc đọc, chạy phụ đề):
edge-tts trả sẵn, giọng tự thu thì không. Cách làm: Whisper trên Groq nghe lại
audio cho mốc thời gian TỪNG TỪ, rồi khớp chuỗi từ của script với chuỗi từ
nghe được (difflib) — đọc lệch vài chữ, Whisper nghe sai số/tên máy vẫn khớp
được nhờ các từ xung quanh. Điểm cắt giữa 2 câu/2 đoạn đặt ở GIỮA khoảng lặng
nên không cắt cụt chữ.
"""
import re
import shutil
import sys
import tempfile
import threading
import time
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from moviepy import AudioFileClip, concatenate_audioclips

from .media_step import finish_manifest, manifest_entry, plan_segment
from .script_parser import clean_narration, parse_segments
from .utils import match_by_name

AUDIO_TYPES = ["mp3", "wav", "m4a", "aac", "ogg", "flac", "mp4", "mov", "webm"]
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")
_MIN_COVERAGE = 0.5  # khớp dưới 50% số từ = gần như chắc chắn nộp nhầm file/đọc kịch bản khác


class _Log:
    """In tiến độ ra cửa sổ PowerShell đang chạy Streamlit (+ gọi callback để web hiện thanh %)."""

    def __init__(self, progress=None):
        self.progress = progress
        self.t0 = time.time()

    def __call__(self, pct: int, msg: str) -> None:
        line = (f"[{datetime.now():%H:%M:%S}] [Giọng tự thu] {pct:3d}% | {msg} "
                f"(đã chạy {time.time() - self.t0:.0f}s)")
        try:
            print(line, flush=True)
        except UnicodeEncodeError:   # PowerShell cũ không in được dấu tiếng Việt
            print(line.encode("ascii", "replace").decode(), flush=True)
        if self.progress:
            try:
                self.progress(pct, msg)
            except Exception:
                pass

    def heartbeat(self, pct: int, msg: str, every: float = 5.0):
        """Chạy nền: cứ `every` giây in 1 dòng 'vẫn đang chờ …' cho tới khi .set()."""
        stop = threading.Event()

        def run():
            start = time.time()
            while not stop.wait(every):
                print(f"[{datetime.now():%H:%M:%S}] [Giọng tự thu] {pct:3d}% | {msg} — vẫn đang chờ "
                      f"{time.time() - start:.0f}s...", flush=True)

        threading.Thread(target=run, daemon=True).start()
        return stop


def _sentences(text_with_tags: str) -> list[str]:
    text = " ".join(clean_narration(text_with_tags).split())
    return [s for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def _norm(word: str) -> str:
    """So khớp không phân biệt dấu/hoa thường/dấu câu — Whisper hay nhầm dấu."""
    word = unicodedata.normalize("NFD", word.lower().replace("đ", "d"))
    return "".join(c for c in word if c.isalnum() and not unicodedata.combining(c))


_IMG_RE = re.compile(r"\[\[IMG:\d+\]\]")
_SCREEN_RE = re.compile(r"\[\s*text\s*on\s*screen\s*:[^\]]*\]", re.IGNORECASE)


def _tagged_lines(raw: str) -> str:
    """Giữ thẻ giọng đọc + [Sound Effect: ...] (giọng AI đời mới đọc thẻ như chỉ dẫn), bỏ [[IMG:n]] và
    [Text on screen] (chỉ dẫn dựng hình)."""
    text = _SCREEN_RE.sub("", _IMG_RE.sub("", raw))
    lines = [" ".join(l.split()) for l in text.splitlines()]
    return "\n".join(l for l in lines if l)


def recording_script(script: str, selected_products: dict, keep_tags: bool = False) -> str:
    """Kịch bản để thu âm: mỗi đoạn 1 mục, chỉ còn lời đọc. keep_tags=True: giữ thẻ giọng đọc & hiệu ứng."""
    products_by_name = {p["name"]: p for p in selected_products.get("selected_products", [])}
    lines = [
        "KỊCH BẢN THU ÂM — đọc lần lượt từ trên xuống.",
        "Thu 1 file liền cả bài, hoặc mỗi đoạn 1 file (đặt tên 01, 02, 03... cho đúng thứ tự).",
        "Đọc lệch vài chữ không sao — tool tự nghe lại để khớp ảnh/phụ đề theo câu.",
        *(["Thẻ [..] là chỉ dẫn giọng đọc / hiệu ứng — KHÔNG đọc thành lời."] if keep_tags else []),
        "",
    ]
    for n, seg in enumerate(parse_segments(script), 1):
        if seg.kind == "hook":
            title = "MỞ ĐẦU"
        elif seg.kind == "cta":
            title = "KẾT"
        else:
            product = match_by_name(seg.product_name or "", products_by_name)
            rank = f"TOP {product['rank']} — " if product and product.get("rank") else ""
            title = f"{rank}{seg.product_name}"
        lines.append(f"=== {n}. {title} ===")
        raw_blocks = [b["raw_text"] for b in seg.image_blocks] if seg.image_blocks else [seg.raw_text]
        for raw in raw_blocks:
            text = _tagged_lines(raw) if keep_tags else " ".join(_sentences(raw))
            if text:
                lines += [text, ""]
    return "\n".join(lines).rstrip() + "\n"


def _merge_audio(files: list[Path], out_path: Path, log=None) -> float:
    clips = []
    for i, f in enumerate(files, 1):
        clip = AudioFileClip(str(f))
        clips.append(clip)
        if log:
            log(2 + int(6 * i / len(files)), f"Đọc file {i}/{len(files)}: {Path(f).name} ({clip.duration:.1f}s)")
    try:
        merged = concatenate_audioclips(clips) if len(clips) > 1 else clips[0]
        merged.write_audiofile(str(out_path), logger=None)
        return merged.duration
    finally:
        for c in clips:
            c.close()


def _align(units: list[dict], words: list[dict], total: float) -> float:
    """Gán units[i]["start"/"end"] (giây, trên file liền) từ mốc Whisper.
    Trả về tỉ lệ từ của script khớp được."""
    script_words, owner = [], []
    for i, u in enumerate(units):
        for w in u["text"].split():
            n = _norm(w)
            if n:
                script_words.append(n)
                owner.append(i)
    heard = [_norm(w["word"]) for w in words]

    matched = {}
    for a, b, size in SequenceMatcher(None, script_words, heard, autojunk=False).get_matching_blocks():
        for k in range(size):
            matched[a + k] = b + k

    first, last = {}, {}
    for u in units:
        u["n_words"], u["n_matched"] = 0, 0
    for o in owner:
        units[o]["n_words"] += 1
    for si, ti in matched.items():
        u = owner[si]
        units[u]["n_matched"] += 1
        first[u] = min(first.get(u, ti), ti)
        last[u] = max(last.get(u, ti), ti)
    for i, u in enumerate(units):
        u["start"] = words[first[i]]["start"] if i in first else None
        u["end"] = words[last[i]]["end"] if i in last else None

    # Câu không khớp được từ nào: chia đều khoảng trống giữa 2 câu khớp kề bên theo độ dài chữ.
    i = 0
    while i < len(units):
        if units[i]["start"] is not None:
            i += 1
            continue
        j = i
        while j < len(units) and units[j]["start"] is None:
            j += 1
        lo = units[i - 1]["end"] if i > 0 else 0.0
        hi = units[j]["start"] if j < len(units) else total
        span = max(hi - lo, 0.0)
        weights = [max(len(units[k]["text"]), 1) for k in range(i, j)]
        cursor = lo
        for k, wgt in zip(range(i, j), weights):
            units[k]["start"] = cursor
            cursor += span * wgt / sum(weights)
            units[k]["end"] = cursor
        i = j

    # Giữ đúng thứ tự (khớp nhầm có thể làm 1 câu "lùi" về trước câu trước nó).
    for k in range(1, len(units)):
        units[k]["start"] = max(units[k]["start"], units[k - 1]["start"])
        units[k]["end"] = max(units[k]["end"], units[k]["start"])
    return len(matched) / max(len(script_words), 1)


def import_recorded_voice(
    script: str,
    selected_products: dict,
    out_dir: Path,
    audio_files: list[Path],
    groq_client,
    text_client=None,
    progress=None,
) -> dict:
    """Dùng audio tự thu làm giọng đọc. Trả về báo cáo
    {"coverage", "duration", "segments": [{"name", "duration", "coverage"}]}.
    Tiến độ in ra cửa sổ PowerShell chạy Streamlit; `progress(pct, msg)` (không bắt buộc) để web hiện thanh %."""
    log = _Log(progress)
    try:
        return _import(script, selected_products, out_dir, audio_files, groq_client, text_client, log)
    except Exception as e:
        log(100, f"LỖI: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc(file=sys.stdout)
        raise


def _import(script, selected_products, out_dir, audio_files, groq_client, text_client, log) -> dict:
    out_dir = Path(out_dir)
    audio_dir = out_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    full_path = audio_dir / "_giong_tu_thu_full.mp3"

    log(1, f"Bắt đầu — {len(audio_files)} file audio, run {out_dir.name}")
    total = _merge_audio([Path(f) for f in audio_files], full_path, log)
    log(10, f"Đã nối thành 1 file liền: {total / 60:.1f} phút")

    # Bản nén mono 16kHz cho Whisper (giới hạn 25MB; 1 tiếng ~ 15MB).
    tmp = Path(tempfile.mkdtemp(prefix="voice_"))
    try:
        small = tmp / "whisper.mp3"
        log(12, "Nén audio mono 16kHz để gửi Whisper...")
        with AudioFileClip(str(full_path)) as clip:
            clip.write_audiofile(str(small), fps=16000, bitrate="32k", ffmpeg_params=["-ac", "1"], logger=None)
        size_mb = small.stat().st_size / 1024 / 1024
        log(20, f"Đã nén: {size_mb:.1f} MB" + (" ⚠️ VƯỢT 25MB — Groq sẽ từ chối, hãy cắt audio ngắn lại"
                                              if size_mb > 25 else ""))
        log(22, "Gửi audio lên Whisper (Groq) — thường 10–60 giây...")
        beat = log.heartbeat(22, "Whisper (Groq) đang nghe audio")
        try:
            words = groq_client.transcribe_words(small)
        finally:
            beat.set()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not words:
        raise RuntimeError("Whisper không nghe được lời nào trong audio — kiểm tra lại file đã nộp.")
    log(60, f"Whisper nghe được {len(words)} từ (đến giây {words[-1].get('end', 0):.0f})")

    products_by_name = {p["name"]: p for p in selected_products.get("selected_products", [])}
    plans, units = [], []
    for seg in parse_segments(script):
        product, base_name, blocks = plan_segment(seg, products_by_name, out_dir)
        plans.append((seg, product, base_name))
        for block in blocks:
            for text in _sentences(block["text_with_tags"]):
                units.append({"seg": len(plans) - 1, "text": text, "image": block["image"]})
    if not units:
        raise ValueError("Script không có lời đọc nào.")

    log(62, f"Khớp {len(units)} câu của script với lời đã nghe...")
    coverage = _align(units, words, total)
    log(65, f"Khớp được {coverage:.0%} số từ của script")
    if coverage < _MIN_COVERAGE:
        raise RuntimeError(
            f"Audio chỉ khớp {coverage:.0%} số từ của script — có vẻ nộp nhầm file, sai thứ tự file, "
            "hoặc đọc theo bản script khác. Kiểm tra lại rồi nộp lại."
        )

    # Điểm cắt giữa 2 câu = giữa khoảng lặng; đầu bài từ 0, cuối bài tới hết file.
    cuts = [0.0]
    for a, b in zip(units, units[1:]):
        cuts.append(max(cuts[-1], (a["end"] + b["start"]) / 2))
    cuts.append(total)

    manifest, report = [], []
    with AudioFileClip(str(full_path)) as full:
        for si, (seg, product, base_name) in enumerate(plans):
            idx = [k for k, u in enumerate(units) if u["seg"] == si]
            log(65 + int(25 * si / max(len(plans), 1)), f"Cắt audio đoạn {si + 1}/{len(plans)}: {base_name}")
            audio_path, beats = None, []
            if idx:
                seg_start, seg_end = cuts[idx[0]], cuts[idx[-1] + 1]
                audio_path = audio_dir / f"{base_name}.mp3"
                full.subclipped(seg_start, min(seg_end, full.duration)).write_audiofile(str(audio_path), logger=None)
                beats = [{"text": units[k]["text"], "start": round(cuts[k] - seg_start, 3),
                          "end": round(cuts[k + 1] - seg_start, 3), "image": units[k]["image"]} for k in idx]
                n_words = sum(units[k]["n_words"] for k in idx)
                report.append({"name": base_name, "duration": round(seg_end - seg_start, 1),
                               "coverage": sum(units[k]["n_matched"] for k in idx) / max(n_words, 1)})
                cov = report[-1]["coverage"]
                log(65 + int(25 * (si + 1) / max(len(plans), 1)),
                    f"  ✓ {base_name}: {seg_end - seg_start:.1f}s, khớp {cov:.0%}"
                    + (" ⚠️ đọc lệch script nhiều" if cov < 0.7 else ""))
            else:
                log(65 + int(25 * (si + 1) / max(len(plans), 1)), f"  ⚠️ {base_name}: KHÔNG có câu nào khớp — đoạn này thiếu audio")
            manifest.append(manifest_entry(seg, product, base_name, audio_path, beats, out_dir))

    log(92, "Sinh chữ nhấn mạnh + ghi 06_media_manifest.json + mô tả YouTube (Groq)...")
    beat = log.heartbeat(92, "Đang sinh chữ nhấn mạnh (Groq)")
    try:
        finish_manifest(manifest, out_dir, text_client)
    finally:
        beat.set()
    log(100, f"XONG — {total / 60:.1f} phút audio, khớp {coverage:.0%} số từ, {len(report)} đoạn")
    return {"coverage": coverage, "duration": total, "segments": report}
