"""File thông tin sản phẩm (.md) cho từng Top — export từ extension tải trang Shopee
(tiêu đề, link, thương hiệu, giá, rating, lượt bán, thuộc tính, mô tả, Item ID / Shop ID…).

Lưu ở:  <run>/product_media/<rank>_<tên>/product_info.md
Dùng ở: prompts/04b_product_segment.md ({{PRODUCT_INFO}}) — Gemini viết lời bình theo THÔNG SỐ THẬT trong
        file (pin, trọng lượng, công nghệ, rating, lượt bán…) thay vì đoán từ tên sản phẩm.

Có kiểm tra file .md có đúng sản phẩm không (so Item ID trong file với link Shopee của Top đó).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .utils import slugify

INFO_FILENAME = "product_info.md"
PROMPT_MAX_CHARS = 3500


def info_path(product: dict, run_dir: Path) -> Path:
    return Path(run_dir) / "product_media" / f"{product['rank']:02d}_{slugify(product['name'])}" / INFO_FILENAME


def _field(text: str, label: str) -> Optional[str]:
    m = re.search(rf"^\s*-\s*\*\*{re.escape(label)}\*\*\s*:\s*(.+?)\s*$", text, re.MULTILINE | re.IGNORECASE)
    return m.group(1).strip() if m else None


def _section(text: str, title: str) -> str:
    m = re.search(rf"^##\s*{re.escape(title)}\s*$(.*?)(?=^##\s|\Z)", text, re.MULTILINE | re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _num(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    m = re.search(r"[\d.,]+", raw)
    if not m:
        return None
    s = m.group(0)
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", s):
        s = re.sub(r"[.,]", "", s)
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


def parse_product_md(text: str) -> dict:
    text = text.replace("\r\n", "\n")
    title = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    link = re.search(r"\*\*Product Link\*\*\s*:\s*\[[^\]]*\]\(([^)]+)\)", text)
    attrs = {}
    for line in _section(text, "Product Attributes").splitlines():
        m = re.match(r"\s*-\s*\*\*(.+?)\*\*\s*:\s*(.+)$", line)
        if m:
            attrs[m.group(1).strip()] = m.group(2).strip()
    desc = _section(text, "Description")
    desc = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", desc)          # bỏ ảnh markdown
    desc = re.sub(r"[ \t]+\n", "\n", desc)
    desc = re.sub(r"\n{2,}", "\n", desc).strip()  # bỏ dòng trống thừa (tốn token prompt)
    return {
        "title": title.group(1).strip() if title else "",
        "link": link.group(1).strip() if link else None,
        "item_id": _field(text, "Item ID"),
        "shop_id": _field(text, "Shop ID"),
        "brand": _field(text, "Brand"),
        "shop": _field(text, "Shop") or _field(text, "Shop Name"),
        "location": _field(text, "Location"),
        "price": _num(_field(text, "Current Price")),
        "original_price": _num(_field(text, "Original Price")),
        "discount": _field(text, "Discount"),
        "rating": _num(_field(text, "Rating")),
        "reviews": _num(_field(text, "Total Reviews")),
        "sold": _num(_field(text, "Sold")),
        "shop_rating": _num(_field(text, "Shop Rating")),
        "official_shop": _field(text, "Official Shop"),
        "attributes": attrs,
        "description": desc,
    }


def _ids_from_link(link: str) -> tuple[Optional[str], Optional[str]]:
    """(shop_id, item_id) từ link Shopee: '…-i.<shop>.<item>' hoặc '/product/<shop>/<item>'."""
    link = link or ""
    m = re.search(r"i\.(\d+)\.(\d+)", link) or re.search(r"/product/(\d+)/(\d+)", link)
    return (m.group(1), m.group(2)) if m else (None, None)


def match_product(info: dict, product: dict) -> Optional[bool]:
    """True = cùng sản phẩm (Item ID khớp), False = KHÁC sản phẩm, None = không đủ dữ liệu để biết."""
    _, item = _ids_from_link(product.get("affiliate_link", ""))
    file_item = info.get("item_id") or _ids_from_link(info.get("link") or "")[1]
    if item and file_item:
        return item == str(file_item).strip()
    return None


def find_owner(info: dict, products: list[dict]) -> Optional[dict]:
    """Sản phẩm (trong 5 Top) khớp với file .md — theo Item ID, rồi tới tên gần giống."""
    for p in products:
        if match_product(info, p):
            return p
    import difflib
    title = (info.get("title") or "").lower()
    best, ratio = None, 0.0
    for p in products:
        r = difflib.SequenceMatcher(None, title, p.get("name", "").lower()).ratio()
        if r > ratio:
            best, ratio = p, r
    return best if ratio >= 0.8 else None


def load_info(product: dict, run_dir: Path) -> Optional[dict]:
    path = info_path(product, run_dir)
    if not path.exists():
        return None
    try:
        return parse_product_md(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None


def prompt_block(product: dict, run_dir: Optional[Path]) -> str:
    """Đoạn chèn vào prompt 04b. Không có file / file của sản phẩm khác -> chuỗi rỗng."""
    if run_dir is None:
        return ""
    info = load_info(product, run_dir)
    if not info or match_product(info, product) is False:
        return ""
    lines = ["Thông tin chi tiết từ trang Shopee của sản phẩm (NGUỒN SỰ THẬT — chỉ nói thông số / tính năng có ở "
             "đây hoặc nhìn thấy trong ảnh, TUYỆT ĐỐI không bịa thông số khác; nói số liệu tự nhiên, không đọc như "
             "bảng thông số):"]
    if info.get("brand"):
        lines.append(f"- Thương hiệu: {info['brand']}")
    if info.get("price"):
        money = lambda v: f"{int(v):,}".replace(",", ".") + "đ"
        extra = f" (giá gốc {money(info['original_price'])}, giảm {info['discount']})" \
            if info.get("original_price") and info.get("discount") else ""
        lines.append(f"- Giá: {money(info['price'])}" + extra)
    if info.get("rating"):
        rv = f" từ {int(info['reviews'])} đánh giá" if info.get("reviews") else ""
        lines.append(f"- Đánh giá: {info['rating']:g}/5{rv}")
    if info.get("sold"):
        lines.append(f"- Đã bán: {int(info['sold'])}")
    if info.get("shop"):
        off = " (shop chính hãng / Mall)" if (info.get("official_shop") or "").lower() == "yes" else ""
        lines.append(f"- Shop: {info['shop']}{off}" + (f", gửi từ {info['location']}" if info.get("location") else ""))
    attrs = {k: v for k, v in (info.get("attributes") or {}).items() if k.lower() not in ("kho", "gửi từ")}
    if attrs:
        lines.append("- Thuộc tính: " + "; ".join(f"{k}: {v}" for k, v in attrs.items()))
    if info.get("description"):
        lines.append("- Mô tả của người bán:\n" + info["description"])
    text = "\n".join(lines)
    if len(text) > PROMPT_MAX_CHARS:
        text = text[:PROMPT_MAX_CHARS].rsplit("\n", 1)[0] + "\n…(đã rút gọn)"
    return text + "\n"
