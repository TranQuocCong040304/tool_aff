"""Ghép 08_youtube_description.txt — CHỈ chạy được sau khi có audio (Bước 2,
xem src/media_step.py) vì cần thời lượng THẬT từng đoạn để tính mốc thời gian
(timestamp), thứ mà lúc viết script (05_metadata.json, chỉ có phần sáng tạo:
tiêu đề/hook/tags) chưa thể biết được.

Tên sản phẩm trong link/timestamp LUÔN lấy nguyên văn từ 03_products_selected.json
(dữ liệu Shopee thật) — KHÔNG để LLM tự đặt biệt danh/tagline riêng cho từng
sản phẩm, vì đã gặp thật: LLM từng gán nhầm tính năng của sản phẩm A cho
timestamp của sản phẩm B khi viết tự do. Tagline hiển thị trong timestamp
trích thẳng từ `reason_selected` (câu lý do chọn ĐÚNG sản phẩm đó, đã dùng ở
Bước 1) thay vì để LLM viết lại.

Định dạng phỏng theo các video "Top 5 review" phổ biến trên YouTube: đoạn mở
đầu -> danh sách link mua hàng -> mốc thời gian -> nhắc like/subscribe -> hashtag.
"""
import json
import re
from pathlib import Path
from typing import Optional

_HASHTAG_STRIP_RE = re.compile(r"[^\w]+", re.UNICODE)


def _hashtag(tag: str) -> str:
    return "#" + _HASHTAG_STRIP_RE.sub("", tag)


def _fmt_time(seconds: float) -> str:
    total = int(seconds)
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _short_reason(reason: str, max_len: int = 55) -> str:
    """Cắt reason_selected (đã dùng đúng cho sản phẩm này ở Bước 1) thành 1
    cụm ngắn cho dòng timestamp — cắt tại dấu chấm/phẩy đầu tiên trong giới
    hạn để không cụt giữa câu, thay vì để LLM viết lại (dễ lẫn sản phẩm)."""
    reason = reason.strip()
    if len(reason) <= max_len:
        return reason
    cut = reason[:max_len]
    for sep in (". ", ", "):
        idx = cut.rfind(sep)
        if idx > 15:
            return cut[:idx]
    last_space = cut.rfind(" ")
    return (cut[:last_space] if last_space > 15 else cut) + "..."


def _segment_duration(seg: dict) -> float:
    """Thời lượng thật của 1 đoạn = mốc kết thúc của beat cuối cùng (các beat
    đã được chuẩn hoá liền mạch từ 0 -> hết trong src/tts.py)."""
    beats = seg.get("beats") or []
    return max((b["end"] for b in beats), default=0.0)


_SEP = "───────────────────────────────"
_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
_COPYRIGHT = ("Một số nội dung sử dụng trong video được trích từ Shopee, nếu bạn có bất kỳ vấn đề bản quyền nào, "
              "hãy liên hệ với tôi qua email.")


def _chapter_time(seconds: float) -> str:
    """Định dạng chương YouTube: 00:00 (mm:ss, có số 0 đầu) — chương đầu BẮT BUỘC 00:00."""
    total = int(seconds)
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _short_name(name: str, max_len: int = 48, prefix=None) -> str:
    """Tên gọn: bỏ phần đầu chung với các sản phẩm khác (vd "Máy Đo Huyết Áp Bắp Tay"), lấy phần trước | hoặc ,
    đầu tiên. `prefix` = danh sách tên của cả 5 sản phẩm."""
    full = name
    if prefix:
        w = name.split()
        best = 0
        for k in range(1, len(w)):
            if sum(1 for o in prefix if [x.lower() for x in o.split()[:k]] == [x.lower() for x in w[:k]]) >= 3:
                best = k
        if best >= 2:
            name = " ".join(w[best:])
    head = re.split(r"\s[|–-]\s|\||,", name)[0].strip()
    if len(head) < 12:
        head = re.split(r"\s[|–-]\s|\||,", full)[0].strip()
    return head if len(head) <= max_len else head[: max_len - 1].rsplit(" ", 1)[0] + "…"


def _ascii_tag(text: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFD", text.lower()).replace("đ", "d")
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "", t)


