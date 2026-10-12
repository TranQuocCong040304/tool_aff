"""Phân tích file CSV từ khóa -> từ khóa chính / phụ / kéo traffic / đuôi dài /
loại bỏ, rồi ghép thành tiêu đề SEO theo 10 công thức tăng CTR (xếp hạng ưu
tiên) cho video review sản phẩm affiliate trên YouTube.

Toàn bộ chạy OFFLINE bằng luật (không gọi API, không tốn quota). Có thêm 1 bước
tùy chọn nhờ Groq viết thêm tiêu đề (prompts/07_seo_titles.md) — kết quả AI
cũng được chấm điểm bằng đúng bộ luật ở đây.

Công thức tổng quát:
    [TỪ KHÓA CHÍNH] + [NGƯỜI DÙNG / TÌNH HUỐNG] + [MÓC CẢM XÚC] + [BẰNG CHỨNG / THỜI GIAN]

Đầu vào: CSV export từ tool nghiên cứu keyword (vd "..._matching_terms.csv",
"..._related_keywords.csv") — bắt buộc cột "Keyword", các cột "Search volume",
"Competition", "Overall" có thì dùng.
"""
from __future__ import annotations

import csv
import io
import math
import re
import statistics
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

# ------------------------------------------------------------ hằng số --

ROLE_MAIN = "Chính"
ROLE_TRAFFIC = "Kéo traffic"
ROLE_SECONDARY = "Phụ"
ROLE_LONGTAIL = "Đuôi dài"
ROLE_TAG = "Tag không dấu"
ROLE_NOISE = "Loại"
ROLES = [ROLE_MAIN, ROLE_TRAFFIC, ROLE_SECONDARY, ROLE_LONGTAIL, ROLE_TAG, ROLE_NOISE]
ROLE_ORDER = {r: i for i, r in enumerate(ROLES)}

GROUP_PRODUCT = "Sản phẩm / biến thể"
GROUP_AUDIENCE = "Đối tượng"
GROUP_BUY = "Mua hàng / giá"
GROUP_REVIEW = "Đánh giá / tốt nhất"
GROUP_GUIDE = "Hướng dẫn"
GROUP_PROBLEM = "Vấn đề / nhu cầu"
GROUP_BRAND = "Thương hiệu"
GROUP_OTHER = "Khác"
GROUPS = [GROUP_PRODUCT, GROUP_AUDIENCE, GROUP_BUY, GROUP_REVIEW, GROUP_GUIDE,
          GROUP_PROBLEM, GROUP_BRAND, GROUP_OTHER]

# Cụm nhận diện ý định (so khớp trên chữ CÓ DẤU, chữ thường, theo ranh giới từ —
# không so trên chữ bỏ dấu vì "giá" và "già" sẽ trùng nhau thành "gia").
_BUY_PHRASES = ["giá bao nhiêu", "bao nhiêu tiền", "giá rẻ", "giá", "mua ở đâu", "mua", "bán",
                "khuyến mãi", "sale", "chính hãng", "rẻ", "bảng giá"]
_REVIEW_PHRASES = ["tốt nhất", "loại nào tốt", "loại nào", "có tốt không", "review", "đánh giá",
                   "nên mua", "top", "so sánh", "vs", "hay nhất", "tốt", "nên dùng"]
_GUIDE_PHRASES = ["cách", "hướng dẫn", "sử dụng", "cài đặt", "kết nối", "vệ sinh", "sửa",
                  "thay pin", "đeo", "chọn"]
_PROBLEM_PHRASES = ["bị", "kém", "yếu", "đau", "hỏng", "lỗi", "khó", "mất", "ù", "nặng tai",
                    "lãng tai", "điếc"]
# Dấu hiệu tên cửa hàng / đơn vị bán -> loại
_STORE_MARKERS = ["shop", "store", "medical", "siêu thị", "cửa hàng", "nhà thuốc", "official",
                  "mall", "showroom", "đại lý", "công ty", "tphcm", "hà nội", "hcm", "24h"]
# Từ tiếng Anh/không dấu phổ biến, KHÔNG coi là tên thương hiệu
_COMMON_ASCII = {"mini", "pro", "max", "plus", "lite", "bluetooth", "usb", "sale", "review", "top",
                 "vs", "wifi", "led", "smart", "air", "type", "c", "ultra", "new", "set", "combo"}
# Từ quá chung, không dùng để xét "có liên quan tới sản phẩm"
_GENERIC_TOKENS = {"cho", "của", "và", "với", "có", "không", "loại", "nào", "tốt", "nhất", "giá",
                   "mua", "bán", "rẻ", "cách", "bị", "máy", "đồ", "cái", "chiếc", "bộ", "mới",
                   "hiện", "nay", "là", "gì", "các", "những", "nên", "dùng", "sử", "dụng"}
# Phần đuôi cần cắt để lấy "tên sản phẩm" từ 1 từ khóa
_STRIP_SUFFIXES = ["giá bao nhiêu tiền", "giá bao nhiêu", "bao nhiêu tiền", "loại nào tốt nhất",
                   "loại nào tốt", "loại nào", "tốt nhất hiện nay", "tốt nhất", "hiện nay",
                   "có tốt không", "giá rẻ", "bảng giá", "chính hãng", "giá", "review", "đánh giá",
                   "mua ở đâu", "ở đâu", "nên mua", "nào tốt"]
_STRIP_PREFIXES = ["cách sử dụng", "hướng dẫn sử dụng", "cách chọn", "cách dùng", "cách đeo",
                   "hướng dẫn", "cách", "review", "đánh giá", "top", "giá", "mua"]

# Cụm KHÔNG được dùng trong tiêu đề (dễ bị YouTube hạn chế / mất niềm tin),
# đặc biệt với sản phẩm sức khỏe, y tế.
BANNED_PHRASES = ["chữa khỏi", "chữa điếc", "chữa bệnh", "điều trị", "khỏi hẳn", "khỏi 100%",
                  "100%", "thần kỳ", "thần dược", "thay thế bác sĩ", "vĩnh viễn", "tuyệt đối",
                  "cam kết khỏi", "hết bệnh"]
_MEDICAL_MARKERS = ["trợ thính", "huyết áp", "đường huyết", "thực phẩm chức năng", "y tế", "nhiệt kế",
                    "xông", "massage", "mát xa", "vật lý trị liệu", "thuốc", "chức năng", "khí dung",
                    "oxy", "spo2", "nẹp", "đai lưng", "thuốc"]

