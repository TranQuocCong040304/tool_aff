"""Từ khóa có KHỚP với sản phẩm thật trong video không — chặn tiêu đề / ngách hứa sản phẩm không có.

Vd video review Bosch, Sunhouse, SRAPP mà tiêu đề "Top 5 Bếp Từ Panasonic Viền Đen P46" là gây hiểu lầm
(YouTube coi là nội dung lừa đảo / gây hiểu lầm, người xem bấm vào rồi thoát ngay -> kênh bị đánh giá thấp).

Quy tắc: bỏ các chữ chung chung (tốt nhất, giá rẻ, nên mua, cho gia đình, năm, con số...), các chữ CÒN LẠI của từ khóa
(thương hiệu, mã model, đặc điểm như "viền đen", "đôi", "âm") phải cùng xuất hiện trong tên sản phẩm.
  - Tiêu đề "Top N <từ khóa>": từ khóa phải đúng với >= 80% sản phẩm của video.
  - Chọn ngách: từ khóa phải có >= 1 sản phẩm trong file JSON đầu vào.
"""
from __future__ import annotations

import math
import re
import unicodedata

_GENERIC_VI = set("""
tốt nhất giá rẻ loại nào nên mua review đánh top so sánh cho gia đình nhỏ chính hãng cao cấp bình dân
bán chạy mới năm hiện nay đáng tiền siêu bền chất lượng tiết kiệm và với có không là của ở đâu bao nhiêu dưới trên
triệu k nghìn ngàn tr vnd đồng thế nào gì hay đẹp xịn xài dùng sử sao như chọn tư vấn uy tín chuẩn hot trend
2023 2024 2025 2026 2027 2028 2029 2030
""".split())
_GENERIC = {unicodedata.normalize("NFD", w).replace("đ", "d") for w in _GENERIC_VI}
_GENERIC = {"".join(c for c in w if unicodedata.category(c) != "Mn") for w in _GENERIC}


# Cùng nghĩa: "máy rửa chén" = "máy rửa bát", "seri 8" / "serie 8" = "series 8"
_SYN = {"chén": "bát", "chen": "bat", "seri": "series", "serie": "series"}


def _syn(t: str) -> str:
    return " " + " ".join(_SYN.get(w, w) for w in t.split()) + " "


