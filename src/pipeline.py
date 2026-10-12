"""Chuỗi prompt, chia làm 2 giai đoạn để chèn điểm dừng thủ công ở giữa, và
định tuyến 2 provider theo đúng việc mỗi bên làm tốt:

- `text_client` (Groq — free tier rộng rãi 30 rpm/~14.400 ngày): mọi bước chỉ
  cần text, không cần xem ảnh — ngách, lọc keyword, lọc sản phẩm, hook, CTA,
  metadata.
- `vision_client` (Gemini — multimodal, free tier hẹp 5 rpm/20 ngày): CHỈ bước
  viết lời bình từng sản phẩm, vì đây là bước duy nhất cần xem ảnh/video thật.

Nhờ vậy 1 video chỉ còn tốn ~5 request Gemini (thay vì 11) — vừa đúng bằng số
sản phẩm, phần còn lại dồn hết sang Groq.

Giai đoạn 1 (chọn lọc): Ngách -> Lọc keyword -> Lọc 5 sản phẩm.
    Dừng lại ở đây để người dùng vào link từng sản phẩm lấy ảnh/video chi tiết
    thật (product_media/<rank>_<slug>/) — xem src/product_links.py.

Giai đoạn 2 (viết script): Hook -> lời bình từng sản phẩm (kèm ảnh thật nếu có)
    -> CTA -> Metadata. Script được LẮP RÁP bằng code (không nhờ LLM tự gắn
    thẻ), nên tên sản phẩm trong "### SẢN PHẨM:" luôn khớp tuyệt đối với
    03_products_selected.json.
"""
import inspect
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import List, Optional

from .models import Product
from .product_info import prompt_block as product_info_block
from .product_links import find_product_media
from .prompt_loader import render
from .seo_titles import apply_chosen_title
from .script_length import key as length_key, rule as length_rule
from .seo_context import (SeoContext, allocate_keywords, cta_seo_block, hook_seo_block, hook_title_block,
                          load_seo_context, metadata_blocks, segment_seo_block)
from .utils import match_by_name

_IMG_MARKER_RE = re.compile(r"\[\[IMG:(\d+)\]\]")


SELECTION_REPORT_FILE = "02b_selection_report.json"


@dataclass
class SelectionResult:
    niche: dict
    filtered_keywords: dict
    selected_products: dict
    report: dict = field(default_factory=dict)   # báo cáo minh bạch cho dashboard tab 🎯


@dataclass
class ScriptResult:
    script: str
    metadata: dict
    flow_prompts: dict  # {rank_str: prompt_text} — sản phẩm không có ảnh/video thật


def _keywords_to_bullet_list(keywords: List[dict]) -> str:
    lines = []
    for kw in keywords:
        parts = [kw["keyword"]]
        if kw.get("search_volume") is not None:
            parts.append(f"volume={kw['search_volume']}")
        elif kw.get("volume_raw"):
            parts.append(f"volume={kw['volume_raw']}")  # vd "<750": không rõ số thật
        if kw.get("competition") is not None:
            parts.append(f"competition={kw['competition']:.1f}")
        if kw.get("related_score") is not None:
            parts.append(f"related_score={kw['related_score']:.1f}")
        lines.append("- " + ", ".join(parts))
    return "\n".join(lines)


def _products_to_json(products: List[Product]) -> str:
    """Bản RÚT GỌN gửi cho LLM: mã "id" ngắn thay cho 2 URL dài (ảnh + link
    Shopee) và bỏ trường rỗng. Đã gặp thật: 30 sản phẩm kèm URL đầy đủ + bắt LLM
    chép lại URL trong câu trả lời -> 8151 token, vượt giới hạn 8000 token/phút
    của Groq free tier (lỗi 413). LLM chỉ cần tên/giá/lượt bán để chọn; URL được
    code gắn lại sau (`_attach_product_details`) nên cũng không bao giờ bị LLM
    chép sai link. Không indent/khoảng trắng thừa vì cũng tốn token."""
    rows = []
    for i, p in enumerate(products, 1):
        row = {"id": i, "name": p.name, "price": int(p.price)}
        if p.sold is not None:
            row["sold"] = p.sold
        if p.rating is not None:
            row["rating"] = p.rating
        if getattr(p, "reviews", None):
            row["reviews"] = p.reviews
        if p.discount:
            row["discount"] = p.discount
        rows.append(row)
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))


