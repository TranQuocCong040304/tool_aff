"""Giao diện tab "🔑 Từ khóa & Tiêu đề SEO" (gọi từ app.py).

Luồng: thả CSV từ khóa -> tool tự phân loại (chính / kéo traffic / phụ / đuôi
dài / loại) -> sửa tay nếu cần ngay trên bảng -> tiêu đề SEO được ghép theo 10
công thức CTR, chấm điểm, xếp hạng -> copy / tải về / gắn vào run đang làm.
Phần phân loại + ghép tiêu đề chạy offline (không tốn quota); nút "Nhờ AI viết
thêm" mới gọi Groq.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from dotenv import dotenv_values

from .llm_errors import LLMJSONError, LLMQuotaExceededError, LLMServerError
from .vidiq_client import CREDITS_PER_CALL, VidIQClient, VidIQError
from .vidiq_flow import (ScoreCache, is_near_duplicate, TitleMemory, VidIQParams, estimate_credits, initial_drafts, memory_drafts,
                         pick_targets, run_vidiq_workflow)
from .prompt_loader import render
from .seo_titles import (
    strip_accents,
    vn_title_case,
    STATUS_CHECK, STATUS_REFERENCE, competitor_angles,
    FORMULAS, GROUPS, ROLE_LONGTAIL, ROLE_MAIN, ROLE_NOISE, ROLE_SECONDARY, ROLE_TAG, ROLE_TRAFFIC, ROLES,
    SEO_FILE, TitleOptions, ai_prompt_payload, classify, generate_titles, is_medical, keywords_to_rows,
    read_keyword_csvs, rows_to_keywords, score_title, suggest_tags, titles_to_rows,
)


@st.cache_data(show_spinner=False)
def _read(files: tuple[tuple[str, bytes], ...]):
    return read_keyword_csvs(files)


def _fmt_num(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "–"
    if isinstance(v, float) and not v.is_integer():
        return f"{v:.1f}".replace(".", ",")
    return f"{int(v):,}".replace(",", ".")


def _csv_bytes(rows: list[dict]) -> bytes:
    if not rows:
        return b""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")  # BOM để Excel đọc đúng tiếng Việt


def _product_names(input_dir: Path, run_dir: Path | None) -> tuple[list[str], str]:
    """Tên sản phẩm để xác nhận từ khóa phụ thuộc xuất xứ / đối tượng / thương hiệu.
    Ưu tiên 5 sản phẩm đã chọn của run đang mở (đúng sản phẩm sẽ review), không có thì
    lấy toàn bộ sản phẩm trong các file JSON ở data/input/."""
    if run_dir is not None:
        sel = run_dir / "03_products_selected.json"
        if sel.exists():
            try:
                data = json.loads(sel.read_text(encoding="utf-8"))
                names = [p.get("name", "") for p in data.get("selected_products", [])]
                if names:
                    return names, f"{len(names)} sản phẩm đã chọn của run `{run_dir.name}`"
            except (ValueError, OSError):
                pass
    names = []
    for path in sorted(input_dir.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        items = raw.get("products", []) if isinstance(raw, dict) else raw
        names += [str(i.get("name") or i.get("title") or "") for i in items if isinstance(i, dict)]
    names = list(dict.fromkeys(n for n in names if n))
    return names, (f"{len(names)} sản phẩm trong file JSON ở data/input/" if names else "")


RAW_CSV_DIR = "_csv_goc"   # CSV gốc đã lọc được chuyển vào đây (không bị đọc trùng với file đã phân loại)


def is_classified_csv(data: bytes) -> bool:
    """File "Từ khóa đã phân loại" do tool xuất (có cột Dùng + Vai trò)."""
    head = data[:2000].decode("utf-8-sig", errors="ignore").splitlines()[:1]
    return bool(head) and "Vai trò" in head[0] and "Dùng" in head[0]


def render_keyword_filter(input_dir: Path, uploaded) -> None:
    """Tab 📥: thả CSV từ khóa thô -> bấm Lọc -> tool phân loại (giống tab 🔑) và LƯU file
    'Từ khóa đã phân loại' vào data/input/ — bước Chọn sản phẩm dùng luôn file này
    (từ khóa bị Loại / bỏ tick Dùng không được gửi cho Groq)."""
    st.markdown("#### 🔎 Lọc & phân loại từ khóa")
    done = st.session_state.pop("_kw_filter_done", None)
    if done:
        st.success(f"Đã lọc {done['total']} từ khóa → lưu `{done['file']}` vào data/input/"
                   + (f" (file gốc chuyển vào `data/input/{RAW_CSV_DIR}/`)" if done["moved"] else "") + ".")
        m = st.columns(5)
        for col, (label, n) in zip(m, done["counts"]):
            col.metric(label, n)
        st.write(f"🎯 **Từ khóa chính:** {done['main']}")
        if done["traffic"]:
            st.write("🚀 **Kéo traffic:** " + " · ".join(done["traffic"]))
        if done["check"]:
            st.warning(f"⚠️ {done['check']} từ khóa cần bạn xác nhận (đang tắt Dùng) — sang tab **🔑 Từ khóa & "
                       "Tiêu đề SEO** để tick Dùng / đổi vai trò; tab đó tự đọc file vừa lưu.")

    up_csv = [f for f in (uploaded or []) if f.name.lower().endswith(".csv")]
    up_other = [f for f in (uploaded or []) if not f.name.lower().endswith(".csv")]
    raw_up = [(f.name, f.getvalue()) for f in up_csv if not is_classified_csv(f.getvalue())]
    raw_existing = [p for p in sorted(input_dir.glob("*.csv")) if not is_classified_csv(p.read_bytes())]
    names = {n for n, _ in raw_up}
    files = raw_up + [(p.name, p.read_bytes()) for p in raw_existing if p.name not in names]
    if not files:
        st.caption("Thả file CSV từ khóa thô (export từ tool nghiên cứu keyword) vào ô trên để lọc. "
                   "File đã phân loại thì không cần lọc lại.")
        return
    st.caption("Sẽ lọc: " + ", ".join(f"`{n}`" for n, _ in files)
               + " — chạy offline, không tốn quota. Kết quả giống nút ⬇️ Từ khóa đã phân loại (.csv) ở tab 🔑.")
    try:
        base_keywords, seeds = _read(tuple(files))
    except ValueError as e:
        st.error(f"[LỖI DỮ LIỆU] {e}")
        return
    auto = classify([k.__class__(**asdict(k)) for k in base_keywords], seeds=seeds)
    auto_main = next(k.keyword for k in auto if k.role == ROLE_MAIN)
    options = [k.keyword for k in base_keywords]
    if auto_main not in options:
        options.insert(0, auto_main)
    c1, c2 = st.columns([3, 1])
    main_kw = c1.selectbox("Từ khóa chính (tự đoán — đổi nếu chưa đúng)", options, index=options.index(auto_main),
                           key="inp_main_kw")
    n_traffic = c2.number_input("Số từ khóa kéo traffic", 3, 10, 5, key="inp_n_traffic")
    move_raw = st.checkbox(f"Chuyển file CSV gốc vào `data/input/{RAW_CSV_DIR}/` (giữ lại để đối chiếu, tránh đọc "
                           "trùng với file đã phân loại)", value=True, key="inp_move_raw")
    if not st.button("🔎 Lọc từ khóa & lưu vào data/input/", type="primary", key="inp_filter_btn"):
        return
    for f in up_other:   # JSON sản phẩm thả cùng lúc -> lưu luôn (dùng để xác nhận từ khóa xuất xứ / thương hiệu)
        (input_dir / f.name).write_bytes(f.getvalue())
    product_names, _ = _product_names(input_dir, None)
    classified = classify([k.__class__(**asdict(k)) for k in base_keywords], main_keyword=main_kw, seeds=seeds,
                          n_traffic=int(n_traffic), product_names=product_names)
    main = next(k for k in classified if k.role == ROLE_MAIN)
    out_name = f"tu-khoa_{main.keyword.replace(' ', '-')}.csv"
    (input_dir / out_name).write_bytes(_csv_bytes(keywords_to_rows(classified)))
    raw_dir = input_dir / RAW_CSV_DIR
    for name, data in raw_up:
        if move_raw:
            raw_dir.mkdir(exist_ok=True)
            (raw_dir / name).write_bytes(data)
            (input_dir / name).unlink(missing_ok=True)
        else:
            (input_dir / name).write_bytes(data)
    if move_raw:
        for p in raw_existing:
            if p.exists():
                raw_dir.mkdir(exist_ok=True)
                p.replace(raw_dir / p.name)
    st.session_state["_kw_filter_done"] = {
        "file": out_name, "total": len(classified), "moved": move_raw, "main": main.keyword,
        "counts": [(r, sum(1 for k in classified if k.role == r and k.use))
                   for r in (ROLE_TRAFFIC, ROLE_SECONDARY, ROLE_LONGTAIL, ROLE_TAG)]
                  + [("Loại / chưa dùng", sum(1 for k in classified if not k.use))],
        "traffic": [f"{k.keyword} ({_fmt_num(k.search_volume)})" for k in classified if k.role == ROLE_TRAFFIC],
        "check": sum(1 for k in classified if k.role != ROLE_NOISE and not k.use and k.status == STATUS_CHECK),
    }
    st.rerun()


def render_seo_tab(input_dir: Path, run_dir: Path | None, text_client, client_err: str | None) -> None:
    """Chỉ còn: Tag & mô tả + Chấm điểm tiêu đề bằng vidIQ. Từ khóa lấy từ file CSV trong data/input/
    (lọc / phân loại ở tab 📥 Dữ liệu đầu vào — tôn trọng cột Dùng / Vai trò của file đã phân loại)."""
    st.subheader("Tag YouTube & chấm điểm tiêu đề")
    existing = sorted(input_dir.glob("*.csv"))
    if not existing:
        st.info("Chưa có file CSV từ khóa — sang tab **📥 Dữ liệu đầu vào** thả file CSV rồi bấm **🔎 Lọc từ khóa**.")
        return
    files: list[tuple[str, bytes]] = [(p.name, p.read_bytes()) for p in existing]
    st.caption("Từ khóa lấy từ: " + ", ".join(f"`{n}`" for n, _ in files)
               + " (đổi / lọc lại ở tab 📥 Dữ liệu đầu vào).")
    try:
        base_keywords, seeds = _read(tuple(files))
    except ValueError as e:
        st.error(f"[LỖI DỮ LIỆU] {e}")
        return
    product_names, _ = _product_names(input_dir, run_dir)
    keywords = classify([k.__class__(**asdict(k)) for k in base_keywords], seeds=seeds,
                        product_names=product_names)
    main = next(k for k in keywords if k.role == ROLE_MAIN)
    opts = TitleOptions()
    editor_key = "seo_editor_" + hashlib.md5(
        (json.dumps([n for n, _ in files]) + main.keyword).encode("utf-8")).hexdigest()[:10]
    st.markdown(f"**🎯 Từ khóa chính:** `{main.keyword}`")

    # ---------------------------------------------------------- 4 công thức tiêu đề --
    _render_formula_titles(keywords, main, [n for n, _ in files], run_dir, text_client, client_err, product_names)

    # ---------------------------------------------------------- tag + mô tả --
    st.markdown("### 🏷️ Tag & mô tả")
    tags = suggest_tags(keywords)
    st.markdown("**🏷️ Tag YouTube** (dán vào ô Thẻ khi đăng — đã giới hạn < 500 ký tự)")
    st.code(", ".join(tags), language=None, wrap_lines=True)
    secondary = [k.keyword for k in keywords if k.role in (ROLE_TRAFFIC, ROLE_SECONDARY)][:6]
    if secondary:
        st.markdown("**📝 Nên có trong 2 dòng đầu mô tả:** " + ", ".join(f"`{s}`" for s in secondary))
    with st.expander("✅ Checklist trước khi đăng"):
        st.markdown(
            "- Từ khóa chính nằm trong 5 từ đầu\n"
            f"- Tiêu đề không quá {opts.max_chars} ký tự\n"
            "- Chỉ có 1 móc cảm xúc (1 câu hỏi hoặc 1 lời hứa)\n"
            "- Có số hoặc năm (nếu hợp nội dung)\n"
            "- Chữ trên thumbnail bổ sung cho tiêu đề, không lặp lại (vd tiêu đề “Giá Bao Nhiêu?” → thumbnail "
            "“CHỈ 5 TRIỆU?”)\n"
            "- Mọi lời hứa trong tiêu đề đều có thật trong video"
        )

    # ---------------------------------------------------------- vidIQ --
    _render_vidiq_section(keywords, opts, text_client, client_err, editor_key, input_dir.parent, product_names)


# ------------------------------------------------------- 4 công thức tiêu đề --

def _volume_of(k) -> int:
    return int(getattr(k, "search_volume", 0) or 0)


def _render_formula_titles(keywords, main, source_files: list[str], run_dir: Path | None, text_client,
                           client_err: str | None, product_names: list[str]) -> None:
    from .title_formulas import ai_fill, build_titles, price_segment, save_to_run, traffic_candidates

    st.markdown("### 📝 Tiêu đề theo 4 công thức")
    products = []
    if run_dir is not None and (run_dir / "03_products_selected.json").exists():
        try:
            products = json.loads((run_dir / "03_products_selected.json").read_text(encoding="utf-8")).get(
                "selected_products", [])
        except (ValueError, OSError):
            products = []
    run_names = [p.get("name", "") for p in products if p.get("name")]
    prices = [float(p["price"]) for p in products if p.get("price")]
    rejected: list = []
    cands = traffic_candidates(keywords, run_names, main.keyword, prices, rejected)
    if not run_names:
        st.info("Chưa có 5 sản phẩm của run → chưa kiểm tra được từ khóa có khớp sản phẩm không. Chọn sản phẩm "
                "trước rồi quay lại để tiêu đề không hứa sản phẩm video không có.")
    if rejected:
        with st.expander(f"⛔ {len(rejected)} từ khóa volume cao bị loại vì KHÔNG khớp sản phẩm trong video",
                         expanded=bool(rejected) and _volume_of(rejected[0][0]) > _volume_of(cands[0]) if cands else True):
            st.caption("Đặt các từ khóa này vào tiêu đề là hứa sản phẩm video không có (gây hiểu lầm người xem, "
                       "YouTube có thể coi là nội dung lừa đảo). Muốn dùng → chọn lại sản phẩm đúng từ khóa đó.")
            for k, why in rejected[:12]:
                st.markdown(f"- `{k.keyword}` — volume {_fmt_num(k.search_volume)} · {why}")
    if not cands:
        st.warning("Không có từ khóa nào trong file CSV khớp với sản phẩm của video để làm từ khóa traffic.")
        return
    top = cands[0]
    if top.search_volume:
        st.success(f"🚀 **Từ khóa traffic (volume cao nhất trong CSV, khớp sản phẩm):** `{top.keyword}` — volume "
                   f"{_fmt_num(top.search_volume)}" + (f", cạnh tranh {top.competition:g}" if top.competition else ""))
    else:
        st.warning(f"Không từ khóa nào trong CSV khớp sản phẩm → dùng phần chung của từ khóa chính: `{top.keyword}`.")
    ckey = hashlib.md5(("|".join(source_files) + str(run_dir)).encode("utf-8")).hexdigest()[:8]
    labels = [f"{k.keyword} — {_fmt_num(k.search_volume) if k.search_volume else 'không có volume'}" for k in cands[:10]]
    pick = st.selectbox("Từ khóa traffic dùng trong tiêu đề", range(len(labels)), format_func=labels.__getitem__,
                        key=f"ft_kw_{ckey}", help="Mặc định = volume cao nhất. Đổi nếu từ khóa đó không hợp video.")
    traffic = cands[pick]

    seg_price = price_segment([p.get("price") for p in products])
    ai_key = f"ft_ai_{ckey}_{traffic.keyword}"
    ai = st.session_state.get(ai_key, {})

    c1, c2, c3 = st.columns(3)
    n = c1.number_input("Top N", 3, 20, len(products) or 5, key=f"ft_n_{ckey}")
    year = c2.number_input("Năm", 2024, 2035, datetime.now().year, key=f"ft_year_{ckey}")
    review_word = c3.selectbox("CT1 mở đầu bằng", ["Review", "Đánh Giá"], key=f"ft_rv_{ckey}")
    adjective = st.text_input("Tính từ nổi bật (CT1)", ai.get("adjective") or "Đáng Mua Nhất", key=f"ft_adj_{ai_key}")
    benefit = st.text_input("Lợi ích / vấn đề giải quyết (CT2, đứng sau chữ “giúp”)", ai.get("benefit", ""),
                            key=f"ft_ben_{ai_key}", placeholder="vd Nấu Ăn Nhanh Hơn — để trống thì bỏ CT2")
    seg_opts = [o for o in (seg_price, "Giá Rẻ", ai.get("audience")) if o]
    seg_opts = list(dict.fromkeys(seg_opts)) + ["✍️ Tự nhập"]
    seg_pick = st.selectbox("Phân khúc giá / đối tượng (CT3)", seg_opts, key=f"ft_seg_{ai_key}",
                            help="“Dưới X” tính từ giá cao nhất trong 5 sản phẩm của run (làm tròn lên).")
    segment = st.text_input("Phân khúc tự nhập", "Cho Gia Đình", key=f"ft_segc_{ckey}") \
        if seg_pick == "✍️ Tự nhập" else seg_pick
    if st.button("✨ AI gợi ý tính từ / lợi ích / đối tượng (Groq)", key=f"ft_aibtn_{ckey}",
                 disabled=text_client is None, help=client_err or "Dựa trên điểm mạnh thật của sản phẩm trong run."):
        with st.spinner("Đang hỏi Groq..."):
            try:
                st.session_state[ai_key] = ai_fill(text_client, traffic.keyword, products,
                                                   ", ".join(filter(None, [traffic.audience or ""])))
                st.rerun()
            except (LLMQuotaExceededError, LLMServerError, LLMJSONError, Exception) as e:
                st.error(f"[LỖI] AI gợi ý thất bại: {type(e).__name__}: {e}")

    titles = build_titles(traffic.keyword, int(n), int(year), review_word, adjective, benefit, segment)
    for t in titles:
        st.markdown(f"**{t['label']}** · {t['chars']} ký tự" + (" · ⚠️ " + "; ".join(t["warnings"]) if t["warnings"] else ""))
        st.code(t["text"], language=None)
    if run_dir is None:
        st.info("Chọn / tạo 1 run để lưu tiêu đề vào run (bước Script & Audio sẽ tự dùng).")
        return
    chosen = st.radio("Tiêu đề dùng cho video", [t["text"] for t in titles], key=f"ft_choose_{ckey}")
    if st.button("💾 Lưu tiêu đề + từ khóa traffic vào run", type="primary", key=f"ft_save_{ckey}"):
        path = save_to_run(run_dir, keywords, traffic, titles, chosen, suggest_tags(keywords), source_files,
                           {"top_n": int(n), "year": int(year), "review_word": review_word, "adjective": adjective,
                            "benefit": benefit, "segment": segment})
        st.success(f"Đã lưu vào `{run_dir.name}/{path.name}` — tab Script & Audio sẽ lấy tiêu đề này cho hook, "
                   "mô tả YouTube dùng từ khóa traffic.")


# ------------------------------------------------------------------ vidIQ --

def _vidiq_config(env_path: Path) -> dict:
    """Đọc lại .env mỗi lần vẽ trang — dán key xong chỉ cần F5, không phải khởi động lại Streamlit."""
    import os

    file_vals = dotenv_values(env_path) if env_path.exists() else {}

    def get(name: str, default: str = "") -> str:
        return (file_vals.get(name) or os.environ.get(name) or default).strip()

    return {"api_key": get("VIDIQ_API_KEY"), "channel_id": get("VIDIQ_CHANNEL_ID"),
            "url": get("VIDIQ_MCP_URL", "https://mcp.vidiq.com/mcp")}


def _render_vidiq_section(keywords, opts: TitleOptions, text_client, client_err, editor_key: str,
                          data_dir: Path, product_names: list | None = None) -> dict | None:
    st.markdown("### 🎯 Chấm điểm tiêu đề bằng vidIQ (tùy chọn)")
    st.caption(
        "Quy trình: lọc từ khóa volume ≥ ngưỡng → chọn 3-5 từ khóa mục tiêu → chấm 8-10 tiêu đề nháp bằng "
        "vidiq_score_title → chưa có tiêu đề ≥ 90 thì lấy mẫu điểm cao (vidiq_generate_titles), nhờ Groq viết lại "
        "rồi chấm lại, tối đa N vòng. Điểm ở mục này 100% do vidIQ trả về — code tự đọc, AI không báo điểm."
    )
    cfg = _vidiq_config(data_dir.parent / ".env")
    state = st.session_state.get("vidiq_run")
    saved_run = state["run"] if state and state.get("key") == editor_key else None

    if not cfg["api_key"]:
        st.info(
            "Chưa có **VIDIQ_API_KEY** trong file `.env`. Tạo API key tại **app.vidiq.com → Account Settings → MCP** "
            "(vidIQ MCP chỉ có ở gói Max), dán vào dòng `VIDIQ_API_KEY=` trong `.env` rồi tải lại trang (F5)."
        )
        return saved_run

    c1, c2 = st.columns([3, 1])
    channel_id = c1.text_input("ID kênh YouTube (để vidIQ chấm sát kênh của bạn — bỏ trống cũng được)",
                               cfg["channel_id"], key="vidiq_channel", placeholder="UCxxxxxxxxxxxxxxxxxxxxxx")
    title_type = c2.radio("Loại video", ["long", "short"], horizontal=True, key="vidiq_type",
                          help="long = video thường, short = Shorts")
    with st.expander("⚙️ Tham số quy trình & giới hạn credit"):
        p1, p2, p3 = st.columns(3)
        min_volume = p1.number_input("Bỏ từ khóa volume dưới", 0, 100000, 750, step=50, key="vidiq_minvol")
        n_targets = p2.number_input("Số từ khóa mục tiêu", 1, 8, 4, key="vidiq_ntargets")
        n_titles = p3.number_input("Số tiêu đề mỗi vòng", 3, 15, 8, key="vidiq_ntitles")
        p4, p5, p6 = st.columns(3)
        threshold = p4.number_input("Ngưỡng điểm đạt", 50, 100, 90, key="vidiq_threshold")
        max_rounds = p5.number_input("Số vòng tối đa", 1, 6, 4, key="vidiq_rounds")
        max_credits = p6.number_input("Trần credit mỗi lần chạy", 5, 1000, 150, step=5, key="vidiq_maxcredits")
        p7, p8 = st.columns(2)
        use_memory = p7.checkbox("📚 Vòng 1 thử trước khung tiêu đề điểm cao đã học", value=True, key="vidiq_usemem",
                                 help="Dùng lại cấu trúc các tiêu đề từng đạt điểm cao (kể cả của sản phẩm khác), "
                                      "điền từ khóa mới vào.")
        stop_after = p8.number_input("Số tiêu đề đạt ngưỡng cần có", 1, 10, 3, key="vidiq_need",
                                     help="Chạy tới khi có đủ N tiêu đề KHÁC NHAU đạt ngưỡng thì dừng (tiêu đề chỉ khác "
                                          "1-2 từ tính là 1). 1 = rẻ nhất; 3 = đủ lựa chọn để test A/B. Vẫn bị giới hạn "
                                          "bởi số vòng tối đa và trần credit.")
    f1, f2, f3 = st.columns([2, 1, 1])
    tested = f1.checkbox("Mình đã thực sự dùng / test các sản phẩm này", value=False, key="vidiq_tested",
                         help="Chưa tick = tool loại mọi tiêu đề hứa 'Tôi Thử', 'Thực Nghiệm', '30 Ngày', 'Kiểm Tra "
                              "Thực Tế'... (video tổng hợp từ thông số + đánh giá người mua không thực hiện được lời hứa đó).")
    top_n = f2.number_input("Số sản phẩm (Top N)", 3, 10, 5, key="vidiq_topn")
    max_chars = f3.number_input("Tối đa ký tự", 40, 100, 70, key="vidiq_maxchars",
                                help="YouTube cắt tiêu đề khoảng 60–70 ký tự trên điện thoại.")
    params = VidIQParams(min_volume=int(min_volume), n_targets=int(n_targets), n_titles=int(n_titles),
                         threshold=float(threshold), max_rounds=int(max_rounds), max_credits=int(max_credits),
                         title_type=title_type, use_memory=use_memory, stop_after_passed=int(stop_after),
                         tested=bool(tested), top_n=int(top_n), max_chars=int(max_chars),
                         product_names=list(product_names or []))
    est = estimate_credits(params)

    cache = ScoreCache(data_dir / "vidiq_cache.json")
    memory = TitleMemory(data_dir / "title_memory.json", cache=cache)
    mstats = memory.stats(params.threshold)
    with st.expander(f"📚 Bộ nhớ đã học: {mstats['total']} tiêu đề đã chấm, {mstats['passed']} tiêu đề "
                     f"≥ {params.threshold:g} điểm"):
        st.caption("Mọi tiêu đề vidIQ đã chấm được lưu ở `data/title_memory.json`. Tool rút ra KHUNG tiêu đề (thay từ "
                   "khóa bằng {TỪ KHÓA}, năm bằng {NĂM}) để lần sau, kể cả với sản phẩm khác: vòng 1 thử khung điểm cao "
                   "trước, và Groq được xem tiêu đề điểm cao / thấp đã học khi viết lại. Càng chạy nhiều, càng ít vòng.")
        cur_niche = next((k.keyword for k in keywords if k.role == ROLE_MAIN), "")
        rows, seen_tpl = [], set()
        for e in sorted(memory._annotated([k.keyword for k in keywords], cur_niche),
                        key=lambda e: e["score"], reverse=True):
            if not e["template"] or e["template"] in seen_tpl:
                continue
            seen_tpl.add(e["template"])
            if e["same_niche"]:
                usable = "✅ Cùng sản phẩm"
            elif e["transferable"]:
                usable = "✅ Dùng chéo được"
            else:
                usable = "❌ Có từ riêng: " + ", ".join(dict.fromkeys(e["leaks"]))
            rows.append({"Điểm vidIQ": e["score"], "Khung tiêu đề": vn_title_case(e["template"]),
                         "Học từ sản phẩm": e.get("niche") or "(cache cũ)", "Dùng cho sản phẩm này": usable,
                         "Tiêu đề gốc": e["title"]})
        if rows:
            st.dataframe(pd.DataFrame(rows[:30]), hide_index=True, width="stretch")
            st.caption("Khung từ sản phẩm khác chỉ được dùng khi không còn chữ riêng của sản phẩm cũ (vd khung "
                       "“{Từ Khóa}: Gần Như Tàng Hình Trong Tai!” học từ máy trợ thính sẽ KHÔNG đem sang nồi chiên). "
                       "Điểm của khung từ sản phẩm khác chỉ là dự đoán — vidIQ vẫn chấm lại thật.")
        else:
            st.info("Chưa có khung nào cho sản phẩm này — chạy quy trình vidIQ để tool bắt đầu học.")
        if mstats["total"] and st.button("🗑️ Xóa bộ nhớ đã học", key="vidiq_clear_mem",
                                          help="Chỉ xóa bộ nhớ khung tiêu đề — cache điểm (tránh trả credit lại) vẫn giữ."):
            memory.clear()
            st.rerun()

    targets = pick_targets(keywords, params)
    learned = memory_drafts(memory, targets, opts, params, keywords)
    drafts = [(t, k) for t, k, _ in learned] + [
        (t, k) for t, k in initial_drafts(keywords, targets, opts, params)
        if t.lower() not in {x[0].lower() for x in learned}]
    drafts = drafts[:max(params.n_titles, len(learned))]
    with st.expander(f"👀 Xem trước vòng 1 — {len(targets)} từ khóa mục tiêu, {len(drafts)} tiêu đề nháp "
                     f"({len(learned)} từ khung đã học) — 0 credit"):
        st.markdown("**Từ khóa mục tiêu:** " + " · ".join(
            f"`{k.keyword}` ({_fmt_num(k.search_volume)} / {_fmt_num(k.competition)})" for k in targets))
        learned_scores = {t.lower(): sc for t, _, sc in learned}
        for title, k in drafts:
            hit = cache.get(title, channel_id, title_type)
            tag = ""
            if hit and hit.get("score") is not None:
                tag = f" — **đã chấm: {hit['score']:g} điểm (miễn phí)**"
            elif title.lower() in learned_scores:
                sc = learned_scores[title.lower()]
                tag = (f" — 📚 khung từng đạt {sc:g}" if sc >= 0
                       else f" — 📚 khung từ sản phẩm khác, từng đạt {-sc:g} (dự đoán)")
            st.markdown(f"- {title}  ·  _{k.keyword}_{tag}")

    st.caption(f"💳 Mỗi lần chấm / gợi ý tốn {CREDITS_PER_CALL} credit. Lần chạy này tối đa **{est} credit**. "
               "Tiêu đề đã chấm trước đó (cùng kênh, cùng loại) lấy lại từ cache `data/vidiq_cache.json`, không tốn thêm.")

    b1, b2 = st.columns(2)
    if b1.button("🔌 Kiểm tra kết nối vidIQ (0 credit)", key="vidiq_check"):
        try:
            client = VidIQClient(api_key=cfg["api_key"], url=cfg["url"], channel_id=channel_id)
            tools = client.list_tools()
            balance = client.balance()
            client.close()
            st.success(f"Kết nối OK — vidIQ có {len(tools)} tool." + (f" Credit: {balance[:200]}" if balance else ""))
            names = [t.get("name") for t in tools]
            missing = [n for n in ("vidiq_score_title", "vidiq_generate_titles")
                       if not any(n in (x or "") for x in names)]
            if missing:
                st.warning("Không thấy tool: " + ", ".join(missing) + " — tool sẽ tự tìm tool có tên gần giống.")
            with st.expander("Danh sách tool + tham số"):
                st.json([{"name": t.get("name"), "description": (t.get("description") or "")[:200],
                          "params": list(((t.get("inputSchema") or {}).get("properties") or {}).keys())}
                         for t in tools])
        except VidIQError as e:
            st.error(f"[vidIQ] {e}")

    agree = st.checkbox(f"Đồng ý dùng tối đa {est} credit vidIQ cho lần chạy này", key="vidiq_agree")
    if b2.button("🚀 Chạy quy trình vidIQ", type="primary", disabled=not agree or not drafts, key="vidiq_run_btn",
                 help=None if drafts else "Không có tiêu đề nháp nào chứa từ khóa mục tiêu — hạ ngưỡng volume."):
        if client_err:
            st.warning("Groq chưa cấu hình — chỉ chấm được vòng 1, không viết lại được.")
        status = st.status("Đang chạy quy trình vidIQ...", expanded=True)
        try:
            client = VidIQClient(api_key=cfg["api_key"], url=cfg["url"], channel_id=channel_id)
            run = run_vidiq_workflow(client, None if client_err else text_client, keywords, opts, params,
                                     cache, progress=status.write, memory=memory)
            client.close()
        except VidIQError as e:
            status.update(label="Lỗi vidIQ", state="error")
            st.error(f"[vidIQ] {e}")
            return saved_run
        status.update(label=f"Xong — {len(run.passed)} tiêu đề đạt ≥ {params.threshold:g}, tốn {run.credits_used} credit",
                      state="complete", expanded=False)
        saved_run = run.to_dict()
        st.session_state["vidiq_run"] = {"key": editor_key, "run": saved_run}

    if not saved_run:
        return None

    results = saved_run["results"]
    thr = saved_run["threshold"]
    scored = [r for r in results if r.get("score") is not None]
    passed = [r for r in scored if r["score"] >= thr]
    need = int(stop_after)
    # nhóm gần trùng (chỉ khác 1-2 từ): hiện bản điểm cao nhất, các biến thể gom lại bên dưới
    distinct, variants = [], {}
    for r in sorted(scored, key=lambda r: r["score"], reverse=True):
        parent = next((d for d in distinct if is_near_duplicate(r["title"], d["title"], r.get("keyword"),
                                                                 d.get("keyword"))), None)
        if parent is None:
            distinct.append(r)
        else:
            variants.setdefault(parent["title"], []).append(r)
    distinct_ok = [r for r in distinct if r["score"] >= thr]
    msg = f"(sau {saved_run['rounds']} vòng, tốn {saved_run['credits_used']} credit)"
    if len(distinct_ok) >= need:
        st.success(f"✅ {len(distinct_ok)} tiêu đề khác nhau đạt ≥ {thr:g} điểm vidIQ {msg}.")
        show = distinct_ok
    elif distinct_ok:
        st.warning(f"Mới có {len(distinct_ok)}/{need} tiêu đề khác nhau đạt ≥ {thr:g} điểm {msg} — hết số vòng / trần "
                   "credit. Bấm chạy lại để tìm thêm (tiêu đề đã chấm lấy từ cache, không tốn credit), hoặc tăng "
                   "'Số vòng tối đa'. Dưới đây là các tiêu đề đạt + các tiêu đề gần đạt nhất.")
        show = distinct_ok + [r for r in distinct if r["score"] < thr][:need - len(distinct_ok)]
    else:
        st.warning(f"Chưa có tiêu đề nào đạt {thr:g} điểm {msg} — dưới đây là các tiêu đề tốt nhất kèm điểm THẬT "
                   "của vidIQ.")
        show = distinct[:need]
    for i, r in enumerate(show, 1):
        badge = "✅" if r["score"] >= thr else "⚠️ chưa đạt"
        st.markdown(f"**#{i} · {r['score']:g} điểm vidIQ** {badge} · {len(r['title'])} ký tự · từ khóa "
                    f"`{r['keyword']}` · vòng {r['round']} ({r['source']}{', cache' if r.get('cached') else ''})")
        st.code(r["title"], language=None)
        if variants.get(r["title"]):
            st.caption("Biến thể gần giống (không tính thêm): " + " · ".join(
                f"{v['title']} ({v['score']:g})" for v in variants[r["title"]][:3]))
    unreadable = [r for r in results if r.get("score") is None]
    if unreadable:
        with st.expander(f"⚠️ {len(unreadable)} tiêu đề không đọc được điểm từ kết quả vidIQ"):
            for r in unreadable:
                st.markdown(f"- {r['title']}")
                st.code(r.get("raw") or "(trống)", language=None)
    with st.expander(f"📊 Tất cả {len(results)} tiêu đề đã chấm"):
        rows = [{"Điểm vidIQ": r.get("score"), "Tiêu đề": r["title"], "Ký tự": len(r["title"]),
                 "Từ khóa": r["keyword"], "Volume": r.get("volume"), "Vòng": r["round"],
                 "Nguồn": r["source"] + (" (cache)" if r.get("cached") else "")} for r in results]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                     column_config={"Điểm vidIQ": st.column_config.ProgressColumn(
                         "Điểm vidIQ", min_value=0, max_value=100, format="%.0f")})
        st.download_button("⬇️ Kết quả vidIQ (.csv)", _csv_bytes(rows), file_name="vidiq_titles.csv",
                           mime="text/csv", key="vidiq_dl")
    if saved_run.get("samples"):
        with st.expander("💡 Mẫu tiêu đề điểm cao vidIQ gợi ý (để học cấu trúc)"):
            for smp in saved_run["samples"]:
                sc = f" — {smp['score']:g} điểm" if smp.get("score") is not None else ""
                st.markdown(f"- {smp['title']}{sc}")
    with st.expander("📜 Nhật ký chạy"):
        st.code("\n".join(saved_run.get("log", [])), language=None)
    return saved_run
