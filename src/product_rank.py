"""Độ tin cậy + chống trùng sản phẩm khi chọn Top 5 (code làm, không nhờ AI).

- trust_score: ưu tiên LƯỢT BÁN cao, RATING cao, nhiều LƯỢT ĐÁNH GIÁ (nếu file JSON có trường này — kết quả tìm kiếm
  Shopee hiện chỉ có lượt bán + rating).
- is_duplicate: 2 tin đăng là CÙNG 1 MẪU (cùng mã model, hoặc cùng hãng + tên gần giống + cùng công suất / dung tích)
  — vd "Bếp Từ Cảm Ứng SUNHOUSE 2200W..." và "[TẶNG NỒI]Bếp Từ Cảm Ứng SUN HOUSE... 2200W" của 2 shop.
"""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Optional

MIN_SOLD = 20          # dưới mức này coi là "chưa đủ người mua để tin"
MIN_RATING = 4.0

_NOISE = set("""
tang kem noi lau inox free giao lap cat da chinh hang hang moi nhat 2023 2024 2025 2026 2027 cao cap sieu ben
bao hanh nam thang voucher shopee len den tr doc quyen online nhap khau tu duc nguyen chiec mien phi
va voi cho co the loai sale gia re tot new series serial doi dien tu bep cam ung hong ngoai
cong suat nau nhanh che do muc nhiet phim bam mat kinh cuong luc tiet kiem dien nang pro plus max
""".split())
_UNIT_RE = re.compile(r"^\d+(?:[.,]\d+)?(w|kw|l|ml|cm|mm|v|lit|inch|kg|g|mah|h)$")
_BRAND_FIX = {"sun house": "sunhouse", "lock lock": "locknlock", "lock&lock": "locknlock", "lock n lock": "locknlock",
              "bluestone": "bluestone", "blue stone": "bluestone"}


def _ascii(text: str) -> str:
    t = unicodedata.normalize("NFD", (text or "").lower()).replace("đ", "d")
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    for a, b in _BRAND_FIX.items():
        t = t.replace(a, b)
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", t)).strip()


def _tokens(name: str) -> list[str]:
    return _ascii(re.sub(r"\[[^\]]*\]", " ", name or "")).split()


def model_codes(name: str) -> set[str]:
    """Mã model: chữ + số lẫn nhau (shd6154, bt205, puj611bb5e, 9109s) — bỏ thông số (2200w, 60cm, 1.5l) và năm."""
    out = set()
    toks = _tokens(name)
    for i, t in enumerate(toks):
        if _UNIT_RE.match(t) or t.isdigit() or re.fullmatch(r"(19|20)\d\d", t):
            continue
        if len(t) >= 3 and re.search(r"\d", t) and re.search(r"[a-z]", t):
            out.add(t)
        # "SHB 8101", "KZ C37K": tiền tố chữ ngắn + số -> ghép
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if (re.fullmatch(r"[a-z]{2,4}", t) and t not in _NOISE and re.fullmatch(r"\d{3,}[a-z]?", nxt)
                and not _UNIT_RE.match(nxt) and not re.fullmatch(r"(19|20)\d\d", nxt)):
            out.add(t + nxt)
    return out


def specs(name: str) -> set[str]:
    return {t for t in _tokens(name) if _UNIT_RE.match(t)}


def _signature(name: str) -> set[str]:
    return {t for t in _tokens(name) if t not in _NOISE and not t.isdigit() and len(t) > 1}


def is_duplicate(a: str, b: str, price_a: float = 0, price_b: float = 0) -> bool:
    ca, cb = model_codes(a), model_codes(b)
    if ca and cb:
        return bool(ca & cb)                    # cả 2 có mã model -> trùng khi chung mã
    if ca & cb:
        return True
    sa, sb = _signature(a), _signature(b)
    if not sa or not sb:
        return False
    if specs(a) and specs(b) and not (specs(a) & specs(b)):
        return False                            # khác công suất / dung tích -> khác mẫu
    jac = len(sa & sb) / len(sa | sb)
    if price_a and price_b and abs(price_a - price_b) / max(price_a, price_b) > 0.5:
        return False
    return jac >= 0.5 or (len(sa & sb) >= 2 and (sa <= sb or sb <= sa))


def trust_score(sold: Optional[int], rating: Optional[float], reviews: Optional[int] = None) -> float:
    s = math.log10(1 + (sold or 0)) * 2.0                 # 10 -> 2.1, 100 -> 4, 1k -> 6, 10k -> 8
    r = ((float(rating) - 4.0) * 3.0) if rating else -0.5  # 5.0 -> +3, 4.5 -> +1.5, 4.0 -> 0
    n = math.log10(1 + reviews) * 1.5 if reviews else 0.0
    return round(s + r + n, 2)


def trusted(sold: Optional[int], rating: Optional[float]) -> bool:
    return (sold or 0) >= MIN_SOLD and (rating is None or float(rating) >= MIN_RATING)