def _attach_product_details(selected_products: dict, products: List[Product]) -> dict:
    """Gắn lại tên/giá/ảnh/link GỐC từ file JSON vào 5 sản phẩm LLM chọn (theo "id",
    dự phòng khớp theo tên nếu LLM quên id)."""
    by_id = {i: p for i, p in enumerate(products, 1)}
    by_name = {p.name: p for p in products}
    for item in selected_products.get("selected_products", []):
        product = None
        try:
            product = by_id.get(int(item.get("id")))
        except (TypeError, ValueError):
            pass
        if product is None and item.get("name"):
            product = match_by_name(item["name"], by_name)
        if product is None:
            print(f"[CẢNH BÁO] Không khớp được sản phẩm LLM chọn với dữ liệu gốc: {item.get('id')} {item.get('name')}")
            continue
        item["name"] = product.name
        item["price"] = product.price
        item["image_url"] = product.image_url
        item["affiliate_link"] = product.affiliate_link
    return selected_products


def _duplicate_pairs(selected: dict) -> list[tuple]:
    """Các cặp (id, id) trong 5 sản phẩm đã chọn là CÙNG 1 mẫu (cùng id / link, cùng mã model, tên gần giống)."""
    from .product_rank import is_duplicate
    items = selected.get("selected_products", [])
    out = []
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            same_link = a.get("affiliate_link") and a.get("affiliate_link") == b.get("affiliate_link")
            if str(a.get("id")) == str(b.get("id")) or same_link or is_duplicate(
                    a.get("name", ""), b.get("name", ""), _price_of(a) or 0, _price_of(b) or 0):
                out.append((a.get("id"), b.get("id")))
    return out


def _replace_duplicates(selected: dict, products: List[Product]) -> tuple[dict, list[str]]:
    """Lưới an toàn cuối: còn trùng thì CODE thay sản phẩm trùng bằng sản phẩm khác mẫu, đáng tin nhất, giá gần nhất."""
    from .product_rank import is_duplicate, trust_score
    items = selected.get("selected_products", [])
    kept, replaced = [], []
    for item in items:
        twin = next((k for k in kept if str(k.get("id")) == str(item.get("id")) or is_duplicate(
            k.get("name", ""), item.get("name", ""), _price_of(k) or 0, _price_of(item) or 0)), None)
        if twin is None:
            kept.append(item)
            continue
        price = _price_of(item) or 0
        pool = [(i, p) for i, p in enumerate(products, 1)
                if not any(is_duplicate(p.name, k.get("name", ""), p.price, _price_of(k) or 0)
                           or str(k.get("id")) == str(i) for k in kept)]
        if not pool:
            replaced.append(f"Bỏ “{item.get('name', '')[:60]}” (trùng, không còn sản phẩm khác mẫu để thay)")
            continue
        i, p = min(pool, key=lambda ip: (abs(ip[1].price - price) / max(price, 1) > 0.5,
                                          -trust_score(ip[1].sold, ip[1].rating, getattr(ip[1], "reviews", None)),
                                          abs(ip[1].price - price)))
        new = {**item, "id": i, "name": p.name, "price": p.price, "image_url": p.image_url,
               "affiliate_link": p.affiliate_link, "needs_met": [], "needs_missing": [],
               "reason_selected": f"Tool tự thay vì sản phẩm Groq chọn trùng mẫu với “{twin.get('name', '')[:40]}”.",
               "weakness": "", "nitpick": item.get("nitpick", "")}
        replaced.append(f"“{item.get('name', '')[:50]}” trùng “{twin.get('name', '')[:50]}” → thay bằng “{p.name[:50]}”")
        print("[CẢNH BÁO] " + replaced[-1])
        kept.append(new)
    return {**selected, "selected_products": kept}, replaced


_SCORE_WEIGHTS = {"tinh_nang": 0.4, "danh_gia": 0.25, "gia_tri": 0.2, "lien_quan": 0.15}


def _price_of(p: dict) -> Optional[float]:
    try:
        return float(p["price"])
    except (KeyError, TypeError, ValueError):
        return None


