"""Kịch bản theo MẪU KÊNH (tab ✍️ Script & Audio) — 1 nút tạo nhiều kịch bản cho nhiều kênh.

Mẫu kịch bản = 1 file prompt trong prompts/script_templates/<tên>.md (dòng đầu "# Tên mẫu"), do bạn viết
(cấu trúc, giọng văn, độ dài từng phần...). Mẫu dùng CHUNG cho mọi kênh: chỗ cần tên kênh ghi {{KENH}} — lúc tạo
kịch bản điền tên kênh (để trống -> "kênh của mình"). Đầu vào tự gom:
  - Tiêu đề SEO (tab 🔑 / nhập tay)
  - Toàn bộ từ khóa (file CSV upload ở tab ✍️, hoặc 09_seo_keywords.json của run) — đã phân loại
    Chính / Kéo traffic / Phụ / Đuôi dài + nhóm ý định, chỉ lấy từ khóa đang tick Dùng
  - 5 sản phẩm của run: tên, giá, hạng, lý do chọn, điểm thiếu, file .md thông tin, ẢNH thật (tab 🖼️)

Gemini viết TỪNG PHẦN (mở đầu → 5 sản phẩm Top 5→Top 1 → kết) để kịch bản dài không bị cắt; mỗi phần
xong là lưu ngay (<run>/scripts/<id>.json) — hết quota giữa chừng thì chạy lại sẽ tiếp tục, không viết lại.

QUY TẮC TRUNG THỰC (HONESTY_RULES) luôn được gắn SAU mẫu và ưu tiên hơn mẫu khi mâu thuẫn: không bịa thông số
"đã test", không tự nhận đại lý uỷ quyền / mall, không bịa voucher / flash sale / số lượng giới hạn, không bịa
"shop dính phốt", không hứa hẹn y tế. Voucher / ưu đãi THẬT và "đã test thật" chỉ được nói khi bạn nhập / xác nhận.
"""
from __future__ import annotations

import inspect
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .product_info import prompt_block as product_info_block
from .product_links import find_product_media
from .script_length import count_words, rule as length_rule
from .seo_titles import ROLE_MAIN, ROLE_NOISE, ROLE_TAG, SEO_FILE, classify, read_keyword_csvs
from .utils import slugify

# Số phần sản phẩm viết song song (free tier Gemini hay giới hạn request/phút -> đừng để quá cao).
PARALLEL = max(1, int(os.environ.get("SCRIPT_PARALLEL", "3") or 3))
SCRIPT_USE_VIDEO = os.environ.get("SCRIPT_USE_VIDEO", "0") == "1"


def _generate(client, prompt: str, images, video) -> str:
    """Gọi client.generate_text, bật chế độ viết nhanh (fast=True) nếu client hỗ trợ."""
    try:
        if "fast" in inspect.signature(client.generate_text).parameters:
            return client.generate_text(prompt, images=images, video=video, fast=True)
    except (TypeError, ValueError):
        pass
    return client.generate_text(prompt, images=images, video=video)


TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "prompts" / "script_templates"
SCRIPTS_SUBDIR = "scripts"
KEYWORDS_FILE = "script_keywords.csv"

HONESTY_RULES = """QUY TẮC TRUNG THỰC BẮT BUỘC — ƯU TIÊN CAO HƠN MỌI YÊU CẦU Ở TRÊN KHI MÂU THUẪN:
1. Thông số, tính năng, giá, rating, lượt bán: CHỈ lấy từ DỮ LIỆU SẢN PHẨM bên dưới hoặc thứ nhìn thấy trong ảnh đính
   kèm. Không bịa số liệu. {tested_rule}
   - Câu "theo đánh giá của người mua" CHỈ dùng khi dữ liệu có số sao / nhận xét đó; điều chỉ có trong mô tả của người bán
     thì nói "theo nhà sản xuất / người bán".
   - Các dòng "đáp ứng / còn thiếu / lý do chọn / phân khúc" là nhận xét của bước chọn sản phẩm (để sắp thứ hạng), KHÔNG
     phải thông số: tính năng nào (vd lọc ồn, Bluetooth, chống nước) chỉ được khẳng định khi có trong dữ liệu Shopee.
2. Không tự nhận / khẳng định "đại lý uỷ quyền", "kho mall chính hãng", "lô mới nhất", "hàng chính hãng" trừ khi dữ liệu
   sản phẩm ghi rõ (vd Official Shop: Yes, hoặc tên sản phẩm có "Chính hãng" / "Mall").
3. {offer_rule}
4. Không bịa "gian hàng dính phốt", không nêu tên / bôi nhọ shop hay thương hiệu khác. Cảnh báo rủi ro chỉ ở mức chung
   (hàng trôi nổi không rõ nguồn gốc, nên so giá, xem đánh giá, kiểm tra bảo hành).
5. "Chê" nhược điểm phải dựa trên dữ liệu thật (thiếu tính năng so với hạng trên, điểm chê nhẹ trong dữ liệu, điều
   nhìn thấy trong ảnh) — không bịa lỗi.
{medical_rule}6. Ở phần kết, nói rõ 1 lần rằng link trong bình luận / mô tả là link tiếp thị liên kết (affiliate) — người xem mua qua
   link thì kênh nhận hoa hồng, giá không đổi.
"""

OUTPUT_RULES = """ĐỊNH DẠNG ĐẦU RA (bắt buộc):
- Viết VĂN NÓI để đọc bằng giọng AI (TTS), tiếng Việt tự nhiên, không gạch đầu dòng, không tiêu đề markdown.
- Chỉ dẫn dựng: [Text on screen: ...] và [Sound Effect: ...], mỗi chỉ dẫn nằm RIÊNG 1 dòng. Ngoài 2 loại này và THẺ
  GIỌNG ĐỌC đầu đoạn (nếu được yêu cầu ở cuối prompt), không viết ghi chú đạo diễn nào khác.
- [Sound Effect: ...] được hệ thống đổi thành ÂM THANH THẬT trong audio — tên CHỈ chọn trong: Pop, Ding, Chime, Swoosh,
  Tension, Heartbeat, Tick Tock, Win (vd [Sound Effect: Swoosh]). Đặt giữa 2 câu, không chen giữa câu.
- Từ khóa phải nói NGUYÊN VĂN (đúng chữ trong bảng từ khóa) để phụ đề YouTube chứa đúng cụm đó.
- Gọi sản phẩm theo số link = số Top của nó (vd "link số 3" là Top 3).
- Chỉ viết ĐÚNG PHẦN được yêu cầu bên dưới, không viết lại phần khác, không giải thích thêm.
"""