# 10 công thức (mã gốc CT1..CT10 giữ đúng như tài liệu), xếp theo ưu tiên CTR.
FORMULAS = {
    "CT2": {"rank": 1, "name": "Đối tượng + Lợi ích", "pattern": "[Sản phẩm] Cho [Đối tượng]: [Lợi ích cụ thể]",
            "why": "Người xem thấy ngay “dành cho mình / bố mẹ mình” — tiêu đề nhắm đúng người nên CTR cao nhất."},
    "CT1": {"rank": 2, "name": "Hỏi giá", "pattern": "[Sản phẩm] Giá Bao Nhiêu? [Bảng giá / Năm]",
            "why": "Trùng đúng câu người ta gõ tìm kiếm, ý định mua rõ nhất — dễ ra đơn affiliate."},
    "CT8": {"rank": 3, "name": "Quà tặng người thân", "pattern": "Tặng [Người thân] [Sản phẩm]: [Câu hỏi / bằng chứng]",
            "why": "Người mua thường là con cái, không phải người dùng — ít đối thủ, cảm xúc mạnh."},
    "CT4": {"rank": 4, "name": "Top danh sách", "pattern": "Top [Số] [Sản phẩm] [Đáng Mua / Tốt Nhất] [Năm] (Đã Test)",
            "why": "Có số + “tốt nhất” nên người xem biết trước sẽ nhận được gì; khớp format video Top 5."},
    "CT3": {"rank": 5, "name": "Dùng thử thực tế", "pattern": "Dùng Thử [Sản phẩm] [Số ngày]: [Có Đáng Tiền?]",
            "why": "Tạo tò mò + niềm tin — trải nghiệm thật là lý do người ta bấm xem."},
    "CT6": {"rank": 6, "name": "Cảnh báo / sai lầm", "pattern": "[Số] Sai Lầm Khi Mua [Sản phẩm]",
            "why": "CTR có thể rất cao nhưng chỉ dùng khi video thật sự nói về các sai lầm đó."},
    "CT5": {"rank": 7, "name": "So sánh", "pattern": "[Sản phẩm A] vs [Sản phẩm B]: Chọn Loại Nào?",
            "why": "Người đã quyết mua và đang cân nhắc — ít view hơn nhưng chất lượng cao."},
    "CT9": {"rank": 8, "name": "Vấn đề → giải pháp", "pattern": "[Vấn đề] Nên Dùng Gì? [Sản phẩm] Có Giúp Được Không?",
            "why": "Kéo người mới, nhưng ý định mua còn mơ hồ."},
    "CT7": {"rank": 9, "name": "Hướng dẫn", "pattern": "Cách [Hành động] [Sản phẩm] Đúng Cách + [Đối tượng]",
            "why": "CTR thấp hơn (người xem đã mua) nhưng kéo traffic dài hạn."},
    "CT10": {"rank": 10, "name": "Mở hộp / thương hiệu", "pattern": "Mở Hộp [Brand / Sản phẩm]: [Giá] + [Nhận xét]",
             "why": "Chỉ hiệu quả khi người xem đã biết thương hiệu và bạn có link affiliate đúng hãng."},
    "COMBO": {"rank": 0, "name": "Ghép 2 công thức top", "pattern": "[Từ khóa] Cho [Đối tượng]: Giá Bao Nhiêu? …",
              "why": "Ghép 2 công thức hàng đầu: giữ từ khóa ở đầu + đúng 1 móc cảm xúc."},
}
_FORMULA_BASE_SCORE = {0: 30, 1: 30, 2: 28, 3: 26, 4: 25, 5: 23, 6: 18, 7: 16, 8: 14, 9: 10, 10: 8}

# Từ khóa PHỤ THUỘC sản phẩm thực tế: chỉ nên dùng khi sản phẩm review đúng là như vậy
# (vd không có máy xuất xứ Nhật thì đừng nhắm "nhật bản" chỉ vì có volume).
_ORIGIN_MARKERS = ["nhật bản", "nhật", "hàn quốc", "mỹ", "đức", "nhập khẩu", "trung quốc", "thụy sĩ",
                   "đan mạch", "úc", "châu âu", "xách tay", "singapore", "thái lan", "pháp", "anh quốc"]
_SPECIAL_AUDIENCE_MARKERS = ["trẻ em", "cho bé", "em bé", "trẻ nhỏ", "sơ sinh", "học sinh", "bà bầu", "mẹ bầu",
                             "thú cưng", "chó", "mèo"]
STATUS_USE = "✅ Dùng"
STATUS_CHECK = "⚠️ Cần xác nhận"
STATUS_REFERENCE = "📝 Tham khảo"
STATUS_NOISE = "❌ Loại"
COMPETITOR_TITLE_WORDS = 8  # từ khóa dài hơn = thường là nguyên tiêu đề video đối thủ

DEFAULT_BENEFITS = ["Mình Test Thực Tế", "Có Đáng Tiền?", "Review Thật Sau 7 Ngày"]


# ------------------------------------------------------------ tiện ích --

def strip_accents(text: str) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def _norm(text: str) -> str:
    """Chữ thường, NFC, gộp khoảng trắng — để so khớp có dấu."""
    text = unicodedata.normalize("NFC", str(text)).lower()
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^\w]+", _norm(text)) if t]


def _has_phrase(text_norm: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text_norm) is not None


