"""Quy trình tạo tiêu đề SEO có chấm điểm bằng vidIQ (6 bước):

1. Lọc từ khóa: bỏ volume < ngưỡng (mặc định 750) và từ khóa đã bị đánh dấu Loại.
2. Chọn 3-5 từ khóa mục tiêu: volume cao, cạnh tranh thấp.
3. Viết 8-10 tiêu đề nháp (≤ 100 ký tự, mỗi tiêu đề chứa ≥ 1 từ khóa mục tiêu) —
   lấy từ bộ ghép 10 công thức CTR (src/seo_titles.py), không tốn API.
4. Chấm TỪNG tiêu đề bằng vidiq_score_title (type="long", 5 credit/lần).
5. Chưa có tiêu đề ≥ ngưỡng (90): gọi vidiq_generate_titles để lấy mẫu điểm cao,
   nhờ Groq viết lại theo mẫu (prompts/08_vidiq_rewrite.md), chấm lại. Tối đa N vòng.
6. Xuất kết quả: CHỈ dùng điểm do vidIQ trả về (code tự trích từ kết quả tool,
   không để AI báo điểm). Không đạt sau N vòng -> trả tiêu đề tốt nhất kèm điểm thật.

Có trần credit cho mỗi lần chạy + cache điểm theo (kênh, loại, tiêu đề) trong
data/vidiq_cache.json để không bao giờ trả tiền chấm lại cùng 1 tiêu đề.

HỌC TỪ CÁC LẦN CHẠY TRƯỚC (data/title_memory.json — `TitleMemory`): mọi tiêu đề
vidIQ đã chấm được lưu lại, rút thành KHUNG tiêu đề bằng cách thay từ khóa bằng
{TỪ KHÓA} và năm bằng {NĂM} (vd "{TỪ KHÓA} {NĂM}: Tôi Thử 30 Ngày Và Kết Quả Thật
Bất Ngờ" — 93 điểm). Lần sau, kể cả sản phẩm khác:
  - Vòng 1 thử TRƯỚC các khung điểm cao đã học (điền từ khóa mới), rồi mới tới
    tiêu đề ghép từ công thức.
  - Dừng ngay khi đủ số tiêu đề đạt ngưỡng (không chấm nốt cả vòng) -> tiết kiệm credit.
  - Groq viết lại được xem các tiêu đề điểm cao/thấp đã học (few-shot).
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .llm_errors import LLMJSONError, LLMQuotaExceededError, LLMServerError
from .prompt_loader import render
from .seo_titles import (GROUP_BRAND, ROLE_NOISE, ROLE_TAG, Keyword, is_usable, TitleOptions, _tokens, generate_titles, is_medical,
                         product_part, strip_accents, vn_title_case)
from .vidiq_client import CREDITS_PER_CALL, VidIQClient, VidIQError


@dataclass
class VidIQParams:
    min_volume: int = 750
    n_targets: int = 4
    n_titles: int = 8
    threshold: float = 90
    max_rounds: int = 4
    max_credits: int = 150
    title_type: str = "long"
    language: str = "vi"
    max_chars: int = 70              # YouTube cắt tiêu đề ~60-70 ký tự trên điện thoại
    use_memory: bool = True          # vòng 1 thử trước các khung tiêu đề điểm cao đã học
    stop_after_passed: int = 3       # cần đủ N tiêu đề đạt ngưỡng (KHÁC NHAU thật sự) rồi mới dừng
    # --- tiêu đề phải ĐÚNG NỘI DUNG video review Top N affiliate (không chỉ điểm vidIQ cao) ---
    tested: bool = False             # người làm video đã dùng / test thật -> mới được hứa "thử", "thực nghiệm"
    top_n: int = 5
    product_names: list = field(default_factory=list)   # tên sản phẩm trong video (lọc từ khóa thương hiệu lạc)


@dataclass
class ScoredTitle:
    title: str
    score: Optional[float]
    keyword: str
    volume: Optional[int]
    round: int
    source: str
    cached: bool = False
    raw: str = ""


def _skeleton_words(title: str, keyword: Optional[str] = None) -> set[str]:
    """Bộ từ của PHẦN KHUNG tiêu đề: bỏ từ khóa, năm, số."""
    words = [strip_accents(t) for t in _tokens(title) if not t.isdigit()]
    if keyword:
        kw = [strip_accents(t) for t in _tokens(keyword)]
        for i in range(len(words) - len(kw) + 1):
            if words[i:i + len(kw)] == kw:
                words = words[:i] + words[i + len(kw):]
                break
    return set(words)


def is_near_duplicate(a: str, b: str, kw_a: Optional[str] = None, kw_b: Optional[str] = None,
                      limit: float = 0.6) -> bool:
    """2 tiêu đề coi như 1 lựa chọn nếu PHẦN KHUNG gần giống nhau:
    - chỉ khác 1-2 từ ('…Kết Quả Thật Bất Ngờ' vs '…Kết Quả Thật Kinh Ngạc'), hoặc
    - cùng khung, chỉ đổi từ khóa ('Máy Trợ Thính Siêu Nhỏ: Tôi Thử…' vs 'Tai Nghe…: Tôi Thử…')."""
    wa, wb = _skeleton_words(a, kw_a), _skeleton_words(b, kw_b)
    if not wa or not wb:
        return a.strip().lower() == b.strip().lower()
    return len(wa & wb) / len(wa | wb) >= limit


def distinct_titles(results: list["ScoredTitle"]) -> list["ScoredTitle"]:
    """Giữ tiêu đề điểm cao nhất của mỗi nhóm gần trùng."""
    out: list[ScoredTitle] = []
    for r in sorted(results, key=lambda r: r.score or 0, reverse=True):
        if not any(is_near_duplicate(r.title, o.title, r.keyword, o.keyword) for o in out):
            out.append(r)
    return out


@dataclass
class VidIQRun:
    targets: list[dict] = field(default_factory=list)
    results: list[ScoredTitle] = field(default_factory=list)
    samples: list[dict] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    credits_used: int = 0
    rounds: int = 0
    threshold: float = 90
    reached: bool = False
    channel_id: str = ""
    finished_at: str = ""

    @property
    def distinct_passed(self) -> list[ScoredTitle]:
        return distinct_titles(self.passed)

    @property
    def passed(self) -> list[ScoredTitle]:
        return [r for r in self.results if r.score is not None and r.score >= self.threshold]

    def to_dict(self) -> dict:
        return {**asdict(self), "passed": [asdict(r) for r in self.passed],
                "distinct_passed": [asdict(r) for r in self.distinct_passed]}


def estimate_credits(p: VidIQParams) -> int:
    """Trần credit tối đa (vòng 1 + mỗi vòng sau: 1 lần gợi ý mẫu ở vòng đầu + n tiêu đề)."""
    worst = p.n_titles * p.max_rounds + (1 if p.max_rounds > 1 else 0)
    return min(worst * CREDITS_PER_CALL, p.max_credits)


# ------------------------------------------------------------------ cache --

class ScoreCache:
    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except ValueError:
            self.data = {}

    @staticmethod
    def _key(title: str, channel: str, title_type: str) -> str:
        return f"{channel}|{title_type}|{unicodedata.normalize('NFC', title).strip().lower()}"

    def get(self, title: str, channel: str, title_type: str) -> Optional[dict]:
        return self.data.get(self._key(title, channel, title_type))

    def set(self, title: str, channel: str, title_type: str, score: Optional[float]) -> None:
        self.data[self._key(title, channel, title_type)] = {"score": score, "at": datetime.now().isoformat(timespec="seconds")}
        self._save()

    def get_samples(self, keyword: str, channel: str, language: str) -> Optional[list]:
        hit = self.data.get(self._key("samples:" + keyword, channel, language))
        return hit["samples"] if hit else None

    def set_samples(self, keyword: str, channel: str, language: str, samples: list) -> None:
        self.data[self._key("samples:" + keyword, channel, language)] = {
            "samples": samples, "at": datetime.now().isoformat(timespec="seconds")}
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")


# ------------------------------------------------------- bộ nhớ đã học --

KW_SLOT, YEAR_SLOT = "{TỪ KHÓA}", "{NĂM}"


def _norm_niche(text: str) -> str:
    return " ".join(strip_accents(t) for t in _tokens(text or ""))


def make_template(title: str, keyword: Optional[str]) -> Optional[str]:
    """'Máy Trợ Thính Siêu Nhỏ 2026: Tôi Thử…' + 'máy trợ thính siêu nhỏ'
    -> '{TỪ KHÓA} {NĂM}: Tôi Thử…'. Không thấy từ khóa trong tiêu đề -> None."""
    if not keyword:
        return None
    title = unicodedata.normalize("NFC", title)
    low, kw = title.lower(), unicodedata.normalize("NFC", keyword).lower().strip()
    i = low.find(kw)
    if i < 0 or len(low) != len(title):
        return None
    tpl = title[:i] + KW_SLOT + title[i + len(kw):]
    return re.sub(r"(?<!\d)20\d\d(?!\d)", YEAR_SLOT, tpl)


_NEUTRAL_WORDS = {"cho", "và", "của", "có", "không", "khi", "là", "này", "nào", "với", "để", "thật", "mới",
                  "tôi", "bạn", "mình", "thử", "mua", "giá", "top", "năm", "ngày", "tuần", "tháng",
                  # từ chỉ Ý ĐỊNH tìm kiếm (giá, đánh giá, hướng dẫn) — dùng cho sản phẩm nào cũng được
                  "bao", "nhiêu", "tiền", "rẻ", "tốt", "nhất", "hiện", "nay", "loại", "nên", "cách", "sử", "dụng",
                  "hướng", "dẫn", "review", "đánh", "so", "sánh", "chính", "hãng", "đâu", "ở", "bảng"}


def niche_vocab(keywords: list[Keyword], niche: str) -> set[str]:
    """Từ RIÊNG của sản phẩm: tên sản phẩm (đã cắt phần ý định như 'giá bao nhiêu', 'tốt nhất')
    + đối tượng ('người già', 'trẻ em'...)."""
    texts = [niche]
    for k in keywords:
        if is_usable(k):
            texts.append(product_part(k.keyword))
            if k.audience:
                texts.append(k.audience)
    return niche_words(texts)


def niche_words(texts: list[str]) -> set[str]:
    """Từ đặc trưng của 1 ngách (bỏ dấu), lấy từ từ khóa chính + các từ khóa đã dùng."""
    # So khớp CÓ DẤU: bỏ dấu thì "Tại" (Tại Sao…) trùng "tai" (tai nghe) -> chặn nhầm khung.
    words = set()
    for t in texts:
        words |= set(_tokens(t or ""))
    return {w for w in words if w not in _NEUTRAL_WORDS and not w.isdigit()}


def template_leaks(template: str, vocab: set[str]) -> list[str]:
    """Các từ RIÊNG của ngách cũ còn sót trong khung (ngoài chỗ {TỪ KHÓA}), vd khung
    '{TỪ KHÓA}: Gần Như Tàng Hình Trong Tai!' học từ máy trợ thính còn sót 'tai'."""
    body = template.replace(KW_SLOT, " ").replace(YEAR_SLOT, " ")
    return [w for w in _tokens(body) if w in vocab]


def fill_template(template: str, keyword: str, year: int, title_case: bool = True) -> str:
    text = template.replace(KW_SLOT, keyword).replace(YEAR_SLOT, str(year))
    text = " ".join(text.split())
    return vn_title_case(text) if title_case else text[0].upper() + text[1:]


class TitleMemory:
    """Lưu mọi tiêu đề đã chấm (điểm THẬT của vidIQ) qua các lần chạy / các sản phẩm."""

    def __init__(self, path: Path, cache: Optional["ScoreCache"] = None):
        self.path = Path(path)
        try:
            self.entries: list[dict] = (json.loads(self.path.read_text(encoding="utf-8"))
                                        if self.path.exists() else [])
        except ValueError:
            self.entries = []
        if cache is not None:  # nhập các điểm đã có trong cache từ trước khi có bộ nhớ
            known = {e["title"].lower() for e in self.entries}
            for key, val in cache.data.items():
                parts = key.split("|", 2)
                if len(parts) == 3 and not parts[2].startswith("samples:") and isinstance(val, dict) \
                        and val.get("score") is not None and parts[2] not in known:
                    self.entries.append({"title": parts[2], "score": val["score"], "keyword": None,
                                         "template": None, "niche": None, "source": "cache cũ",
                                         "channel": parts[0], "type": parts[1], "at": val.get("at")})

    def add(self, title: str, score: Optional[float], keyword: str, niche: str, source: str,
            channel: str, title_type: str, vocab: Optional[set[str]] = None) -> None:
        """niche = từ khóa chính của sản phẩm; vocab = từ đặc trưng của ngách (để biết khung
        nào dùng chéo sang sản phẩm khác được)."""
        if score is None:
            return
        entry = {"title": title, "score": score, "keyword": keyword, "template": make_template(title, keyword),
                 "niche": niche, "source": source, "channel": channel, "type": title_type,
                 "vocab": sorted(vocab or niche_words([niche, keyword])),
                 "at": datetime.now().isoformat(timespec="seconds")}
        for i, e in enumerate(self.entries):
            if e["title"].lower() == title.lower():
                self.entries[i] = {**e, **{k: v for k, v in entry.items() if v is not None}}
                break
        else:
            self.entries.append(entry)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.entries, ensure_ascii=False, indent=1), encoding="utf-8")

    def clear(self) -> None:
        self.entries = []
        self.save()

    def _annotated(self, keywords: list[str], niche: str) -> list[dict]:
        """Gắn cho mỗi mục: khung, cùng ngách hay không, có dùng chéo được không."""
        niche_n = _norm_niche(niche)
        out = []
        for e in self.entries:
            tpl, same = e.get("template"), bool(e.get("niche")) and _norm_niche(e["niche"]) == niche_n
            if not e.get("niche"):
                # mục nhập từ cache cũ (không rõ sản phẩm): chỉ coi là CÙNG NGÁCH nếu tiêu đề chứa
                # từ khóa của sản phẩm hiện tại; không thì bỏ qua — tránh lẫn sản phẩm.
                tpl = None
                for kw in sorted(keywords, key=len, reverse=True):
                    tpl = make_template(e["title"], kw)
                    if tpl:
                        same = True
                        break
            vocab = set(e.get("vocab") or niche_words([e.get("niche") or "", e.get("keyword") or ""]))
            leaks = template_leaks(tpl, vocab) if tpl else []
            out.append({**e, "template": tpl, "same_niche": same, "leaks": leaks,
                        "transferable": bool(tpl) and not leaks})
        return out

    def best_templates(self, keywords: list[str], title_type: str, min_score: float,
                       niche: str = "") -> list[dict]:
        """Khung điểm cao nhất dùng được cho sản phẩm hiện tại (mỗi khung 1 dòng):
        - khung học từ CÙNG ngách: luôn dùng được, xếp trước;
        - khung từ ngách KHÁC: chỉ khi không sót từ riêng của ngách cũ (vd 'tai', 'người già')."""
        best: dict[str, dict] = {}
        for e in self._annotated(keywords, niche):
            if not e["template"] or e.get("type") not in (None, title_type) or e["score"] < min_score:
                continue
            if not (e["same_niche"] or e["transferable"]):
                continue
            cur = best.get(e["template"])
            if cur is None or (e["same_niche"], e["score"]) > (cur["same_niche"], cur["score"]):
                best[e["template"]] = e
        return sorted(best.values(), key=lambda e: (e["same_niche"], e["score"]), reverse=True)

    def examples(self, title_type: str, keywords: Optional[list[str]] = None, niche: str = "",
                 n_good: int = 10, n_bad: int = 6) -> dict:
        """Ví dụ cho Groq: tiêu đề CÙNG ngách (nguyên văn) + chỉ KHUNG trung tính của ngách khác
        (không đưa tên sản phẩm khác cho Groq, tránh nó viết lẫn sản phẩm)."""
        pool = [e for e in self._annotated(keywords or [], niche) if e.get("type") in (None, title_type)]
        same = sorted([e for e in pool if e["same_niche"]], key=lambda e: e["score"], reverse=True)
        other = sorted([e for e in pool if not e["same_niche"] and e["transferable"]],
                       key=lambda e: e["score"], reverse=True)
        good = [{"title": e["title"], "score": e["score"], "khung": e["template"]} for e in same[:n_good]]
        seen = {g["khung"] for g in good}
        for e in other:
            if len(good) >= n_good + 4:
                break
            if e["template"] not in seen:
                seen.add(e["template"])
                good.append({"khung (từ sản phẩm khác)": e["template"], "score": e["score"]})
        bad = [{"title": e["title"], "score": e["score"]} for e in same[-n_bad:]] if len(same) > n_good else []
        return {"diem_cao": good, "diem_thap": bad}

    def stats(self, threshold: float) -> dict:
        return {"total": len(self.entries), "passed": sum(1 for e in self.entries if e["score"] >= threshold),
                "templates": len({e["template"] for e in self.entries if e.get("template")})}


# ------------------------------------------------------------- bước 1-3 --

def _ascii(text: str) -> str:
    return " ".join(strip_accents(t) for t in _tokens(text))


# Từ khóa / tiêu đề kiểu HƯỚNG DẪN (cách sạc pin, cách dùng, sửa lỗi...) — không hợp video review Top N.
_GUIDE_RE = re.compile(r"\b(cach|huong dan|sac pin|sua|ve sinh|loi|thao lap|lap dat|cai dat|reset|ket noi voi)\b")
# Hứa đã tự dùng / test — chỉ được dùng khi tested=True.
_CLAIM_RE = re.compile(r"\b(thu|test|thuc nghiem|thuc te \d+|kiem (tra|nghiem) thuc te|trai nghiem|\d+ ngay|dap hop|"
                       r"toi (da )?dung|minh (da )?dung|sau \d+)\b")
# Tiêu đề phải cho thấy đây là video review / so sánh / chọn mua.
_REVIEW_RE = re.compile(r"\b(top|review|danh gia|so sanh|nen mua|tot nhat|dang mua|chon mua|chon|loai nao|bang gia|"
                        r"gia bao nhieu|mua o dau|xep hang|dang tien|re nhat|cao cap)\b")


def _foreign_brand(k: Keyword, p: VidIQParams, main: str) -> Optional[str]:
    """Từ khóa nhóm THƯƠNG HIỆU / có mã máy (vd 'tefal', 'ajl', 'hr2041/10') mà thương hiệu đó KHÔNG có trong sản phẩm
    của video -> trả về chữ đó (không dùng làm mục tiêu: tiêu đề sẽ hứa sản phẩm video không có)."""
    if not p.product_names:
        return None
    names = " ".join(_ascii(n) for n in p.product_names)
    main_t = set(_ascii(main).split())
    brandish = k.group == GROUP_BRAND
    for tok in _tokens(k.keyword):
        a = strip_accents(tok)
        if a in main_t or a in names:
            continue
        not_vn = tok == a and (re.search(r"[fjwz\d]", a) or a[-1] in "bdklqrsvx")   # không phải âm tiết tiếng Việt
        if (brandish and tok == a and len(a) >= 3) or not_vn:
            return tok
    return None


def content_issues(title: str, p: VidIQParams) -> list[str]:
    """Lý do tiêu đề KHÔNG hợp nội dung video review Top N (rỗng = dùng được)."""
    t = _ascii(title)
    issues = []
    if not p.tested and _CLAIM_RE.search(t):
        issues.append("hứa đã tự dùng / test / thực nghiệm (bạn chưa test thật)")
    if _GUIDE_RE.search(t):
        issues.append("là tiêu đề hướng dẫn, không phải review")
    if not _REVIEW_RE.search(t):
        issues.append("không thấy dấu hiệu video review / so sánh / chọn mua")
    if len(title) > p.max_chars:
        issues.append(f"dài {len(title)} > {p.max_chars} ký tự")
    return issues


def pick_targets(keywords: list[Keyword], p: VidIQParams) -> list[Keyword]:
    # "<750" không có volume thật (search_volume = None) -> không bao giờ lọt ngưỡng 750.
    main = next((k.keyword for k in keywords if k.role == "Chính"), "")
    pool = [k for k in keywords if is_usable(k)
            and (k.search_volume or 0) >= p.min_volume and len(_tokens(k.keyword)) <= 8
            and not _GUIDE_RE.search(_ascii(k.keyword)) and not _foreign_brand(k, p, main)]
    # từ khóa chính luôn là mục tiêu số 1 (đúng chủ đề video), dù volume thấp hơn
    main_k = next((k for k in keywords if k.role == "Chính"), None)
    pool.sort(key=lambda k: (k.search_volume or 0) * (1 - min(k.competition or 0, 100) / 100), reverse=True)
    if main_k is not None:
        pool = [main_k] + [k for k in pool if k is not main_k]
    if not pool:
        pool = [k for k in keywords if is_usable(k)][:1]
    return pool[:p.n_targets]


def _matching_target(title: str, targets: list[Keyword]) -> Optional[Keyword]:
    t = _ascii(title)
    for k in targets:  # ưu tiên khớp nguyên văn từ khóa
        if _ascii(k.keyword) in t:
            return k
    for k in targets:  # rồi tới tên sản phẩm của từ khóa (vd "máy trợ thính" trong "máy trợ thính giá bao nhiêu")
        part = _ascii(product_part(k.keyword))
        if len(part.split()) >= 2 and part in t:
            return k
    return None


def initial_drafts(keywords: list[Keyword], targets: list[Keyword], opts: TitleOptions,
                   p: VidIQParams) -> list[tuple[str, Keyword]]:
    draft_opts = TitleOptions(**{**asdict(opts), "max_chars": p.max_chars})
    drafts, per_formula = [], {}
    for t in generate_titles(keywords, draft_opts):
        if t.chars > p.max_chars:
            continue
        k = _matching_target(t.text, targets)
        if k is None or per_formula.get(t.formula, 0) >= 2 or content_issues(t.text, p):
            continue
        drafts.append((t.text, k))
        per_formula[t.formula] = per_formula.get(t.formula, 0) + 1
        if len(drafts) >= p.n_titles:
            break
    return drafts


def memory_drafts(memory: Optional[TitleMemory], targets: list[Keyword], opts: TitleOptions,
                  p: VidIQParams, all_keywords: list[Keyword]) -> list[tuple[str, Keyword, float]]:
    """Điền từ khóa mục tiêu MỚI vào các khung tiêu đề điểm cao đã học.
    Trả về [(tiêu đề, từ khóa, điểm cũ của khung)] — khung điểm cao nhất đứng đầu."""
    if memory is None or not p.use_memory or not targets:
        return []
    niche = next((k.keyword for k in all_keywords if k.role == "Chính"), targets[0].keyword)
    templates = memory.best_templates([k.keyword for k in all_keywords], p.title_type,
                                      min_score=max(p.threshold - 8, 0), niche=niche)
    out, used_templates = [], []
    for e in templates:
        # mỗi KHUNG chỉ thử 1 lần (khung gần trùng nhau, vd '…Bất Ngờ' / '…Kinh Ngạc', cũng tính là 1)
        if any(is_near_duplicate(e["template"], u) for u in used_templates):
            continue
        for k in targets:  # từ khóa mạnh nhất mà tiêu đề vẫn vừa độ dài
            title = fill_template(e["template"], k.keyword, opts.year, opts.title_case)
            if not content_issues(title, p):
                out.append((title, k, e["score"] if e["same_niche"] else -e["score"]))  # âm = từ ngách khác
                used_templates.append(e["template"])
                break
        if len(out) >= max(p.n_titles // 2, 2):
            break
    return out


# ------------------------------------------------------------- bước 4-6 --

def _goal_note(run: "VidIQRun", p: VidIQParams) -> str:
    ok = run.distinct_passed
    need = max(p.stop_after_passed, 1) - len(ok)
    if not ok:
        return f"Mục tiêu: có ít nhất {p.stop_after_passed} tiêu đề KHÁC NHAU đạt ≥ {p.threshold:g} điểm."
    return (f"Đã có {len(ok)} tiêu đề đạt: " + json.dumps([r.title for r in ok], ensure_ascii=False)
            + f". Cần thêm {need} tiêu đề đạt nữa, PHẢI KHÁC HẲN cấu trúc / góc nhìn các tiêu đề đã đạt "
            "(không chỉ đổi 1-2 từ — đổi kiểu móc: câu hỏi, con số, so sánh, cảnh báo, đối tượng...).")


def run_vidiq_workflow(client: VidIQClient, text_client, keywords: list[Keyword], opts: TitleOptions,
                       p: VidIQParams, cache: ScoreCache,
                       progress: Optional[Callable[[str], None]] = None,
                       memory: Optional[TitleMemory] = None) -> VidIQRun:
    run = VidIQRun(threshold=p.threshold, channel_id=client.channel_id)

    def log(msg: str) -> None:
        run.log.append(msg)
        if progress:
            progress(msg)

    targets = pick_targets(keywords, p)
    log("Lọc tiêu đề theo NỘI DUNG video (review Top %d, %s, ≤ %d ký tự, không từ khóa hướng dẫn / thương hiệu "
        "không có trong video)." % (p.top_n, "đã test thật" if p.tested else "chưa test — cấm hứa thử / thực nghiệm",
                                     p.max_chars))
    run.targets = [{"keyword": k.keyword, "volume": k.search_volume, "competition": k.competition} for k in targets]
    log("Từ khóa mục tiêu: " + ", ".join(f"{k.keyword} ({k.search_volume})" for k in targets))
    seen: set[str] = set()

    def can_spend() -> bool:
        return client.credits_used + CREDITS_PER_CALL <= p.max_credits

    def enough() -> bool:
        return len(run.distinct_passed) >= max(p.stop_after_passed, 1)

    def score_batch(cands: list[tuple[str, Keyword]], round_no: int, source) -> bool:
        """False = dừng (đã đủ tiêu đề đạt / hết trần credit / lỗi vidIQ).
        source: chuỗi, hoặc list cùng độ dài cands (nguồn riêng từng tiêu đề)."""
        for i, (title, k) in enumerate(cands):
            if enough():
                log(f"🎯 Đã đủ {p.stop_after_passed} tiêu đề KHÁC NHAU đạt ngưỡng — dừng sớm, không chấm tiếp.")
                return False
            key = title.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            hit = cache.get(title, client.channel_id, p.title_type)
            if hit is not None:
                score, raw, cached = hit["score"], "", True
                log(f"  [cache] {score} — {title}")
            else:
                if not can_spend():
                    log(f"⛔ Dừng: chạm trần {p.max_credits} credit cho lần chạy này.")
                    return False
                try:
                    score, raw = client.score_title(title, p.title_type)
                except Exception as e:  # VidIQError hoặc lỗi mạng bất ngờ — dừng, giữ kết quả đã có
                    log(f"⛔ Lỗi vidIQ: {e}")
                    return False
                cached = False
                cache.set(title, client.channel_id, p.title_type, score)
                log(f"  [vidIQ] {score if score is not None else '?'} — {title}")
            src = source[i] if isinstance(source, list) else source
            run.results.append(ScoredTitle(title=title, score=score, keyword=k.keyword, volume=k.search_volume,
                                           round=round_no, source=src, cached=cached, raw=raw[:400]))
            if memory is not None:
                memory.add(title, score, k.keyword, niche, src, client.channel_id, p.title_type, vocab=vocab)
                memory.save()
        if enough():
            log(f"🎯 Đã đủ {p.stop_after_passed} tiêu đề KHÁC NHAU đạt ngưỡng — dừng.")
            return False
        return True

    niche = next((k.keyword for k in keywords if k.role == "Chính"), targets[0].keyword if targets else "")
    vocab = niche_vocab(keywords, niche)

    # Vòng 1 — khung điểm cao đã học (nếu có) TRƯỚC, rồi tới tiêu đề ghép từ 10 công thức
    run.rounds = 1
    learned = memory_drafts(memory, targets, opts, p, keywords)
    formula = [(t, k) for t, k in initial_drafts(keywords, targets, opts, p)
               if t.lower() not in {x[0].lower() for x in learned}]
    drafts = [(t, k) for t, k, _ in learned] + formula
    drafts = drafts[:max(p.n_titles, len(learned))]
    sources = [f"Mẫu đã học ({sc:g})" if sc >= 0 else f"Mẫu từ sản phẩm khác ({-sc:g})" for _, _, sc in learned] \
        + ["Công thức"] * (len(drafts) - len(learned))
    log(f"Vòng 1: {len(learned)} tiêu đề từ khung điểm cao đã học + {len(drafts) - len(learned)} tiêu đề "
        "từ 10 công thức (khung đã học chấm trước).")
    ok = score_batch(drafts, 1, sources)

    for round_no in range(2, p.max_rounds + 1):
        if not ok or enough():
            break
        if not run.samples:
            cached_samples = cache.get_samples(targets[0].keyword, client.channel_id, p.language)
            if cached_samples:
                run.samples = cached_samples
                log(f"[cache] Dùng lại {len(run.samples)} mẫu tiêu đề vidIQ đã lấy trước đó.")
            elif can_spend():
                try:
                    run.samples, _ = client.generate_titles(targets[0].keyword, p.language)
                    cache.set_samples(targets[0].keyword, client.channel_id, p.language, run.samples)
                    log(f"Lấy {len(run.samples)} mẫu tiêu đề điểm cao từ vidIQ cho '{targets[0].keyword}'.")
                except VidIQError as e:
                    log(f"⚠️ Không lấy được mẫu từ vidIQ: {e}")
        if text_client is None:
            log("⚠️ Không có Groq để viết lại — dừng sau vòng 1.")
            break
        scored = sorted([r for r in run.results if r.score is not None], key=lambda r: r.score, reverse=True)
        prompt = render(
            "08_vidiq_rewrite.md",
            TOP_N=p.top_n,
            PRODUCTS_JSON=json.dumps(p.product_names[:8], ensure_ascii=False),
            TESTED_NOTE=("Người làm video ĐÃ dùng / test thật các sản phẩm — được nói trải nghiệm thật."
                         if p.tested else "Người làm video CHƯA test: CẤM 'tôi thử', 'test', 'thực nghiệm', "
                         "'trải nghiệm X ngày', 'kiểm tra thực tế', 'đập hộp'."),
            TARGETS_JSON=json.dumps(run.targets, ensure_ascii=False),
            SCORED_JSON=json.dumps([{"title": r.title, "score": r.score} for r in scored[:15]], ensure_ascii=False),
            GOAL_NOTE=_goal_note(run, p),
            SAMPLES_JSON=json.dumps(run.samples[:12], ensure_ascii=False),
            LEARNED_JSON=json.dumps(memory.examples(p.title_type, [k.keyword for k in keywords], niche)
                                    if memory else {}, ensure_ascii=False),
            N_TITLES=p.n_titles, THRESHOLD=p.threshold, MAX_CHARS=p.max_chars, CURRENT_YEAR=opts.year,
            MEDICAL_NOTE=("Sản phẩm sức khỏe/y tế: TUYỆT ĐỐI không dùng 'chữa khỏi', '100%', 'thay thế bác sĩ'."
                          if is_medical(keywords) else ""),
        )
        try:
            data = text_client.generate_json(prompt)
        except (LLMJSONError, LLMQuotaExceededError, LLMServerError) as e:
            log(f"⛔ Groq lỗi khi viết lại: {e}")
            break
        except Exception as e:  # mất mạng, lỗi SDK... — không được làm mất các điểm vidIQ đã trả credit
            log(f"⛔ Groq lỗi khi viết lại ({type(e).__name__}): {e}")
            break
        cands, dropped = [], []
        for item in data.get("titles", []):
            title = " ".join(str(item.get("title", "")).split())
            k = _matching_target(title, targets)
            if not title or k is None:
                continue
            bad = content_issues(title, p)
            if bad:
                dropped.append(f"{title} ({'; '.join(bad)})")
                continue
            cands.append((title, k))
        for d in dropped:
            log(f"  [bỏ — không hợp nội dung review] {d}")
        run.rounds = round_no
        log(f"Vòng {round_no}: Groq viết lại {len(cands)} tiêu đề hợp lệ theo mẫu điểm cao — chấm lại.")
        if not cands:
            break
        ok = score_batch(cands, round_no, "Groq viết lại")

    run.results.sort(key=lambda r: (r.score is not None, r.score or 0), reverse=True)
    run.reached = len(run.distinct_passed) >= max(p.stop_after_passed, 1)
    run.credits_used = client.credits_used
    run.finished_at = datetime.now().isoformat(timespec="seconds")
    n_ok = len(run.distinct_passed)
    if run.reached:
        log(f"✅ {n_ok} tiêu đề khác nhau đạt ≥ {p.threshold} điểm vidIQ. Tốn {run.credits_used} credit.")
    elif n_ok:
        log(f"⚠️ Mới có {n_ok}/{p.stop_after_passed} tiêu đề khác nhau đạt ≥ {p.threshold} sau {run.rounds} vòng "
            f"(hết vòng / trần credit). Tốn {run.credits_used} credit.")
    else:
        best = next((r for r in run.results if r.score is not None), None)
        log(f"❌ Chưa có tiêu đề nào đạt {p.threshold} sau {run.rounds} vòng"
            + (f" — tốt nhất {best.score}: {best.title}" if best else "") + f". Tốn {run.credits_used} credit.")
    return run