# ------------------------------------------------------------------ mẫu --

def list_templates() -> list[dict]:
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(TEMPLATES_DIR.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        m = re.match(r"\s*#\s+(.+)", text)
        out.append({"id": p.stem, "name": m.group(1).strip() if m else p.stem, "path": p,
                    "body": text[m.end():].strip() if m else text.strip()})
    return out


def save_template(name: str, body: str, template_id: Optional[str] = None) -> str:
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    tid = template_id or slugify(name, max_len=60)
    (TEMPLATES_DIR / f"{tid}.md").write_text(f"# {name.strip()}\n\n{body.strip()}\n", encoding="utf-8")
    return tid


def delete_template(template_id: str) -> None:
    (TEMPLATES_DIR / f"{template_id}.md").unlink(missing_ok=True)


# ------------------------------------------------------------- dữ liệu vào --

def input_keyword_csvs(run_dir: Path) -> list[Path]:
    """CSV từ khóa trong data/input/ (đã lọc ở tab 📥) — file ĐÃ PHÂN LOẠI trước, mới sửa nhất trước."""
    input_dir = Path(run_dir).resolve().parent.parent / "input"
    if not input_dir.exists():
        return []
    def is_classified_csv(data: bytes) -> bool:   # file "Từ khóa đã phân loại" (có cột Dùng + Vai trò)
        head = data[:2000].decode("utf-8-sig", errors="ignore").splitlines()[:1]
        return bool(head) and "Vai trò" in head[0] and "Dùng" in head[0]
    files = sorted(input_dir.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return sorted(files, key=lambda p: not is_classified_csv(p.read_bytes()))


def load_keyword_rows(run_dir: Path, input_csv: Optional[Path] = None) -> tuple[list[dict], str]:
    """(danh sách từ khóa đang Dùng, nguồn). Ưu tiên: CSV upload ở tab ✍️ -> CSV trong data/input/ (tab 📥,
    `input_csv` = file chọn, mặc định file đã phân loại mới nhất) -> 09_seo_keywords.json."""
    csv_path = run_dir / KEYWORDS_FILE
    if not csv_path.exists():
        cands = input_keyword_csvs(run_dir)
        pick = input_csv if input_csv in cands else (cands[0] if cands else None)
        if pick is not None:
            from .seo_titles import seed_from_filename
            kws, seeds = read_keyword_csvs([(pick.name, pick.read_bytes())])
            s = seed_from_filename(pick.name)
            kws = classify(kws, seeds=[s] if s else seeds)
            rows = [{"keyword": k.keyword, "role": k.role, "group": k.group, "volume": k.volume_display or None}
                    for k in kws if k.use and k.role not in (ROLE_NOISE, ROLE_TAG)]
            return rows, f"Tab 📥 Dữ liệu đầu vào: data/input/{pick.name}"
    if csv_path.exists():
        kws, seeds = read_keyword_csvs([(csv_path.name, csv_path.read_bytes())])
        orig = run_dir / "script_keywords_name.txt"
        if orig.exists():
            from .seo_titles import seed_from_filename
            s = seed_from_filename(orig.read_text(encoding="utf-8").strip())
            seeds = [s] if s else seeds
        kws = classify(kws, seeds=seeds)
        rows = [{"keyword": k.keyword, "role": k.role, "group": k.group, "volume": k.volume_display or None}
                for k in kws if k.use and k.role not in (ROLE_NOISE, ROLE_TAG)]
        name = orig.read_text(encoding="utf-8").strip() if orig.exists() else csv_path.name
        return rows, f"File CSV upload ở tab ✍️: {name}"
    seo = run_dir / SEO_FILE
    if seo.exists():
        data = json.loads(seo.read_text(encoding="utf-8"))
        rows = [{"keyword": k["keyword"], "role": k.get("role"), "group": k.get("group"),
                 "volume": k.get("volume_raw") or k.get("search_volume")}
                for k in data.get("keywords", []) if k.get("use", True) and k.get("role") not in (ROLE_NOISE, ROLE_TAG)]
        return rows, "Tab 🔑 (09_seo_keywords.json)"
    return [], ""


def default_title(run_dir: Path) -> str:
    seo = run_dir / SEO_FILE
    if seo.exists():
        t = json.loads(seo.read_text(encoding="utf-8")).get("chosen_title") or ""
        if t:
            from .seo_titles import vn_title_case
            return vn_title_case(t) if t == t.lower() else t
    meta = run_dir / "05_metadata.json"
    if meta.exists():
        return json.loads(meta.read_text(encoding="utf-8")).get("title") or ""
    return ""


def _keywords_table(rows: list[dict]) -> str:
    order = {ROLE_MAIN: 0, "Kéo traffic": 1, "Phụ": 2, "Đuôi dài": 3}
    rows = sorted(rows, key=lambda r: order.get(r.get("role"), 9))
    return "\n".join(f"- {r['keyword']} | vai trò: {r.get('role') or '?'} | nhóm ý định: {r.get('group') or '?'}"
                     f" | volume: {r.get('volume') if r.get('volume') not in (None, '') else '?'}" for r in rows)


def _product_summary(p: dict) -> str:
    price = p.get("price")
    price_s = f"{int(price):,}đ".replace(",", ".") if isinstance(price, (int, float)) else str(price or "")
    parts = [f"Top {p.get('rank')} (link số {p.get('rank')}): {p.get('name')} — giá {price_s}"]
    for key, label in (("tier", "phân khúc"), ("reason_selected", "lý do chọn"), ("needs_met", "đáp ứng"),
                       ("needs_missing", "còn thiếu"), ("weakness", "điểm thua hạng trên"), ("nitpick", "chê nhẹ")):
        v = p.get(key)
        if v:
            parts.append(f"{label}: {', '.join(v) if isinstance(v, list) else v}")
    return " · ".join(parts)


# ------------------------------------------------------------- viết kịch bản --

# ------------------------------------------------------------- tiêu đề SEO là xương sống --

_QUESTION_WORDS = ("bao nhiêu", "ở đâu", "là gì", "nào tốt", "nào", "cách ", "để làm gì", "dùng để", "có nên", "tại sao",
                   "vì sao", "như thế nào", "khi nào", "review", "so sánh")
_TEST_WORDS = ("thực nghiệm", "test", "trải nghiệm", "dùng thử", "đo thực tế", "kiểm chứng", "tự mua")


def _norm_text(t: str) -> str:
    from .seo_titles import strip_accents
    return " " + re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", strip_accents((t or "").lower()))).strip() + " "


def title_plan(title: str, keywords: list[dict], tested: bool = False) -> dict:
    """Phân tích tiêu đề SEO -> từ khóa chính (cụm có TRONG tiêu đề), phụ (thân video), traffic (mô tả / tags /
    timestamps) + các lời hứa của tiêu đề mà kịch bản phải thực hiện + cảnh báo."""
    t = _norm_text(title)
    in_title = [k for k in keywords if _norm_text(k["keyword"]).strip() and _norm_text(k["keyword"]) in t]
    # bỏ cụm nằm gọn trong cụm dài hơn cũng có trong tiêu đề ("máy trợ thính" ⊂ "máy trợ thính siêu nhỏ")
    main = [k for k in in_title if not any(o is not k and _norm_text(k["keyword"]) in _norm_text(o["keyword"])
                                           and len(o["keyword"]) > len(k["keyword"]) for o in in_title)]
    main = sorted(main, key=lambda k: -len(k["keyword"]))[:2]
    csv_main = [k for k in keywords if k.get("role") == ROLE_MAIN]
    warnings = []
    if not main:
        main = csv_main[:1]
        warnings.append("Tiêu đề không chứa nguyên văn cụm từ khóa nào trong file CSV — nên đưa từ khóa chính vào tiêu đề.")
    elif csv_main and all(_norm_text(c["keyword"]) not in t for c in csv_main):
        warnings.append(f"Từ khóa Chính trong CSV (“{csv_main[0]['keyword']}”) KHÔNG có trong tiêu đề — tool lấy cụm trong "
                        f"tiêu đề (“{main[0]['keyword']}”) làm từ khóa chính của video; “{csv_main[0]['keyword']}” "
                        "chuyển sang từ khóa traffic (mô tả / tags).")
    main_ids = {id(k) for k in main} | {id(k) for k in in_title}
    secondary, traffic = [], []
    for k in keywords:
        if id(k) in main_ids:
            continue
        kw = k["keyword"].lower()
        if (k.get("role") in (ROLE_MAIN, "Kéo traffic") or any(q in kw for q in _QUESTION_WORDS)
                or k.get("group") in ("Mua hàng / giá", "Đánh giá / tốt nhất", "Thương hiệu")):
            traffic.append(k)
        else:
            secondary.append(k)
    # Lời hứa của tiêu đề
    promises = []
    low = title.lower()
    year = re.search(r"\b(20\d\d)\b", title)
    if year:
        promises.append(f"Năm {year.group(1)}: nói rõ đây là danh sách cập nhật {year.group(1)} ngay ở mở đầu.")
    top = re.search(r"top\s*(\d+)", low)
    if top:
        promises.append(f"Top {top.group(1)}: đếm ngược đủ {top.group(1)} mẫu, nói rõ thứ hạng.")
    if any(w in low for w in ("đánh giá cao", "tốt nhất", "đáng mua", "bán chạy", "hot nhất")):
        promises.append("“Đánh giá cao / tốt nhất”: MỖI sản phẩm phải dẫn bằng chứng từ dữ liệu (số sao, số lượt đánh giá, "
                        "lượt bán); số liệu yếu (ít đánh giá, lượt bán thấp) thì nói thật, không thổi phồng.")
    if re.search(r"(?<!đánh )giá|\brẻ\b|triệu|nghìn|\d+\s*k\b", low):
        promises.append("Giá: nói giá cụ thể của từng mẫu và khoảng giá cả danh sách.")
    if any(w in low for w in _TEST_WORDS):
        if tested:
            promises.append("“Thực nghiệm / trải nghiệm”: kể trải nghiệm thật của người làm video (ngôi thứ nhất), số liệu "
                            "vẫn chỉ lấy từ dữ liệu.")
        else:
            promises.append("“Thực nghiệm / trải nghiệm”: người làm video CHƯA test — KHÔNG nói đã test / đo thực tế. Nói rõ 1 "
                            "lần ở mở đầu: danh sách dựa trên đánh giá thực tế của người mua và thông số nhà sản xuất.")
            warnings.append("Tiêu đề hứa “thực nghiệm / trải nghiệm / test” nhưng bạn CHƯA tick “đã test thật” — video không "
                            "thực hiện được lời hứa này. Nên đổi tiêu đề (vd “… Dựa Trên Đánh Giá Người Mua”).")
    # Thuộc tính chính trong từ khóa tiêu đề ("siêu nhỏ", "không dây", "cho người già"...)
    if main:
        mk = _norm_text(main[0]["keyword"])
        cores = [k for k in keywords if 2 <= len(k["keyword"].split()) < len(main[0]["keyword"].split())
                 and mk.startswith(_norm_text(k["keyword"]).rstrip() + " ")]
        if cores:
            core = max(cores, key=lambda k: len(k["keyword"]))["keyword"]
            attr = main[0]["keyword"][len(core):].strip()
            if attr and not any(q in attr.lower() for q in _QUESTION_WORDS + ("giá",)):
                promises.append(f"“{attr}” (thuộc tính trong từ khóa chính): MỖI sản phẩm nói dữ liệu liên quan "
                                f"(kích thước, trọng lượng, kiểu đeo… nếu có trong dữ liệu / ảnh). Mẫu nào KHÔNG thật sự "
                                f"“{attr}” thì nói thật và giải thích vì sao vẫn có mặt (vd bù bằng tính năng).")
    return {"main": main, "secondary": secondary, "traffic": traffic, "promises": promises, "warnings": warnings}


def _product_kw_texts(products: list[dict], run_dir: Optional[Path]) -> list[dict]:
    """Tên + thương hiệu + thuộc tính + đầu mô tả Shopee của từng sản phẩm — để gán từ khóa phụ đúng sản phẩm."""
    from .product_info import info_path, parse_product_md
    out = []
    for p in products:
        text = ""
        if run_dir is not None:
            path = info_path(p, Path(run_dir))
            if path.exists():
                try:
                    info = parse_product_md(path.read_text(encoding="utf-8", errors="ignore"))
                    text = " . ".join([info.get("title") or "", info.get("brand") or "",
                                       " . ".join(f"{k}: {v}" for k, v in (info.get("attributes") or {}).items()),
                                       (info.get("description") or "")[:1500]])
                except (OSError, ValueError):
                    text = ""
        out.append({"rank": p.get("rank"), "name": p.get("name", ""), "price": p.get("price") or 0, "text": text})
    return out


def screen_text_line(plan: dict, title: str) -> str:
    """Dòng chữ to trên màn hình ở mở đầu — luôn chứa nguyên văn từ khóa chính."""
    main = plan["main"][0]["keyword"] if plan.get("main") else ""
    top = re.search(r"top\s*(\d+)", (title or "").lower())
    tail = f" — Top {top.group(1)} Đáng Mua Nhất" if top else ""
    from .seo_titles import vn_title_case
    return f"[Text on screen: {vn_title_case(main)}{tail}]" if main else ""


def _keyword_block(plan: dict, products: Optional[list[dict]] = None, run_dir: Optional[Path] = None,
                   title: str = "") -> str:
    fmt = lambda ks: "; ".join(k["keyword"] for k in ks) or "(không có)"
    main = fmt(plan["main"])
    screen = screen_text_line(plan, title)
    screen_rule = (f"\n   → [Text on screen] BẮT BUỘC ở MỞ ĐẦU: 1 dòng riêng NGAY SAU câu đầu tiên, viết đúng như sau "
                   f"(được sửa vài chữ cuối nhưng PHẢI giữ nguyên văn từ khóa chính): {screen}") if screen else ""
    if products:
        from .keyword_fit import assign_secondary
        by_rank, generic, dropped = assign_secondary(plan["secondary"], _product_kw_texts(products, run_dir))
        lines = [f"   - Top {r} (link số {r}): {fmt(by_rank.get(r, []))}"
                 for r in sorted(by_rank, key=lambda x: -(x or 0))]
        if generic:
            lines.append(f"   - Dùng cho mẫu nào cũng được (tối đa 2 cụm cả video): {fmt(generic)}")
        secondary = ("2) TỪ KHÓA PHỤ → THÂN VIDEO — tool ĐÃ GÁN SẴN cho từng sản phẩm (chỉ nói ở ĐÚNG sản phẩm đó, mỗi "
                     "cụm 1 lần cả video, nguyên văn, tự nhiên; sản phẩm ghi “(không có)” thì không cần chèn):\n"
                     + "\n".join(lines) + "\n"
                     f"   (Tool đã loại {len(dropped)} cụm không khớp 5 sản phẩm: sai thương hiệu / model / dung tích, "
                     "từ khóa hướng dẫn – sửa lỗi – lắp đặt. KHÔNG tự thêm từ khóa ngoài danh sách trên.)\n")
    else:
        secondary = (f"2) TỪ KHÓA PHỤ → THÂN VIDEO (từng sản phẩm: tính năng, lợi ích, chi tiết): "
                     f"{fmt(plan['secondary'][:30])}\n"
                     "   → Mỗi sản phẩm lồng 1–3 cụm ĐÚNG với sản phẩm đó. Mỗi cụm nói tối đa 1 lần cả video, nguyên văn, "
                     "tự nhiên. Cụm chứa tên thương hiệu / cửa hàng KHÔNG có trong 5 sản phẩm thì KHÔNG nói.\n")
    return ("CÁCH DÙNG TỪ KHÓA (ưu tiên hơn phần ma trận từ khóa trong mẫu nếu khác):\n"
            f"1) TỪ KHÓA CHÍNH = cụm có trong tiêu đề: {main}\n"
            "   → HOOK 0–15 giây: câu ĐẦU TIÊN nói nguyên văn từ khóa chính; nhắc lại 1 lần nữa trước giây 15. "
            "Nhắc lại 1 lần ở phần KẾT. Không nhồi ở mọi sản phẩm." + screen_rule + "\n"
            + secondary +
            f"3) TỪ KHÓA TRAFFIC → tiêu đề, 3 dòng đầu mô tả, tags, timestamps (KHÔNG bắt buộc nói trong lời đọc): "
            f"{fmt(plan['traffic'][:40])}\n")


def _title_block(title: str, plan: dict) -> str:
    promises = "\n".join(f"- {p_}" for p_ in plan["promises"]) or "- (thực hiện đúng ý tiêu đề)"
    return (f"TIÊU ĐỀ SEO — XƯƠNG SỐNG CỦA CẢ KỊCH BẢN: \"{title}\"\n"
            "Mọi phần (mở đầu, từng sản phẩm, kết) phải phục vụ lời hứa của tiêu đề; người bấm vào vì tiêu đề phải nghe "
            "thấy đúng điều đó trong 5 giây đầu. Lời hứa cần thực hiện:\n" + promises + "\n")


def _base_context(title: str, keywords: list[dict], products: list[dict], niche: dict, tested: bool = False,
                  run_dir: Optional[Path] = None) -> str:
    plan = title_plan(title, keywords, tested)
    return (_title_block(title, plan) + "\n"
            f"NGÁCH: {niche.get('niche', '')}\n\n"
            + _keyword_block(plan, products, run_dir, title) + "\n"
            "5 SẢN PHẨM TRONG VIDEO (đếm ngược Top 5 -> Top 1; Top 1 cao cấp nhất):\n"
            + "\n".join(_product_summary(p) for p in sorted(products, key=lambda x: -(x.get('rank') or 0))))


def _rules(products: list[dict], tested: bool, offers: str, medical: bool) -> str:
    tested_rule = ("Người làm video ĐÃ XÁC NHẬN có trải nghiệm / test thật — được nói ở ngôi thứ nhất, nhưng số liệu vẫn chỉ "
                   "lấy từ dữ liệu.") if tested else (
        "Người làm video CHƯA trải nghiệm / test thật: KHÔNG nói 'mình đã test', 'đo thực tế'; dùng 'theo thông số nhà sản "
        "xuất', 'theo đánh giá của người mua'.")
    offer_rule = (f"Ưu đãi / voucher THẬT người làm video cung cấp (chỉ được nói đúng những ưu đãi này): {offers.strip()}"
                  if offers.strip() else
                  "Không bịa voucher, mã giảm giá ẩn, flash sale, 'số lượng có hạn', 'chương trình trợ giá độc quyền của "
                  "kênh'. Kêu gọi kiểu trung thực: 'bấm link để xem giá và ưu đãi đang có hôm nay'.")
    medical_rule = ("5b. Sản phẩm sức khỏe / y tế: không hứa chữa khỏi, không nói thay thế bác sĩ; nhắc nên đi khám chuyên "
                    "khoa nếu tình trạng nặng.\n") if medical else ""
    return HONESTY_RULES.format(tested_rule=tested_rule, offer_rule=offer_rule, medical_rule=medical_rule)


def _section_task(kind: str, product: Optional[dict] = None, n_products: int = 5) -> str:
    if kind == "intro":
        return ("PHẦN CẦN VIẾT LÚC NÀY: PHẦN 1 — Mở đầu & Hook theo đúng mẫu (độ dài theo mẫu). Có thể nhắc sẽ có 5 sản "
                "phẩm đếm ngược, nhưng CHƯA đi vào chi tiết từng sản phẩm.")
    if kind == "outro":
        return ("PHẦN CẦN VIẾT LÚC NÀY: PHẦN 3 — Giảm tải nhận thức (ma trận quyết định: ai nên chọn Top mấy, dựa đúng "
                "phân khúc / điểm thiếu của từng sản phẩm) & CTA kết theo mẫu (độ dài theo mẫu).")
    return (f"PHẦN CẦN VIẾT LÚC NÀY: PHẦN 2 — Đánh giá chi tiết RIÊNG sản phẩm Top {product.get('rank')} "
            f"(link số {product.get('rank')}): \"{product.get('name')}\" theo đúng phễu của mẫu (độ dài theo mẫu cho 1 "
            "sản phẩm). Ảnh thật của sản phẩm (nếu có) được đính kèm — mô tả đúng những gì thấy trong ảnh.")


def build_prompt(template_body: str, kind: str, title: str, keywords: list[dict], products: list[dict], niche: dict,
                 run_dir: Path, tested: bool, offers: str, previous: str = "", product: Optional[dict] = None,
                 channel: str = "", length: Optional[dict] = None) -> str:
    medical = any(k in (niche.get("niche", "") + " " + " ".join(p.get("name", "") for p in products)).lower()
                  for k in ("trợ thính", "huyết áp", "y tế", "đường huyết", "thuốc", "chức năng"))
    kenh = f'kênh "{channel.strip()}"' if channel.strip() else "kênh của mình"
    template_body = template_body.replace("{{KENH}}", kenh)   # mẫu chung: tên kênh điền lúc tạo
    parts = [template_body.strip(), "=" * 60, _rules(products, tested, offers, medical), OUTPUT_RULES,
             "=" * 60, _base_context(title, keywords, products, niche, tested, run_dir)]
    if product is not None:
        info = product_info_block(product, run_dir)
        parts.append(f"DỮ LIỆU CHI TIẾT CỦA SẢN PHẨM NÀY (Top {product.get('rank')}):\n{_product_summary(product)}\n"
                     + (info or "(không có file thông tin .md — chỉ dựa vào dữ liệu trên và ảnh)"))
    else:
        infos = [b for b in (product_info_block(p, run_dir) for p in products) if b]
        if infos and kind == "outro":
            parts.append("DỮ LIỆU CHI TIẾT CÁC SẢN PHẨM:\n" + "\n".join(infos)[:6000])
    if previous:
        parts.append("ĐOẠN ĐÃ VIẾT NGAY TRƯỚC (để nối mạch, KHÔNG lặp lại):\n" + previous[-1200:])
    task = _section_task(kind, product)
    if length:   # có độ dài mục tiêu -> bỏ "(độ dài theo mẫu)" để không mâu thuẫn với quy tắc độ dài
        task = re.sub(r" ?\(độ dài theo mẫu[^)]*\)", "", task)
    parts.append(task + length_rule(length, kind))
    return "\n\n".join(parts)


def _clean_section(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```[a-z]*\n|\n```$", "", text)
    text = re.sub(r"^\s*#{1,6}\s+.*$", "", text, flags=re.MULTILINE)   # bỏ tiêu đề markdown nếu model lỡ viết
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def generate_scripts_for_template(template: dict, run_dir: Path, vision_client, title: str, keywords: list[dict],
                                  tested: bool, offers: str, resume_id: Optional[str] = None,
                                  progress: Optional[Callable[[str], None]] = None, channel: str = "",
                                  length: Optional[dict] = None) -> Path:
    """Viết 1 kịch bản theo 1 mẫu. Lưu sau MỖI phần -> lỗi giữa chừng thì gọi lại với resume_id để viết tiếp."""
    run_dir = Path(run_dir)
    products = json.loads((run_dir / "03_products_selected.json").read_text(encoding="utf-8")).get("selected_products", [])
    niche = json.loads((run_dir / "01_niche.json").read_text(encoding="utf-8")) if (run_dir / "01_niche.json").exists() else {}
    out_dir = run_dir / SCRIPTS_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    sid = resume_id or f"{template['id']}__{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    path = out_dir / f"{sid}.json"
    doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "id": sid, "template_id": template["id"], "template_name": template["name"], "title": title,
        "channel": channel, "length_minutes": list(length["minutes"]) if length else None,
        "keyword_count": len(keywords), "tested": tested, "offers": offers,
        "created_at": datetime.now().isoformat(timespec="seconds"), "sections": [], "complete": False}

    plan = [("intro", None)] + [("product", p) for p in sorted(products, key=lambda x: -(x.get("rank") or 0))] + [("outro", None)]
    order = {(k, p.get("rank") if p else None): i for i, (k, p) in enumerate(plan)}
    lock = threading.Lock()

    def label_of(kind, p):
        return {"intro": "Phần 1 — Mở đầu", "outro": "Phần 3 — Kết & CTA"}.get(kind) or f"Top {p['rank']}: {p['name'][:50]}"

    def done_keys():
        return {(s_["kind"], s_.get("rank")) for s_ in doc["sections"]}

    def text_of(kind, rank=None):
        return next((s_["text"] for s_ in doc["sections"] if s_["kind"] == kind and s_.get("rank") == rank), "")

    def write(kind, p, previous):
        prompt = build_prompt(template["body"], kind, title, keywords, products, niche, run_dir, tested, offers,
                              previous=previous, product=p, channel=doc.get("channel", channel), length=length)
        images, video = find_product_media(p, run_dir) if p else ([], None)
        if not SCRIPT_USE_VIDEO and images:
            video = None   # video chỉ dùng lúc dựng phim; viết script dùng ảnh + file .md (nhanh hơn 1–2 phút)
        text = _clean_section(_generate(vision_client, prompt, images, video))
        return {"kind": kind, "rank": p.get("rank") if p else None, "product_name": p.get("name") if p else None,
                "label": label_of(kind, p), "text": text, "images_used": len(images)}

    def save(section):
        with lock:
            doc["sections"].append(section)
            doc["sections"].sort(key=lambda s_: order.get((s_["kind"], s_.get("rank")), 99))
            path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    # 1) Mở đầu (các phần sau nối mạch theo nó)
    if ("intro", None) not in done_keys():
        if progress:
            progress(f"✍️ {template['name']}: đang viết Phần 1 — Mở đầu…")
        save(write("intro", None, ""))

    # 2) 5 sản phẩm — viết SONG SONG (độc lập nhau), mỗi phần xong là lưu ngay
    todo = [p for k, p in plan if k == "product" and ("product", p.get("rank")) not in done_keys()]
    if todo:
        if progress:
            progress(f"✍️ {template['name']}: đang viết {len(todo)} sản phẩm song song "
                     f"({min(PARALLEL, len(todo))} cùng lúc)…")
        errors_ = []
        with ThreadPoolExecutor(max_workers=max(1, min(PARALLEL, len(todo)))) as ex:
            futs = {ex.submit(write, "product", p, ""): p for p in todo}
            for fut in as_completed(futs):
                p = futs[fut]
                try:
                    save(fut.result())
                    if progress:
                        progress(f"   ✓ Top {p['rank']} xong")
                except Exception as e:   # phần khác vẫn chạy tiếp & được lưu; báo lỗi sau
                    errors_.append(e)
        if errors_:
            raise errors_[0]

    # 3) Kết — nối mạch theo Top 1
    if ("outro", None) not in done_keys():
        if progress:
            progress(f"✍️ {template['name']}: đang viết Phần 3 — Kết & CTA…")
        top1 = min((p.get("rank") or 99 for p in products), default=1)
        save(write("outro", None, text_of("product", top1)))
    doc["complete"] = True
    doc["finished_at"] = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / f"{sid}.md").write_text(to_markdown(doc), encoding="utf-8")
    return path


# ------------------------------------------------------------- khung chat AI --
# Không tốn API: tool gom prompt 1 lần cho cả kịch bản -> bạn dán vào khung chat (Claude / Gemini / ChatGPT web)
# -> dán câu trả lời lại -> tool tách đúng 7 phần, lưu như kịch bản bình thường (📌 Dùng cho Audio như cũ).

CHAT_HEADERS = ["=== MỞ ĐẦU ==="] + [f"=== TOP {r} ===" for r in (5, 4, 3, 2, 1)] + ["=== KẾT ==="]


def _is_medical(niche: dict, products: list[dict]) -> bool:
    return any(k in (niche.get("niche", "") + " " + " ".join(p.get("name", "") for p in products)).lower()
               for k in ("trợ thính", "huyết áp", "y tế", "đường huyết", "thuốc", "chức năng"))


def _load_run(run_dir: Path) -> tuple[list[dict], dict]:
    products = json.loads((run_dir / "03_products_selected.json").read_text(encoding="utf-8")).get("selected_products", [])
    niche = json.loads((run_dir / "01_niche.json").read_text(encoding="utf-8")) if (run_dir / "01_niche.json").exists() else {}
    return products, niche


def build_chat_prompt(template: dict, run_dir: Path, title: str, keywords: list[dict], tested: bool, offers: str,
                      channel: str = "", length: Optional[dict] = None, with_images: bool = False) -> str:
    """1 prompt cho CẢ kịch bản (mở đầu + 5 sản phẩm + kết) để dán vào khung chat AI."""
    run_dir = Path(run_dir)
    products, niche = _load_run(run_dir)
    ranks = [p.get("rank") for p in sorted(products, key=lambda x: -(x.get("rank") or 0))]
    kenh = f'kênh "{channel.strip()}"' if channel.strip() else "kênh của mình"
    out_rules = OUTPUT_RULES.replace(
        "- Chỉ viết ĐÚNG PHẦN được yêu cầu bên dưới, không viết lại phần khác, không giải thích thêm.\n", "")
    details = []
    for p in sorted(products, key=lambda x: -(x.get("rank") or 0)):
        info = product_info_block(p, run_dir)
        details.append(f"--- Top {p.get('rank')} ---\n{_product_summary(p)}\n"
                       + (info or "(không có file thông tin .md — chỉ dựa vào dữ liệu trên"
                          + (" và ảnh đính kèm)" if with_images else ")")))
    img_note = ("\nẢNH ĐÍNH KÈM: tên file dạng top<số>_<thứ tự>.jpg (vd top3_2.jpg = ảnh thứ 2 của Top 3) — chỉ mô tả "
                "đúng những gì thấy trong ảnh của ĐÚNG sản phẩm đó.\n") if with_images else ""
    headers = "\n".join(["=== MỞ ĐẦU ==="] + [f"=== TOP {r} ===" for r in ranks] + ["=== KẾT ==="])
    # Quy tắc độ dài từng phần; hiệu ứng âm thanh + thẻ giọng đọc chỉ ghi 1 LẦN cho cả kịch bản (đỡ dài prompt).
    plain = dict(length, sfx=False, tones=False) if length else None
    extras = []
    if length and length.get("sfx"):
        from .script_length import _SFX_LIST, _SFX_RULES
        extras.append(f"HIỆU ỨNG ÂM THANH (hệ thống chèn âm thanh thật): mỗi chỉ dẫn nằm RIÊNG 1 dòng, giữa 2 câu, tên chỉ "
                      f"chọn trong: {_SFX_LIST}. Tối đa 3 hiệu ứng mỗi phần, không 2 hiệu ứng liền nhau.\n"
                      f"- MỞ ĐẦU: {_SFX_RULES['hook']}\n- Mỗi TOP: {_SFX_RULES['product']}\n- KẾT: {_SFX_RULES['cta']}")
    if length and length.get("tones"):
        from .voice_tones import _FLOW, tone_rule
        menu = tone_rule("product").split("Nhịp gợi ý cho đoạn này:")[0].strip()
        extras.append(menu + f"\nNhịp gợi ý:\n- MỞ ĐẦU: {_FLOW['hook']}\n- Mỗi TOP: {_FLOW['product']}\n"
                      f"- KẾT: {_FLOW['cta']}\nNgoài cặp [cảm xúc] + [Anti-sell], không đặt 2 thẻ giọng liền nhau; thẻ giọng không thay đổi quy tắc trung thực.")
    tasks = ("NHIỆM VỤ: viết TRỌN kịch bản trong 1 lần trả lời, theo đúng mẫu ở trên:\n"
             "- Phần MỞ ĐẦU: Mở đầu & Hook (có thể nhắc 5 sản phẩm đếm ngược, CHƯA đi vào chi tiết)."
             + length_rule(plain, "hook") +
             "\n- Phần TOP 5 → TOP 1: mỗi sản phẩm 1 phần riêng, theo đúng phễu của mẫu, chỉ dùng dữ liệu của ĐÚNG sản "
             "phẩm đó; cuối mỗi phần có 1 câu chuyển sang Top kế tiếp (Top 1 thì không)."
             + length_rule(plain, "product").replace("đoạn này", "MỖI phần sản phẩm") +
             "\n- Phần KẾT: ma trận quyết định (ai nên chọn Top mấy, dựa đúng phân khúc / điểm thiếu) & CTA."
             + length_rule(plain, "cta")
             + ("\n\n" + "\n\n".join(extras) if extras else ""))
    plan = title_plan(title, keywords, tested)
    meta = ("\n=== MÔ TẢ YOUTUBE ===\n(phần 8, KHÔNG đọc thành lời) gồm: 3 dòng đầu mô tả — dòng 1 chứa nguyên văn từ khóa "
            "chính, dòng 2–3 lồng 2–3 từ khóa traffic tự nhiên; 1 dòng trống; danh sách CHƯƠNG (chưa cần giờ, mỗi dòng 1 "
            "chương: Mở đầu, Top 5 … Top 1, Kết — tên chương ngắn, chứa từ khóa traffic / câu hỏi phù hợp); 1 dòng trống; "
            "dòng \"Tags:\" + 15–25 tag phân cách dấu phẩy (ưu tiên từ khóa chính + traffic, tổng ≤ 480 ký tự).")
    fmt = ("ĐỊNH DẠNG TRẢ LỜI BẮT BUỘC (phần mềm đọc lại tự động): đủ 8 phần theo ĐÚNG thứ tự, mỗi phần bắt đầu bằng 1 "
           "dòng tiêu đề viết NGUYÊN VĂN như dưới (không thêm chữ, không in đậm):\n" + headers + meta +
           "\nKhông viết gì ngoài 8 phần (không chào hỏi, không giải thích, không markdown, không ```).\n\n"
           "TỰ KIỂM TRA TRƯỚC KHI TRẢ LỜI: (1) câu đầu tiên có từ khóa chính “" +
           (plan["main"][0]["keyword"] if plan["main"] else "") + "” và ngay sau đó có dòng [Text on screen] chứa từ "
           "khóa chính? (2) mọi lời hứa của tiêu đề đã được thực hiện? "
           "(3) mọi con số đều có trong dữ liệu? (4) không có từ khóa phụ nào dùng sai sản phẩm?")
    return "\n\n".join([template["body"].replace("{{KENH}}", kenh).strip(), "=" * 60,
                         _rules(products, tested, offers, _is_medical(niche, products)), out_rules, "=" * 60,
                         _base_context(title, keywords, products, niche, tested, run_dir),
                         "DỮ LIỆU CHI TIẾT TỪNG SẢN PHẨM (nguồn sự thật):\n" + "\n\n".join(details) + img_note,
                         "=" * 60, tasks, fmt])


# Tiêu đề phần: "=== TOP 3 ===" (cho phép in đậm / thêm tên sau) hoặc "## TOP 3" — dòng văn thường có chữ "Top 3"
# ở đầu KHÔNG bị coi là tiêu đề.
_CHAT_HEAD_RE = re.compile(
    r"^[ \t*_>`]*(?:={2,}|#{1,4})[ \t*_]*(MỞ\s*ĐẦU|MO\s*DAU|TOP\s*(\d+)|KẾT|KET)\b[^\n]*$",
    re.IGNORECASE | re.MULTILINE)


_ANY_HEAD_RE = re.compile(r"^[ \t*_>`]*={2,}[^=\n]{1,60}={2,}[ \t*_`]*$", re.MULTILINE)
_META_HEAD_RE = re.compile(r"^[ \t*_>`]*(?:={2,}|#{1,4})[ \t*_]*M[ÔO]\s*T[ẢA]\b[^\n]*$", re.IGNORECASE | re.MULTILINE)


def parse_chat_meta(text: str) -> str:
    """Phần "=== MÔ TẢ YOUTUBE ===" (3 dòng đầu mô tả, chương, tags) trong câu trả lời — rỗng nếu không có."""
    text = (text or "").replace("\r\n", "\n")
    m = _META_HEAD_RE.search(text)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = _ANY_HEAD_RE.search(rest)
    body = rest[: nxt.start()] if nxt else rest
    body = re.sub(r"^\s*```[a-z]*\s*$", "", body, flags=re.MULTILINE)
    return re.sub(r"\*\*(.+?)\*\*", r"\1", body).strip()


def parse_chat_output(text: str, products: list[dict]) -> tuple[list[dict], list[str]]:
    """Câu trả lời từ khung chat -> các phần kịch bản. Trả (sections, các phần còn thiếu)."""
    text = (text or "").replace("\r\n", "\n")
    fenced = re.search(r"```[a-zA-Z]*\n(.*?)```", text, re.DOTALL)
    if fenced and _CHAT_HEAD_RE.search(fenced.group(1)):
        text = fenced.group(1)              # AI bọc kịch bản trong ``` -> bỏ lời chào / lời kết bên ngoài
    text = re.sub(r"^\s*```[a-z]*\s*$", "", text, flags=re.MULTILINE)
    heads = []
    for m in _CHAT_HEAD_RE.finditer(text):
        line = m.group(0).strip()
        if len(line) > 120:            # câu văn dài có chữ "Top 3" ở đầu -> không phải tiêu đề
            continue
        word = m.group(1).upper()
        if m.group(2):
            key = ("product", int(m.group(2)))
        elif word.startswith(("MỞ", "MO")):
            key = ("intro", None)
        else:
            key = ("outro", None)
        heads.append((m.start(), m.end(), key))
    by_rank = {p.get("rank"): p for p in products}
    stops = sorted({m.start() for m in _ANY_HEAD_RE.finditer(text)} | {h[0] for h in heads})
    found = {}
    for i, (st_, en, key) in enumerate(heads):
        nxt = next((x for x in stops if x >= en), len(text))
        body = text[en:nxt]
        body = re.sub(r"\*\*(.+?)\*\*", r"\1", body)   # bỏ in đậm markdown
        body = _clean_section(body)
        if body and key not in found and (key[0] != "product" or key[1] in by_rank):
            found[key] = body
    plan = [("intro", None)] + [("product", r) for r in sorted(by_rank, key=lambda r: -(r or 0))] + [("outro", None)]
    sections, missing = [], []
    for kind, rank in plan:
        label = {"intro": "Phần 1 — Mở đầu", "outro": "Phần 3 — Kết & CTA"}.get(kind) or \
            f"Top {rank}: {by_rank[rank].get('name', '')[:50]}"
        if (kind, rank) not in found:
            missing.append(label)
            continue
        sections.append({"kind": kind, "rank": rank, "product_name": by_rank[rank].get("name") if rank else None,
                         "label": label, "text": found[(kind, rank)], "images_used": 0})
    return sections, missing


def save_chat_script(template: dict, run_dir: Path, title: str, keywords: list[dict], tested: bool, offers: str,
                     sections: list[dict], channel: str = "", length: Optional[dict] = None,
                     youtube_meta: str = "") -> Path:
    run_dir = Path(run_dir)
    out_dir = run_dir / SCRIPTS_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    sid = f"{template['id']}__chat_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    doc = {"id": sid, "template_id": template["id"], "template_name": template["name"] + " (khung chat)",
           "title": title, "channel": channel, "length_minutes": list(length["minutes"]) if length else None,
           "keyword_count": len(keywords), "tested": tested, "offers": offers, "source": "chat",
           "created_at": now, "finished_at": now, "sections": sections, "complete": True,
           "youtube_meta": youtube_meta}
    path = out_dir / f"{sid}.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / f"{sid}.md").write_text(to_markdown(doc), encoding="utf-8")
    return path