def _contains_seq(haystack: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return n > 0 and any(haystack[i:i + n] == needle for i in range(len(haystack) - n + 1))


def _parse_int(raw) -> Optional[int]:
    if raw is None or str(raw).strip() == "":
        return None
    s = str(raw).strip().lower()
    mult = 1
    if s.endswith("k"):
        mult, s = 1000, s[:-1]
        try:
            return int(float(s.replace(",", ".")) * mult)
        except ValueError:
            return None
    digits = re.sub(r"[^\d]", "", s)
    return int(digits) if digits else None


def _parse_float(raw) -> Optional[float]:
    if raw is None or str(raw).strip() == "":
        return None
    s = str(raw).strip()
    if "," in s and "." not in s:  # "17,9" kiểu Việt Nam
        s = s.replace(",", ".")
    s = re.sub(r"[^\d.]", "", s)
    try:
        return float(s) if s else None
    except ValueError:
        return None


def vn_title_case(text: str) -> str:
    """Viết hoa chữ cái đầu mỗi từ (kiểu tiêu đề YouTube), giữ nguyên 'vs' và
    các từ đã có chữ hoa/số bên trong (mã model, thương hiệu)."""
    out = []
    for word in text.split(" "):
        if not word or word.lower() == "vs":
            out.append(word.lower() if word else word)
            continue
        if any(c.isupper() for c in word[1:]):
            out.append(word)
            continue
        for i, c in enumerate(word):
            if c.isalpha():
                word = word[:i] + c.upper() + word[i + 1:]
                break
        out.append(word)
    return " ".join(out)


def seed_from_filename(name: str) -> Optional[str]:
    """'Tai-nghe-trợ-thính-không-dây_related_keywords.csv' -> 'tai nghe trợ thính không dây'."""
    stem = Path(name).stem
    stem = re.sub(r"\s*\(\d+\)$", "", stem)  # "abc (1).csv" khi tải trùng tên
    # file do chính tool xuất ra: "tu-khoa_<từ khóa chính>.csv", "tieu-de-seo_<…>.csv"
    stem = re.sub(r"^(tu-khoa|tieu-de-seo|seo)_", "", stem, flags=re.IGNORECASE)
    stem = re.split(r"_(matching|related|keyword|questions|search)", stem, flags=re.IGNORECASE)[0]
    seed = _norm(stem.replace("-", " ").replace("_", " "))
    return seed or None


# ------------------------------------------------------------ đọc CSV --

@dataclass
class Keyword:
    keyword: str
    search_volume: Optional[int] = None
    competition: Optional[float] = None
    overall: Optional[float] = None
    sources: list[str] = field(default_factory=list)
    role: str = ROLE_LONGTAIL
    group: str = GROUP_OTHER
    audience: Optional[str] = None
    product_part: Optional[str] = None
    golden: bool = False
    opportunity: float = 0.0
    note: str = ""                 # lý do giữ / loại (hiện ở cột "Lý do giữ/loại")
    volume_raw: str = ""           # volume NGUYÊN VĂN từ file nguồn, vd "3673" hoặc "<750"
    volume_bound: Optional[int] = None  # với "<750": cận trên 750 (KHÔNG phải volume thật)
    use: bool = True               # có đưa vào tiêu đề / tag / vidIQ không
    status: str = ""
    edited: bool = False           # người dùng đã sửa tay trên bảng
    import_use: Optional[bool] = None   # cột "Dùng" trong file "Từ khóa đã phân loại" tải lên lại
    import_role: Optional[str] = None   # cột "Vai trò" trong file đó

    @property
    def volume_display(self) -> str:
        if self.volume_raw:
            return self.volume_raw
        return "" if self.search_volume is None else str(self.search_volume)


def read_keyword_csvs(files: Iterable[tuple[str, bytes]]) -> tuple[list[Keyword], list[str]]:
    """files: [(tên file, nội dung bytes)]. Trả về (danh sách từ khóa đã gộp/dedupe,
    danh sách seed đoán từ tên file)."""
    merged: dict[str, Keyword] = {}
    seeds: list[str] = []
    for name, data in files:
        seed = seed_from_filename(name)
        if seed and seed not in seeds:
            seeds.append(seed)
        text = data.decode("utf-8-sig", errors="replace")
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        fields = {(c or "").strip().lower(): c for c in (reader.fieldnames or [])}
        kw_col = fields.get("keyword") or fields.get("từ khóa") or fields.get("keywords")
        if not kw_col:
            raise ValueError(f"{name}: cần có cột 'Keyword' (đang có: {list(fields.values())}).")
        # Nhận cả file "Từ khóa đã phân loại (.csv)" do chính tool xuất (cột Volume gốc / Dùng / Vai trò):
        # "Volume gốc" giữ nguyên chữ nguồn (kể cả "<750") nên ưu tiên hơn "Volume (số)".
        vol_col = (fields.get("search volume") or fields.get("volume gốc") or fields.get("volume")
                   or fields.get("volume (số)") or fields.get("lượng tìm kiếm"))
        comp_col = fields.get("competition") or fields.get("cạnh tranh")
        overall_col = fields.get("overall")
        use_col = fields.get("dùng")
        role_col = fields.get("vai trò")
        for row in reader:
            raw = (row.get(kw_col) or "").strip()
            if not raw:
                continue
            kw_text = re.sub(r"\s+", " ", unicodedata.normalize("NFC", raw))
            key = _norm(kw_text)
            entry = merged.setdefault(key, Keyword(keyword=kw_text))
            if name not in entry.sources:
                entry.sources.append(name)
            if entry.search_volume is None and not entry.volume_raw and vol_col:
                raw_vol = (row.get(vol_col) or "").strip()
                entry.volume_raw = raw_vol
                if re.match(r"^[<>≤≥]", raw_vol):
                    # Tool keyword ghi "<750" = dưới ngưỡng hiển thị, KHÔNG biết số thật.
                    # Không được coi là 750 (sẽ lọt ngưỡng volume >= 750 và sai xếp hạng)
                    # -> search_volume để trống, chỉ lưu cận trên để tham khảo.
                    entry.search_volume = None
                    entry.volume_bound = _parse_int(raw_vol)
                else:
                    entry.search_volume = _parse_int(raw_vol)
            if entry.competition is None and comp_col:
                entry.competition = _parse_float(row.get(comp_col))
            if entry.overall is None and overall_col:
                entry.overall = _parse_float(row.get(overall_col))
            if use_col and entry.import_use is None:
                val = str(row.get(use_col) or "").strip().lower()
                if val in ("true", "false", "1", "0", "có", "không", "x", ""):
                    entry.import_use = val in ("true", "1", "có", "x")
            if role_col and entry.import_role is None and (row.get(role_col) or "").strip() in ROLES:
                entry.import_role = row[role_col].strip()
    keywords = sorted(merged.values(), key=lambda k: k.search_volume or 0, reverse=True)
    if not keywords:
        raise ValueError("Không đọc được từ khóa nào từ các file CSV.")
    return keywords, seeds


# ------------------------------------------------------------ phân loại --

def product_part(keyword: str) -> str:
    """Cắt phần ý định để lấy tên sản phẩm: 'máy trợ thính giá bao nhiêu' ->
    'máy trợ thính', 'tai nghe trợ thính cho người già' -> 'tai nghe trợ thính'."""
    text = _norm(keyword)
    text = re.split(r"(?<!\w)cho(?!\w)", text)[0].strip() or text
    changed = True
    while changed:
        changed = False
        for suf in _STRIP_SUFFIXES:
            if text.endswith(" " + suf):
                text = text[: -len(suf) - 1].strip()
                changed = True
        for pre in _STRIP_PREFIXES:
            if text.startswith(pre + " "):
                text = text[len(pre) + 1:].strip()
                changed = True
    return text


def audience_of(keyword: str) -> Optional[str]:
    text = _norm(keyword)
    m = re.search(r"(?<!\w)cho (.+)$", text)
    if not m:
        return None
    aud = m.group(1)
    for suf in _STRIP_SUFFIXES:
        if aud.endswith(" " + suf):
            aud = aud[: -len(suf) - 1]
    return aud.strip() or None


def _pick_main(keywords: list[Keyword], seeds: list[str]) -> Keyword:
    by_norm = {_norm(k.keyword): k for k in keywords}
    by_ascii = {strip_accents(_norm(k.keyword)): k for k in keywords}
    for seed in seeds:
        if seed in by_norm:
            return by_norm[seed]
        if strip_accents(seed) in by_ascii:
            return by_ascii[strip_accents(seed)]
    # Không khớp tên file: chọn từ khóa "gốc" (nằm trong nhiều từ khóa khác) có điểm tốt nhất.
    toks = {id(k): _tokens(k.keyword) for k in keywords}

    def contained_count(k: Keyword) -> int:
        t = toks[id(k)]
        return sum(1 for o in keywords if o is not k and _contains_seq(toks[id(o)], t))

    def strength(k: Keyword) -> float:
        if k.overall is not None:
            return k.overall
        return (k.search_volume or 0) / (1 + (k.competition or 0))

    roots = [k for k in keywords if contained_count(k) >= 1 and len(toks[id(k)]) >= 2]
    pool = roots or keywords
    return max(pool, key=strength)


def _conditional_reason(k: "Keyword", norm: str, main_norm: str, product_names_ascii: list[str],
                        product_names: list[str]) -> Optional[tuple[bool, str]]:
    """Từ khóa phụ thuộc thuộc tính sản phẩm (xuất xứ / đối tượng đặc biệt / thương hiệu).
    Trả về (đã khớp sản phẩm?, lý do) hoặc None nếu không phải loại từ khóa này."""
    kind, marker = None, None
    for m in _ORIGIN_MARKERS:
        if _has_phrase(norm, m) and not _has_phrase(main_norm, m):
            kind, marker = "xuất xứ", m
            break
    if kind is None:
        for m in _SPECIAL_AUDIENCE_MARKERS:
            if _has_phrase(norm, m) and not _has_phrase(main_norm, m):
                kind, marker = "đối tượng đặc biệt", m
                break
    if kind is None and k.group == GROUP_BRAND:
        extra = [t for t in _tokens(k.keyword) if strip_accents(t) not in
                 {strip_accents(x) for x in _tokens(main_norm)}]
        if extra:
            kind, marker = "thương hiệu", " ".join(extra)
    if kind is None:
        return None
    if not product_names:
        return False, (f"Phụ thuộc sản phẩm thực tế ({kind}: “{marker}”) — chưa có file JSON sản phẩm để đối chiếu. "
                       "Chỉ tick Dùng nếu video thật sự có sản phẩm / nhu cầu này.")
    marker_ascii = strip_accents(marker)
    for name, name_ascii in zip(product_names, product_names_ascii):
        if re.search(rf"(?<!\w){re.escape(marker_ascii)}(?!\w)", name_ascii):
            return True, f"Phụ thuộc sản phẩm ({kind}: “{marker}”) — có sản phẩm khớp: {name[:60]}"
    return False, (f"Phụ thuộc sản phẩm thực tế ({kind}: “{marker}”) — không sản phẩm nào trong file JSON khớp. "
                   "Chỉ tick Dùng nếu video thật sự có sản phẩm / nhu cầu này.")


def classify(keywords: list[Keyword], main_keyword: Optional[str] = None, seeds: Optional[list[str]] = None,
             n_traffic: int = 5, product_names: Optional[list[str]] = None) -> list[Keyword]:
    """Gán role + group + trạng thái dùng cho từng từ khóa (trả lại list đã sắp xếp).

    product_names: tên các sản phẩm sẽ review (từ JSON sản phẩm / run đang chọn) để
    xác nhận các từ khóa phụ thuộc xuất xứ, đối tượng đặc biệt, thương hiệu."""
    product_names = [n for n in (product_names or []) if n]
    product_names_ascii = [strip_accents(_norm(n)) for n in product_names]
    seeds = list(seeds or [])
    imported_main = next((k.keyword for k in keywords if k.import_role == ROLE_MAIN), None)
    if imported_main:
        seeds.insert(0, _norm(imported_main))  # file đã phân loại: giữ đúng từ khóa chính bạn đã chốt
    if main_keyword:
        seeds.insert(0, _norm(main_keyword))
    main = _pick_main(keywords, seeds)
    if main_keyword and _norm(main.keyword) != _norm(main_keyword):
        main = Keyword(keyword=main_keyword.strip())  # người dùng tự gõ từ khóa không có trong CSV
        keywords = [main] + keywords

    main_tokens = _tokens(main.keyword)
    vocab = {strip_accents(t) for t in main_tokens if t not in _GENERIC_TOKENS}
    main_ascii_tokens = {strip_accents(t) for t in main_tokens}

    vols = [k.search_volume for k in keywords if k.search_volume]
    comps = [k.competition for k in keywords if k.competition is not None]
    median_comp = statistics.median(comps) if comps else None
    ascii_seen: dict[str, Keyword] = {}

    main_norm = _norm(main.keyword)
    for k in keywords:
        k.role, k.note, k.golden, k.use, k.status, k.edited = ROLE_LONGTAIL, "", False, True, "", False
        norm = _norm(k.keyword)
        toks = _tokens(k.keyword)
        ascii_toks = [strip_accents(t) for t in toks]
        k.audience = audience_of(k.keyword)
        k.product_part = product_part(k.keyword)
        if k.search_volume is not None:
            k.opportunity = round((k.search_volume or 0) / (1 + (k.competition or 0)), 1)

        # nhóm ý định
        if k is main:
            k.group = GROUP_PRODUCT
        elif any(_has_phrase(norm, p) for p in _BUY_PHRASES):
            k.group = GROUP_BUY
        elif any(_has_phrase(norm, p) for p in _GUIDE_PHRASES) and toks[0] in {"cách", "hướng", "chọn", "sử"}:
            k.group = GROUP_GUIDE
        elif k.audience:
            k.group = GROUP_AUDIENCE
        elif any(_has_phrase(norm, p) for p in _REVIEW_PHRASES):
            k.group = GROUP_REVIEW
        elif any(_has_phrase(norm, p) for p in _GUIDE_PHRASES):
            k.group = GROUP_GUIDE
        else:
            extra = [t for t in ascii_toks if t not in main_ascii_tokens]
            is_ascii_word = [t for t in toks if t.isascii() and t.isalpha() and t not in _COMMON_ASCII
                             and strip_accents(t) not in main_ascii_tokens]
            if (main_ascii_tokens.issubset(set(ascii_toks)) and extra and len(is_ascii_word) == len(extra)
                    and not any(t != strip_accents(t) for t in toks if strip_accents(t) in extra)):
                k.group = GROUP_BRAND
            elif any(_has_phrase(norm, p) for p in _PROBLEM_PHRASES):
                k.group = GROUP_PROBLEM
            else:
                k.group = GROUP_PRODUCT

        if k is main:
            k.role, k.note = ROLE_MAIN, "Trùng tên sản phẩm — luôn đặt trong 5 từ đầu tiêu đề"
            continue

        # loại bỏ (KHÔNG xóa khỏi dữ liệu — chỉ bỏ tick Dùng, vẫn xuất ra file để rà soát lại)
        relevant = bool(vocab & set(ascii_toks))
        weak_metrics = k.overall is not None and k.overall < 30 and (k.competition or 0) > 40
        if any(_has_phrase(norm, m) for m in _STORE_MARKERS):
            k.role, k.note = ROLE_NOISE, "Tên cửa hàng / đơn vị bán — loại, trừ khi bạn đang nghiên cứu đối thủ"
        elif re.search(r"(?<!\w)[a-z]{2,}\s?\d{3,}(?!\w)", norm) and not relevant:
            k.role, k.note = ROLE_NOISE, "Mã sản phẩm / mã shop — chỉ giữ nếu đúng mã sản phẩm bạn review"
        elif not relevant:
            k.role, k.note = ROLE_NOISE, "Quá rộng / lạc đề — giữ trong dữ liệu thô, không nhắm làm mục tiêu"
        elif weak_metrics and k.group != GROUP_PROBLEM:
            k.role, k.note = ROLE_NOISE, "Cạnh tranh cao, điểm Overall thấp"
        elif weak_metrics:
            # Không loại chỉ vì 1-2 chỉ số: từ khóa VẤN ĐỀ (vd "bị lãng tai") vẫn có giá trị theo ngữ cảnh.
            k.use, k.status = False, STATUS_CHECK
            k.note = (f"Overall thấp ({k.overall:.0f}), cạnh tranh cao ({k.competition:g}) nhưng đúng vấn đề người xem "
                      "gặp — tick Dùng làm từ khóa phụ nếu video có nói về vấn đề này")

        if k.role != ROLE_NOISE and len(toks) > COMPETITOR_TITLE_WORDS:
            k.use, k.status = False, STATUS_REFERENCE
            k.note = ("Cụm quá dài, giống tiêu đề đối thủ — chỉ tham khảo góc nội dung, "
                      "không dùng làm từ khóa mục tiêu / nguyên văn")

        if k.role != ROLE_NOISE and k.use:
            cond = _conditional_reason(k, norm, main_norm, product_names_ascii, product_names)
            if cond is not None:
                matched, reason = cond
                k.note = reason
                if not matched:
                    k.use, k.status = False, STATUS_CHECK

        # bản không dấu của 1 từ khóa có dấu -> chỉ dùng làm tag
        key_ascii = " ".join(ascii_toks)
        if k.role != ROLE_NOISE and norm == key_ascii and any(t != strip_accents(t) for t in main_tokens):
            k.role, k.note = ROLE_TAG, "Bản không dấu — đưa vào tag"
        if k.role != ROLE_NOISE:
            ascii_seen.setdefault(key_ascii, k)

    # Chỉ từ khóa đang Dùng, có volume THẬT (không tính "<750") mới được xếp kéo traffic / phụ.
    relevant_kws = [k for k in keywords if k.role == ROLE_LONGTAIL and k.use
                    and len(_tokens(k.keyword)) <= COMPETITOR_TITLE_WORDS]
    # Kéo traffic: top N theo volume
    by_vol = sorted(relevant_kws, key=lambda k: k.search_volume or 0, reverse=True)
    for k in by_vol[:n_traffic]:
        if k.search_volume:
            k.role = ROLE_TRAFFIC
    # Phụ: volume >= 50% của từ khóa chính (hoặc >= trung vị nếu không có volume chính)
    if main.search_volume:
        threshold = main.search_volume * 0.5
    else:
        threshold = statistics.median(vols) if vols else 0
    for k in relevant_kws:
        if k.role == ROLE_LONGTAIL and (k.search_volume or 0) >= threshold and threshold > 0:
            k.role = ROLE_SECONDARY
    # Điểm vàng: volume >= ngưỡng phụ và cạnh tranh <= trung vị
    for k in keywords:
        if (k.role in (ROLE_TRAFFIC, ROLE_SECONDARY, ROLE_MAIN) and median_comp is not None
                and k.competition is not None and k.competition <= median_comp
                and (k.search_volume or 0) >= threshold):
            k.golden = True
        if k.role == ROLE_LONGTAIL and k.competition is not None and median_comp is not None \
                and k.competition <= median_comp and not k.note:
            k.note = "Ít cạnh tranh — dùng cho mô tả + tag"
        if not k.note:
            if k.role == ROLE_TRAFFIC:
                k.note = f"Top {n_traffic} volume trong nhóm liên quan — ghép vào tiêu đề để kéo traffic"
            elif k.role == ROLE_SECONDARY:
                k.note = "Volume ≥ 50% từ khóa chính — đưa vào tiêu đề phụ / 2 dòng đầu mô tả"
            elif k.role == ROLE_LONGTAIL and k.volume_bound is not None:
                k.note = f"Volume {k.volume_raw} (dưới ngưỡng hiển thị, không rõ số thật) — dùng cho mô tả + tag"
            elif k.role == ROLE_LONGTAIL:
                k.note = "Volume thấp hơn nhóm phụ — dùng cho mô tả + tag"
        if k.role == ROLE_NOISE:
            k.use = False
        # File "Từ khóa đã phân loại" tải lên lại: GIỮ quyết định Dùng / Vai trò bạn đã chỉnh trong file đó.
        if k.role != ROLE_MAIN and (k.import_use is not None or k.import_role):
            role = k.import_role if k.import_role and k.import_role != ROLE_MAIN else k.role
            use = k.import_use if k.import_use is not None else k.use
            if (role, use) != (k.role, k.use):
                k.note = f"Theo file đã phân loại bạn tải lên ({'giữ' if use else 'bỏ'}) — gợi ý gốc: {k.note}"
            k.role, k.use = role, bool(use and role != ROLE_NOISE)
        k.status = refresh_status(k)

    keywords.sort(key=lambda k: (ROLE_ORDER.get(k.role, 99), not k.use, -(k.search_volume or 0),
                                 -(k.overall or 0)))
    return keywords


def refresh_status(k: Keyword) -> str:
    if k.role == ROLE_NOISE:
        return STATUS_NOISE
    if k.use:
        return STATUS_USE
    return k.status if k.status in (STATUS_CHECK, STATUS_REFERENCE) else STATUS_CHECK


def is_usable(k: Keyword) -> bool:
    """Từ khóa được đưa vào tiêu đề / tag / vidIQ."""
    return k.use and k.role not in (ROLE_NOISE, ROLE_TAG)


def competitor_angles(keywords: list[Keyword]) -> list[Keyword]:
    """Cụm dài giống tiêu đề đối thủ — gợi ý góc nội dung, không phải từ khóa mục tiêu."""
    return [k for k in keywords if k.role != ROLE_NOISE and len(_tokens(k.keyword)) > COMPETITOR_TITLE_WORDS]


# ------------------------------------------------------------ ghép tiêu đề --

@dataclass
class TitleOptions:
    benefits: list[str] = field(default_factory=list)  # lợi ích nổi bật của sản phẩm
    gift_to: str = "Bố Mẹ"                               # người nhận quà (CT8)
    top_n: int = 5                                       # số sản phẩm trong video Top N
    trial_days: int = 7                                  # số ngày dùng thử (CT3)
    year: int = field(default_factory=lambda: datetime.now().year)
    max_chars: int = 60
    title_case: bool = True


@dataclass
class Title:
    text: str
    formula: str
    keyword: str
    search_volume: Optional[int] = None
    competition: Optional[float] = None
    score: int = 0
    chars: int = 0
    warnings: list[str] = field(default_factory=list)
    source: str = "Công thức"

    @property
    def formula_label(self) -> str:
        meta = FORMULAS.get(self.formula.split("+")[0], {})
        if "+" in self.formula:
            return f"{self.formula} (ghép)"
        return f"{self.formula} – {meta.get('name', '')}"


def _cap(text: str) -> str:
    return vn_title_case(text)


def _ctx(keywords: list[Keyword]) -> dict:
    """Rút ra các 'biến' để điền vào công thức từ bảng từ khóa đã phân loại."""
    usable = [k for k in keywords if is_usable(k)]
    main = next((k for k in keywords if k.role == ROLE_MAIN), usable[0] if usable else None)
    if main is None:
        raise ValueError("Không còn từ khóa nào để tạo tiêu đề (tất cả đều bị đánh dấu Loại).")

    def info(k: Keyword) -> dict:
        return {"kw": k.keyword, "vol": k.search_volume, "comp": k.competition}

    main_ascii = {strip_accents(t) for t in _tokens(main.keyword) if t not in _GENERIC_TOKENS}

    # Tên sản phẩm (gộp volume theo product_part)
    products: dict[str, dict] = {}
    for k in usable:
        part = k.product_part or product_part(k.keyword)
        if not part or not (main_ascii & {strip_accents(t) for t in _tokens(part)}):
            continue
        if k.group in (GROUP_PROBLEM, GROUP_GUIDE) and part != _norm(k.keyword):
            pass
        entry = products.setdefault(part, {"name": part, "vol": 0, "comp": None, "kw": k.keyword})
        entry["vol"] += k.search_volume or 0
        if _norm(k.keyword) == part:
            entry.update(kw=k.keyword, comp=k.competition)
    main_part = _norm(main.keyword)
    products.setdefault(main_part, {"name": main_part, "vol": main.search_volume or 0,
                                    "comp": main.competition, "kw": main.keyword})
    product_list = sorted(products.values(), key=lambda p: p["vol"], reverse=True)
    # sản phẩm "thật" từ khóa đúng nguyên văn (để biết volume/cạnh tranh khi chấm điểm)
    exact = {_norm(k.keyword): k for k in usable}
    for p in product_list:
        k = exact.get(p["name"])
        p["vol_exact"] = k.search_volume if k else None
        p["comp"] = k.competition if k else p["comp"]

    audiences = []
    for k in sorted(usable, key=lambda k: k.search_volume or 0, reverse=True):
        # bỏ đối tượng quá dài (thường là nguyên tiêu đề video của đối thủ lọt vào CSV)
        if k.audience and k.product_part and len(k.audience.split()) <= 4:
            audiences.append({"aud": k.audience, "part": k.product_part, **info(k)})

    buy = [k for k in usable if k.group == GROUP_BUY]
    buy.sort(key=lambda k: ((k.competition or 50), -(k.search_volume or 0)))
    review = [k for k in usable if k.group == GROUP_REVIEW]
    guides = sorted([k for k in usable if k.group == GROUP_GUIDE], key=lambda k: -(k.search_volume or 0))
    problems = sorted([k for k in usable if k.group == GROUP_PROBLEM], key=lambda k: -(k.search_volume or 0))
    brands = []
    for k in sorted([k for k in usable if k.group == GROUP_BRAND], key=lambda k: -(k.search_volume or 0)):
        extra = [t for t in _tokens(k.keyword) if strip_accents(t) not in
                 {strip_accents(x) for x in _tokens(main.keyword)}]
        if extra:
            brands.append({"brand": " ".join(extra), **info(k)})

    # cặp so sánh: 2 tên sản phẩm volume cao nhất, không cái nào nằm trong cái kia
    pair = None
    for i, a in enumerate(product_list):
        for b in product_list[i + 1:]:
            ta, tb = _tokens(a["name"]), _tokens(b["name"])
            if not _contains_seq(ta, tb) and not _contains_seq(tb, ta):
                pair = (a, b)
                break
        if pair:
            break

    return {"main": info(main), "main_part": main_part, "products": product_list, "audiences": audiences,
            "buy": [info(k) | {"part": k.product_part} for k in buy], "review": [info(k) for k in review],
            "guides": [info(k) for k in guides], "problems": [info(k) for k in problems],
            "brands": brands, "pair": pair}


def _lookup(keywords: list[Keyword], text: str) -> Optional[Keyword]:
    n = _norm(text)
    return next((k for k in keywords if _norm(k.keyword) == n), None)


def generate_titles(keywords: list[Keyword], opts: Optional[TitleOptions] = None) -> list[Title]:
    """Sinh tiêu đề theo 10 công thức + các tổ hợp ghép, chấm điểm, sắp xếp."""
    opts = opts or TitleOptions()
    c = _ctx(keywords)
    Y, N, D, R = opts.year, opts.top_n, opts.trial_days, opts.gift_to.strip() or "Bố Mẹ"
    benefits = [b.strip() for b in opts.benefits if b.strip()]
    benefit_list = benefits + [b for b in DEFAULT_BENEFITS if b not in benefits]

    main = c["main"]["kw"]
    prods = c["products"]
    # Tên sản phẩm dùng để ghép: từ khóa chính + tên SP có tổng volume cao nhất +
    # từ khóa kéo traffic dạng tên SP có volume nguyên văn cao nhất (vd "máy trợ thính siêu nhỏ").
    top_prod = next((p["name"] for p in prods if p["name"] != c["main_part"]), None)
    traffic_prod = next((_norm(k.keyword) for k in sorted(keywords, key=lambda k: -(k.search_volume or 0))
                         if k.role in (ROLE_TRAFFIC, ROLE_SECONDARY) and k.group == GROUP_PRODUCT
                         and len(_tokens(k.keyword)) <= 6), None)
    names = list(dict.fromkeys([main] + [n for n in (top_prod, traffic_prod) if n]))

    raw: list[tuple[str, str, str]] = []  # (text, formula, keyword dùng)

    def add(text: str, formula: str, kw: str):
        raw.append((text, formula, kw))

    # CT2 – Đối tượng + Lợi ích
    for a in c["audiences"][:3]:
        prod = a["part"]
        for b in benefit_list[:3]:
            add(f"{prod} cho {a['aud']}: {b}", "CT2", a["kw"])
    if not c["audiences"]:
        for b in benefit_list[:2]:
            add(f"{main} cho người mới: {b}", "CT2", main)

    # CT1 – Hỏi giá
    price_names = [b["part"] for b in c["buy"][:2] if b.get("part")] or names
    for p in dict.fromkeys(price_names + names):
        add(f"{p} giá bao nhiêu? Bảng giá mới nhất {Y}", "CT1", p)
        add(f"{p} giá bao nhiêu? Đừng mua vội", "CT1", p)

    # CT8 – Quà tặng
    for p in names:
        add(f"Tặng {R} {p}: Có nên không?", "CT8", p)
        add(f"Tặng {R} {p}: Test thực tế {D} ngày", "CT8", p)
    if c["audiences"]:
        a = c["audiences"][0]
        add(f"{R} lớn tuổi: Nên mua {a['part']} loại nào?" if "già" in a["aud"] or "lớn tuổi" in a["aud"]
            else f"{R} nên dùng {a['part']} loại nào?", "CT8", a["part"])

    # CT4 – Top danh sách
    for p in names:
        add(f"Top {N} {p} tốt nhất {Y} (Đã test thực tế)", "CT4", p)
        add(f"Top {N} {p} đáng mua {Y}", "CT4", p)
    if c["review"]:
        add(f"Top {N} {product_part(c['review'][0]['kw'])} tốt nhất hiện nay (Đã test)", "CT4",
            c["review"][0]["kw"])

    # CT3 – Dùng thử thực tế
    for p in names:
        add(f"Dùng thử {p} {D} ngày: Có đáng tiền?", "CT3", p)
        add(f"Dùng {p} cả tuần: Sự thật sau review", "CT3", p)

    # CT6 – Cảnh báo / sai lầm
    for p in names[1:] or names:
        add(f"5 sai lầm khi mua {p}", "CT6", p)
        add(f"Mua {p}: 5 sai lầm ai cũng mắc", "CT6", p)
    if c["audiences"]:
        a = c["audiences"][0]
        add(f"5 sai lầm khi mua {a['part']} cho {a['aud']}", "CT6", a["kw"])
    add(f"Đừng mua {main} khi chưa xem video này", "CT6", main)

    # CT5 – So sánh
    if c["pair"]:
        a, b = c["pair"]
        add(f"{a['name']} vs {b['name']}: Chọn loại nào?", "CT5", a["name"])
        add(f"{a['name']} hay {b['name']}: Khác gì nhau?", "CT5", a["name"])

    # CT9 – Vấn đề -> giải pháp
    for pb in c["problems"][:2]:
        add(f"{pb['kw']}: {names[1] if len(names) > 1 else main} có giúp được không?", "CT9", pb["kw"])
        add(f"{pb['kw']} nên dùng gì? Mình test {D} ngày", "CT9", pb["kw"])
    if c["audiences"]:
        a = c["audiences"][-1]
        p = names[1] if len(names) > 1 else main
        add(f"{a['aud']} dùng {p} được không?", "CT9", p)

    # CT7 – Hướng dẫn
    for g in c["guides"][:2]:
        add(f"{g['kw']} đúng cách cho người mới", "CT7", g["kw"])
    add(f"Cách chọn {main} phù hợp với {R}", "CT7", main)

    # CT10 – Mở hộp / thương hiệu
    for br in c["brands"][:2]:
        add(f"Mở hộp {br['kw']}: Giá và cảm nhận", "CT10", br["kw"])
        add(f"{_cap(br['brand'])} có tốt không? Review {c['main_part']} sau 1 tuần", "CT10", br["kw"])
    if not c["brands"]:
        add(f"Mở hộp {main}: Giá và cảm nhận thật", "CT10", main)

    # COMBO – ghép 2 công thức top
    for a in c["audiences"][:2]:
        add(f"{a['part']} cho {a['aud']}: Giá bao nhiêu?", "CT2+CT1", a["kw"])
        add(f"Top {N} {a['part']} cho {a['aud']} đáng mua {Y}", "CT4+CT2", a["kw"])
    for p in names[:2]:
        add(f"Tặng {R} {p}: Test thực tế {D} ngày", "CT8+CT3", p)

    # dedupe + chấm điểm
    out: list[Title] = []
    seen = set()
    for text, formula, kw in raw:
        text = re.sub(r"\s+", " ", text).strip()
        text = text[0].upper() + text[1:]
        if opts.title_case:
            text = vn_title_case(text)
        key = strip_accents(text.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(score_title(text, formula, kw, keywords, opts))
    out.sort(key=lambda t: t.score, reverse=True)
    return out


def score_title(text: str, formula: str, kw: str, keywords: list[Keyword],
                opts: Optional[TitleOptions] = None, source: str = "Công thức") -> Title:
    """Điểm CTR ước lượng 0-100 (luật, không phải số đo thật — chỉ để xếp hạng).

    - Công thức: theo thứ hạng ưu tiên CTR (CT2 > CT1 > CT8 > CT4 > CT3 > …)
    - Từ khóa dùng trong tiêu đề: volume cao (+), cạnh tranh thấp (+)
    - Độ dài 40-60 ký tự (+), > 60 bị cắt trên YouTube (-)
    - Từ khóa nằm trong 5 từ đầu (+), có số/năm (+), chỉ 1 câu hỏi
    - Có cụm cam kết sai sự thật / y tế (chữa khỏi, 100%...) -> trừ nặng
    """
    opts = opts or TitleOptions()
    warnings: list[str] = []
    rank = FORMULAS.get(formula, FORMULAS.get(formula.split("+")[0], {"rank": 10}))["rank"]
    if "+" in formula:
        rank = 0
    score = _FORMULA_BASE_SCORE.get(rank, 8)

    k = _lookup(keywords, kw)
    vol = k.search_volume if k else None
    comp = k.competition if k else None
    max_vol = max((x.search_volume or 0) for x in keywords) or 1
    if vol:
        score += 25 * math.log(vol + 1) / math.log(max_vol + 1)
    else:
        score += 8
    score += 15 * (1 - min(comp, 100) / 100) if comp is not None else 7

    chars = len(text)
    if chars <= opts.max_chars:
        score += 15 if chars >= 40 else 10 if chars >= 30 else 5
    elif chars <= opts.max_chars + 10:
        score += 3
        warnings.append(f"Dài {chars} ký tự — có thể bị cắt trên mobile")
    else:
        score -= 10
        warnings.append(f"Quá dài ({chars} ký tự) — sẽ bị cắt")

    words_ascii = strip_accents(text.lower())
    kw_ascii = strip_accents(_norm(kw))
    pos = words_ascii.find(kw_ascii) if kw_ascii else -1
    if pos == -1:
        # thử với tên sản phẩm của từ khóa
        kw_ascii = strip_accents(product_part(kw))
        pos = words_ascii.find(kw_ascii)
    if pos != -1 and len(words_ascii[:pos].split()) <= 4:
        score += 10
    else:
        warnings.append("Từ khóa không nằm trong 5 từ đầu")

    if re.search(r"\d", text):
        score += 5
    if text.count("?") > 1:
        score -= 5
        warnings.append("Nhiều hơn 1 câu hỏi — nên giữ 1 móc cảm xúc")
    low = _norm(text)
    bad = [p for p in BANNED_PHRASES if p in low]
    if bad:
        score -= 40
        warnings.append("Có cụm dễ bị hạn chế / sai sự thật: " + ", ".join(bad))

    return Title(text=text, formula=formula, keyword=k.keyword if k else kw, search_volume=vol,
                 competition=comp, score=max(0, min(100, round(score))), chars=chars, warnings=warnings,
                 source=source)


def is_medical(keywords: list[Keyword]) -> bool:
    main = next((k for k in keywords if k.role == ROLE_MAIN), None)
    text = _norm(main.keyword) if main else ""
    return any(m in text for m in _MEDICAL_MARKERS)


def suggest_tags(keywords: list[Keyword], limit_chars: int = 480) -> list[str]:
    """Tag YouTube (giới hạn 500 ký tự): chính -> kéo traffic -> phụ -> đuôi dài -> không dấu."""
    tags: list[str] = []
    order = [ROLE_MAIN, ROLE_TRAFFIC, ROLE_SECONDARY, ROLE_LONGTAIL, ROLE_TAG]
    pool = sorted([k for k in keywords if k.role in order and (k.use or k.role == ROLE_TAG)],
                  key=lambda k: (order.index(k.role), -(k.search_volume or 0)))
    main = next((k for k in keywords if k.role == ROLE_MAIN), None)
    candidates = [k.keyword for k in pool]
    if main:
        candidates.insert(1, strip_accents(main.keyword.lower()))
    total = 0
    for t in dict.fromkeys(_norm(x) for x in candidates):
        if len(t.split()) > 7 or len(t) > 60:
            continue  # tag quá dài (thường là tiêu đề đối thủ) — bỏ
        if total + len(t) + 1 > limit_chars:
            break
        tags.append(t)
        total += len(t) + 1
    return tags


def keywords_to_rows(keywords: list[Keyword]) -> list[dict]:
    """Cột theo cấu trúc chuẩn: Volume gốc giữ NGUYÊN VĂN nguồn (kể cả "<750"), Volume (số)
    chỉ có khi nguồn ghi số thật; Cạnh tranh / Overall giữ nguyên điểm nguồn."""
    return [{
        "Dùng": bool(k.use and k.role != ROLE_NOISE),
        "Từ khóa": k.keyword,
        "Vai trò": k.role,
        "Nhóm ý định": k.group,
        "Volume gốc": k.volume_display,
        "Volume (số)": k.search_volume,
        "Cạnh tranh": k.competition,
        "Overall": k.overall,
        "Điểm vàng": "⭐" if k.golden else "",
        "Trạng thái": k.status or refresh_status(k),
        "Lý do giữ/loại": k.note,
        "Nguồn": ", ".join(k.sources),
    } for k in keywords]


def rows_to_keywords(rows: list[dict], original: list[Keyword]) -> list[Keyword]:
    """Áp lại chỉnh sửa tay từ bảng (vai trò / nhóm / bỏ chọn) lên danh sách từ khóa."""
    by_kw = {_norm(k.keyword): k for k in original}
    out = []
    for r in rows:
        k = by_kw.get(_norm(r["Từ khóa"]))
        if k is None:
            continue
        k = Keyword(**{**asdict(k)})
        use = bool(r.get("Dùng", True))
        role = r.get("Vai trò") or k.role
        if (use, role) != (k.use, k.role):
            k.edited = True
            if not k.note.startswith("Sửa tay"):
                k.note = f"Sửa tay ({'giữ' if use and role != ROLE_NOISE else 'bỏ'}) — gợi ý gốc: {k.note}"
        k.use, k.role = use and role != ROLE_NOISE, role
        k.group = r.get("Nhóm ý định") or k.group
        k.status = refresh_status(k)
        out.append(k)
    # đảm bảo đúng 1 từ khóa chính
    mains = [k for k in out if k.role == ROLE_MAIN]
    for extra in mains[1:]:
        extra.role = ROLE_SECONDARY
    return out


def titles_to_rows(titles: list[Title]) -> list[dict]:
    return [{
        "Điểm CTR": t.score,
        "Tiêu đề": t.text,
        "Ký tự": t.chars,
        "Công thức": t.formula_label,
        "Từ khóa SEO": t.keyword,
        "Volume": t.search_volume,
        "Cạnh tranh": t.competition,
        "Nguồn": t.source,
        "Lưu ý": "; ".join(t.warnings),
    } for t in titles]


def ai_prompt_payload(keywords: list[Keyword], opts: TitleOptions) -> dict:
    """Biến để điền vào prompts/07_seo_titles.md."""
    import json

    def pick(role, n):
        return [{"keyword": k.keyword, "volume": k.volume_display or None, "competition": k.competition,
                 "group": k.group} for k in keywords if k.role == role and (k.use or role == ROLE_MAIN)][:n]

    data = {
        "main": pick(ROLE_MAIN, 1),
        "traffic": pick(ROLE_TRAFFIC, 8),
        "secondary": pick(ROLE_SECONDARY, 12),
        "longtail": pick(ROLE_LONGTAIL, 15),
    }
    formulas = "\n".join(f"- {fid} ({m['name']}): {m['pattern']} — {m['why']}"
                         for fid, m in sorted(FORMULAS.items(), key=lambda x: x[1]["rank"]) if fid != "COMBO")
    return {
        "KEYWORDS_JSON": json.dumps(data, ensure_ascii=False, indent=1),
        "FORMULAS": formulas,
        "CURRENT_YEAR": opts.year,
        "TOP_N": opts.top_n,
        "MAX_CHARS": opts.max_chars,
        "BENEFITS": ", ".join(opts.benefits) or "(không có — tự chọn lợi ích chung, không bịa thông số)",
        "GIFT_TO": opts.gift_to,
        "MEDICAL_NOTE": ("Đây là sản phẩm sức khỏe/y tế: TUYỆT ĐỐI không dùng 'chữa khỏi', '100%', "
                         "'thay thế bác sĩ'… Dùng 'nghe rõ hơn', 'dễ dùng', 'trải nghiệm thực tế'.")
        if is_medical(keywords) else "",
    }


# ------------------------------------------------------------ lưu vào run --

SEO_FILE = "09_seo_keywords.json"


def apply_chosen_title(metadata: dict, run_dir: Path) -> dict:
    """Nếu người dùng đã chọn tiêu đề ở tab Từ khóa & Tiêu đề SEO (lưu trong
    09_seo_keywords.json của run) thì dùng tiêu đề đó thay cho tiêu đề AI tự
    đặt trong 05_metadata.json. Tags giữ nguyên vì description.py biến tags
    thành hashtag (YouTube bỏ hết hashtag nếu > 15)."""
    import json

    path = Path(run_dir) / SEO_FILE
    if not path.exists():
        return metadata
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return metadata
    title = data.get("chosen_title")
    if title and title == title.lower():
        title = vn_title_case(title)
    if title:
        metadata = {**(metadata or {}), "title": title, "title_ai_original": (metadata or {}).get("title")}
    if data.get("tags"):
        # Danh sách THẺ đầy đủ (dán vào ô "Thẻ" khi đăng YouTube) — tách riêng khỏi "tags" (hashtag
        # cuối mô tả) vì YouTube bỏ qua toàn bộ hashtag nếu có quá 15 cái.
        metadata = {**(metadata or {}), "youtube_tags": list(data["tags"])}
    return metadata
