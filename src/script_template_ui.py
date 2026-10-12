"""Khung "🧩 Kịch bản theo mẫu kênh" trong tab ✍️ Script & Audio (gọi từ app.py).

Đầu vào: tiêu đề SEO (ô nhập) + file CSV từ khóa (ô upload) + 5 sản phẩm & ảnh / file .md của tab 🖼️.
Chọn 1 hoặc nhiều mẫu (mỗi kênh 1 mẫu) -> bấm 1 nút "🚀 Tạo kịch bản" -> mỗi mẫu ra 1 kịch bản riêng
(<run>/scripts/). Kịch bản nào ưng thì bấm "📌 Dùng cho Audio" để chuyển thành 04_script.txt.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pandas as pd
import streamlit as st

from .llm_errors import LLMJSONError, LLMQuotaExceededError, LLMServerError
from .product_info import load_info, match_product
from .product_links import list_all_product_images
from .script_templates import (KEYWORDS_FILE, build_chat_prompt, chat_images_zip, parse_chat_meta, parse_chat_output,
                               save_chat_script, title_plan, default_title, delete_template, generate_scripts_for_template,
                               input_keyword_csvs, list_scripts, list_templates, load_keyword_rows, overlays, save_template,
                               to_audio_script, to_markdown, voice_only, word_count)

from .script_length import (DEFAULT_MINUTES, budget, count_words, fmt_minutes, load_sfx, load_target, load_tones,
                            load_wpm, measure_wpm, save_wpm,
                            save_target)
from .sfx import SFX_DIR, SFX_NAMES, sfx_path
from .script_parser import parse_segments
from .voice_tones import TONE_GROUPS

CALLS_PER_SCRIPT = 7   # mở đầu + 5 sản phẩm + kết
NAME_FILE = "script_keywords_name.txt"
SETTINGS_FILE = "script_template_settings.json"


def _settings(run_dir: Path) -> dict:
    p = run_dir / SETTINGS_FILE
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (ValueError, OSError):
        return {}


def _save_settings(run_dir: Path, data: dict) -> None:
    (run_dir / SETTINGS_FILE).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ------------------------------------------------------------------ đầu vào --

def _render_keywords(run_dir: Path) -> list[dict]:
    st.markdown("**2️⃣ Từ khóa** — tự lấy file đã lọc ở tab 📥 Dữ liệu đầu vào (data/input/). Muốn dùng file khác "
                "cho riêng run này thì thả CSV vào ô dưới. Tool tự phân loại Chính / Kéo traffic / Phụ / Đuôi dài.")
    up = st.file_uploader("File CSV từ khóa", type=["csv"], key=f"tpl_kw_{run_dir.name}_{st.session_state.get('tpl_kw_ver', 0)}",
                          label_visibility="collapsed")
    if up is not None:
        data = up.getvalue()
        csv_path = run_dir / KEYWORDS_FILE
        if not csv_path.exists() or csv_path.read_bytes() != data:
            csv_path.write_bytes(data)
            (run_dir / NAME_FILE).write_text(up.name, encoding="utf-8")
    input_csv = None
    cands = input_keyword_csvs(run_dir)
    if not (run_dir / KEYWORDS_FILE).exists() and len(cands) > 1:
        names = [p.name for p in cands]
        pick = st.selectbox("File từ khóa trong data/input/ (tab 📥)", names, key=f"tpl_kw_input_{run_dir.name}",
                            help="Mặc định file đã phân loại mới nhất. Muốn dùng file khác thì chọn ở đây.")
        input_csv = cands[names.index(pick)]
    rows, source = load_keyword_rows(run_dir, input_csv)
    if not rows:
        st.warning("Chưa có từ khóa — lọc file CSV ở tab 📥 Dữ liệu đầu vào (🔎 Lọc từ khóa) hoặc upload CSV ở trên.")
        return []
    counts = Counter(r.get("role") or "?" for r in rows)
    c1, c2 = st.columns([4, 1])
    c1.caption(f"Nguồn: {source} · **{len(rows)} từ khóa** dùng được — "
               + " · ".join(f"{k}: {v}" for k, v in counts.most_common()))
    if (run_dir / KEYWORDS_FILE).exists() and c2.button("🗑️ Bỏ file CSV", key=f"tpl_kw_del_{run_dir.name}",
                                                       help="Quay về dùng file từ khóa ở tab 📥 Dữ liệu đầu vào"):
        (run_dir / KEYWORDS_FILE).unlink(missing_ok=True)
        (run_dir / NAME_FILE).unlink(missing_ok=True)
        st.session_state["tpl_kw_ver"] = st.session_state.get("tpl_kw_ver", 0) + 1   # xóa file khỏi ô upload
        st.rerun()
    with st.expander("Xem bảng từ khóa sẽ gửi AI"):
        st.dataframe(pd.DataFrame([{"Từ khóa": r["keyword"], "Vai trò": r.get("role"), "Nhóm": r.get("group"),
                                    "Volume": r.get("volume")} for r in rows]),
                     hide_index=True, width="stretch")
    return rows


def _render_products(run_dir: Path, products: list[dict]) -> None:
    st.markdown("**3️⃣ Sản phẩm** — lấy từ tab 🖼️ Ảnh sản phẩm (ảnh thật + file .md thông tin).")
    rows, missing = [], []
    for p in sorted(products, key=lambda x: x.get("rank") or 0):
        n_img = len(list_all_product_images(p, run_dir))
        info = load_info(p, run_dir)
        md = "✅" if info is not None and match_product(info, p) is not False else ("⚠️ sai SP" if info else "—")
        if not n_img and md != "✅":
            missing.append(p.get("rank"))
        rows.append({"Top": p.get("rank"), "Sản phẩm": p.get("name"), "Ảnh": n_img, "File .md": md})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if missing:
        st.caption(f"⚠️ Top {', '.join(map(str, missing))} chưa có ảnh lẫn file .md — AI chỉ có tên & giá, lời bình sẽ "
                   "chung chung. Nên bổ sung ở tab 🖼️.")


def _render_template_manager(templates: list[dict]) -> None:
    with st.expander("✏️ Quản lý mẫu kịch bản (thêm / sửa / xóa — mỗi kênh 1 mẫu)"):
        st.caption("Mẫu = prompt cấu trúc kịch bản (giọng văn, các phần, độ dài...). Chỗ cần tên kênh ghi {{KENH}}. Lưu ở "
                   "prompts/script_templates/. Quy tắc trung thực của tool luôn được gắn thêm sau mẫu.")
        opts = ["➕ Mẫu mới"] + [t["name"] for t in templates]
        pick = st.selectbox("Mẫu", opts, key="tpl_edit_pick")
        cur = next((t for t in templates if t["name"] == pick), None)
        name = st.text_input("Tên mẫu (tên kênh)", cur["name"] if cur else "", key=f"tpl_name_{pick}")
        body = st.text_area("Nội dung mẫu (prompt)", cur["body"] if cur else "", height=300, key=f"tpl_body_{pick}")
        c1, c2 = st.columns(2)
        if c1.button("💾 Lưu mẫu", key=f"tpl_save_{pick}", disabled=not (name.strip() and body.strip())):
            save_template(name, body, cur["id"] if cur else None)
            st.success("Đã lưu mẫu.")
            st.rerun()
        if cur and c2.button("🗑️ Xóa mẫu", key=f"tpl_del_{pick}"):
            delete_template(cur["id"])
            st.rerun()


# ------------------------------------------------------------------ kết quả --

def _render_results(run_dir: Path, products: list[dict]) -> None:
    scripts = list_scripts(run_dir)
    if not scripts:
        return
    st.markdown(f"**📚 Kịch bản đã tạo ({len(scripts)})**")
    for doc in scripts:
        sid = doc["id"]
        done = doc.get("complete")
        head = (f"{'✅' if done else '⏸️ dở dang'} {doc.get('template_name')} · "
                f"{doc.get('created_at', '').replace('T', ' ')} · {len(doc.get('sections', []))}/7 phần · "
                f"~{word_count(doc):,} từ".replace(",", ".") + f" ≈ {fmt_minutes(word_count(doc))} (phút:giây)")
        with st.expander(head):
            st.caption(f"Tiêu đề: {doc.get('title')} · kênh: {doc.get('channel') or '(chung)'} · "
                       f"{doc.get('keyword_count')} từ khóa · "
                       f"{'đã test thật' if doc.get('tested') else 'chưa test thật'} · "
                       f"ưu đãi: {doc.get('offers') or 'không nhập'}")
            lm = doc.get("length_minutes")
            render_length_report([(s_["label"], s_["kind"], s_["text"]) for s_ in doc.get("sections", [])],
                                 budget(*lm, n_products=max(len(products), 1)) if lm else None)
            md = to_markdown(doc)
            t1, t2, t3 = st.tabs(["📝 Kịch bản", "🎬 Chữ trên màn hình & âm thanh", "🔊 Chỉ lời đọc"])
            if doc.get("youtube_meta"):
                with st.expander("📝 Mô tả YouTube / chương / tags (từ khóa traffic)"):
                    st.code(doc["youtube_meta"], language=None, wrap_lines=True)
            with t1:
                st.markdown(md)
            with t2:
                ov = overlays(doc)
                if ov:
                    st.dataframe(pd.DataFrame(ov), hide_index=True, width="stretch")
                else:
                    st.caption("Không có chỉ dẫn [Text on screen] / [Sound Effect].")
            with t3:
                st.text("\n\n".join(voice_only(s["text"]) for s in doc.get("sections", [])))
            c1, c2, c3, c4 = st.columns(4)
            c1.download_button("⬇️ .md", md.encode("utf-8"), file_name=f"{sid}.md", mime="text/markdown",
                               key=f"dl_md_{sid}")
            c2.download_button("⬇️ Lời đọc .txt",
                               "\n\n".join(voice_only(s["text"]) for s in doc.get("sections", [])).encode("utf-8"),
                               file_name=f"{sid}_loi-doc.txt", mime="text/plain", key=f"dl_txt_{sid}")
            if done:
                target = run_dir / "04_script.txt"
                confirm = True
                if target.exists():
                    confirm = c4.checkbox("Ghi đè 04_script.txt", key=f"ow_{sid}")
                if c3.button("📌 Dùng cho Audio", key=f"use_{sid}", disabled=not confirm,
                             help="Chuyển kịch bản này thành 04_script.txt (bỏ chỉ dẫn chữ / âm thanh) để sinh "
                                  "audio & ghép video ở bên dưới."):
                    apply_to_audio(run_dir, doc)
                    st.session_state["_tpl_used_msg"] = f"Đã dùng “{doc.get('template_name')}” làm 04_script.txt."
                    st.rerun()
            if st.button("🗑️ Xóa kịch bản", key=f"rm_{sid}"):
                Path(doc["_path"]).unlink(missing_ok=True)
                Path(doc["_path"]).with_suffix(".md").unlink(missing_ok=True)
                st.rerun()


# ------------------------------------------------------------------ độ dài --

def _parse_mmss(text: str) -> float | None:
    """'5:00' / '5:30' / '5' (phút) -> số giây."""
    text = (text or "").strip().replace(",", ".")
    try:
        if ":" in text:
            m, sec = text.split(":", 1)
            return int(m) * 60 + float(sec)
        return float(text) * 60 if text else None
    except ValueError:
        return None


def _render_speed(run_dir: Path) -> int:
    """Tốc độ giọng đọc (từ/phút) — dùng chung mọi run; đo lại được từ voice thật."""
    cur = load_wpm()
    c1, c2 = st.columns([2, 3])
    wpm = int(c1.number_input("🗣️ Tốc độ giọng đọc (từ/phút)", 80, 400, cur, 5, key="voice_wpm",
                              help="Mỗi giọng / công cụ đọc nhanh chậm khác nhau. Đo lại ở ô bên cạnh sau khi có voice thật."))
    if wpm != cur:
        save_wpm(wpm)
    with c2.expander("📏 Đo tốc độ từ voice thật"):
        m = measure_wpm(run_dir)
        if m:
            st.caption(f"Audio đã sinh của run này: {m['words']:,} từ / {int(m['seconds'] // 60)}:{int(m['seconds'] % 60):02d}"
                       f" → **{m['wpm']} từ/phút**".replace(",", "."))
            if st.button(f"Dùng {m['wpm']} từ/phút", key=f"wpm_auto_{run_dir.name}"):
                save_wpm(m["wpm"])
                st.session_state.pop("voice_wpm", None)
                st.rerun()
        script = run_dir / "04_script.txt"
        if script.exists():
            try:   # chỉ đếm LỜI ĐỌC (bỏ dòng "### SẢN PHẨM: …" và thẻ)
                words = sum(count_words(sg.raw_text) for sg in parse_segments(script.read_text(encoding="utf-8")))
            except ValueError:
                words = 0
            if words < 50:
                return wpm
            dur = st.text_input(f"Voice của 04_script.txt hiện tại ({words:,} từ) dài bao lâu? (phút:giây)".replace(",", "."),
                                placeholder="vd 5:00", key=f"wpm_dur_{run_dir.name}")
            secs = _parse_mmss(dur)
            if secs and secs >= 20:
                measured = int(round(words / secs * 60))
                if st.button(f"Dùng {measured} từ/phút", key=f"wpm_manual_{run_dir.name}"):
                    save_wpm(measured)
                    st.session_state.pop("voice_wpm", None)
                    st.rerun()
    return wpm


def render_length_target(run_dir: Path, n_products: int) -> dict:
    """Độ dài video mục tiêu (phút) + bật/tắt hiệu ứng âm thanh — dùng cho cả kịch bản theo mẫu lẫn script mặc định."""
    lo, hi = load_target(run_dir)
    sfx_on = load_sfx(run_dir)
    tones_on = load_tones(run_dir)
    with st.container(border=True):
        val = st.slider("⏱️ Độ dài video mục tiêu (phút)", 2, 30, (lo, hi), key=f"len_{run_dir.name}",
                        help="Tool tự chia số từ cho hook / từng sản phẩm / CTA (theo tốc độ giọng đọc bên dưới) và gắn "
                             "vào prompt — ưu tiên hơn độ dài ghi trong mẫu.")
        sfx = st.checkbox("🔊 Chèn hiệu ứng âm thanh (Swoosh / Pop / Ding / Tension / Win…) vào script & audio",
                          value=sfx_on, key=f"sfx_{run_dir.name}",
                          help="AI đặt [Sound Effect: …] ở mở đầu, chuyển sản phẩm, trước nhược điểm, trước câu bấm "
                               "link. Lúc sinh audio, tool chèn ÂM THANH THẬT vào đúng chỗ đó.")
        tones = st.checkbox("🎭 Gắn thẻ giọng đọc đầu mỗi đoạn ([Giọng trầm, nghiêm túc], [Chê nhẹ / Khách quan], "
                            "[Năng lượng cao, bùng nổ]…)", value=tones_on, key=f"tones_{run_dir.name}",
                            help="Cho giọng AI đọc được thẻ (ElevenLabs v3, Gemini TTS, Google Vids…) đọc đúng ngữ điệu. "
                                 "edge-tts KHÔNG đọc được cảm xúc — thẻ chỉ thành 1 nhịp ngắt ngắn.")
        wpm = _render_speed(run_dir)
        if tuple(val) != (lo, hi) or sfx != sfx_on or tones != tones_on:
            save_target(run_dir, *val, sfx=sfx, tones=tones)
        b = budget(*val, n_products=n_products, sfx=sfx, tones=tones, wpm=wpm)
        st.caption(f"≈ **{b['total'][0]:,}–{b['total'][1]:,} từ** cả video · hook {b['hook'][0]}–{b['hook'][1]} từ · "
                   f"mỗi sản phẩm {b['product'][0]}–{b['product'][1]} từ · CTA {b['cta'][0]}–{b['cta'][1]} từ"
                   .replace(",", "."))
        if tones:
            with st.expander("🎭 Bộ thẻ giọng đọc AI sẽ dùng"):
                for group, items in TONE_GROUPS.items():
                    st.markdown(f"**{group}**  \n" + "  \n".join(f"`{t}` — {use}" for t, use in items))
        if sfx:
            with st.expander("🎧 Nghe thử hiệu ứng âm thanh"):
                st.caption(f"Muốn dùng tiếng của bạn: thả file cùng tên (vd swoosh.mp3) vào thư mục {SFX_DIR} — "
                           "tool ưu tiên file của bạn thay cho tiếng tự tạo.")
                cols = st.columns(2)
                for i, (k, desc) in enumerate(SFX_NAMES.items()):
                    with cols[i % 2]:
                        st.caption(desc)
                        st.audio(str(sfx_path(k)))
    return b


def render_length_report(parts: list[tuple[str, str, str]], b: dict | None) -> None:
    """parts: [(nhãn, loại hook|product|cta|intro|outro, text)]. Bảng số từ + phút ước tính, so với mục tiêu."""
    if not parts:
        return
    rows, total = [], 0
    for label, kind, text in parts:
        w = count_words(text)
        total += w
        row = {"Phần": label, "Số từ": w, "Thời lượng": fmt_minutes(w)}
        if b:
            lo, hi = b[{"intro": "hook", "outro": "cta"}.get(kind, kind)]
            row["Mục tiêu"] = f"{lo}–{hi}"
            row[""] = "✅" if lo * 0.85 <= w <= hi * 1.15 else ("⬆️ dài" if w > hi else "⬇️ ngắn")
        rows.append(row)
    msg = f"Tổng **{total:,} từ ≈ {fmt_minutes(total)}** (phút:giây)".replace(",", ".")
    if b:
        lo_m, hi_m = b["minutes"]
        ok = b["total"][0] * 0.9 <= total <= b["total"][1] * 1.1
        msg += f" (mục tiêu {lo_m}–{hi_m} phút) " + ("✅" if ok else "⚠️ lệch mục tiêu — sửa tay hoặc viết lại")
    with st.expander(f"⏱️ Độ dài: {msg.replace('**', '')}"):
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption(f"Ước tính theo tốc độ giọng đọc {load_wpm()} từ/phút (chỉnh / đo lại ở ô 🗣️ phía trên).")


# ------------------------------------------------------------------ 04_script.txt --

SOURCE_FILE = "04_script_source.json"


def apply_to_audio(run_dir: Path, doc: dict) -> None:
    """Kịch bản (mẫu kênh / khung chat) -> 04_script.txt dùng cho sinh audio, kịch bản thu âm, ghép video."""
    (run_dir / "04_script.txt").write_text(to_audio_script(doc), encoding="utf-8")
    from .script_templates import SCREEN_TEXT_FILE, screen_texts
    (run_dir / SCREEN_TEXT_FILE).write_text(json.dumps(screen_texts(doc, run_dir), ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    (run_dir / SOURCE_FILE).write_text(json.dumps(
        {"id": doc.get("id"), "name": doc.get("template_name"), "created_at": doc.get("created_at")},
        ensure_ascii=False), encoding="utf-8")


def render_audio_source_notice(run_dir: Path) -> None:
    """Cho biết 04_script.txt đang là kịch bản nào; cảnh báo nếu có kịch bản mới hơn chưa được dùng."""
    script = run_dir / "04_script.txt"
    if not script.exists():
        return
    try:
        src = json.loads((run_dir / SOURCE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        src = {}
    done = [d for d in list_scripts(run_dir) if d.get("complete")]
    newest = max(done, key=lambda d: Path(d["_path"]).stat().st_mtime, default=None)
    label = (f"kịch bản “{src.get('name')}” ({(src.get('created_at') or '').replace('T', ' ')})" if src.get("id")
             else "script mặc định / sửa tay")
    st.caption(f"📄 04_script.txt (dùng cho audio, kịch bản thu âm, ghép video) đang là: **{label}**")
    if newest and newest.get("id") != src.get("id") and Path(newest["_path"]).stat().st_mtime > script.stat().st_mtime:
        st.warning(f"Có kịch bản MỚI HƠN chưa được dùng: “{newest.get('template_name')}” "
                   f"({newest.get('created_at', '').replace('T', ' ')}). Kịch bản thu âm bên dưới vẫn là bản CŨ — "
                   "thu theo bản cũ thì audio mới không khớp. Bấm nút dưới để dùng bản mới.")
        if st.button(f"📌 Dùng “{newest.get('template_name')}” cho Audio", key=f"use_newest_{run_dir.name}",
                     type="primary"):
            apply_to_audio(run_dir, newest)
            st.rerun()


# ------------------------------------------------------------------ kiểm tra tiêu đề --

def _render_title_check(title: str, keywords: list[dict], tested: bool) -> None:
    plan = title_plan(title, keywords, tested)
    with st.container(border=True):
        st.markdown("**🎯 Kịch bản bám tiêu đề SEO** — AI sẽ được giao:")
        main = ", ".join(f"“{k['keyword']}”" for k in plan["main"]) or "(chưa có)"
        st.markdown(f"- **Từ khóa chính** (hook 0–15 giây + chữ trên màn hình mở đầu + nhắc lại ở kết): {main}\n"
                    f"- **Từ khóa phụ** (thân video, từng sản phẩm): {len(plan['secondary'])} cụm\n"
                    f"- **Từ khóa traffic** (3 dòng đầu mô tả, tags, chương / timestamps): {len(plan['traffic'])} cụm")
        if plan["promises"]:
            st.markdown("**Lời hứa của tiêu đề phải thực hiện:**  \n" + "  \n".join(f"• {p_}" for p_ in plan["promises"]))
        for w in plan["warnings"]:
            st.warning(w)


# ------------------------------------------------------------------ khung chat --

def _render_chat_mode(run_dir: Path, templates: list[dict], chosen: list[str], title: str, keywords: list[dict],
                      tested: bool, offers: str, channel: str, length: dict | None, products: list[dict]) -> None:
    if not (title.strip() and keywords and products and templates):
        st.caption("Cần tiêu đề, từ khóa và sản phẩm trước (bước 1–3 ở trên).")
        return
    names = [t["name"] for t in templates]
    name = st.selectbox("Mẫu", names, index=names.index(chosen[0]) if chosen and chosen[0] in names else 0,
                        key=f"chat_tpl_{run_dir.name}")
    tpl = next(t for t in templates if t["name"] == name)
    with_images = st.checkbox("Mình sẽ kéo ảnh sản phẩm vào khung chat (AI mô tả theo ảnh)", value=False,
                              key=f"chat_img_{run_dir.name}")
    prompt = build_chat_prompt(tpl, run_dir, title.strip(), keywords, tested, offers, channel.strip(), length,
                               with_images=with_images)
    st.markdown("**Bước 1** — copy prompt (nút 📋 góc phải khung dưới) → dán vào khung chat AI → gửi.")
    st.code(prompt, language=None, height=220, wrap_lines=True)
    c1, c2 = st.columns(2)
    c1.download_button("⬇️ Tải prompt (.txt)", prompt.encode("utf-8"), file_name=f"prompt_{tpl['id']}.txt",
                       mime="text/plain", key=f"chat_dl_{run_dir.name}")
    if with_images:
        data, n = chat_images_zip(run_dir)
        c2.download_button(f"⬇️ Tải {n} ảnh (.zip) để kéo vào chat", data, file_name=f"anh_{run_dir.name}.zip",
                           mime="application/zip", key=f"chat_zip_{run_dir.name}", disabled=not n)
    st.caption(f"Prompt ~{len(prompt):,} ký tự. Đã gồm mẫu, quy tắc trung thực, độ dài, hiệu ứng, thẻ giọng và dữ liệu 5 "
               "sản phẩm. Câu trả lời bị cắt giữa chừng thì nhắn \"viết tiếp\" rồi dán cả 2 phần.".replace(",", "."))

    st.markdown("**Bước 2** — dán NGUYÊN câu trả lời của AI vào đây:")
    pasted = st.text_area("Kết quả từ khung chat", height=220, key=f"chat_paste_{run_dir.name}",
                          label_visibility="collapsed",
                          placeholder="=== MỞ ĐẦU ===\n...\n=== TOP 5 ===\n...\n=== KẾT ===\n...")
    if pasted.strip():
        sections, missing = parse_chat_output(pasted, products)
        meta = parse_chat_meta(pasted)
        if meta:
            with st.expander("📝 Mô tả YouTube / chương / tags AI viết kèm"):
                st.code(meta, language=None, wrap_lines=True)
        render_length_report([(s_["label"], s_["kind"], s_["text"]) for s_ in sections], length)
        if missing:
            st.warning("Chưa thấy các phần: " + " · ".join(missing) + ". Nhắn AI: \"viết thêm phần "
                       + missing[0].split(":")[0] + " theo đúng định dạng === … ===\" rồi dán bổ sung.")
        use_now = st.checkbox("📌 Dùng luôn cho Audio / thu âm (ghi đè 04_script.txt)", value=True,
                              key=f"chat_use_{run_dir.name}",
                              help="Bỏ tick nếu chỉ muốn lưu để so sánh — khi đó phải bấm 📌 Dùng cho Audio ở 📚 sau.")
        if st.button("💾 Lưu kịch bản từ khung chat", key=f"chat_save_{run_dir.name}", disabled=bool(missing),
                     type="primary"):
            path = save_chat_script(tpl, run_dir, title.strip(), keywords, tested, offers, sections, channel.strip(),
                                    length, youtube_meta=parse_chat_meta(pasted))
            if use_now:
                apply_to_audio(run_dir, json.loads(path.read_text(encoding="utf-8")))
                st.session_state["_tpl_used_msg"] = (f"Đã lưu kịch bản “{name}” từ khung chat và dùng làm 04_script.txt "
                                                     "(kịch bản thu âm / audio đã cập nhật).")
            else:
                st.session_state["_tpl_used_msg"] = (f"Đã lưu kịch bản “{name}” từ khung chat — xem ở 📚 Kịch bản đã "
                                                     "tạo, bấm 📌 Dùng cho Audio để dùng.")
            st.session_state.pop(f"chat_paste_{run_dir.name}", None)
            st.rerun()


# ------------------------------------------------------------------ chính --

def render_template_scripts(run_dir: Path, selection_data: dict, vision_client, client_err,
                            length: dict | None = None) -> None:
    products = selection_data.get("selected_products", [])
    st.markdown("### 🧩 Kịch bản theo mẫu kênh — 1 nút, nhiều kênh")
    msg = st.session_state.pop("_tpl_used_msg", None)
    if msg:
        st.success(msg + " Kéo xuống phần Audio để sinh giọng đọc.")
    settings = _settings(run_dir)

    with st.container(border=True):
        st.markdown("**1️⃣ Tiêu đề SEO**")
        title = st.text_input("Tiêu đề video", settings.get("title") or default_title(run_dir),
                              key=f"tpl_title_{run_dir.name}", label_visibility="collapsed",
                              placeholder="Nhập tiêu đề SEO (hoặc chọn ở tab 🔑)")
        channel = st.text_input("Tên kênh (không bắt buộc — điền vào chỗ {{KENH}} của mẫu, để trống = “kênh của mình”)",
                                settings.get("channel", ""), key=f"tpl_channel_{run_dir.name}",
                                placeholder="vd: Review Đồ Gia Dụng")
        keywords = _render_keywords(run_dir)
        _render_products(run_dir, products)

        st.markdown("**4️⃣ Xác nhận để AI không nói sai sự thật**")
        tested = st.checkbox("Mình đã thực sự dùng / test các sản phẩm này (cho phép nói “mình đã test”)",
                             value=bool(settings.get("tested")), key=f"tpl_tested_{run_dir.name}")
        offers = st.text_area("Ưu đãi / voucher THẬT đang có (bỏ trống = AI không được nhắc voucher, flash sale, "
                              "số lượng có hạn)", settings.get("offers", ""), height=70,
                              key=f"tpl_offers_{run_dir.name}",
                              placeholder="vd: Shopee đang giảm 15% đến 12/10, mã freeship EXTRA đơn từ 0đ")

        if title.strip() and keywords:
            _render_title_check(title.strip(), keywords, tested)

        templates = list_templates()
        st.markdown("**5️⃣ Chọn mẫu kịch bản (mỗi kênh 1 mẫu — chọn nhiều để ra nhiều kịch bản)**")
        names = [t["name"] for t in templates]
        default_sel = [n for n in settings.get("templates", []) if n in names] or names[:1]
        chosen = st.multiselect("Mẫu", names, default=default_sel, key=f"tpl_pick_{run_dir.name}",
                                label_visibility="collapsed")
        _render_template_manager(templates)

        mode = st.radio("6️⃣ Cách viết kịch bản",
                        ["🚀 Tự động bằng API key (1 nút, nhiều kênh)", "💬 Qua khung chat AI (Claude / Gemini / ChatGPT web — không tốn API)"],
                        key=f"tpl_mode_{run_dir.name}", horizontal=True)
        if mode.startswith("💬"):
            _render_chat_mode(run_dir, templates, chosen, title, keywords, tested, offers, channel, length, products)
            return _render_results(run_dir, products)

        n_calls = len(chosen) * CALLS_PER_SCRIPT
        ready = bool(title.strip() and keywords and chosen and products) and client_err is None
        st.caption(f"Sẽ tạo **{len(chosen)} kịch bản** · ~{n_calls} lần gọi Gemini ({CALLS_PER_SCRIPT}/kịch bản: "
                   "mở đầu, 5 sản phẩm, kết). Mỗi phần xong là lưu — lỗi quota giữa chừng thì bấm lại sẽ viết tiếp.")
        if client_err:
            st.error(f"Chưa dùng được Gemini: {client_err}")
        if st.button("🚀 Tạo kịch bản", type="primary", disabled=not ready, key=f"tpl_go_{run_dir.name}"):
            _save_settings(run_dir, {"title": title.strip(), "channel": channel.strip(), "tested": tested, "offers": offers, "templates": chosen})
            by_name = {t["name"]: t for t in templates}
            # Kịch bản dở dang của cùng mẫu + cùng tiêu đề -> viết tiếp thay vì viết lại từ đầu.
            unfinished = {d["template_id"]: d["id"] for d in list_scripts(run_dir)
                          if not d.get("complete") and d.get("title") == title.strip()
                          and d.get("length_minutes") == (list(length["minutes"]) if length else None)}
            with st.status(f"Đang tạo {len(chosen)} kịch bản…", expanded=True) as status:
                ok = 0
                for name in chosen:
                    tpl = by_name[name]
                    try:
                        generate_scripts_for_template(tpl, run_dir, vision_client, title.strip(), keywords, tested,
                                                      offers, resume_id=unfinished.get(tpl["id"]),
                                                      progress=status.write, channel=channel.strip(),
                                                      length=length)
                        ok += 1
                        status.write(f"✅ Xong: {name}")
                    except (LLMJSONError, LLMQuotaExceededError, LLMServerError, RuntimeError) as e:
                        status.write(f"❌ {name}: {e} — phần đã viết được lưu, bấm lại nút để viết tiếp.")
                status.update(label=f"Xong {ok}/{len(chosen)} kịch bản",
                              state="complete" if ok == len(chosen) else "error")
            if ok:
                st.rerun()

    _render_results(run_dir, products)