def _ascii(text: str) -> str:
    t = unicodedata.normalize("NFD", (text or "").lower()).replace("đ", "d")
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return _syn(re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", t)).strip())


def _accented(text: str) -> str:
    t = unicodedata.normalize("NFC", (text or "").lower())
    return _syn(re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", t)).strip())


def _has_marks(text: str) -> bool:
    t = unicodedata.normalize("NFC", (text or "").lower())
    return any(unicodedata.category(c) == "Mn" for c in unicodedata.normalize("NFD", t)) or "đ" in t


def _norm_pair(keyword: str):
    """So sánh CÓ DẤU (tránh "báo" ≈ "bảo", "tủ" ≈ "từ"); từ khóa gõ không dấu thì so không dấu."""
    return (_accented, ) if _has_marks(keyword) else (_ascii, )


def specific_tokens(keyword: str) -> list[str]:
    """Chữ mang thông tin cụ thể (thương hiệu / model / đặc điểm) — bỏ chữ chung và con số trần."""
    if _has_marks(keyword):
        return [w for w in _accented(keyword).split() if w not in _GENERIC_VI and not w.isdigit()]
    return [w for w in _ascii(keyword).split() if w not in _GENERIC and not w.isdigit()]


def product_matches(keyword: str, product_names: list[str]) -> list[int]:
    """Chỉ số các sản phẩm có tên chứa ĐỦ mọi chữ cụ thể của từ khóa."""
    norm = _norm_pair(keyword)[0]
    toks = specific_tokens(keyword)
    names = [norm(n) for n in product_names]
    return [i for i, n in enumerate(names) if all(f" {t} " in n for t in toks)]


def missing_tokens(keyword: str, product_names: list[str]) -> list[str]:
    """Chữ cụ thể của từ khóa mà KHÔNG sản phẩm nào có (vd ['panasonic', 'p46'])."""
    norm = _norm_pair(keyword)[0]
    names = " ".join(norm(n) for n in product_names)
    return [t for t in specific_tokens(keyword) if f" {t} " not in names]


def fits_products(keyword: str, product_names: list[str], min_share: float = 0.8) -> bool:
    if not product_names:
        return True
    need = max(1, math.ceil(len(product_names) * min_share))
    return len(product_matches(keyword, product_names)) >= need


def why_not(keyword: str, product_names: list[str]) -> str:
    miss = missing_tokens(keyword, product_names)
    if miss:
        return "không sản phẩm nào có: " + ", ".join(f"“{m}”" for m in miss)
    n = len(product_matches(keyword, product_names))
    return f"chỉ {n}/{len(product_names)} sản phẩm đúng từ khóa này"


_PRICE_CAP_RE = re.compile(r"\b(duoi|tam|khoang|re hon|chi)\s+(\d+(?:[.,]\d+)?)\s*(trieu|tr|k|nghin|ngan)\b")


def price_ok(keyword: str, prices: list[float]) -> bool:
    """'dưới 10 triệu' / 'dưới 500k' mà có sản phẩm đắt hơn -> sai."""
    m = _PRICE_CAP_RE.search(_ascii(keyword))
    if not m or not prices:
        return True
    cap = float(m.group(2).replace(",", ".")) * (1_000_000 if m.group(3) in ("trieu", "tr") else 1_000)
    return max(float(p) for p in prices if p) <= cap * 1.05



# ------------------------------------------------- gán từ khóa phụ cho từng sản phẩm --

# Từ khóa hướng dẫn / sửa lỗi / lắp đặt / tự chế / "test thực tế"... -> không phải nội dung video review Top 5
_NOT_REVIEW_VI = re.compile(
    r" (cách|hướng dẫn|hướng sử dụng|sửa|sửa chữa|báo lỗi|lỗi|lắp|lắp đặt|tháo|vệ sinh|xử lý|"
    r"bảo dưỡng|bảo trì|tự làm|tự chế|chế máy|chế|test|thực tế|trải nghiệm|có sạch không|ở đâu|xe máy|linh kiện|"
    r"phụ kiện|mạch|dây tiếp|tiếp địa|tiếp đất|ống nước|thùng tủ|chân tủ|kích thước thùng|thi công|biểu tượng|xếp|"
    r"đổ muối|nối|chở) ")
_NOT_REVIEW_ASCII = re.compile(
    r" (cach|huong dan|huong su dung|sua|sua chua|bao loi|loi [a-z]{0,3}\d+\w*|lap dat|thao|ve sinh|xu ly|"
    r"bao duong|bao tri|tu lam|tu che|test|thuc te|trai nghiem|co sach khong|o dau|xe may|linh kien|phu kien|"
    r"tiep dia|tiep dat|ong nuoc|thung tu|chan tu|thi cong|bieu tuong) ")


def _not_review(keyword: str) -> bool:
    return bool(_NOT_REVIEW_VI.search(_accented(keyword)) if _has_marks(keyword)
                else _NOT_REVIEW_ASCII.search(_ascii(keyword)))


def _volume_num(k: dict) -> int:
    v = k.get("volume") if k.get("volume") is not None else k.get("search_volume")
    if isinstance(v, (int, float)):
        return int(v)
    m = re.search(r"\d+", str(v or "").replace(".", ""))
    return int(m.group()) if m else 0


def _tokens_with_digits(keyword: str) -> tuple[list[str], callable]:
    """Chữ cụ thể của từ khóa, GIỮ con số (8 bộ, series 4) — gán cho từng sản phẩm phải đúng cả số."""
    if _has_marks(keyword):
        norm = _accented
        toks = [w for w in norm(keyword).split() if w not in _GENERIC_VI]
    else:
        norm = _ascii
        toks = [w for w in norm(keyword).split() if w not in _GENERIC]
    return [w for w in toks if not re.fullmatch(r"(19|20)\d\d", w)], norm


_WEAK_ANCHORS = set("hàng hãng chính nhà bảo hành năm mới tặng kèm quà giao lắp máy rửa bát bộ cao cấp".split())
_PRICE_IN_KW = re.compile(r"(\d+(?:[.,]\d+)?)\s*(triệu|tr|k|nghìn|ngàn|trieu|nghin|ngan)\b")


def _pairs_ok(toks: list[str], text: str, name: str) -> bool:
    """Mỗi cặp chữ liền nhau trong từ khóa phải liền nhau trong dữ liệu sản phẩm — trừ khi 1 chữ là thương hiệu / model
    có trong TÊN sản phẩm (vd "bosch serie 4": "Bosch" ở tên, "Series 4" ở mô tả). Chặn "nhà hàng" ghép từ "nhà sản
    xuất" + "hàng chính hãng"."""
    for i, (a, b) in enumerate(zip(toks, toks[1:])):
        if f" {a} {b} " in text:
            continue
        if b.isdigit() and i + 2 < len(toks):
            continue                        # "bát 8 bộ": kiểm ở cặp "8 bộ" (con số phải đi liền chữ sau nó)
        if a.isdigit() or b.isdigit():
            return False                    # "8 bộ", "series 4": số phải liền đúng chữ, không châm chước
        if any(w not in _WEAK_ANCHORS and f" {w} " in name for w in (a, b)):
            continue
        return False
    return True


def _price_fits(keyword: str, price: float) -> bool:
    m = _PRICE_IN_KW.search(keyword.lower())
    if not m:
        return True
    if not price:
        return False
    cap = float(m.group(1).replace(",", ".")) * (1_000_000 if m.group(2).startswith("tr") else 1_000)
    if re.search(r"dưới|duoi|tầm|tam|khoảng|khoang", keyword.lower()):
        return price <= cap * 1.05 and (not re.search(r"tầm|tam|khoảng|khoang", keyword.lower()) or price >= cap * 0.7)
    return abs(price - cap) <= cap * 0.2


def assign_secondary(keywords: list[dict], products: list[dict], per_product: int = 4,
                     generic_max: int = 6) -> tuple[dict, list[dict], list[dict]]:
    """Gán từ khóa phụ cho ĐÚNG sản phẩm: mọi chữ cụ thể của từ khóa (thương hiệu, model, "8 bộ", "series 4", "để bàn")
    phải có trong tên / thuộc tính / đầu mô tả của sản phẩm đó (cặp chữ liền nhau phải liền nhau), từ khóa có giá phải
    đúng giá. products = [{"rank", "name", "text", "price"}].
    Trả về ({rank: [từ khóa]}, [từ khóa dùng cho mẫu nào cũng được], [bị loại])."""
    n = len(products)
    prod = {p["rank"]: p for p in products}
    by_rank, generic, dropped = {p["rank"]: [] for p in products}, [], []
    for k in sorted(keywords, key=lambda k: -_volume_num(k)):
        kw = (k.get("keyword") or "").strip()
        if not kw or _not_review(kw):
            dropped.append(k)
            continue
        toks, norm = _tokens_with_digits(kw)
        hits = []
        for r, p in prod.items():
            text, name = norm(p.get("name", "") + " . " + p.get("text", "")), norm(p.get("name", ""))
            if (all(f" {w} " in text for w in toks) and _pairs_ok(toks, text, name)
                    and _price_fits(kw, float(p.get("price") or 0))):
                hits.append(r)
        if not hits:
            dropped.append(k)
        elif n and len(hits) >= math.ceil(n * 0.8):
            if len(generic) < generic_max:
                generic.append(k)
        else:
            # Mỗi cụm chỉ giao cho 1 sản phẩm (cả video nói 1 lần): ưu tiên sản phẩm có NGUYÊN cụm trong tên,
            # rồi sản phẩm đang ít từ khóa nhất.
            phrase = " " + " ".join(toks) + " "
            def _key(r):
                return (phrase not in norm(prod[r].get("name", "")), len(by_rank[r]))
            for r in sorted(hits, key=_key):
                if len(by_rank[r]) < per_product:
                    by_rank[r].append(k)
                    break
    return by_rank, generic, dropped
