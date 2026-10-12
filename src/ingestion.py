"""Đọc dữ liệu đầu vào: CSV từ khóa (1 hoặc nhiều file) và JSON sản phẩm Shopee.

Đã cập nhật theo dữ liệu thật (khác giả định ban đầu):

- Từ khóa: export từ tool nghiên cứu keyword (vd "..._matching_terms.csv",
  "..._related_keywords.csv"), cột "Keyword", "Search volume", "Competition",
  "Overall", (tùy file) "Related score". Có thể có nhiều file cho cùng 1 ngách
  -> `load_keywords` nhận một thư mục và gộp tất cả *.csv trong đó, dedupe theo
  từ khóa, giữ lại các chỉ số nếu có.

- Sản phẩm: export từ extension scrape Shopee, dạng
  {"_meta": {...}, "searchQuery": "...", "products": [ {...} ]}. Mỗi sản phẩm
  có "productUrl" (KHÔNG phải link affiliate đã gắn tag), "price" dạng chuỗi
  "₫4.095.000", "salesVolume" dạng chuỗi "218 sold" / "1k sold".
"""
import csv
import json
import re
from pathlib import Path
from typing import List, Optional

from .models import Product

_NAME_KEYS = ("name", "product_name", "title")
_PRICE_KEYS = ("price", "product_price")
_IMAGE_KEYS = ("imageUrl", "image_url", "image", "thumbnail")
_LINK_KEYS = ("affiliate_link", "link", "url", "product_link")
_URL_ONLY_KEYS = ("productUrl",)  # link chưa gắn tag affiliate


def _first_present(d: dict, keys: tuple, required: bool, field_label: str):
    for key in keys:
        if key in d and d[key] not in (None, ""):
            return d[key]
    if required:
        raise ValueError(
            f"Không tìm thấy trường '{field_label}' trong sản phẩm: {d}. "
            f"Đã thử các khóa: {keys}"
        )
    return None


def _parse_price(raw) -> float:
    if isinstance(raw, (int, float)):
        return float(raw)
    digits = re.sub(r"[^\d]", "", str(raw))
    if not digits:
        raise ValueError(f"Không parse được giá tiền: {raw!r}")
    return float(digits)


def _parse_sold(raw: Optional[str]) -> Optional[int]:
    if not raw:
        return None
    m = re.search(r"([\d.,]+)\s*(k)?", str(raw), re.IGNORECASE)
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    if m.group(2):
        num *= 1000
    return int(num)


def _parse_int(raw: Optional[str]) -> Optional[int]:
    if raw is None or str(raw).strip() == "":
        return None
    digits = re.sub(r"[^\d]", "", str(raw))
    return int(digits) if digits else None


def _parse_float(raw: Optional[str]) -> Optional[float]:
    if raw is None or str(raw).strip() == "":
        return None
    cleaned = re.sub(r"[^\d.]", "", str(raw))
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def load_keywords(source: str | Path, max_keywords: int = 60) -> List[dict]:
    """Giữ nguyên chữ ký cũ (main.py dùng) — xem load_keywords_report để lấy thêm báo cáo."""
    return load_keywords_report(source, max_keywords)[0]