def _rerank_by_price_ladder(selected_products: dict) -> dict:
    """Xếp hạng theo BẬC THANG GIÁ: TOP 1 = đắt/cao cấp nhất, TOP 5 = rẻ/bình
    dân nhất — video đếm ngược Top 5 -> Top 1 nên người xem đi từ bình dân lên
    cao cấp. Không tin thứ tự LLM trả về (hay lẫn rank), xếp lại bằng giá; giá
    bằng nhau thì sản phẩm điểm cao hơn đứng trên. Điểm tổng được tính lại từ
    điểm thành phần (LLM hay cộng/nhân sai) và chỉ dùng để CẢNH BÁO khi bậc
    thang bị ngược (hạng trên đắt hơn mà tính năng lại kém hạng dưới)."""
    products = selected_products.get("selected_products", [])

    def total(p):
        sc = p.get("scores") or {}
        try:
            if all(k in sc for k in _SCORE_WEIGHTS):
                return round(sum(float(sc[k]) * w for k, w in _SCORE_WEIGHTS.items()), 2)
            return float(p["score"])
        except (KeyError, TypeError, ValueError):
            return None

    for p in products:
        p["score"] = total(p)
    ordered = sorted(
        enumerate(products),
        key=lambda ip: (
            _price_of(ip[1]) is None,
            -(_price_of(ip[1]) or 0),
            -(ip[1]["score"] or 0),
            ip[1].get("rank") or ip[0] + 1,
        ),
    )
    products = [p for _, p in ordered]
    for i, p in enumerate(products, start=1):
        p["rank"] = i

    for upper, lower in zip(products, products[1:]):
        f_up = (upper.get("scores") or {}).get("tinh_nang")
        f_low = (lower.get("scores") or {}).get("tinh_nang")
        try:
            if f_up is not None and f_low is not None and float(f_up) < float(f_low):
                print(
                    f"  [CẢNH BÁO] TOP {upper['rank']} đắt hơn nhưng điểm tính năng ({f_up}) thấp hơn "
                    f"TOP {lower['rank']} ({f_low}) — nên chạy lại bước chọn sản phẩm hoặc sửa tay "
                    "03_products_selected.json."
                )
        except (TypeError, ValueError):
            pass
    if products and products[0].get("needs_missing"):
        print(
            f"  [CẢNH BÁO] TOP 1 vẫn còn thiếu nhu cầu: {products[0]['needs_missing']} — Top 1 nên đáp "
            "ứng được hết tiêu chí mà Top 5 -> Top 2 còn thiếu."
        )

    selected_products["selected_products"] = products
    return selected_products


def _kw_key(text: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFD", str(text or "").lower().replace("đ", "d"))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", t)).strip()


def _drop_invented_keywords(filtered: dict, keywords: List[dict]) -> tuple:
    """Groq hay tự ghép từ khóa không có trong CSV (đã gặp thật: 'máy trợ thính không dây cao cấp',
    'máy trợ thính cho người điếc nhẹ'...). Chỉ giữ từ khóa CÓ THẬT trong dữ liệu gửi đi, trả về
    (filtered đã lọc, [từ khóa bị loại])."""
    source = {_kw_key(k["keyword"]): k["keyword"] for k in keywords}
    kept, invented = [], []
    for item in (filtered or {}).get("keywords", []):
        kw = str(item.get("keyword", "")).strip()
        if _kw_key(kw) in source:
            kept.append({**item, "keyword": source[_kw_key(kw)]})  # dùng đúng chữ trong CSV
        elif kw:
            invented.append(kw)
    if not kept:  # Groq bịa hết -> dùng tạm từ khóa đầu danh sách (đã xếp: chính trước, rồi volume)
        kept = [{"keyword": k["keyword"], "score": None, "reason": "Tự thêm vì Groq không chọn từ khóa hợp lệ nào"}
                for k in keywords[:10]]
    return {**(filtered or {}), "keywords": kept}, invented


