"""Nối dữ liệu SEO (tab 🔑) vào bước viết script (tab ✍️).

Luồng:
    Tab 🔑 bấm 📌 "Đặt làm tiêu đề video"  ->  <run>/09_seo_keywords.json
    Tab ✍️ bấm "Viết script"               ->  load_seo_context(run_dir)
                                             ->  allocate_keywords(): CODE chia từ khóa cho
                                                 hook / từng sản phẩm / CTA / mô tả
                                             ->  *_block(): đoạn hướng dẫn chèn vào prompt
    Sau khi viết                           ->  keyword_coverage(): đếm từ khóa đã nói

Nguyên tắc (mức "vừa"):
- Hook: nhắc lời hứa của TIÊU ĐỀ + từ khóa chính trong 2 câu đầu + 1 từ khóa kéo traffic.
- Mỗi sản phẩm: tối đa 2 từ khóa ĐÚNG với sản phẩm đó (giá rẻ -> Top rẻ nhất, cao cấp / tốt nhất
  -> Top 1, "siêu nhỏ" -> sản phẩm có "nhỏ/mini" trong tên...). Từ khóa chính nói thêm 1 lần ở
  sản phẩm giữa video.
- CTA: từ khóa chính + 1 từ khóa mua hàng ("giá bao nhiêu"...).
- Mỗi từ khóa phụ 1 lần, từ khóa chính ~3-4 lần cả video. Không nhồi.
- Chỉ từ khóa đang tick "Dùng" (không Loại / không "Cần xác nhận") mới được giao.

Không có 09_seo_keywords.json -> dùng 02_keywords_filtered.json (bước Chọn sản phẩm), không có tiêu đề.
Không có gì -> SeoContext rỗng, các block = "" -> prompt y như trước khi có tính năng này.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .seo_titles import (GROUP_BRAND, GROUP_BUY, GROUP_GUIDE, GROUP_PROBLEM, GROUP_REVIEW, ROLE_LONGTAIL,
                         ROLE_MAIN, ROLE_NOISE, ROLE_SECONDARY, ROLE_TAG, ROLE_TRAFFIC, SEO_FILE, product_part,
                         strip_accents, vn_title_case)

SOURCE_SEO_TAB = "Tab 🔑 Từ khóa & Tiêu đề SEO (09_seo_keywords.json)"
SOURCE_SELECTION = "Bước Chọn sản phẩm (02_keywords_filtered.json) — chưa có tiêu đề"
SOURCE_NONE = ""

MAX_PER_PRODUCT = 2
MAIN_MAX_MENTIONS = 5     # quá số này -> cảnh báo nhồi từ khóa chính
OTHER_MAX_MENTIONS = 3    # quá số này -> cảnh báo nhồi từ khóa phụ

# Cụm cho thấy tiêu đề hứa TRẢI NGHIỆM CÁ NHÂN -> phải có thật mới được nói như đã làm.
_CLAIM_PATTERNS = [r"tôi thử", r"mình thử", r"dùng thử", r"đeo thử", r"test thực tế", r"đã test", r"tự test",
                   r"trải nghiệm thực tế", r"review thật", r"sau \d+ (?:ngày|tuần|tháng)",
                   r"\d+ (?:ngày|tuần|tháng)", r"cả tuần", r"cả tháng", r"tôi mua", r"mình mua"]

# Từ đồng nghĩa khi so từ khóa với tên sản phẩm (bỏ dấu)
_SYNONYMS = {"nho": {"nho", "mini", "nano"}, "mini": {"mini", "nho"}, "khong day": {"khong day", "bluetooth", "wireless"},
             "nhat": {"nhat", "japan"}, "sac": {"sac", "rechargeable"}}
_CHEAP_MARKERS = ["giá rẻ", "rẻ", "bình dân", "sinh viên", "dưới"]
_PREMIUM_MARKERS = ["cao cấp", "tốt nhất", "xịn", "chính hãng", "đáng mua nhất", "nhập khẩu"]
_GENERIC_MODIFIERS = {"cho", "loai", "nao", "tot", "nhat", "hien", "nay", "gia", "re", "bao", "nhieu", "cao", "cap"}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(text or "")).lower()).strip()


def _ascii(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", strip_accents(_norm(text)))).strip()


def _has(text_ascii: str, phrase_ascii: str) -> bool:
    return bool(phrase_ascii) and re.search(rf"(?<!\w){re.escape(phrase_ascii)}(?!\w)", text_ascii) is not None


@dataclass
class SeoContext:
    source: str = SOURCE_NONE
    title: Optional[str] = None
    main: Optional[str] = None
    keywords: list[dict] = field(default_factory=list)   # từ khóa ĐANG DÙNG (trừ chính), đã xếp ưu tiên
    tags: list[str] = field(default_factory=list)
    saved_at: Optional[str] = None

    @property
    def has_keywords(self) -> bool:
        return bool(self.main or self.keywords)

    @property
    def title_claims(self) -> list[str]:
        """Cụm trải nghiệm cá nhân trong tiêu đề (vd 'tôi thử', '30 ngày')."""
        if not self.title:
            return []
        t = _norm(self.title)
        found = []
        for pat in _CLAIM_PATTERNS:
            m = re.search(pat, t)
            if m and not any(m.group(0) in f for f in found):
                found.append(m.group(0))
        return found


def load_seo_context(run_dir: Optional[Path]) -> SeoContext:
    if run_dir is None:
        return SeoContext()
    run_dir = Path(run_dir)
    seo_path = run_dir / SEO_FILE
    if seo_path.exists():
        try:
            data = json.loads(seo_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            data = {}
        rows = data.get("keywords") or []
        usable = [r for r in rows if r.get("use", True) and r.get("role") not in (ROLE_NOISE, ROLE_TAG)
                  and len(_norm(r.get("keyword", "")).split()) <= 8]
        main = next((r["keyword"] for r in usable if r.get("role") == ROLE_MAIN), data.get("main_keyword"))
        order = {ROLE_TRAFFIC: 0, ROLE_SECONDARY: 1, ROLE_LONGTAIL: 2}
        others = sorted([r for r in usable if r.get("role") != ROLE_MAIN],
                        key=lambda r: (order.get(r.get("role"), 3), -(r.get("search_volume") or 0)))
        title = data.get("chosen_title")
        if title and title == title.lower():
            title = vn_title_case(title)  # tiêu đề lưu từ cache vidIQ có thể bị chữ thường toàn bộ
        return SeoContext(source=SOURCE_SEO_TAB, title=title, main=_norm(main) if main else None,
                          keywords=[_slim(r) for r in others], tags=list(data.get("tags") or []),
                          saved_at=data.get("saved_at"))
    filt = run_dir / "02_keywords_filtered.json"
    if filt.exists():
        try:
            data = json.loads(filt.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            data = {}
        kws = sorted(data.get("keywords") or [], key=lambda k: -(k.get("score") or 0))
        kws = [k for k in kws if k.get("keyword") and len(_norm(k["keyword"]).split()) <= 8]
        if kws:
            rows = [{"keyword": k["keyword"], "role": ROLE_TRAFFIC if i < 4 else ROLE_SECONDARY, "group": "",
                     "audience": _audience(k["keyword"]), "product_part": product_part(k["keyword"])}
                    for i, k in enumerate(kws[1:12])]
            return SeoContext(source=SOURCE_SELECTION, main=_norm(kws[0]["keyword"]), keywords=rows)
    return SeoContext()


def _audience(keyword: str) -> Optional[str]:
    m = re.search(r"(?<!\w)cho (.+)$", _norm(keyword))
    return m.group(1).strip() if m else None


def _slim(r: dict) -> dict:
    return {"keyword": r["keyword"], "role": r.get("role"), "group": r.get("group") or "",
            "audience": r.get("audience") or _audience(r["keyword"]),
            "product_part": r.get("product_part") or product_part(r["keyword"]),
            "volume": r.get("volume_raw") or r.get("search_volume")}


# ---------------------------------------------------------------- chia từ khóa --

@dataclass
class Allocation:
    hook: list[str] = field(default_factory=list)
    cta: list[str] = field(default_factory=list)
    segments: dict[str, list[str]] = field(default_factory=dict)   # rank (str) -> từ khóa
    description: list[str] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)           # từ khóa -> vì sao giao chỗ đó

    def all_assigned(self) -> dict[str, list[str]]:
        """từ khóa -> danh sách phần được giao (để hiển thị / kiểm tra)."""
        out: dict[str, list[str]] = {}
        for kw in self.hook:
            out.setdefault(kw, []).append("Hook")
        for rank, kws in sorted(self.segments.items(), key=lambda x: -int(x[0])):
            for kw in kws:
                out.setdefault(kw, []).append(f"Top {rank}")
        for kw in self.cta:
            out.setdefault(kw, []).append("CTA")
        return out


def _product_text(p: dict) -> str:
    parts = [p.get("name", ""), " ".join(p.get("needs_met") or []), p.get("reason_selected") or "",
             p.get("tier") or ""]
    return _ascii(" ".join(parts))


def _modifier_match(kw: dict, main: str, product_ascii: str) -> bool:
    """Phần 'đặc tính' của từ khóa (vd 'siêu nhỏ', 'không dây', 'nhật bản') có trong sản phẩm không."""
    main_tokens = set(_ascii(main).split())
    words = [w for w in _ascii(kw.get("product_part") or kw["keyword"]).split()
             if w not in main_tokens and w not in _GENERIC_MODIFIERS and w not in {"may", "tai", "nghe"}]
    if not words:
        return False
    phrase = " ".join(words)
    candidates = {phrase} | {w for w in words if len(w) > 2}
    for c in list(candidates):
        candidates |= _SYNONYMS.get(c, set())
    return any(_has(product_ascii, c) for c in candidates)


def allocate_keywords(ctx: SeoContext, products: list[dict]) -> Allocation:
    alloc = Allocation()
    if not ctx.has_keywords or not products:
        return alloc
    main = _norm(ctx.main) if ctx.main else None
    kws = [{**k, "keyword": _norm(k["keyword"])} for k in ctx.keywords]
    ranks = sorted([str(p.get("rank")) for p in products], key=lambda r: int(r))   # "1".."5"
    by_rank = {str(p.get("rank")): p for p in products}
    alloc.segments = {r: [] for r in ranks}
    used: set[str] = set()

    # Hook: từ khóa CÓ TRONG TIÊU ĐỀ nói trước (2 câu đầu — khớp đúng thứ người xem vừa bấm vào),
    # rồi tới từ khóa chính; không có tiêu đề -> từ khóa chính + từ khóa kéo traffic mạnh nhất.
    title_kw = title_keyword(ctx)
    if title_kw:
        alloc.hook.append(title_kw)
        alloc.reasons[title_kw] = "Có trong tiêu đề -> nói ở 2 câu đầu hook"
        used.add(title_kw)
    if main and main not in alloc.hook:
        alloc.hook.append(main)
    if main:
        alloc.reasons.setdefault(main, "Từ khóa chính: hook, giữa video, CTA")
        alloc.reasons[main] = "Từ khóa chính: hook, giữa video, CTA"
    if len(alloc.hook) < 2:
        hook_kw = next((k for k in kws if k.get("group") not in (GROUP_BUY, GROUP_GUIDE, GROUP_PROBLEM)
                        and not _is_cheap(k) and not _is_premium(k) and k["keyword"] not in alloc.hook), None)
        if hook_kw:
            alloc.hook.append(hook_kw["keyword"])
            used.add(hook_kw["keyword"])
            alloc.reasons[hook_kw["keyword"]] = "Kéo traffic mạnh nhất -> nói ở hook"

    # CTA: từ khóa chính + 1 từ khóa mua hàng
    if main:
        alloc.cta.append(main)
    buy_kw = next((k for k in kws if k.get("group") == GROUP_BUY and not _is_cheap(k) and k["keyword"] not in used),
                  None)
    if buy_kw:
        alloc.cta.append(buy_kw["keyword"])
        used.add(buy_kw["keyword"])
        alloc.reasons[buy_kw["keyword"]] = "Ý định mua hàng -> CTA"

    cheapest = ranks[-1]
    top1 = ranks[0]
    middle = ranks[len(ranks) // 2]
    if main:
        alloc.segments[middle].append(main)  # nhắc từ khóa chính 1 lần giữa video

    audience_of_kw = {k["keyword"]: k.get("audience") for k in kws}

    def give(rank: str, kw: dict, why: str) -> bool:
        seg = alloc.segments[rank]
        own = [x for x in seg if x != main]
        if len(own) >= MAX_PER_PRODUCT or kw["keyword"] in seg:
            return False
        # không giao 2 cụm lồng nhau cho cùng 1 đoạn ("tốt nhất" + "tốt nhất hiện nay")
        if any(_nested(kw["keyword"], x) for x in seg):
            return False
        # không giao 2 cụm cùng đối tượng cho 1 đoạn ("tai nghe … cho người già" + "máy … cho người già")
        aud = _ascii(kw.get("audience") or "")
        if aud and any(_ascii(audience_of_kw.get(x) or "") == aud for x in seg):
            return False
        seg.append(kw["keyword"])
        used.add(kw["keyword"])
        alloc.reasons[kw["keyword"]] = why
        return True

    pending = [k for k in kws if k["keyword"] not in used and k.get("group") not in (GROUP_GUIDE, GROUP_PROBLEM)]
    # 1) khớp rõ ràng: giá rẻ -> Top rẻ nhất; cao cấp/tốt nhất -> Top 1; đặc tính/đối tượng/thương hiệu có trong sản phẩm
    rest = []
    for k in pending:
        if _is_cheap(k):
            if not give(cheapest, k, f"Ý định giá rẻ -> Top {cheapest} (rẻ nhất)"):
                rest.append(k)
            continue
        matched = [r for r in ranks if _modifier_match(k, main or "", _product_text(by_rank[r]))]
        if not matched and (_is_premium(k) or k.get("group") == GROUP_REVIEW):
            if not give(top1, k, "Cao cấp / tốt nhất -> Top 1"):
                rest.append(k)
            continue
        if not matched and k.get("audience"):
            aud = _ascii(k["audience"])
            matched = [r for r in ranks if aud and _has(_product_text(by_rank[r]), aud)]
        if matched:
            matched.sort(key=lambda r: len([x for x in alloc.segments[r] if x != main]))
            label = "đặc tính / đối tượng" if k.get("group") != GROUP_BRAND else "thương hiệu"
            if not any(give(r, k, f"Khớp {label} với sản phẩm Top {r}") for r in matched):
                rest.append(k)
            continue
        if k.get("group") == GROUP_BRAND:
            continue  # thương hiệu không khớp sản phẩm nào -> KHÔNG giao (tránh nói sai hãng)
        if _has_specific_modifier(k, main or ""):
            continue  # đặc tính cụ thể (vd 'nhật bản', 'siêu nhỏ') không sản phẩm nào có -> không giao, tránh nói sai
        rest.append(k)
    # 2) từ khóa chung (tên sản phẩm, đối tượng chung) -> rải đều cho sản phẩm còn ít từ khóa
    for k in rest:
        if k.get("group") == GROUP_BRAND or _has_specific_modifier(k, main or "") and not k.get("audience"):
            continue
        order = sorted(ranks, key=lambda r: (len([x for x in alloc.segments[r] if x != main]), -int(r)))
        for r in order:
            if give(r, k, f"Từ khóa chung -> rải cho Top {r}"):
                break

    alloc.description = [x for x in [main] + [k["keyword"] for k in kws] if x][:6]
    return alloc


def _nested(a: str, b: str) -> bool:
    ta, tb = _ascii(a).split(), _ascii(b).split()
    return set(ta) <= set(tb) or set(tb) <= set(ta)


def title_keyword(ctx: SeoContext) -> Optional[str]:
    """Từ khóa (chính hoặc phụ đang dùng) DÀI NHẤT xuất hiện nguyên văn trong tiêu đề."""
    if not ctx.title:
        return None
    t = _ascii(ctx.title)
    cands = ([ctx.main] if ctx.main else []) + [k["keyword"] for k in ctx.keywords]
    hits = [_norm(c) for c in cands if _has(t, _ascii(c))]
    return max(hits, key=lambda c: len(_ascii(c))) if hits else None


def _is_cheap(k: dict) -> bool:
    t = _norm(k["keyword"])
    return any(re.search(rf"(?<!\w){m}(?!\w)", t) for m in _CHEAP_MARKERS)


def _is_premium(k: dict) -> bool:
    t = _norm(k["keyword"])
    return any(m in t for m in _PREMIUM_MARKERS)


def _has_specific_modifier(k: dict, main: str) -> bool:
    main_tokens = set(_ascii(main).split())
    words = [w for w in _ascii(k.get("product_part") or k["keyword"]).split()
             if w not in main_tokens and w not in _GENERIC_MODIFIERS and w not in {"may", "tai", "nghe", "tro", "thinh"}]
    return bool(words)


# ---------------------------------------------------------------- khối prompt --

def _quote_list(kws: list[str]) -> str:
    return "\n".join(f'- "{k}"' for k in kws)


def hook_title_block(ctx: SeoContext, claims_confirmed: bool) -> str:
    if not ctx.title:
        return ""
    lines = [f'Tiêu đề video (người xem bấm vào CHÍNH VÌ tiêu đề này): "{ctx.title}"',
             "- Trong 2 câu ĐẦU TIÊN, nhắc lại đúng chủ đề / lời hứa của tiêu đề để người xem thấy ngay đúng thứ "
             "họ đang tìm — nếu không họ sẽ thoát sớm. Sau đó mới giới thiệu đếm ngược Top 5."]
    claims = ctx.title_claims
    if claims and claims_confirmed:
        lines.append(f"- Tiêu đề có lời hứa trải nghiệm ({', '.join(claims)}). Người làm video ĐÃ XÁC NHẬN nội dung "
                     "này là thật -> được nói ở ngôi thứ nhất, đúng như tiêu đề.")
    elif claims:
        lines.append(f"- Tiêu đề có cụm trải nghiệm ({', '.join(claims)}) nhưng CHƯA được xác nhận là thật: TUYỆT ĐỐI "
                     "KHÔNG bịa trải nghiệm cá nhân (không nói 'mình đã dùng 30 ngày', 'mình test thử'). Thay bằng "
                     "cách nói trung thực như 'mình đã tìm hiểu, so sánh kỹ thông số và đánh giá người mua…', vẫn bám "
                     "đúng chủ đề tiêu đề.")
    return "\n".join(lines) + "\n"


def hook_seo_block(ctx: SeoContext, alloc: Allocation) -> str:
    if not alloc.hook:
        return ""
    first = alloc.hook[0]
    extra = alloc.hook[1:]
    s = (f'Từ khóa SEO — nói NGUYÊN VĂN, tự nhiên (phụ đề YouTube sẽ chứa đúng cụm này, đừng viết tắt hay đổi từ):\n'
         f'- "{first}": nói trong 2 câu đầu, 1 lần.\n')
    if extra:
        s += "".join(f'- "{k}": nói 1 lần ở bất kỳ câu nào.\n' for k in extra)
    s += "- Không lặp từ khóa quá số lần trên, không nhồi — nghe phải như người thật nói chuyện.\n"
    return s


def segment_seo_block(ctx: SeoContext, alloc: Allocation, rank) -> str:
    kws = alloc.segments.get(str(rank)) or []
    if not kws:
        return ""
    return ("Từ khóa SEO cho đoạn này — lồng NGUYÊN VĂN vào lời bình (ở khối ảnh nào cũng được), mỗi cụm TỐI ĐA "
            "1 lần, nói tự nhiên như đang trò chuyện. Nếu cụm nào thật sự KHÔNG đúng với sản phẩm này thì bỏ qua, "
            "không nói sai sự thật để chèn từ khóa:\n" + _quote_list(kws) + "\n")


def cta_seo_block(ctx: SeoContext, alloc: Allocation) -> str:
    if not alloc.cta:
        return ""
    return ("Từ khóa SEO — nói NGUYÊN VĂN mỗi cụm 1 lần, tự nhiên (vd '…link máy trợ thính siêu nhỏ ở phần mô tả'):\n"
            + _quote_list(alloc.cta) + "\n")


def metadata_blocks(ctx: SeoContext, alloc: Allocation) -> tuple[str, str]:
    title = ""
    if ctx.title:
        title = (f'Tiêu đề ĐÃ CHỐT (đã chấm điểm SEO): "{ctx.title}" — trả về NGUYÊN VĂN tiêu đề này ở trường '
                 '"title", không sửa.\n')
    seo = ""
    if alloc.description:
        seo = ("Từ khóa SEO: 2 câu ĐẦU của \"hook_description\" phải chứa tự nhiên các cụm sau (nguyên văn, mỗi cụm "
               "1 lần, không nhồi): " + ", ".join(f'"{k}"' for k in alloc.description[:4]) + ".\n")
    return title, seo


# ---------------------------------------------------------------- kiểm tra --

def _sections(script: str) -> list[tuple[str, str]]:
    from .script_parser import parse_segments
    try:
        segs = parse_segments(script)
    except ValueError:
        return [("Toàn bài", script)]
    out = []
    names = {}
    for s in segs:
        if s.kind == "product":
            names.setdefault(s.product_name, len(names))
            out.append((f"SP: {s.product_name}", s.raw_text))
        else:
            out.append(("Hook" if s.kind == "hook" else "CTA", s.raw_text))
    return out


def count_mentions(text: str, keyword: str, longer: Optional[list[str]] = None) -> int:
    """Đếm số lần nói cụm `keyword`. Lần nằm TRONG 1 cụm dài hơn đang theo dõi (vd 'máy trợ thính'
    trong 'máy trợ thính giá rẻ') không tính — đã tính cho cụm dài đó rồi."""
    t = _ascii(text)
    kw = _ascii(keyword)
    for lg in sorted(longer or [], key=len, reverse=True):
        lg_a = _ascii(lg)
        if lg_a != kw and _has(lg_a, kw):
            t = re.sub(rf"(?<!\w){re.escape(lg_a)}(?!\w)", " § ", t)
    return len(re.findall(rf"(?<!\w){re.escape(kw)}(?!\w)", t))


def keyword_coverage(script: str, ctx: SeoContext, alloc: Allocation,
                     products: Optional[list[dict]] = None) -> list[dict]:
    """Bảng từ khóa đã nói — CODE đếm (không nhờ AI). Đếm không phân biệt hoa thường / dấu câu."""
    rank_by_name = {p.get("name"): p.get("rank") for p in (products or [])}
    sections = _sections(script)
    assigned = alloc.all_assigned()
    rows = []
    tracked = list(dict.fromkeys(([_norm(ctx.main)] if ctx.main else []) + list(assigned)))
    for kw in tracked:
        where, total = [], 0
        for label, text in sections:
            n = count_mentions(text, kw, tracked)
            if n:
                if label.startswith("SP: "):
                    r = rank_by_name.get(label[4:])
                    label = f"Top {r}" if r else label[:30]
                where.append(f"{label}×{n}" if n > 1 else label)
                total += n
        is_main = kw == _norm(ctx.main or "")
        generic = not is_main and len(_ascii(kw).split()) <= 3  # tên sản phẩm chung, vd "máy trợ thính"
        limit = MAIN_MAX_MENTIONS if is_main else OTHER_MAX_MENTIONS
        if total == 0:
            status = "⚠️ Chưa nói"
        elif generic and total > limit:
            status = "✅ Đã nói (tên sản phẩm chung — nhắc nhiều là bình thường)"
        elif total > limit:
            status = f"⚠️ Lặp nhiều (>{limit}) — nghe như nhồi từ khóa"
        else:
            status = "✅ Đã nói"
        rows.append({"Từ khóa": kw, "Vai trò": "Chính" if is_main else "Phụ / kéo traffic",
                     "Được giao cho": ", ".join(assigned.get(kw, [])), "Số lần nói": total,
                     "Nói ở": ", ".join(where), "Trạng thái": status})
    return rows