def _latest_script_doc(run_dir: Path) -> dict:
    """Kịch bản theo mẫu mới nhất của run (tiêu đề SEO, tên kênh, ưu đãi, phần MÔ TẢ YOUTUBE AI viết)."""
    docs = []
    for p in (run_dir / "scripts").glob("*.json") if (run_dir / "scripts").exists() else []:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        docs.append((d.get("finished_at") or d.get("created_at") or "", d))
    return max(docs, key=lambda x: x[0])[1] if docs else {}


def _keywords(run_dir: Path, title: str = "") -> dict:
    try:
        from .script_templates import load_keyword_rows
        rows, _ = load_keyword_rows(run_dir)
    except Exception:
        rows = []
    by_role = {}
    for r in rows:
        by_role.setdefault(r.get("role") or "", []).append(r["keyword"])
    main = (by_role.get("Chính") or [""])[0]
    if title:   # từ khóa chính của VIDEO = cụm có trong tiêu đề (như lúc viết kịch bản)
        try:
            from .script_templates import title_plan
            plan = title_plan(title, rows, False)
            if plan.get("main"):
                main = plan["main"][0]["keyword"]
        except Exception:
            pass
    return {"main": main, "traffic": by_role.get("Kéo traffic", []),
            "secondary": by_role.get("Phụ", []), "longtail": by_role.get("Đuôi dài", [])}


def _product_facts(p: dict, run_dir: Path) -> str:
    try:
        from .product_info import load_info
        info = load_info(p, run_dir) or {}
    except Exception:
        info = {}
    bits = [f"Top {p.get('rank')}: {p['name']}"]
    if info.get("brand") and info["brand"] != "N/A":
        bits.append(f"thương hiệu {info['brand']}")
    if info.get("rating"):
        bits.append(f"{info['rating']}/5 sao" + (f" từ {int(info['reviews'])} đánh giá" if info.get("reviews") else ""))
    if info.get("sold"):
        bits.append(f"đã bán {int(info['sold'])}")
    if info.get("discount"):
        bits.append(f"đang giảm {info['discount']}")
    for k, label in (("reason_selected", "lý do xếp hạng"), ("nitpick", "điểm chê")):
        if p.get(k):
            bits.append(f"{label}: {p[k]}")
    if p.get("needs_met"):
        bits.append("đáp ứng: " + ", ".join(p["needs_met"]))
    return " · ".join(bits)


def _ai_parts(text_client, ctx: dict) -> dict:
    """Groq viết phần chữ (mô tả mở đầu, nhãn từng Top, tóm tắt, tên chương, đoạn SEO, hashtag) — CHỈ từ dữ liệu."""
    if text_client is None:
        return {}
    prompt = (
        "Bạn viết MÔ TẢ VIDEO YOUTUBE cho kênh review Top 5 sản phẩm Shopee (affiliate). Văn phong tự nhiên, cuốn hút, "
        "chuẩn SEO. CHỈ dùng thông tin trong DỮ LIỆU — không bịa thông số, không bịa voucher / flash sale / 'số lượng có "
        "hạn' (chỉ nhắc ưu đãi khi mục ƯU ĐÃI có ghi), không nói đã test / trải nghiệm thực tế nếu ĐÃ TEST = không, "
        "sản phẩm sức khỏe không hứa chữa bệnh.\n\n"
        f"TIÊU ĐỀ VIDEO: {ctx['title']}\nTÊN KÊNH: {ctx['channel']}\nTỪ KHÓA CHÍNH: {ctx['kw']['main']}\n"
        f"TỪ KHÓA KÉO TRAFFIC: {', '.join(ctx['kw']['traffic'][:12])}\n"
        f"TỪ KHÓA PHỤ / ĐUÔI DÀI: {', '.join((ctx['kw']['secondary'] + ctx['kw']['longtail'])[:20])}\n"
        f"ĐÃ TEST: {'có' if ctx['tested'] else 'không'}\nƯU ĐÃI: {ctx['offers'] or '(không có)'}\n"
        "DỮ LIỆU 5 SẢN PHẨM:\n" + "\n".join(ctx["facts"]) + "\n\n"
        "Trả về JSON đúng dạng:\n"
        '{"intro": "2–3 câu: câu 1 là câu hỏi nỗi đau chứa NGUYÊN VĂN từ khóa chính; câu 2 \\"Trong video này, '
        '“<TÊN KÊNH>” sẽ review chi tiết Top 5 …\\"; câu 3 rủ xem để chọn chân ái",\n'
        ' "products": [{"rank": 1, "short_name": "tên gọn ≤ 40 ký tự (thương hiệu + model)", '
        '"tagline": "≤ 9 chữ, dạng \\"Trùm cuối - điểm mạnh nhất\\" / \\"Ngon bổ rẻ - …\\""}, … đủ 5 Top],\n'
        ' "summary": "3–4 câu tóm tắt nội dung video: so sánh gì, giúp người xem quyết định thế nào",\n'
        ' "chapters": {"hook": "tên chương mở đầu ≤ 60 ký tự", "5": "…", "4": "…", "3": "…", "2": "…", "1": "…", '
        '"cta": "tên chương kết"} (mỗi chương ngắn, có từ khóa / câu hỏi phù hợp, Top x có tên gọn sản phẩm),\n'
        ' "seo_paragraph": "4–5 câu lồng TỰ NHIÊN 6–10 từ khóa kéo traffic / đuôi dài, nhắc tên kênh 1 lần",\n'
        ' "hashtags": ["10–12 hashtag viết liền không dấu, chữ thường, không có dấu #"]}'
    )
    try:
        data = text_client.generate_json(prompt) or {}
    except Exception as e:
        print(f"  [Mô tả YouTube] Groq lỗi ({type(e).__name__}: {e}) — dùng mẫu mô tả tự ghép.")
        return {}
    return data if isinstance(data, dict) else {}