def chat_images_zip(run_dir: Path, per_product: int = 3) -> tuple[bytes, int]:
    """Zip ảnh (tối đa per_product ảnh / sản phẩm, đặt tên top<rank>_<i>) để kéo vào khung chat."""
    import io
    import zipfile
    from .product_links import list_all_product_images
    run_dir = Path(run_dir)
    products, _ = _load_run(run_dir)
    buf, n = io.BytesIO(), 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in products:
            for i, img in enumerate(list_all_product_images(p, run_dir)[:per_product], 1):
                zf.write(img, f"top{p.get('rank')}_{i}{Path(img).suffix.lower() or '.jpg'}")
                n += 1
    return buf.getvalue(), n


# ------------------------------------------------------------- xuất --

_CUE_RE = re.compile(r"^\s*\[(Text on screen|Sound Effect)\s*:[^\]]*\]\s*$", re.IGNORECASE | re.MULTILINE)
_CUE_INLINE_RE = re.compile(r"\[(Text on screen|Sound Effect)\s*:[^\]]*\]", re.IGNORECASE)


def to_markdown(doc: dict) -> str:
    out = [f"# {doc.get('title', '')}", "",
           f"_Mẫu: {doc.get('template_name')} · tạo lúc {doc.get('created_at', '').replace('T', ' ')} · "
           f"{len(doc.get('sections', []))} phần · ~{word_count(doc)} từ_", ""]
    for s in doc.get("sections", []):
        out += [f"## {s['label']}", "", s["text"], ""]
    if doc.get("youtube_meta"):
        out += ["## Mô tả YouTube / chương / tags", "", doc["youtube_meta"], ""]
    return "\n".join(out)