def run_selection_stage(keywords: List[dict], products: List[Product], text_client,
                        report: Optional[dict] = None) -> SelectionResult:
    """text_client: client chỉ cần generate_json() — dùng GroqClient (free tier rộng).
    report: báo cáo đầu vào từ src/product_filter.prepare_selection_inputs (để lưu cùng run)."""
    report = dict(report or {})
    # Chỉ cho Groq chọn ngách từ những từ khóa CÓ sản phẩm để bán (thương hiệu / model / đặc điểm có trong tên sản
    # phẩm của file JSON). Vd CSV có "bếp từ panasonic viền đen p46" volume cao nhưng JSON không có sản phẩm Panasonic
    # nào -> không được thành ngách (từng ra ngách Panasonic mà 5 sản phẩm là Bosch / Sunhouse -> tiêu đề gây hiểu lầm).
    from .keyword_fit import product_matches
    names = [p.name for p in products]
    sellable = [k for k in keywords if product_matches(k.get("keyword", ""), names)]
    no_product = [k.get("keyword") for k in keywords if not product_matches(k.get("keyword", ""), names)]
    report["no_product_keywords"] = no_product
    niche_keywords = sellable if len(sellable) >= 5 else keywords
    prompt1 = render("01_ngach.md", KEYWORDS_LIST=_keywords_to_bullet_list(niche_keywords),
                     CURRENT_YEAR=date.today().year)
    niche = text_client.generate_json(prompt1)

    prompt2 = render(
        "02_loc_keyword.md",
        NICHE_JSON=json.dumps(niche, ensure_ascii=False),
        KEYWORDS_LIST=_keywords_to_bullet_list(niche_keywords),
    )
    filtered_keywords, invented = _drop_invented_keywords(text_client.generate_json(prompt2), keywords)
    kept, seen, no_prod_removed = [], set(), []
    for k in filtered_keywords.get("keywords", []):
        kw = (k.get("keyword") or "").strip()
        if not kw or kw.lower() in seen:
            continue                                    # Groq hay lặp 1 từ khóa nhiều lần
        seen.add(kw.lower())
        if len(sellable) >= 5 and not product_matches(kw, names):
            no_prod_removed.append(kw)
            continue
        kept.append(k)
    filtered_keywords = {**filtered_keywords, "keywords": kept}
    report["groq_keywords"] = {"kept": kept, "invented_removed": invented, "no_product_removed": no_prod_removed}

    prompt3 = render(
        "03_loc_san_pham.md",
        NICHE_JSON=json.dumps(niche, ensure_ascii=False),
        FILTERED_KEYWORDS_JSON=json.dumps(filtered_keywords, ensure_ascii=False),
        PRODUCTS_JSON=_products_to_json(products),
    )
    picked = _attach_product_details(text_client.generate_json(prompt3), products)
    dups = _duplicate_pairs(picked)
    if dups:
        # Groq chọn trùng mẫu -> hỏi lại 1 lần, nói rõ cặp nào trùng
        note = "\n\nLẦN TRƯỚC BẠN CHỌN TRÙNG (cùng 1 mẫu / cùng 1 sản phẩm) — TUYỆT ĐỐI không lặp lại: " + "; ".join(
            f"id {a} và id {b}" for a, b in dups) + ". Chọn lại 5 sản phẩm KHÁC MẪU nhau."
        print(f"[CẢNH BÁO] Groq chọn trùng mẫu ({len(dups)} cặp) — hỏi lại.")
        picked = _attach_product_details(text_client.generate_json(prompt3 + note), products)
    picked, replaced = _replace_duplicates(picked, products)
    report["duplicates_replaced"] = replaced
    selected_products = _rerank_by_price_ladder(picked)
    from .product_filter import check_selected  # import muộn, tránh vòng import

    report["selected_check"] = check_selected(
        selected_products, [k["keyword"] for k in keywords], [k["keyword"] for k in filtered_keywords["keywords"]])
    report["created_at"] = date.today().isoformat()

    return SelectionResult(niche=niche, filtered_keywords=filtered_keywords, selected_products=selected_products,
                           report=report)