def build_youtube_description(run_dir: Path, text_client=None) -> Optional[str]:
    """Mô tả YouTube chuẩn: mô tả mở đầu → link 5 sản phẩm (Top 1 → Top 5) → tóm tắt → chương (mốc thời gian
    THẬT từ audio) → hashtag → đoạn SEO → công khai affiliate + bản quyền. Phần chữ do Groq viết từ dữ liệu
    thật (có `text_client`), không có thì tự ghép theo mẫu. None nếu chưa có audio / chưa chọn sản phẩm."""
    run_dir = Path(run_dir)
    manifest_path = run_dir / "06_media_manifest.json"
    products_path = run_dir / "03_products_selected.json"
    if not manifest_path.exists() or not products_path.exists():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    metadata_path = run_dir / "05_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
    products = json.loads(products_path.read_text(encoding="utf-8")).get("selected_products", [])
    by_name = {p["name"]: p for p in products}
    doc = _latest_script_doc(run_dir)
    title = doc.get("title") or metadata.get("title") or ""
    kw = _keywords(run_dir, title)
    channel = (doc.get("channel") or "").strip() or "kênh của mình"
    offers = (doc.get("offers") or "").strip()
    ctx = {"title": title, "channel": channel, "offers": offers, "tested": bool(doc.get("tested")), "kw": kw,
           "facts": [_product_facts(p, run_dir) for p in sorted(products, key=lambda x: x.get("rank") or 9)]}
    ai = _ai_parts(text_client, ctx)
    ai_products = {int(x.get("rank")): x for x in ai.get("products") or [] if str(x.get("rank", "")).isdigit()}
    chapters_ai = {str(k): v for k, v in (ai.get("chapters") or {}).items()}
    main_kw = kw["main"] or title
    prefix = [p["name"] for p in products]
    chan_txt = f"“{channel}”" if doc.get("channel") else "kênh mình"

    # 1) Mô tả mở đầu
    intro = (ai.get("intro") or "").strip() or (
        f"Bạn đang tìm {main_kw.lower()} phù hợp với nhu cầu và túi tiền của mình? Trong video này, {chan_txt} sẽ "
        f"review chi tiết Top {len(products)} sản phẩm đáng mua nhất hiện nay, so sánh ưu – nhược điểm dựa trên "
        "thông số nhà sản xuất và đánh giá của người mua. Cùng xem ngay để chọn ra chân ái cho mình nhé!")

    # 2) Link sản phẩm Top 1 → Top 5
    head = "🚨 SĂN ƯU ĐÃI - LINK ĐỂ Ở ĐÂY NHA ANH EM! 🚨" if offers else "🚨 LINK SẢN PHẨM TRONG VIDEO - XEM GIÁ HÔM NAY 🚨"
    link_lines = [head]
    for p in sorted(products, key=lambda x: x.get("rank") or 9):
        if not p.get("affiliate_link"):
            continue
        r = p.get("rank")
        a = ai_products.get(r, {})
        name = (a.get("short_name") or "").strip() or _short_name(p["name"], prefix=prefix)
        tag = (a.get("tagline") or "").strip() or _short_reason(p.get("reason_selected") or "", 45)
        link_lines.append(f"{_MEDALS.get(r, '🔹')} Top {r}" + (f" ({tag})" if tag else "") + f": {name}\n➔ {p['affiliate_link']}")
    links = "\n\n".join(link_lines)
    if offers:
        links += f"\n\n({offers})"

    # 3) Tóm tắt
    summary = (ai.get("summary") or "").strip() or (
        f"Tổng hợp ưu nhược điểm của {len(products)} mẫu {main_kw.lower()} từ phân khúc giá rẻ đến cao cấp, dựa trên "
        "thông số nhà sản xuất và đánh giá thật của người mua. Video so sánh tính năng nổi bật, điểm trừ của từng mẫu "
        "và gợi ý nên chọn mẫu nào theo ngân sách, nhu cầu — giúp bạn chốt đơn chuẩn xác, không lo mua hớ.")

    # 4) Chương — mốc thời gian THẬT từ audio
    ts_lines, t = [], 0.0
    hook_done = cta_done = False
    for seg in manifest:
        dur = _segment_duration(seg)
        label = None
        if seg.get("kind") == "hook" and not hook_done:
            label, hook_done = chapters_ai.get("hook") or metadata.get("hook_label") or "Mở đầu", True
        elif seg.get("kind") == "cta" and not cta_done:
            label, cta_done = chapters_ai.get("cta") or metadata.get("outro_label") or "Tổng kết & nên chọn mẫu nào", True
        elif seg.get("kind") == "product":
            p = by_name.get(seg.get("product_name") or "")
            if p:
                r = p.get("rank")
                label = chapters_ai.get(str(r)) or f"Top {r}: {(ai_products.get(r, {}).get('short_name') or _short_name(p['name'], prefix=prefix))}"
                if not label.lower().startswith("top"):
                    label = f"Top {r}: {label}"
        if label:
            ts_lines.append(f"{_chapter_time(t)} - {label}")
        t += dur

    # 5) Hashtag (≤ 12 — quá 15 YouTube bỏ qua hết)
    raw_tags = [h for h in (ai.get("hashtags") or []) if isinstance(h, str)]
    if not raw_tags:
        raw_tags = [kw["main"]] + kw["traffic"][:6] + ([channel] if doc.get("channel") else []) + [
            "shopeeaffiliate", "reviewshopee"]
    seen, tags = set(), []
    for h in raw_tags:
        tg = _ascii_tag(h)
        if tg and tg not in seen and len(tg) <= 40:
            seen.add(tg)
            tags.append("#" + tg)
    hashtags = " ".join(tags[:12])

    # 6) Đoạn SEO
    seo = (ai.get("seo_paragraph") or "").strip() or (
        f"Video tổng hợp đánh giá {main_kw.lower()} đáng mua nhất hiện nay. "
        + (f"Nếu bạn đang tìm {', '.join(kw['traffic'][:4])}, đây là video dành cho bạn. " if kw["traffic"] else "")
        + (f"Ngoài ra, video cũng gợi ý {', '.join(kw['longtail'][:4])}." if kw["longtail"] else ""))

    parts = [intro, links, _SEP, "📌 TÓM TẮT NỘI DUNG VIDEO:\n" + summary]
    if ts_lines:
        parts += [_SEP, "📌 TIMELINE VIDEO & CHAPTERS:\n" + "\n".join(ts_lines)]
    if hashtags:
        parts += [_SEP, "🔎 HASHTAGS:\n" + hashtags]
    parts += [_SEP, "📝 THÔNG TIN TÌM KIẾM CHI TIẾT (SEO DEEP-INDEX):\n" + seo,
              "⚠️ Các link trong mô tả là link tiếp thị liên kết (affiliate): bạn mua qua link thì kênh nhận một "
              "khoản hoa hồng nhỏ, giá bạn trả không đổi.\n" + _COPYRIGHT]
    return "\n\n".join(x for x in parts if x).replace(f"\n\n{_SEP}\n\n", f"\n{_SEP}\n")