def voice_only(text: str) -> str:
    """Bỏ chỉ dẫn [Text on screen] / [Sound Effect] — còn lại lời đọc."""
    text = _CUE_RE.sub("", text)
    text = _CUE_INLINE_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


_SCREEN_RE = re.compile(r"^\s*\[Text on screen\s*:[^\]]*\]\s*$", re.IGNORECASE | re.MULTILINE)
_SCREEN_INLINE_RE = re.compile(r"\[Text on screen\s*:[^\]]*\]", re.IGNORECASE)


def voice_with_sfx(text: str) -> str:
    """Lời đọc + GIỮ [Sound Effect: ...] (bước sinh audio chèn âm thanh thật), bỏ [Text on screen] (chỉ dẫn dựng)."""
    text = _SCREEN_INLINE_RE.sub("", _SCREEN_RE.sub("", text))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def overlays(doc: dict) -> list[dict]:
    rows = []
    for s in doc.get("sections", []):
        for m in re.finditer(r"\[(Text on screen|Sound Effect)\s*:\s*([^\]]*)\]", s["text"], re.IGNORECASE):
            rows.append({"Phần": s["label"], "Loại": m.group(1), "Nội dung": m.group(2).strip()})
    return rows


SCREEN_TEXT_FILE = "04_screen_text.json"
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")
_ANY_TAG = re.compile(r"\[[^\]]*\]")


