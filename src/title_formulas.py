"""Tiêu đề SEO theo 4 công thức (tab 🔑 Từ khóa & Tiêu đề SEO).

  CT1 (Cơ bản & An toàn):     [Review / Đánh giá] + Top N + [Từ khóa traffic] + [Tính từ nổi bật] + [Năm]
  CT2 (Nhấn mạnh lợi ích):    Top N + [Từ khóa traffic] + giúp [Lợi ích] + tốt nhất [Năm]
  CT3 (Phân khúc giá/Đối tượng): Top N + [Từ khóa traffic] + [Giá rẻ / Dưới X triệu / Cho đối tượng]
  CT4 (Từ khóa lên đầu):       [Từ khóa traffic]: Review Top N sản phẩm đáng mua nhất hiện nay

[Từ khóa traffic] = từ khóa có VOLUME CAO NHẤT trong file CSV ở data/input/ (đang "Dùng", bỏ từ khóa loại / chỉ làm
tag, từ khóa hướng dẫn "cách / sửa / bảo dưỡng…" và từ khóa thương hiệu không có trong sản phẩm của video).
Phần ghép chạy offline; nút "AI gợi ý" (Groq) chỉ điền tính từ / lợi ích / đối tượng dựa trên sản phẩm thật.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from .seo_titles import ROLE_NOISE, ROLE_TAG, Keyword, strip_accents, vn_title_case

FORMULAS = {
    "CT1": "Cơ bản & An toàn",
    "CT2": "Nhấn mạnh lợi ích",
    "CT3": "Phân khúc giá / Đối tượng",
    "CT4": "Đưa từ khóa lên đầu",
}
_GUIDE_RE = re.compile(r"\b(cach|huong dan|sac pin|sua|sua chua|ve sinh|loi|thao lap|lap|lap dat|cai dat|reset|su dung|"
                       r"bao duong|bao tri|thay|linh kien|phu kien|gia do|ket noi)\b")
_BRANDS_HINT = "Thương hiệu"
ROLE_TRAFFIC_FALLBACK = "Chung (không có từ khóa khớp sản phẩm)"


def _a(text: str) -> str:
    return " " + re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", strip_accents((text or "").lower()))).strip() + " "


def _volume(k: Keyword) -> int:
    return int(k.search_volume or 0)


def traffic_candidates(keywords: list[Keyword], product_names: Optional[list[str]] = None,
                       main: str = "", prices: Optional[list[float]] = None,
                       rejected: Optional[list] = None) -> list[Keyword]:
    """Từ khóa dùng được làm [Từ khóa traffic], volume giảm dần (phần tử đầu = volume cao nhất).
    Có sản phẩm của run -> từ khóa phải ĐÚNG với >= 80% sản phẩm (thương hiệu / model / đặc điểm có trong tên sản
    phẩm, "dưới X triệu" đúng với giá) — tiêu đề "Top 5 <từ khóa>" không được hứa sản phẩm video không có.
    `rejected` (list) nhận [(từ khóa, lý do)] của các từ khóa volume cao bị loại vì không khớp sản phẩm."""
    from .keyword_fit import fits_products, price_ok, why_not
    out = []
    for k in keywords:
        if not k.use or k.role in (ROLE_NOISE, ROLE_TAG) or _volume(k) <= 0:
            continue
        if _GUIDE_RE.search(_a(k.keyword)):
            continue
        if product_names and not fits_products(k.keyword, product_names):
            if rejected is not None:
                rejected.append((k, why_not(k.keyword, product_names)))
            continue
        if prices and not price_ok(k.keyword, prices):
            if rejected is not None:
                rejected.append((k, f"có sản phẩm giá {max(prices):,.0f}đ — vượt mức giá trong từ khóa".replace(",", ".")))
            continue
        out.append(k)
    out = sorted(out, key=lambda k: -_volume(k))
    if not out and product_names:
        # Không từ khóa nào khớp -> dùng phần CHUNG của từ khóa chính mà mọi sản phẩm đều đúng (vd "Bếp Từ")
        from .keyword_fit import _ascii
        names = [_ascii(n) for n in product_names]
        words = [w for w in (main or "").split() if sum(f" {_ascii(w).strip()} " in n for n in names) >= 0.8 * len(names)]
        if words:
            generic = " ".join(words)
            same = next((k for k in keywords if _a(k.keyword) == _a(generic)), None)
            out = [same or Keyword(keyword=generic, role=ROLE_TRAFFIC_FALLBACK)]
    if rejected is not None:
        rejected.sort(key=lambda r: -_volume(r[0]))
    return out


def price_segment(prices: list[float]) -> Optional[str]:
    """Giá cao nhất trong video -> "Dưới X Triệu" / "Dưới X00K" (làm tròn LÊN, nên luôn đúng với mọi sản phẩm)."""
    prices = [float(p) for p in prices if p]
    if not prices:
        return None
    top = max(prices)
    if top >= 1_000_000:
        x = math.ceil(top / 500_000) * 0.5
        return f"Dưới {x:g} Triệu".replace(".", ",")
    return f"Dưới {math.ceil(top / 100_000) * 100}K"


def _kw(text: str) -> str:
    return vn_title_case(" ".join((text or "").split()))


def build_titles(traffic: str, n: int = 5, year: Optional[int] = None, review_word: str = "Review",
                 adjective: str = "Đáng Mua Nhất", benefit: str = "", segment: str = "Giá Rẻ") -> list[dict]:
    year = year or datetime.now().year
    kw = _kw(traffic)
    raw = {
        "CT1": f"{review_word} Top {n} {kw} {adjective} {year}",
        "CT2": f"Top {n} {kw} Giúp {benefit} Tốt Nhất {year}" if benefit.strip() else "",
        "CT3": f"Top {n} {kw} {segment}" if segment.strip() else "",
        "CT4": f"{kw}: Review Top {n} Sản Phẩm Đáng Mua Nhất Hiện Nay",
    }
    out = []
    for code, text in raw.items():
        if not text:
            continue
        text = vn_title_case(" ".join(text.split()))
        warn = []
        if len(text) > 70:
            warn.append(f"dài {len(text)} ký tự (> 70, YouTube cắt bớt trên điện thoại)")
        out.append({"formula": code, "label": f"{code} – {FORMULAS[code]}", "text": text, "chars": len(text),
                    "keyword": traffic, "warnings": warn})
    return out


def ai_fill(text_client, traffic: str, products: list[dict], audience_hint: str = "") -> dict:
    from .prompt_loader import render
    lines = "\n".join(f"- {p.get('name', '')[:80]} — " + ", ".join(p.get("needs_met") or [])[:120] for p in products)
    data = text_client.generate_json(render("10_title_formula.md", TRAFFIC=traffic, PRODUCTS=lines or "(không có)",
                                            AUDIENCE_HINT=audience_hint or "(không rõ)"))
    return {k: vn_title_case(str(data.get(k) or "").strip()) for k in ("adjective", "benefit", "audience")}


def save_to_run(run_dir: Path, keywords: list[Keyword], traffic: Keyword, titles: list[dict], chosen: str,
                tags: list[str], source_files: list[str], options: dict) -> Path:
    """Ghi vào 09_seo_keywords.json của run (giữ các trường cũ) — bước Script & Audio / mô tả / metadata đọc file này."""
    from dataclasses import asdict
    from .seo_titles import ROLE_MAIN, SEO_FILE
    path = Path(run_dir) / SEO_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (ValueError, OSError):
        data = {}
    main = next((k.keyword for k in keywords if k.role == ROLE_MAIN), traffic.keyword)
    data.update({
        "main_keyword": main,
        "keywords": [asdict(k) for k in keywords],
        "traffic_keyword": {"keyword": traffic.keyword, "search_volume": traffic.search_volume,
                            "competition": traffic.competition, "role": traffic.role},
        "formula_titles": titles,
        "formula_options": options,
        "tags": tags,
        "source_files": source_files,
        "chosen_title": chosen,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    })
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
