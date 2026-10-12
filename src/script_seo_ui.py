"""Phần giao diện SEO trong tab ✍️ Script & Audio (gọi từ app.py):

- render_seo_input():    khung "🔑 Đầu vào SEO cho script" — nguồn dữ liệu, tiêu đề, xác nhận lời hứa
                          trải nghiệm, từ khóa được giao cho từng phần (xem trước khi tốn quota).
- render_seo_coverage(): bảng "từ khóa đã nói" (code đếm) + nút viết lại hook & CTA (chỉ Groq).
- render_youtube_tags(): danh sách thẻ đầy đủ để dán vào ô Thẻ khi đăng YouTube.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from .llm_errors import LLMJSONError, LLMQuotaExceededError, LLMServerError
from .seo_context import (SOURCE_SEO_TAB, allocate_keywords, keyword_coverage, load_seo_context,
                          title_keyword)
from .seo_titles import SEO_FILE


def _seo_file(run_dir: Path) -> Path:
    return run_dir / SEO_FILE


def _save_claims_flag(run_dir: Path, value: bool) -> None:
    path = _seo_file(run_dir)
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return
    if bool(data.get("claims_confirmed")) != value:
        data["claims_confirmed"] = value
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def render_seo_input(run_dir: Path, selection_data: dict) -> bool:
    """Vẽ khung đầu vào SEO. Trả về claims_confirmed (người dùng xác nhận lời hứa trải nghiệm là thật)."""
    ctx = load_seo_context(run_dir)
    products = selection_data.get("selected_products", [])
    script_path = run_dir / "04_script.txt"
    claims_ok = False

    title = "🔑 Đầu vào SEO cho script"
    if not ctx.has_keywords:
        title += " — chưa có dữ liệu"
    with st.expander(title, expanded=True):
        if not ctx.has_keywords:
            st.info("Chưa có từ khóa SEO cho run này. Lọc từ khóa ở tab **📥 Dữ liệu đầu vào** (🔎 Lọc từ khóa), "
                    "nhập tiêu đề SEO ở mục kịch bản theo mẫu bên dưới. Không có thì script vẫn viết như bình thường "
                    "(không chèn từ khóa, hook không bám tiêu đề).")
            return False
        st.caption(f"Nguồn: {ctx.source}" + (f" — lưu lúc {ctx.saved_at.replace('T', ' ')}" if ctx.saved_at else ""))
        if ctx.title:
            st.markdown(f"**Tiêu đề video:** {ctx.title}")
            tk = title_keyword(ctx)
            if tk:
                st.caption(f"Hook sẽ nói “{tk}” (từ khóa có trong tiêu đề) ngay 2 câu đầu và nhắc lại lời hứa của tiêu đề.")
        elif ctx.source != SOURCE_SEO_TAB:
            st.caption("Chưa có tiêu đề đã chọn — hook viết như cũ, chỉ chèn từ khóa.")

        claims = ctx.title_claims
        if claims:
            saved = False
            try:
                saved = bool(json.loads(_seo_file(run_dir).read_text(encoding="utf-8")).get("claims_confirmed"))
            except (ValueError, OSError):
                pass
            claims_ok = st.checkbox(
                f"Nội dung “{', '.join(claims)}” trong tiêu đề là THẬT (mình đã thực sự trải nghiệm / test)",
                value=saved, key=f"claims_ok_{run_dir.name}",
                help="Tick: hook được nói ở ngôi thứ nhất đúng như tiêu đề. Không tick: hook vẫn bám chủ đề tiêu đề "
                     "nhưng nói trung thực ('mình đã tìm hiểu, so sánh kỹ…'), không bịa trải nghiệm.",
            )
            _save_claims_flag(run_dir, claims_ok)
            if not claims_ok:
                st.warning("Tiêu đề hứa trải nghiệm cá nhân nhưng chưa xác nhận là thật — hook sẽ KHÔNG bịa trải nghiệm. "
                           "Người xem bấm vì lời hứa đó mà video không có thì dễ thoát sớm: nên đổi sang tiêu đề khác "
                           "ở tab 🔑 nếu video là dạng tổng hợp / so sánh.")

        alloc = allocate_keywords(ctx, products)
        name_by_rank = {str(p.get("rank")): p.get("name", "") for p in products}
        rows = [{"Phần": "Hook (mở đầu)", "Từ khóa sẽ nói": ", ".join(alloc.hook)}]
        for rank in sorted(alloc.segments, key=lambda r: -int(r)):
            name = name_by_rank.get(rank, "")
            rows.append({"Phần": f"Top {rank}: {name[:45]}{'…' if len(name) > 45 else ''}",
                         "Từ khóa sẽ nói": ", ".join(alloc.segments[rank]) or "—"})
        rows.append({"Phần": "CTA (kết)", "Từ khóa sẽ nói": ", ".join(alloc.cta)})
        rows.append({"Phần": "Mô tả YouTube (2 câu đầu)", "Từ khóa sẽ nói": ", ".join(alloc.description[:4])})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        assigned = alloc.all_assigned()
        left = [k["keyword"] for k in ctx.keywords if k["keyword"].lower() not in assigned]
        st.caption("Từ khóa được CODE chia theo sản phẩm (giá rẻ → Top rẻ nhất, cao cấp / tốt nhất → Top 1, đặc tính / "
                   "đối tượng → sản phẩm có đặc tính đó); mỗi cụm phụ nói 1 lần, từ khóa chính ~3 lần."
                   + (f" Không giao vào lời đọc (vẫn dùng cho mô tả / thẻ): {', '.join(left[:8])}"
                      + ("…" if len(left) > 8 else "") + "." if left else ""))

        seo_path = _seo_file(run_dir)
        if script_path.exists() and seo_path.exists() and seo_path.stat().st_mtime > script_path.stat().st_mtime:
            st.warning("Tiêu đề / từ khóa SEO được cập nhật SAU khi viết script — hook & CTA có thể chưa khớp. Bấm "
                       "**🔁 Viết lại hook & CTA theo tiêu đề** bên dưới (chỉ Groq, không viết lại lời bình sản phẩm).")
    return claims_ok


def render_seo_coverage(run_dir: Path, script_text: str, selection_data: dict, text_client, client_err,
                        claims_ok: bool, regenerate_fn) -> None:
    ctx = load_seo_context(run_dir)
    if not ctx.has_keywords or not script_text.strip():
        return
    products = selection_data.get("selected_products", [])
    alloc = allocate_keywords(ctx, products)
    rows = keyword_coverage(script_text, ctx, alloc, products)
    missing = [r for r in rows if r["Trạng thái"].startswith("⚠️ Chưa")]
    stuffed = [r for r in rows if "Lặp nhiều" in r["Trạng thái"]]
    label = f"🔎 Từ khóa SEO đã nói trong script — {len(rows) - len(missing)}/{len(rows)} cụm"
    if stuffed:
        label += f", {len(stuffed)} cụm lặp nhiều"
    with st.expander(label, expanded=bool(missing or stuffed)):
        st.caption("Code tự đếm trên script hiện tại (kể cả phần bạn sửa tay, không phân biệt hoa thường). "
                   "“Chưa nói” thường do cụm đó không hợp sản phẩm nên AI bỏ qua — sửa tay ngay trong ô script nếu muốn.")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    if st.button("🔁 Viết lại hook & CTA theo tiêu đề (chỉ Groq — giữ nguyên lời bình sản phẩm)",
                 disabled=client_err is not None, help=client_err or
                 "Dùng khi bạn đổi tiêu đề / từ khóa ở tab 🔑 sau khi đã có script. Không gọi Gemini, không tốn "
                 "quota viết lại lời bình từng sản phẩm; metadata (mô tả) cũng được viết lại."):
        with st.spinner("Groq đang viết lại hook, CTA và metadata..."):
            try:
                result = regenerate_fn(run_dir, text_client, claims_confirmed=claims_ok)
            except (LLMJSONError, LLMQuotaExceededError, LLMServerError, ValueError) as e:
                st.error(f"[LỖI] {e}")
                return
            except Exception as e:  # mất mạng / lỗi SDK — script cũ vẫn giữ nguyên, không ghi gì
                st.error(f"[LỖI] Không viết lại được ({type(e).__name__}): {e}. Script hiện tại vẫn giữ nguyên.")
                return
        msg = "Đã viết lại hook & CTA theo tiêu đề."
        if result.get("audio_stale"):
            msg += " ⚠️ Audio đang là bản cũ — bấm sinh audio lại để giọng đọc khớp script mới."
        st.session_state["seo_regen_msg"] = msg
        st.rerun()
    msg = st.session_state.pop("seo_regen_msg", None)
    if msg:
        st.success(msg)


def render_youtube_tags(metadata: dict) -> None:
    tags = metadata.get("youtube_tags") if metadata else None
    if not tags:
        return
    st.markdown("**🏷️ Thẻ YouTube** (dán vào ô *Thẻ* khi đăng — lấy từ tab 🔑, dưới 500 ký tự)")
    st.code(", ".join(tags), language=None, wrap_lines=True)