def _spoken_sentences(text: str) -> int:
    t = " ".join(_ANY_TAG.sub(" ", text).split())
    return len([x for x in _SENT_SPLIT.split(t) if x.strip()]) if t else 0


def screen_texts(doc: dict, run_dir: Optional[Path] = None) -> list[dict]:
    """Các dòng [Text on screen: ...] của kịch bản -> [{kind, rank, product_name, text, after}] ("after" = số câu đọc
    đứng trước dòng đó trong đoạn -> chữ hiện lúc bắt đầu câu kế tiếp). Mở đầu chưa có dòng nào chứa từ khóa chính thì
    tool TỰ THÊM 1 dòng (sau câu đầu tiên) — CapCut / video hiện chữ này to trên màn hình."""
    out = []
    for s in doc.get("sections", []):
        for m in re.finditer(r"\[Text on screen\s*:\s*([^\]]*)\]", s["text"], re.IGNORECASE):
            if m.group(1).strip():
                out.append({"kind": s["kind"], "rank": s.get("rank"), "product_name": s.get("product_name"),
                            "text": m.group(1).strip(), "after": _spoken_sentences(s["text"][:m.start()])})
    main = ""
    try:
        rows, _ = load_keyword_rows(Path(run_dir)) if run_dir else ([], "")
        plan = title_plan(doc.get("title", ""), rows)
        main = plan["main"][0]["keyword"] if plan["main"] else ""
        line = screen_text_line(plan, doc.get("title", ""))
    except Exception:
        line = ""
    has_intro = any(o["kind"] == "intro" and (not main or _norm_text(main) in _norm_text(o["text"])) for o in out)
    if not has_intro and line and any(s["kind"] == "intro" for s in doc.get("sections", [])):
        out.insert(0, {"kind": "intro", "rank": None, "product_name": None, "auto": True,
                       "text": line[len("[Text on screen:"):-1].strip(), "after": 1})
    return out


def word_count(doc: dict) -> int:
    return sum(count_words(s["text"]) for s in doc.get("sections", []))


def to_audio_script(doc: dict) -> str:
    """Định dạng 04_script.txt cho bước sinh audio / ghép video: [Audio - Lời đọc] + '### SẢN PHẨM: <tên>'."""
    def wrap(t: str) -> str:
        return f"[Audio - Lời đọc]\n{voice_with_sfx(t)}\n[/Audio - Lời đọc]"
    blocks = []
    for s in doc.get("sections", []):
        if s["kind"] == "product":
            blocks.append(f"### SẢN PHẨM: {s['product_name']}\n{wrap(s['text'])}")
        else:
            blocks.append(wrap(s["text"]))
    return "\n\n".join(blocks)


def list_scripts(run_dir: Path) -> list[dict]:
    d = Path(run_dir) / SCRIPTS_SUBDIR
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")) | {"_path": str(p)})
        except ValueError:
            continue
    return out