def load_keywords_report(source: str | Path, max_keywords: int = 60) -> tuple:
    """Như load_keywords nhưng trả thêm báo cáo cho dashboard tab 🎯:
    {"files", "rows", "unused": [{keyword, reason}], "cut": [keyword], "sent": int}.

    - Nhận cả file "Từ khóa đã phân loại (.csv)" do tab 🔑 xuất (cột "Volume gốc" / "Volume (số)",
      "Dùng", "Vai trò"): dòng có Dùng = False (bạn đã bỏ tick / Loại) KHÔNG được gửi cho Groq.
    """
    """(nội dung gốc) Đọc 1 file CSV hoặc gộp tất cả *.csv trong 1 thư mục.

    Trả về list dict: {keyword, search_volume, competition, overall, related_score}
    đã dedupe và sắp xếp theo search_volume giảm dần, giới hạn `max_keywords` dòng
    để không làm prompt quá dài.
    """
    source = Path(source)
    if source.is_dir():
        csv_files = sorted(source.glob("*.csv"))
    elif source.is_file():
        csv_files = [source]
    else:
        raise FileNotFoundError(f"Không tìm thấy file/thư mục từ khóa: {source}")

    if not csv_files:
        raise FileNotFoundError(f"Không có file .csv nào trong: {source}")

    merged: dict[str, dict] = {}
    report = {"files": [p.name for p in csv_files], "rows": 0, "unused": [], "cut": [], "sent": 0}
    unused_keys: set = set()
    for path in csv_files:
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                continue
            
            orig_cols = {c.strip().lower(): c for c in reader.fieldnames}
            
            def get_orig_key(possible_names):
                for n in possible_names:
                    if n.lower() in orig_cols:
                        return orig_cols[n.lower()]
                return None
                
            kw_col = get_orig_key(["Keyword", "Từ khóa", "Từ khoá", "Search term", "Search terms"])
            if not kw_col:
                raise ValueError(f"{path.name} cần có cột 'Keyword' hoặc 'Từ khóa'. Các cột hiện có: {reader.fieldnames}")
            
            vol_col = get_orig_key(["Search volume", "Volume gốc", "Volume", "Volume (số)", "Lượng tìm kiếm",
                                    "Lượt tìm kiếm"])
            use_col = get_orig_key(["Dùng"])
            role_col = get_orig_key(["Vai trò"])
            comp_col = get_orig_key(["Competition", "Cạnh tranh", "Độ cạnh tranh"])
            overall_col = get_orig_key(["Overall", "Điểm tổng", "Điểm"])
            related_col = get_orig_key(["Related score", "Điểm liên quan", "Related"])

            for row in reader:
                kw_raw = row.get(kw_col)
                if not kw_raw or not kw_raw.strip():
                    continue
                keyword = kw_raw.strip()
                key = keyword.lower()
                report["rows"] += 1
                if use_col and str(row.get(use_col) or "").strip().lower() in ("false", "0", "không"):
                    # bạn đã bỏ tick "Dùng" / đánh dấu Loại ở tab 🔑 -> không gửi cho Groq
                    if key not in unused_keys:
                        unused_keys.add(key)
                        report["unused"].append({"keyword": keyword,
                                                 "reason": (row.get(role_col) or "").strip() or "Bỏ tick Dùng"})
                    continue
                entry = merged.setdefault(key, {"keyword": keyword})
                if role_col and (row.get(role_col) or "").strip() and not entry.get("role"):
                    entry["role"] = row[role_col].strip()
                
                if vol_col and entry.get("search_volume") is None and not entry.get("volume_raw"):
                    raw_vol = (row.get(vol_col) or "").strip()
                    if raw_vol.startswith(("<", ">")):
                        # "<750" = dưới ngưỡng hiển thị của tool, KHÔNG phải 750 -> giữ nguyên chữ.
                        entry["volume_raw"] = raw_vol
                    else:
                        entry["search_volume"] = _parse_int(raw_vol)
                
                if comp_col and entry.get("competition") is None:
                    entry["competition"] = _parse_float(row.get(comp_col))
                
                if overall_col and entry.get("overall") is None:
                    entry["overall"] = _parse_float(row.get(overall_col))
                
                if related_col:
                    related_score = _parse_float(row.get(related_col))
                    if related_score is not None:
                        entry["related_score"] = related_score

    keywords = [k for k in merged.values() if k["keyword"].lower() not in unused_keys]
    if not keywords:
        raise ValueError(f"Không đọc được từ khóa nào từ: {source}")
    # Từ khóa chính (cột Vai trò = "Chính") luôn đứng đầu, rồi theo volume.
    keywords.sort(key=lambda k: (k.get("role") == "Chính", k.get("search_volume") or 0), reverse=True)
    report["cut"] = [k["keyword"] for k in keywords[max_keywords:]]
    keywords = keywords[:max_keywords]
    report["sent"] = len(keywords)
    return keywords, report


def _product_key_id(p: Product) -> Optional[str]:
    """Mã sản phẩm Shopee 'shopId.itemId' lấy từ link (vd .../a-i.614923759.43209931173
    hoặc .../Ten-san-pham-i.614923759.43209931173?sp_atk=...) — cùng 1 sản phẩm dù link
    viết khác nhau."""
    m = re.search(r"i\.(\d+)\.(\d+)", p.affiliate_link or "")
    return f"{m.group(1)}.{m.group(2)}" if m else None


def _product_key_name(p: Product) -> str:
    """Tên chuẩn hóa: chữ thường, bỏ dấu câu/khoảng trắng thừa, bỏ tag kiểu [Hàng mới về]."""
    name = re.sub(r"\[[^\]]*\]|【[^】]*】", " ", p.name.lower())
    return re.sub(r"\W+", " ", name).strip()


