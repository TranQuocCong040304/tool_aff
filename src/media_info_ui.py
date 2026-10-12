"""Upload file thông tin sản phẩm (.md) ở tab 🖼️ Ảnh sản phẩm (gọi từ app.py).

- render_bulk_md_upload(): thả cùng lúc nhiều file .md — tool tự khớp vào đúng Top theo Item ID / tên.
- render_product_md():     ô upload .md riêng trong từng Top + tóm tắt thông tin đã lưu.
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from .product_info import find_owner, info_path, load_info, match_product, parse_product_md


def _fmt(v) -> str:
    return f"{int(v):,}".replace(",", ".") if isinstance(v, (int, float)) else str(v)


def md_status(product: dict, run_dir: Path) -> str:
    info = load_info(product, run_dir)
    if info is None:
        return "chưa có file thông tin"
    return "📄 có file thông tin" if match_product(info, product) is not False else "⚠️ file thông tin KHÁC sản phẩm"


def _save(product: dict, run_dir: Path, data: bytes) -> Path:
    path = info_path(product, run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _ver_key(name: str) -> str:
    """Key ô upload có số phiên bản — tăng sau khi lưu để ô upload tự trống (không lưu lại lần nữa)."""
    return f"{name}_v{st.session_state.get(name + '_ver', 0)}"


def _bump(name: str) -> None:
    st.session_state[name + "_ver"] = st.session_state.get(name + "_ver", 0) + 1


def render_bulk_md_upload(run_dir: Path, products: list[dict]) -> None:
    base = f"md_bulk_{run_dir.name}"
    with st.container(border=True):
        st.markdown("**📄 File thông tin sản phẩm (.md)** — thả cùng lúc file của nhiều Top, tool **tự lưu** vào đúng "
                    "sản phẩm theo Item ID trong file (hoặc theo tên).")
        ups = st.file_uploader("File .md thông tin sản phẩm", type=["md", "markdown", "txt"], accept_multiple_files=True,
                               key=_ver_key(base), label_visibility="collapsed")
        if ups:
            # Lưu NGAY khi upload (trước đây phải bấm thêm nút Lưu — dễ quên, file không được lưu).
            done, miss = [], []
            for f in ups:
                raw = f.getvalue()
                try:
                    info = parse_product_md(raw.decode("utf-8-sig"))
                except UnicodeDecodeError:
                    miss.append(f"{f.name} (không đọc được, cần UTF-8)")
                    continue
                owner = find_owner(info, products)
                if owner is None:
                    miss.append(f"{f.name} (Item ID {info.get('item_id') or '?'} không thuộc Top nào)")
                    continue
                _save(owner, run_dir, raw)
                done.append(f"TOP {owner['rank']} ← {f.name}")
            st.session_state[f"md_bulk_msg_{run_dir.name}"] = (done, miss)
            _bump(base)
            st.rerun()
        msg = st.session_state.pop(f"md_bulk_msg_{run_dir.name}", None)
        if msg:
            done, miss = msg
            if done:
                st.success("Đã lưu: " + " · ".join(done))
            if miss:
                st.error("Không khớp được (chưa lưu): " + " · ".join(miss))
        have = sum(1 for p in products if (load_info(p, run_dir) is not None))
        st.caption(f"Đã có file thông tin: **{have}/{len(products)}** sản phẩm.")


def render_product_md(product: dict, run_dir: Path) -> None:
    rank = product["rank"]
    st.markdown("**📄 Thông tin sản phẩm (.md)** — Gemini dùng thông số / rating / lượt bán / mô tả trong file này "
                "để viết lời bình đúng sự thật.")
    base = f"md_{run_dir.name}_{rank}"
    up = st.file_uploader("File .md thông tin sản phẩm", type=["md", "markdown", "txt"], key=_ver_key(base),
                          label_visibility="collapsed")
    saved_msg = st.session_state.pop(base + "_msg", None)
    if saved_msg:
        st.success(saved_msg)
    if up is not None:
        raw = up.getvalue()
        try:
            info_new = parse_product_md(raw.decode("utf-8-sig"))
        except UnicodeDecodeError:
            st.error("Không đọc được file (cần mã hóa UTF-8).")
            info_new = None
        if info_new is not None:
            same = match_product(info_new, product)
            force = True
            if same is False:
                st.error(f"File này là của sản phẩm KHÁC (Item ID {info_new.get('item_id')} không khớp link Top {rank}): "
                         f"“{info_new.get('title', '')[:80]}”.")
                force = st.checkbox("Vẫn lưu cho Top này", key=f"md_force_{run_dir.name}_{rank}")
                if st.button("💾 Vẫn lưu", key=f"md_save_{run_dir.name}_{rank}", disabled=not force):
                    _save(product, run_dir, raw)
                    _bump(base)
                    st.rerun()
            else:
                # Đúng sản phẩm (hoặc không kiểm tra được) -> lưu NGAY, không cần bấm nút.
                _save(product, run_dir, raw)
                st.session_state[base + "_msg"] = f"Đã lưu {up.name}" + (
                    "" if same else " (không kiểm tra được Item ID — xem lại tên sản phẩm trong file cho chắc)")
                _bump(base)
                st.rerun()

    info = load_info(product, run_dir)
    if info is None:
        st.caption("Chưa có file thông tin — Gemini chỉ dựa vào tên / giá / ảnh.")
        return
    same = match_product(info, product)
    head = []
    if info.get("brand"):
        head.append(f"Thương hiệu **{info['brand']}**")
    if info.get("price"):
        head.append(f"Giá **{_fmt(info['price'])}đ**")
    if info.get("rating"):
        head.append(f"⭐ **{info['rating']:g}**" + (f" ({_fmt(info['reviews'])} đánh giá)" if info.get("reviews") else ""))
    if info.get("sold"):
        head.append(f"Đã bán **{_fmt(info['sold'])}**")
    icon = "✅" if same else ("⚠️" if same is False else "ℹ️")
    st.markdown(f"{icon} Đã có file thông tin: " + " · ".join(head))
    if same is False:
        st.warning("Item ID trong file KHÔNG khớp link sản phẩm này — Gemini sẽ KHÔNG dùng file này. Upload lại file đúng.")
    with st.expander("Xem nội dung sẽ gửi Gemini"):
        attrs = info.get("attributes") or {}
        if attrs:
            st.markdown("  \n".join(f"- {k}: {v}" for k, v in attrs.items()))
        st.text(info.get("description") or "(không có mô tả)")
    if st.button("🗑️ Xóa file thông tin", key=f"md_del_{run_dir.name}_{rank}"):
        info_path(product, run_dir).unlink(missing_ok=True)
        st.rerun()
