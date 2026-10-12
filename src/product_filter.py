"""Chuẩn bị dữ liệu cho bước 🎯 Chọn sản phẩm + BÁO CÁO minh bạch cho dashboard.

    prepare_selection_inputs(input_dir) -> (keywords, products_for_groq, report)

1. Từ khóa (data/input/*.csv):
   - File "Từ khóa đã phân loại" (tab 🔑): tôn trọng cột Dùng — bỏ tick là KHÔNG gửi Groq.
   - CSV gốc của tool keyword: tự phân loại như tab 🔑 (src/seo_titles.py) và bỏ các từ khóa Loại
     (tên cửa hàng, mã shop, lạc đề / quá rộng...).
2. Sản phẩm (data/input/*.json): gộp file, bỏ trùng (src/ingestion.py), rồi CODE lọc trước khi gửi Groq:
   - Phụ kiện (pin, núm tai, hộp đựng, dây sạc... "dùng cho máy ...") -> loại.
   - Tên sản phẩm không chứa tên sản phẩm của từ khóa nào (vd "máy trợ thính", "tai nghe trợ thính")
     -> loại (không liên quan).
   - Còn lại (đã đạt độ liên quan) xếp theo LƯỢT BÁN, lấy tối đa 30 — độ liên quan là cửa lọc, không phải
     thứ hạng (tên Shopee nhồi nhiều từ khóa không được ưu tiên hơn hàng bán chạy thật).
   - Nếu lọc xong còn < 5 sản phẩm (CSV và JSON có vẻ khác chủ đề) -> không loại "không liên quan",
     chỉ cảnh báo.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from .ingestion import load_keywords_report, load_products
from .models import Product
from .seo_titles import ROLE_MAIN, ROLE_NOISE, Keyword, classify, product_part, seed_from_filename, strip_accents

from .product_rank import MIN_RATING, MIN_SOLD, is_duplicate, trust_score, trusted

MAX_PRODUCTS_FOR_GROQ = 30
MIN_RELEVANT = 5

STATUS_SENT = "✅ Gửi Groq"
STATUS_ACCESSORY = "❌ Phụ kiện"
STATUS_UNRELATED = "❌ Không chứa từ khóa nào"
STATUS_OVER_LIMIT = "⏭️ Liên quan nhưng ngoài top 30"
STATUS_DUP_MODEL = "❌ Trùng mẫu (giữ tin bán chạy hơn)"
STATUS_LOW_TRUST = "⏭️ Ít lượt bán / rating thấp"
MIN_TRUSTED = 8        # đủ từng này sản phẩm đáng tin thì loại hẳn sản phẩm ít lượt bán / rating thấp

_ACCESSORY_START = ["pin", "núm", "nút tai", "đệm", "dây", "cáp", "sạc", "củ sạc", "hộp", "bao", "túi", "ốp", "vỏ",
                    "miếng", "phụ kiện", "bộ phụ kiện", "dụng cụ", "chổi", "máy sấy", "ống", "lọc", "giá đỡ", "kệ"]
_ACCESSORY_ANYWHERE = ["phụ kiện", "thay thế cho", "dùng cho máy", "dành cho máy", "tương thích với"]


def _ascii(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", strip_accents(str(text or "").lower()))).strip()


def _contains(hay_ascii: str, needle: str) -> bool:
    n = _ascii(needle)
    return bool(n) and re.search(rf"(?<!\w){re.escape(n)}(?!\w)", hay_ascii) is not None


def _clean_name(name: str) -> str:
    return re.sub(r"^\s*(\[[^\]]*\]|【[^】]*】|\([^)]*\))\s*", "", name or "").strip()


def is_accessory(name: str) -> bool:
    n = _clean_name(name).lower()
    first = n.split()
    if not first:
        return False
    start = " ".join(first[:2])
    if any(start == a or start.startswith(a + " ") or first[0] == a for a in _ACCESSORY_START):
        return True
    return any(a in n for a in _ACCESSORY_ANYWHERE)


# ------------------------------------------------------------ từ khóa --

def _auto_drop_noise(keywords: list[dict], seeds: Optional[list[str]] = None) -> tuple[list[dict], list[dict]]:
    """CSV gốc (không có cột Dùng): phân loại như tab 🔑 (từ khóa chính đoán theo TÊN FILE, vd
    'Máy-trợ-thính-giá-bao-nhiêu_related_keywords.csv' -> 'máy trợ thính giá bao nhiêu') và bỏ từ khóa Loại."""
    if any(k.get("role") for k in keywords):
        return keywords, []  # file đã phân loại -> đã lọc theo cột Dùng
    objs = [Keyword(keyword=k["keyword"], search_volume=k.get("search_volume"), competition=k.get("competition"),
                    overall=k.get("overall"), volume_raw=k.get("volume_raw") or "") for k in keywords]
    classified = {o.keyword.lower(): o for o in classify(objs, seeds=seeds or [])}
    keep, dropped = [], []
    for k in keywords:
        o = classified.get(k["keyword"].lower())
        if o is not None and o.role == ROLE_NOISE:
            dropped.append({"keyword": k["keyword"], "reason": f"Tự loại: {o.note}"})
        else:
            if o is not None and o.role == ROLE_MAIN:
                k = {**k, "role": ROLE_MAIN}
            keep.append(k)
    return keep, dropped


def _core_terms(keywords: list[dict]) -> list[str]:
    """Tên sản phẩm rút từ từ khóa ('máy trợ thính giá rẻ' -> 'máy trợ thính'), >= 2 chữ."""
    terms = []
    for k in keywords:
        part = product_part(k["keyword"])
        if len(_ascii(part).split()) >= 2 and len(_ascii(part).split()) <= 6:
            terms.append(part)
    return list(dict.fromkeys(terms))


# ------------------------------------------------------------ chính --

def _count_raw_products(input_dir: Path) -> int:
    n = 0
    for path in sorted(Path(input_dir).glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        n += len(raw.get("products", [])) if isinstance(raw, dict) else len(raw) if isinstance(raw, list) else 0
    return n


def score_product(p: Product, keywords: list[dict], core: list[str]) -> dict:
    name_a = _ascii(_clean_name(p.name))
    matched = [k["keyword"] for k in keywords if _contains(name_a, k["keyword"])]
    core_hit = [c for c in core if _contains(name_a, c)]
    main = [k["keyword"] for k in keywords if k.get("role") == ROLE_MAIN and k["keyword"] in matched]
    score = 3 * len(main) + 2 * len(matched) + (1 if core_hit else 0)
    return {"matched": matched, "core": core_hit, "score": score}


def prepare_selection_inputs(input_dir: Path, max_keywords: int = 60,
                             max_products: int = MAX_PRODUCTS_FOR_GROQ) -> tuple[list[dict], list[Product], dict]:
    input_dir = Path(input_dir)
    # Đọc HẾT rồi mới loại từ khóa rác, SAU ĐÓ mới cắt còn max_keywords (cắt trước thì từ khóa tốt bị cắt oan
    # trong khi chỗ trống lại bị từ khóa rác chiếm).
    keywords, kw_report = load_keywords_report(input_dir, 10 ** 6)
    seeds = [s for s in (seed_from_filename(f) for f in kw_report.get("files", [])) if s]
    keywords, auto_dropped = _auto_drop_noise(keywords, seeds)
    main_first = sorted(keywords, key=lambda k: k.get("role") == ROLE_MAIN, reverse=True)
    keywords, cut = main_first[:max_keywords], main_first[max_keywords:]
    kw_report["unused"] = kw_report.get("unused", []) + auto_dropped
    kw_report["cut"] = [k["keyword"] for k in cut]
    kw_report["sent"] = len(keywords)
    core = _core_terms(keywords)

    raw_count = _count_raw_products(input_dir)
    all_products = load_products(input_dir, max_products=10 ** 9)   # đã bỏ trùng, CHƯA cắt top
    rows = []
    for idx, p in enumerate(all_products):
        sc = score_product(p, keywords, core)
        if is_accessory(p.name):
            status = STATUS_ACCESSORY
        elif not sc["core"] and not sc["matched"]:
            status = STATUS_UNRELATED
        else:
            status = "relevant"
        rows.append({"idx": idx, "name": p.name, "price": p.price, "sold": p.sold, "link": p.affiliate_link,
                     "rating": p.rating, "reviews": getattr(p, "reviews", None),
                     "trust": trust_score(p.sold, p.rating, getattr(p, "reviews", None)),
                     "matched": sc["matched"], "core": sc["core"], "score": sc["score"], "status": status})

    relevant = [r for r in rows if r["status"] == "relevant"]
    warning = None
    if len(relevant) < MIN_RELEVANT:
        warning = (f"Chỉ {len(relevant)} sản phẩm có tên chứa từ khóa — CSV từ khóa và JSON sản phẩm có vẻ KHÁC chủ "
                   "đề. Tạm giữ cả sản phẩm 'không chứa từ khóa' (vẫn loại phụ kiện); nên kiểm tra lại file đầu vào.")
        for r in rows:
            if r["status"] == STATUS_UNRELATED:
                r["status"] = "relevant"
        relevant = [r for r in rows if r["status"] == "relevant"]
    # Độ liên quan là CỬA LỌC (đạt / không đạt), còn XẾP HẠNG theo độ tin cậy: LƯỢT BÁN cao + RATING cao + nhiều
    # LƯỢT ĐÁNH GIÁ (src/product_rank.py). Tên Shopee hay nhồi nhiều từ khóa (vài lượt bán) — xếp theo số từ khóa
    # sẽ đẩy hàng nhồi tên lên trên hàng bán chạy thật.
    relevant.sort(key=lambda r: (r["trust"], r["score"]), reverse=True)
    # Chống trùng: cùng 1 mẫu đăng ở nhiều shop (cùng mã model / cùng hãng + tên gần giống + cùng công suất)
    # -> chỉ giữ tin đáng tin nhất, các tin còn lại KHÔNG gửi Groq (không thể chọn trùng).
    kept_rows = []
    for r in relevant:
        twin = next((k for k in kept_rows if is_duplicate(r["name"], k["name"], r["price"], k["price"])), None)
        if twin is not None:
            r["status"] = STATUS_DUP_MODEL
            r["dup_of"] = twin["name"]
        else:
            kept_rows.append(r)
    trusted_rows = [r for r in kept_rows if trusted(r["sold"], r["rating"])]
    if len(trusted_rows) >= MIN_TRUSTED:
        for r in kept_rows:
            if r not in trusted_rows:
                r["status"] = STATUS_LOW_TRUST
        relevant = trusted_rows
    else:
        relevant = kept_rows
        if warning is None and len(trusted_rows) < 5:
            warning = (f"Chỉ {len(trusted_rows)} sản phẩm có ≥ {MIN_SOLD} lượt bán và rating ≥ {MIN_RATING} — nên "
                       "crawl thêm sản phẩm bán chạy để Top 5 đáng tin hơn.")
    for i, r in enumerate(relevant):
        r["status"] = STATUS_SENT if i < max_products else STATUS_OVER_LIMIT
    sent_rows = [r for r in relevant if r["status"] == STATUS_SENT]
    products = [all_products[r["idx"]] for r in sent_rows]

    report = {
        "keywords": {"files": kw_report.get("files", []), "rows": kw_report.get("rows", 0),
                     "sent": [k["keyword"] for k in keywords], "main": next((k["keyword"] for k in keywords
                                                                              if k.get("role") == ROLE_MAIN), None),
                     "unused": kw_report.get("unused", []), "cut": kw_report.get("cut", []), "core_terms": core},
        "products": {"raw": raw_count, "deduped": len(all_products), "duplicates": max(raw_count - len(all_products), 0),
                     "sent": len(products), "rows": sorted(rows, key=lambda r: (r["status"] != STATUS_SENT,
                                                                                -(r["trust"] or 0))),
                     "warning": warning},
    }
    return keywords, products, report


def check_selected(selected_products: dict, keywords_sent: list[str], filtered: list[str]) -> list[dict]:
    """Kiểm tra 5 sản phẩm Groq chọn: tên chứa từ khóa nào (code đối chiếu, không nhờ AI)."""
    out = []
    pool = list(dict.fromkeys(filtered + keywords_sent))
    for p in selected_products.get("selected_products", []):
        name_a = _ascii(_clean_name(p.get("name", "")))
        matched = [k for k in pool if _contains(name_a, k)]
        out.append({"rank": p.get("rank"), "name": p.get("name"), "matched": matched,
                    "in_source": bool(p.get("affiliate_link")),
                    "status": "✅ Khớp từ khóa" if matched else "⚠️ Tên không chứa từ khóa nào — kiểm tra lại"})
    return out