def dedupe_products(products: List[Product]) -> List[Product]:
    """Bỏ sản phẩm trùng lặp khi gộp nhiều file JSON — trùng nếu CÙNG mã sản phẩm
    Shopee (shopId.itemId / link) HOẶC CÙNG tên (cùng 1 mẫu đăng ở nhiều shop).
    Giữ lại bản có lượt bán cao nhất (đáng tin hơn), vị trí theo lần xuất hiện đầu.
    Không lọc thì vừa tốn token gửi Groq vô ích, vừa có thể chọn trùng 1 sản
    phẩm 2 lần trong Top 5."""
    kept: List[Product] = []
    index_by_key: dict = {}
    dropped: List[str] = []
    for p in products:
        keys = [k for k in (_product_key_id(p), "name:" + _product_key_name(p),
                            "url:" + (p.affiliate_link or "").split("?")[0]) if k and k not in ("name:", "url:")]
        hit = next((index_by_key[k] for k in keys if k in index_by_key), None)
        if hit is None:
            index_by_key.update({k: len(kept) for k in keys})
            kept.append(p)
            continue
        dropped.append(p.name)
        if (p.sold or 0) > (kept[hit].sold or 0):
            kept[hit] = p  # bản này bán chạy hơn -> thay thế
        index_by_key.update({k: hit for k in keys})
    if dropped:
        print(f"[INFO] Bỏ {len(dropped)} sản phẩm trùng lặp giữa các file JSON: "
              + "; ".join(n[:50] for n in dropped))
    return kept


def load_products(source: str | Path, max_products: int = 30) -> List[Product]:
    source = Path(source)
    if source.is_dir():
        json_files = sorted(source.glob("*.json"))
    elif source.is_file():
        json_files = [source]
    else:
        raise FileNotFoundError(f"Không tìm thấy file/thư mục sản phẩm: {source}")

    if not json_files:
        raise FileNotFoundError(f"Không có file .json nào trong: {source}")

    raw_products = []
    for json_path in json_files:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and "products" in raw:
            raw_products.extend(raw["products"])
        elif isinstance(raw, list):
            raw_products.extend(raw)
        else:
            raise ValueError(
                f"File JSON sản phẩm ({json_path.name}) phải là một list, hoặc dict có khóa 'products' "
                "(định dạng export từ extension scrape Shopee)."
            )

    products: List[Product] = []
    warned_untagged_link = False
    for item in raw_products:
        price_raw = _first_present(item, _PRICE_KEYS, required=True, field_label="price")
        link = _first_present(item, _LINK_KEYS, required=False, field_label="affiliate_link")
        is_tagged = True
        if link is None:
            link = _first_present(item, _URL_ONLY_KEYS, required=True, field_label="affiliate_link/productUrl")
            is_tagged = False
            if not warned_untagged_link:
                print(
                    "[CẢNH BÁO] File sản phẩm không có link affiliate đã gắn tag — đang dùng "
                    "productUrl gốc. Cần chuyển các link này qua Shopee Affiliate Portal trước "
                    "khi đăng video, nếu không sẽ KHÔNG nhận được hoa hồng."
                )
                warned_untagged_link = True

        sold = item.get("sold")
        if sold is None:
            sold = _parse_sold(item.get("salesVolume"))
        reviews = next((item.get(k) for k in ("reviewCount", "ratingCount", "rating_count", "reviews", "cmt_count",
                                               "totalRatings") if item.get(k) not in (None, "")), None)
        if isinstance(reviews, str):
            reviews = _parse_sold(reviews)

        products.append(
            Product(
                name=_first_present(item, _NAME_KEYS, required=True, field_label="name"),
                price=_parse_price(price_raw),
                image_url=_first_present(item, _IMAGE_KEYS, required=True, field_label="image_url"),
                affiliate_link=link,
                sold=sold,
                rating=item.get("rating"),
                discount=item.get("discount"),
                position=item.get("position"),
                is_tagged_affiliate_link=is_tagged,
                reviews=reviews,
            )
        )
    if not products:
        raise ValueError(f"Không đọc được sản phẩm nào từ: {source}")

    products = dedupe_products(products)

    if len(products) > max_products:
        # File nhiều sản phẩm (đã gặp thật: 52 sp) gửi hết vào prompt lọc top-5
        # (03_loc_san_pham.md) dễ vượt giới hạn token/phút của Groq (lỗi 413
        # "Request too large" — tài khoản free/mới giới hạn càng thấp). Ưu
        # tiên giữ lại sản phẩm bán chạy nhất trong giới hạn thay vì cắt theo
        # thứ tự file gốc, để không mất ứng viên tốt.
        print(
            f"[INFO] File có {len(products)} sản phẩm — chỉ dùng {max_products} sản phẩm bán chạy nhất "
            f"để tránh vượt giới hạn token/phút của Groq khi lọc top 5."
        )
        products = sorted(products, key=lambda p: p.sold or 0, reverse=True)[:max_products]

    return products
