"""Dashboard minh bạch cho tab 🎯 Chọn sản phẩm (gọi từ app.py).

- render_selection_preview(input_dir): TRƯỚC khi chạy (0 quota) — từ khóa nào gửi Groq / bị bỏ,
  sản phẩm nào gửi Groq / bị loại (trùng, phụ kiện, không chứa từ khóa, ngoài top 30) và vì sao.
- render_selection_report(run_dir): SAU khi chạy — từ khóa Groq chọn (đã chặn từ khóa bịa), 5 sản
  phẩm được chọn khớp từ khóa nào, kèm toàn bộ dữ liệu đầu vào lúc chạy (02b_selection_report.json).
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from .pipeline import SELECTION_REPORT_FILE
from .product_filter import (STATUS_ACCESSORY, STATUS_DUP_MODEL, STATUS_LOW_TRUST, STATUS_OVER_LIMIT, STATUS_SENT, STATUS_UNRELATED, check_selected,
                             prepare_selection_inputs)


def _fmt_money(v) -> str:
    try:
        return f"{int(v):,}đ".replace(",", ".")
    except (TypeError, ValueError):
        return str(v or "")


def _files_signature(input_dir: Path) -> tuple:
    return tuple(sorted((p.name, p.stat().st_mtime, p.stat().st_size)
                        for p in Path(input_dir).glob("*") if p.suffix.lower() in (".csv", ".json")))


@st.cache_data(show_spinner=False)
def _cached(input_dir: str, sig: tuple):
    return prepare_selection_inputs(Path(input_dir))


# ------------------------------------------------------------- phần dùng chung --

def _input_section(report: dict, chosen_names: set | None = None, key: str = "sel") -> None:
    kw = report.get("keywords", {})
    pr = report.get("products", {})
    rows = pr.get("rows", [])
    n_acc = sum(1 for r in rows if r["status"] == STATUS_ACCESSORY)
    n_unrel = sum(1 for r in rows if r["status"] == STATUS_UNRELATED)
    n_over = sum(1 for r in rows if r["status"] == STATUS_OVER_LIMIT)
    n_dup = sum(1 for r in rows if r["status"] == STATUS_DUP_MODEL)
    n_low = sum(1 for r in rows if r["status"] == STATUS_LOW_TRUST)

    st.markdown("**🔑 Từ khóa**")
    c = st.columns(4)
    c[0].metric("Dòng đọc từ CSV", kw.get("rows", 0))
    c[1].metric("Gửi Groq", len(kw.get("sent", [])))
    c[2].metric("Bị bỏ (Dùng = False / Loại)", len(kw.get("unused", [])))
    c[3].metric("Cắt bớt (quá 60)", len(kw.get("cut", [])))
    if kw.get("main"):
        st.caption(f"Từ khóa chính: **{kw['main']}** · File: {', '.join(kw.get('files', []))}")
    with st.expander(f"Xem {len(kw.get('sent', []))} từ khóa gửi Groq · {len(kw.get('unused', []))} từ khóa bị bỏ"):
        a, b = st.columns(2)
        a.markdown("✅ **Gửi Groq**")
        a.dataframe(pd.DataFrame({"Từ khóa": kw.get("sent", [])}), hide_index=True, width="stretch")
        b.markdown("❌ **Không gửi** (bạn bỏ tick ở tab 🔑, hoặc tool tự loại)")
        b.dataframe(pd.DataFrame([{"Từ khóa": u["keyword"], "Lý do": u["reason"]} for u in kw.get("unused", [])]
                                 or [{"Từ khóa": "—", "Lý do": ""}]), hide_index=True, width="stretch")
        if kw.get("core_terms"):
            st.caption("Tên sản phẩm dùng để lọc (rút từ từ khóa): " + ", ".join(kw["core_terms"]))

    st.markdown("**🛍️ Sản phẩm**")
    c = st.columns(5)
    c[0].metric("Trong file JSON", pr.get("raw", 0))
    c[1].metric("Trùng (bỏ)", pr.get("duplicates", 0))
    c[2].metric("Phụ kiện (bỏ)", n_acc)
    c[3].metric("Không chứa từ khóa (bỏ)", n_unrel)
    c[4].metric("Gửi Groq", pr.get("sent", 0), help=f"{n_over} sản phẩm liên quan nhưng ngoài top 30 lượt bán")
    if n_dup or n_low:
        st.caption(f"Thêm: {n_dup} tin trùng mẫu (cùng 1 sản phẩm ở shop khác — giữ tin bán chạy hơn) · {n_low} sản "
                   "phẩm ít lượt bán / rating thấp — đều KHÔNG gửi Groq.")
    if pr.get("warning"):
        st.warning(pr["warning"])
    st.caption("Lọc bằng CODE trước khi gửi Groq: bỏ trùng → bỏ phụ kiện → bỏ sản phẩm có tên không chứa tên sản "
               "phẩm của từ khóa nào → bỏ tin TRÙNG MẪU → bỏ sản phẩm ít lượt bán / rating thấp (khi đủ sản phẩm tốt) "
               "→ xếp theo độ tin cậy (lượt bán + rating + lượt đánh giá), lấy 30. Groq chỉ được chọn trong số này "
               "(và code gắn lại tên / giá / link gốc theo mã sản phẩm, Groq không tự viết link).")
    statuses = ["Tất cả", STATUS_SENT, STATUS_DUP_MODEL, STATUS_LOW_TRUST, STATUS_OVER_LIMIT, STATUS_ACCESSORY,
                STATUS_UNRELATED]
    with st.expander(f"Xem bảng {len(rows)} sản phẩm (sau khi bỏ trùng)"):
        pick = st.radio("Lọc", statuses, horizontal=True, key=f"{key}_filter",
                        label_visibility="collapsed")
        data = [{"Trạng thái": ("⭐ ĐƯỢC CHỌN · " if chosen_names and r["name"] in chosen_names else "") + r["status"],
                 "Tên sản phẩm": r["name"], "Giá": _fmt_money(r["price"]), "Đã bán": r["sold"],
                 "Rating": r.get("rating"), "Đánh giá": r.get("reviews"), "Điểm tin cậy": r.get("trust"),
                 "Trùng với": (r.get("dup_of") or "")[:60],
                 "Từ khóa có trong tên": ", ".join(r["matched"][:4]) + ("…" if len(r["matched"]) > 4 else "")}
                for r in rows if pick == "Tất cả" or r["status"] == pick]
        st.dataframe(pd.DataFrame(data or [{"Trạng thái": "—"}]), hide_index=True, width="stretch")


# ------------------------------------------------------------- trước khi chạy --

def render_selection_preview(input_dir: Path):
    """Trả về (keywords, products, report) để nút chạy dùng lại đúng dữ liệu đã xem."""
    try:
        keywords, products, report = _cached(str(input_dir), _files_signature(input_dir))
    except (FileNotFoundError, ValueError) as e:
        st.error(f"[LỖI DỮ LIỆU ĐẦU VÀO] {e}")
        return None
    with st.container(border=True):
        st.markdown("### 📊 Dữ liệu sẽ gửi Groq — xem trước (0 quota)")
        _input_section(report, key="sel_preview")
    return keywords, products, report


# ------------------------------------------------------------- sau khi chạy --

def _current_csv_status(input_dir: Path | None) -> dict:
    """Run cũ (không có báo cáo): đối chiếu với CSV hiện có trong data/input/ — từ khóa -> trạng thái."""
    if input_dir is None:
        return {}
    try:
        from .ingestion import load_keywords_report
        kws, rep = load_keywords_report(input_dir, max_keywords=10 ** 6)
    except (FileNotFoundError, ValueError):
        return {}
    out = {k["keyword"].lower(): "✅" for k in kws}
    out.update({u["keyword"].lower(): "⚠️ Bạn đã bỏ tick" for u in rep.get("unused", [])})
    return out


def render_selection_report(run_dir: Path, selection_data: dict, input_dir: Path | None = None) -> None:
    path = run_dir / SELECTION_REPORT_FILE
    report = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    filtered = {}
    fpath = run_dir / "02_keywords_filtered.json"
    if fpath.exists():
        filtered = json.loads(fpath.read_text(encoding="utf-8"))

    with st.container(border=True):
        st.markdown("### 📊 Báo cáo chọn sản phẩm")
        if not report:
            st.info("Run này tạo TRƯỚC khi có báo cáo chi tiết — chỉ kiểm tra được 5 sản phẩm với từ khóa Groq đã "
                    "lọc. Chạy lại chọn sản phẩm (tạo run mới) để có đầy đủ báo cáo.")

        # Từ khóa Groq chọn
        gk = report.get("groq_keywords", {})
        kept = gk.get("kept") or filtered.get("keywords", [])
        invented = gk.get("invented_removed", [])
        st.markdown("**🧠 Từ khóa Groq chọn cho ngách** (dùng để chọn sản phẩm)")
        if invented:
            st.warning(f"Đã CHẶN {len(invented)} từ khóa Groq tự bịa (không có trong CSV của bạn): "
                       + ", ".join(f"“{x}”" for x in invented))
        elif report:
            st.caption("✅ Mọi từ khóa Groq chọn đều có thật trong CSV.")
        if gk.get("no_product_removed"):
            st.warning("Đã BỎ từ khóa Groq chọn nhưng không có sản phẩm nào trong file JSON đúng từ khóa đó: "
                       + ", ".join(f"“{x}”" for x in gk["no_product_removed"]))
        nop = report.get("no_product_keywords") or []
        if nop:
            with st.expander(f"⛔ {len(nop)} từ khóa trong CSV KHÔNG có sản phẩm nào để bán (không dùng làm ngách)"):
                st.caption("Thương hiệu / model / đặc điểm trong từ khóa không có trong tên sản phẩm nào của file "
                           "JSON. Muốn làm video theo từ khóa đó → crawl thêm sản phẩm đúng từ khóa vào file JSON.")
                st.write(", ".join(f"`{x}`" for x in nop[:80]))
        sent = set((report.get("keywords") or {}).get("sent", []))
        if report:
            def in_csv(kw: str) -> str:
                return "✅" if kw in sent else "?"
        else:
            current = _current_csv_status(input_dir)
            not_found = [k.get("keyword") for k in kept if current and (k.get("keyword") or "").lower() not in current]
            if not_found:
                st.warning(f"{len(not_found)} từ khóa Groq chọn KHÔNG có trong CSV hiện tại (Groq tự bịa — run cũ chưa "
                           "có bộ chặn): " + ", ".join(f"“{x}”" for x in not_found))

            def in_csv(kw: str) -> str:
                if not current:
                    return "—"
                return current.get((kw or "").lower(), "❌ Không có (Groq bịa)")
        st.dataframe(pd.DataFrame([{
            "Từ khóa": k.get("keyword"), "Điểm phù hợp": k.get("score"), "Lý do": k.get("reason", ""),
            "Có trong CSV": in_csv(k.get("keyword"))} for k in kept]
            or [{"Từ khóa": "—"}]), hide_index=True, width="stretch")
        if not report:
            st.caption("Run cũ: cột 'Có trong CSV' đối chiếu với file CSV HIỆN CÓ trong data/input/.")

        if report.get("duplicates_replaced"):
            st.warning("Groq chọn TRÙNG MẪU — tool đã tự thay:\n" + "\n".join(f"- {x}" for x in report["duplicates_replaced"]))

        # 5 sản phẩm
        st.markdown("**🎯 Kiểm tra 5 sản phẩm được chọn** (code đối chiếu tên sản phẩm với từ khóa)")
        checks = report.get("selected_check") or check_selected(
            selection_data, list(sent), [k.get("keyword", "") for k in kept])
        by_rank = {p.get("rank"): p for p in selection_data.get("selected_products", [])}
        bad = [c for c in checks if not c["matched"]]
        if bad:
            st.error(f"{len(bad)} sản phẩm có tên KHÔNG chứa từ khóa nào — xem lại trước khi làm video.")
        st.dataframe(pd.DataFrame([{
            "Top": c["rank"], "Sản phẩm": c["name"], "Giá": _fmt_money((by_rank.get(c["rank"]) or {}).get("price")),
            "Từ khóa có trong tên": ", ".join(c["matched"][:5]), "Kiểm tra": c["status"]}
            for c in sorted(checks, key=lambda c: c["rank"] or 0)]), hide_index=True, width="stretch")

        if report.get("keywords") or report.get("products"):
            with st.expander("Dữ liệu đầu vào lúc chạy (từ khóa / sản phẩm gửi Groq và bị loại)"):
                _input_section(report, chosen_names={p.get("name") for p in selection_data.get("selected_products", [])},
                               key=f"sel_report_{run_dir.name}")