def save_selection_result(result: SelectionResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "01_niche.json").write_text(
        json.dumps(result.niche, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "02_keywords_filtered.json").write_text(
        json.dumps(result.filtered_keywords, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "03_products_selected.json").write_text(
        json.dumps(result.selected_products, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if result.report:
        (out_dir / SELECTION_REPORT_FILE).write_text(
            json.dumps(result.report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )


def _wrap_audio(text: str) -> str:
    return f"[Audio - Lời đọc]\n{text.strip()}\n[/Audio - Lời đọc]"


_PROGRESS_FILENAME = "04_script_progress.json"


def _progress_path(media_dir: Optional[Path]) -> Optional[Path]:
    return (media_dir / _PROGRESS_FILENAME) if media_dir else None


def _load_progress(media_dir: Optional[Path]) -> dict:
    path = _progress_path(media_dir)
    if path and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"hook": None, "segments": {}, "cta": None, "metadata": None}


def _save_progress(media_dir: Optional[Path], progress: dict) -> None:
    path = _progress_path(media_dir)
    if path:
        path.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")


def _clear_progress(media_dir: Optional[Path]) -> None:
    path = _progress_path(media_dir)
    if path and path.exists():
        path.unlink()


def _build_media_instruction(images: list, video) -> str:
    """Ghép hướng dẫn về ảnh/video + yêu cầu định dạng khối [[IMG:n]] khi có
    ảnh — để mỗi câu lời bình gắn CHÍNH XÁC với 1 ảnh cụ thể (video xem lẫn
    tổng quan, không tách khối vì không thể đồng bộ theo khung hình)."""
    if not images:
        base = (
            "Chưa có ảnh chi tiết riêng cho sản phẩm này — chỉ dựa vào tên/giá/lý do được chọn ở "
            "trên để viết 3-5 câu liền mạch, không bịa chi tiết hình ảnh không có căn cứ."
        )
        if video:
            base += " Có đính kèm video sản phẩm, xem tổng quan để lấy thêm ngữ cảnh khi viết."
        return base

    n = len(images)
    lines = [
        f"Bạn được đính kèm {n} ảnh chụp thực tế theo ĐÚNG thứ tự dưới đây"
        + (" (kèm cả 1 video sản phẩm để xem thêm ngữ cảnh)" if video else "")
        + f". Với MỖI ảnh, viết 1-3 câu MÔ TẢ ĐÚNG những gì nhìn thấy trong CHÍNH ảnh đó (không mô "
        "tả nhầm sang ảnh khác), lồng ghép tự nhiên vào lời bình như đang vừa xem vừa kể.",
        "",
        f"Định dạng BẮT BUỘC — đúng {n} khối theo thứ tự, mỗi khối bắt đầu bằng đúng 1 dòng đánh dấu "
        "sau (không thêm bớt số khối, không viết gì khác trên dòng đánh dấu):",
    ]
    for i in range(1, n + 1):
        lines.append(f"[[IMG:{i}]]")
        lines.append(f"<lời bình cho ảnh {i}>")
    return "\n".join(lines)


def _validate_or_strip_image_blocks(response_text: str, expected_count: int, product_name: str) -> str:
    """Gemini đôi khi không tuân đúng định dạng [[IMG:n]] — nếu số khối không
    khớp, bỏ đồng bộ ảnh cho sản phẩm này (video assembly sẽ luân chuyển ảnh
    như bình thường) thay vì để marker rác lẫn vào lời bình."""
    if expected_count == 0:
        return _IMG_MARKER_RE.sub("", response_text).strip()
    found = [int(m.group(1)) for m in _IMG_MARKER_RE.finditer(response_text)]
    if found == list(range(1, expected_count + 1)):
        return response_text.strip()
    print(
        f"  [CẢNH BÁO] '{product_name[:40]}...': LLM không đánh dấu đúng {expected_count} khối ảnh "
        f"(tìm thấy {found or 'không có'}) — bỏ đồng bộ ảnh-theo-câu, dùng luân chuyển ảnh như cũ."
    )
    return _IMG_MARKER_RE.sub("", response_text).strip()


def _generate_fast(client, prompt: str, images, video) -> str:
    """generate_text với chế độ viết nhanh (giảm thinking, model dự phòng khi 503) nếu client hỗ trợ."""
    try:
        if "fast" in inspect.signature(client.generate_text).parameters:
            return client.generate_text(prompt, images=images, video=video, fast=True)
    except (TypeError, ValueError):
        pass
    return client.generate_text(prompt, images=images, video=video)


def _ranking_summary(products: List[dict]) -> str:
    """Bảng xếp hạng rút gọn cho prompt lời bình — để Gemini biết sản phẩm
    đang viết đứng trên/dưới ai và vì sao, tránh khen hạng thấp như Top 1."""
    rows = [
        {k: p.get(k) for k in ("rank", "tier", "name", "price", "needs_met", "needs_missing", "reason_selected",
                               "weakness") if p.get(k) is not None}
        for p in sorted(products, key=lambda x: x.get("rank") or 99)
    ]
    return json.dumps(rows, ensure_ascii=False, indent=2)


def _gen_hook(niche: dict, selected_products: dict, text_client, seo_ctx: SeoContext, alloc,
              claims_confirmed: bool, length: Optional[dict] = None) -> str:
    prompt = render(
        "04a_hook.md",
        NICHE_JSON=json.dumps(niche, ensure_ascii=False),
        SELECTED_PRODUCTS_JSON=json.dumps(selected_products, ensure_ascii=False),
        TITLE_BLOCK=hook_title_block(seo_ctx, claims_confirmed),
        SEO_BLOCK=hook_seo_block(seo_ctx, alloc),
    ) + length_rule(length, "hook")
    return text_client.generate_text(prompt).strip()


def _gen_cta(niche: dict, top_product: dict, text_client, seo_ctx: SeoContext, alloc,
             length: Optional[dict] = None) -> str:
    prompt = render(
        "04c_cta.md",
        NICHE_JSON=json.dumps(niche, ensure_ascii=False),
        TOP_PRODUCT_JSON=json.dumps(top_product, ensure_ascii=False),
        SEO_BLOCK=cta_seo_block(seo_ctx, alloc),
    ) + length_rule(length, "cta")
    return text_client.generate_text(prompt).strip()


def _gen_metadata(niche: dict, selected_products: dict, hook_text: str, text_client, seo_ctx: SeoContext,
                  alloc) -> dict:
    title_block, seo_block = metadata_blocks(seo_ctx, alloc)
    prompt = render(
        "05_metadata.md",
        CURRENT_YEAR=date.today().year,
        NICHE_JSON=json.dumps(niche, ensure_ascii=False),
        SELECTED_PRODUCTS_JSON=json.dumps(selected_products, ensure_ascii=False),
        SCRIPT_HOOK_EXCERPT=hook_text[:400],
        TITLE_BLOCK=title_block,
        SEO_BLOCK=seo_block,
    )
    return text_client.generate_json(prompt)


def run_script_stage(
    niche: dict,
    selected_products: dict,
    vision_client,
    text_client,
    media_dir: Optional[Path] = None,
    seo_ctx: Optional[SeoContext] = None,
    claims_confirmed: bool = False,
    length: Optional[dict] = None,
) -> ScriptResult:
    """vision_client: GeminiClient (bắt buộc hỗ trợ images/video) — dùng riêng cho
    từng đoạn sản phẩm. text_client: GroqClient — dùng cho hook/CTA/metadata."""
    products = selected_products.get("selected_products", [])
    if not products:
        raise ValueError("selected_products rỗng — không có sản phẩm nào để viết script.")

    progress = _load_progress(media_dir)
    # Đổi độ dài mục tiêu -> phần đã viết dở theo độ dài cũ không dùng lại (viết lại cho đúng độ dài mới).
    if progress and progress.get("length_key") != length_key(length):
        progress = {"hook": None, "segments": {}, "cta": None, "metadata": None,
                    "flow_prompts": progress.get("flow_prompts", {})}
    progress["length_key"] = length_key(length)
    # Từ khóa SEO + tiêu đề đã chọn ở tab 🔑 (09_seo_keywords.json) — không có thì block rỗng,
    # prompt giữ nguyên như trước (xem src/seo_context.py).
    if seo_ctx is None:
        seo_ctx = load_seo_context(media_dir)
    alloc = allocate_keywords(seo_ctx, products)

    if progress.get("hook"):
        print("  (dùng lại hook đã sinh từ lần chạy trước — bỏ qua, không tốn quota)")
        hook_text = progress["hook"]
    else:
        hook_text = _gen_hook(niche, selected_products, text_client, seo_ctx, alloc, claims_confirmed, length)
        progress["hook"] = hook_text
        _save_progress(media_dir, progress)

    segment_blocks = []
    flow_prompts = dict(progress.get("flow_prompts", {}))
    # Đếm ngược TOP 5 -> TOP 1 (tiết lộ sản phẩm tốt nhất sau cùng để giữ chân
    # người xem) — đúng thứ tự phổ biến ở các video Top 5 affiliate, không
    # phải rank tăng dần như trước.
    products_countdown = sorted(products, key=lambda x: -(x.get("rank") or 0))
    tasks = []   # (sản phẩm, prompt, ảnh, video) cần gọi Gemini
    for p in products_countdown:
        rank_key = str(p.get("rank"))
        images, video = find_product_media(p, media_dir) if media_dir else ([], None)

        if not images and not video and rank_key not in flow_prompts:
            prompt_flow = render(
                "06_flow_prompt.md",
                PRODUCT_JSON=json.dumps(p, ensure_ascii=False),
                NICHE_JSON=json.dumps(niche, ensure_ascii=False),
            )
            flow_prompts[rank_key] = text_client.generate_text(prompt_flow).strip()
            progress["flow_prompts"] = flow_prompts
            _save_progress(media_dir, progress)

        if progress.get("segments", {}).get(rank_key):
            print(f"  - Rank {p.get('rank')}: dùng lại lời bình đã sinh từ lần chạy trước — bỏ qua")
            continue
        # Video chỉ dùng lúc dựng phim — viết script dùng ảnh + file .md (khỏi chờ upload/xử lý video 1–2 phút).
        if images and os.environ.get("SCRIPT_USE_VIDEO", "0") != "1":
            video = None
        image_instruction = _build_media_instruction(images, video)
        prompt_seg = render(
            "04b_product_segment.md",
            NICHE_JSON=json.dumps(niche, ensure_ascii=False),
            PRODUCT_JSON=json.dumps(p, ensure_ascii=False),
            RANKING_JSON=_ranking_summary(products),
            RANK=p.get("rank", "?"),
            IMAGE_INSTRUCTION=image_instruction,
            SEO_BLOCK=segment_seo_block(seo_ctx, alloc, p.get("rank")),
            # Thông số / rating / lượt bán / mô tả từ file .md đã upload ở tab 🖼️ (không có -> rỗng)
            PRODUCT_INFO=product_info_block(p, media_dir),
        ) + length_rule(length, "product")
        if images or video:
            media_desc = [f"{len(images)} ảnh"] if images else []
            if video:
                media_desc.append(f"video '{video.name}'")
            print(f"  - Rank {p.get('rank')} ({p['name'][:40]}...): dùng {' + '.join(media_desc)} thật")
        tasks.append((p, prompt_seg, images, video))

    # Lời bình 5 sản phẩm độc lập nhau -> gọi Gemini SONG SONG (SCRIPT_PARALLEL, mặc định 3); xong phần nào lưu ngay.
    if tasks:
        lock = threading.Lock()

        def _write(task):
            p, prompt_seg, images, video = task
            raw = _generate_fast(vision_client, prompt_seg, images, video).strip()
            return p, _validate_or_strip_image_blocks(raw, len(images), p["name"])

        parallel = max(1, min(int(os.environ.get("SCRIPT_PARALLEL", "3") or 3), len(tasks)))
        print(f"  Viết {len(tasks)} đoạn sản phẩm song song ({parallel} cùng lúc)...")
        errors_ = []
        with ThreadPoolExecutor(max_workers=parallel) as ex:
            for fut in as_completed([ex.submit(_write, t) for t in tasks]):
                try:
                    p, seg_text = fut.result()
                except Exception as e:   # đoạn khác vẫn chạy & được lưu — lần sau chỉ viết lại đoạn lỗi
                    errors_.append(e)
                    continue
                with lock:
                    progress.setdefault("segments", {})[str(p.get("rank"))] = seg_text
                    _save_progress(media_dir, progress)
        if errors_:
            raise errors_[0]

    for p in products_countdown:
        seg_text = progress["segments"][str(p.get("rank"))]
        segment_blocks.append(f"### SẢN PHẨM: {p['name']}\n{_wrap_audio(seg_text)}")

    top_product = next((p for p in products if p.get("rank") == 1), products[0])

    if progress.get("cta"):
        print("  (dùng lại CTA đã sinh từ lần chạy trước — bỏ qua, không tốn quota)")
        cta_text = progress["cta"]
    else:
        cta_text = _gen_cta(niche, top_product, text_client, seo_ctx, alloc, length)
        progress["cta"] = cta_text
        _save_progress(media_dir, progress)

    script = "\n\n".join([_wrap_audio(hook_text), *segment_blocks, _wrap_audio(cta_text)])

    if progress.get("metadata"):
        print("  (dùng lại metadata đã sinh từ lần chạy trước — bỏ qua, không tốn quota)")
        metadata = progress["metadata"]
    else:
        metadata = _gen_metadata(niche, selected_products, hook_text, text_client, seo_ctx, alloc)
        progress["metadata"] = metadata
        _save_progress(media_dir, progress)

    _clear_progress(media_dir)
    return ScriptResult(script=script, metadata=metadata, flow_prompts=flow_prompts)


def save_script_result(result: ScriptResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "04_script.txt").write_text(result.script, encoding="utf-8")
    # Tiêu đề đã chọn ở tab "Từ khóa & Tiêu đề SEO" (nếu có) được ưu tiên hơn tiêu đề AI tự đặt.
    metadata = apply_chosen_title(result.metadata, out_dir)
    (out_dir / "05_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if result.flow_prompts:
        products_by_rank = {}  # điền tên sản phẩm cho dễ đọc nếu có sẵn 03_products_selected.json
        products_path = out_dir / "03_products_selected.json"
        if products_path.exists():
            data = json.loads(products_path.read_text(encoding="utf-8"))
            products_by_rank = {str(p["rank"]): p["name"] for p in data.get("selected_products", [])}

        lines = [
            "# Prompt Google Flow — cho sản phẩm chưa có ảnh/video thật\n",
            "Dán từng prompt bên dưới vào https://flow.google để tạo ảnh/video minh họa, rồi thả kết "
            "quả vào đúng thư mục `product_media/<rank>_<tên-sp>/main_images/` tương ứng và chạy lại "
            "`--script-only` để script bám theo ảnh/video đó.\n",
        ]
        for rank_key, prompt_text in sorted(result.flow_prompts.items(), key=lambda kv: int(kv[0])):
            name = products_by_rank.get(rank_key, f"Rank {rank_key}")
            lines.append(f"## TOP {rank_key}: {name}\n")
            lines.append(f"```\n{prompt_text}\n```\n")
        (out_dir / "07_flow_prompts.md").write_text("\n".join(lines), encoding="utf-8")


def _replace_hook_cta(script: str, hook_text: str, cta_text: str) -> str:
    """Thay nội dung đoạn hook (trước sản phẩm đầu tiên) và CTA (sau sản phẩm cuối) trong
    04_script.txt — GIỮ NGUYÊN các đoạn sản phẩm (kể cả phần bạn đã sửa tay)."""
    from .script_parser import _SEGMENT_RE

    matches = list(_SEGMENT_RE.finditer(script))
    product_idx = [i for i, m in enumerate(matches) if m.group("product")]
    if not product_idx:
        raise ValueError("04_script.txt không có đoạn sản phẩm nào (### SẢN PHẨM: …) — viết lại cả script.")
    first_p, last_p = product_idx[0], product_idx[-1]
    hook_m = next((m for i, m in enumerate(matches) if i < first_p), None)
    cta_m = next((m for i, m in enumerate(matches) if i > last_p), None)
    edits = []  # (start, end, text) — thay từ cuối lên để không lệch vị trí
    if cta_m:
        edits.append((cta_m.start("body"), cta_m.end("body"), cta_text.strip()))
    if hook_m:
        edits.append((hook_m.start("body"), hook_m.end("body"), hook_text.strip()))
    out = script
    for start, end, text in sorted(edits, reverse=True):
        out = out[:start] + text + out[end:]
    if not cta_m:
        out = out.rstrip() + "\n\n" + _wrap_audio(cta_text)
    if not hook_m:
        out = _wrap_audio(hook_text) + "\n\n" + out.lstrip()
    return out


def regenerate_hook_cta(run_dir: Path, text_client, claims_confirmed: bool = False,
                        length: Optional[dict] = None) -> dict:
    """Viết lại CHỈ hook + CTA + metadata theo tiêu đề / từ khóa SEO hiện tại (Groq, rẻ) —
    KHÔNG gọi lại Gemini viết lời bình từng sản phẩm. Dùng khi đổi tiêu đề sau khi đã có script."""
    run_dir = Path(run_dir)
    niche = json.loads((run_dir / "01_niche.json").read_text(encoding="utf-8"))
    selected = json.loads((run_dir / "03_products_selected.json").read_text(encoding="utf-8"))
    script_path = run_dir / "04_script.txt"
    script = script_path.read_text(encoding="utf-8")
    products = selected.get("selected_products", [])
    seo_ctx = load_seo_context(run_dir)
    alloc = allocate_keywords(seo_ctx, products)
    top_product = next((p for p in products if p.get("rank") == 1), products[0])

    if length is None:
        from .script_length import budget, load_sfx, load_target, load_tones
        length = budget(*load_target(run_dir), n_products=len(products), sfx=load_sfx(run_dir),
                        tones=load_tones(run_dir))
    hook_text = _gen_hook(niche, selected, text_client, seo_ctx, alloc, claims_confirmed, length)
    cta_text = _gen_cta(niche, top_product, text_client, seo_ctx, alloc, length)
    new_script = _replace_hook_cta(script, hook_text, cta_text)
    metadata = apply_chosen_title(_gen_metadata(niche, selected, hook_text, text_client, seo_ctx, alloc), run_dir)

    script_path.write_text(new_script, encoding="utf-8")
    (run_dir / "05_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"hook": hook_text, "cta": cta_text, "metadata": metadata,
            "audio_stale": (run_dir / "06_media_manifest.json").exists()}
