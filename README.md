# 🎬 Auto Video Affiliate

Tool tự động dựng video review **Top 5 sản phẩm affiliate Shopee** cho YouTube:

**chọn sản phẩm → viết script bám ảnh/video thật → audio có cảm xúc → ghép video → log lịch sử & hiệu suất**

---

## 📑 Mục lục

- [Tính năng chính](#-tính-năng-chính)
- [Kiến trúc](#-kiến-trúc)
- [Lưu ý trước khi dùng](#️-lưu-ý-trước-khi-dùng)
- [Cài đặt](#-cài-đặt)
- [Giao diện web (Streamlit)](#-giao-diện-web-streamlit)
- [Chuẩn bị dữ liệu đầu vào](#-chuẩn-bị-dữ-liệu-đầu-vào)
- [Chạy bằng CLI](#-chạy-bằng-cli)
- [Kết quả đầu ra](#-kết-quả-đầu-ra)
- [Log lịch sử & hiệu suất](#-log-lịch-sử--hiệu-suất)
- [Chỉnh prompt](#️-chỉnh-prompt)
- [Cấu trúc code](#-cấu-trúc-code)

---

## ✨ Tính năng chính

- **Bậc thang giá Top 5 → Top 1**: Top 5 là mẫu bình dân rẻ nhất, Top 1 là mẫu cao cấp nhất. Hạng càng cao càng xịn hơn rõ ràng, và Top 1 đáp ứng **tất cả** các tiêu chí mà Top 5 → Top 2 còn thiếu.
- **Script bám ảnh thật**: Gemini xem từng ảnh/video sản phẩm và viết lời bình đúng với ảnh đang hiện.
- **Review chân thật**: mỗi sản phẩm (kể cả Top 1) có 1 điểm "chê nhẹ" lặt vặt, bỏ qua được — tăng độ tin cậy mà không làm người xem mất hứng mua.
- **Giọng đọc có cảm xúc**: thẻ `[cười nhẹ]`, `[nhấn mạnh]`... tạo khoảng lặng tự nhiên.
- **Video kiểu CapCut**: zoom-in mượt theo từng frame, phụ đề hiện dần từng từ, chữ nhấn mạnh màu vàng, badge TOP/giá, hiệu ứng flash.
- **Mô tả YouTube tự động**: link sản phẩm, timestamp, hashtag ghép sẵn.

---

## 🧠 Kiến trúc

### 2 provider AI

| Provider | Free tier | Dùng cho |
|---|---|---|
| **Groq** | 30 request/phút, ~14.400/ngày | Mọi bước chỉ cần text: chọn ngách, lọc keyword, lọc sản phẩm, hook, CTA, metadata |
| **Gemini** (multimodal) | 5 request/phút, ~20/ngày | **Chỉ** bước viết lời bình từng sản phẩm (cần "nhìn" ảnh/video thật) — ~5 request/video |

### 2 giai đoạn

1. **Chọn lọc**: Ngách → Lọc keyword → Chọn đúng 5 sản phẩm theo bậc thang giá → **dừng lại** để bạn tự lấy ảnh/video chi tiết thật từ Shopee (thumbnail kết quả tìm kiếm không đủ chi tiết).
2. **Viết script + dựng video**: Gemini viết lời bình bám theo từng ảnh → audio giọng liền mạch → ghép video → ghi log vào DB.

---

## ⚠️ Lưu ý trước khi dùng

- **Quota Gemini free ~20 request/ngày** → khoảng 4 video/ngày. Bật billing tại Google AI Studio nếu cần nhiều hơn (chỉ vài cent/video).
- **Model có thể bị ngừng hỗ trợ đột ngột** (đã gặp với `gemini-2.5-flash` và `llama-3.3-70b-versatile`). Nếu gặp lỗi 404 *"model not found"*, đổi `GEMINI_MODEL` / `GROQ_MODEL` trong `.env` sang model đang hoạt động.
- **Edge-TTS không phải API chính thức** — thỉnh thoảng lỗi tạm thời, tool tự retry.
- **Link sản phẩm cần tự gắn tag affiliate** qua Shopee Affiliate Portal trước khi đăng — file scrape chỉ có `productUrl` gốc, tool sẽ cảnh báo khi thiếu.
- **Không nên auto-publish 100%** — kênh đăng hàng loạt không biên tập dễ vi phạm chính sách nội dung tái sử dụng của YouTube. Hãy xem lại `final_video.mp4` trước khi đăng.
- **Thời gian render**: zoom được dựng theo từng frame bằng OpenCV (~18 ms/frame). Mỗi sản phẩm có `video.mp4` thật sẽ tốn thêm thời gian ghép (chỉ chèn 1 câu/sản phẩm).

---

## 📦 Cài đặt

Yêu cầu: **Python 3.10+**, Windows (hoặc Linux/macOS — đổi lệnh kích hoạt venv tương ứng).

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Điền API key vào `.env`:

| Biến | Lấy ở đâu |
|---|---|
| `GEMINI_API_KEY` | <https://aistudio.google.com/apikey> |
| `GROQ_API_KEY` | <https://console.groq.com/keys> (miễn phí, không cần thẻ) |

> `opencv-python-headless` là tuỳ chọn nhưng nên cài (đã có trong `requirements.txt`) — thiếu thì zoom vẫn chạy nhưng render chậm hơn nhiều.

---

## 🖥 Giao diện web (Streamlit)

**Khuyên dùng** thay vì CLI:

```bash
venv\Scripts\activate
streamlit run app.py
```

Mở <http://localhost:8501>. Sidebar dùng để chọn/tạo một **run** (phiên làm việc). Các tab đi đúng theo luồng CLI nhưng thao tác bằng chuột:

- Upload file CSV/JSON đầu vào
- **🔑 Từ khóa & Tiêu đề SEO** — lọc từ khóa chính / phụ / kéo traffic từ CSV, ghép tiêu đề SEO theo 10 công thức CTR (xem mục bên dưới)
- Xem/upload ảnh sản phẩm theo từng thư mục
- Sửa script ngay trên trình duyệt, nghe thử từng audio
- Sửa mô tả YouTube (title, link, timestamp, hashtag) trước khi đăng
- Xem trước và tải video
- Xem lịch sử và nhập số liệu hiệu suất

CLI vẫn dùng song song được (cùng đọc/ghi thư mục `data/`).

> Sau khi sửa code trong `src/`, cần **restart Streamlit** để nhận thay đổi. File prompt `prompts/*.md` thì được đọc lại mỗi lần chạy.

### 🔑 Tab "Từ khóa & Tiêu đề SEO"

Thả 1 hoặc nhiều CSV từ khóa (hoặc dùng luôn CSV trong `data/input/`). Toàn bộ chạy **offline, không tốn quota**:

1. **Phân loại từ khóa** (`src/seo_titles.py` → `classify`):
   - **Chính**: đoán theo tên file (`Tai-nghe-trợ-thính-không-dây_related_keywords.csv` → "tai nghe trợ thính không dây"), không khớp thì lấy từ khóa gốc có Overall cao nhất. Đổi được bằng ô chọn.
   - **Kéo traffic**: top N volume cao nhất (mặc định 5). ⭐ = điểm vàng (volume cao, cạnh tranh dưới trung vị).
   - **Phụ**: volume ≥ 50% từ khóa chính. **Đuôi dài**: còn lại — dùng cho mô tả + tag.
   - **Loại**: tên cửa hàng, mã shop, từ lạc đề / quá rộng, cạnh tranh cao mà Overall thấp. Bản không dấu → chỉ làm tag.
   - Kèm **nhóm ý định**: đối tượng ("cho …"), mua hàng / giá, đánh giá / tốt nhất, hướng dẫn, vấn đề, thương hiệu.
   - Sai chỗ nào thì sửa ngay trên bảng (đổi Vai trò / bỏ tick Dùng) — tiêu đề tự cập nhật.
2. **Ghép tiêu đề** theo 10 công thức, xếp ưu tiên CTR: CT2 Đối tượng + Lợi ích → CT1 Hỏi giá → CT8 Quà tặng → CT4 Top danh sách → CT3 Dùng thử → CT6 Cảnh báo → CT5 So sánh → CT9 Vấn đề → CT7 Hướng dẫn → CT10 Mở hộp, cộng các tổ hợp ghép (CT2+CT1, CT4+CT2, CT8+CT3).
3. **Chấm điểm CTR ước lượng** 0-100: thứ hạng công thức + volume/cạnh tranh của từ khóa dùng + độ dài ≤ 60 ký tự + từ khóa trong 5 từ đầu + có số/năm + chỉ 1 câu hỏi. Cụm "chữa khỏi", "100%", "thay thế bác sĩ"… bị trừ nặng.
4. **Tag YouTube** (< 500 ký tự), từ khóa nên có trong 2 dòng đầu mô tả, checklist trước khi đăng.
5. Tải về CSV/JSON, hoặc **📌 đặt làm tiêu đề video của run** → lưu `09_seo_keywords.json`, ghi đè `title` trong `05_metadata.json` (kể cả khi viết lại script sau này) và đi vào Lịch sử.

Nút **✨ Nhờ AI (Groq) viết thêm** gửi bảng từ khóa đã phân loại + 10 công thức (`prompts/07_seo_titles.md`) — kết quả AI được chấm điểm cùng thang.

#### 🔗 Từ khóa SEO đi vào script (tab ✍️ Script & Audio)

Bấm **📌 Đặt làm tiêu đề video của run này** ở tab 🔑 → lưu `09_seo_keywords.json` vào run. Khi bấm **✍️ Viết script**, tool tự đọc file này (`src/seo_context.py`) — bạn không phải nhập lại:

- **Hook** nhận tiêu đề: 2 câu đầu nhắc lại lời hứa của tiêu đề + nói nguyên văn từ khóa có trong tiêu đề, rồi tới từ khóa chính. Tiêu đề hứa trải nghiệm ("Tôi thử 30 ngày"…) mà chưa tick xác nhận là thật → hook KHÔNG bịa trải nghiệm, nói trung thực ("mình đã tìm hiểu, so sánh kỹ…").
- **Từng sản phẩm** nhận tối đa 2 từ khóa do CODE chia: giá rẻ → Top rẻ nhất; cao cấp / tốt nhất → Top 1; đặc tính / đối tượng / thương hiệu → sản phẩm có đặc tính đó trong tên / nhu cầu (vd "nhập khẩu" → máy Beurer "Nhập Khẩu Đức"); từ khóa chính nhắc thêm 1 lần ở sản phẩm giữa video. Từ khóa tắt Dùng / Loại / Cần xác nhận không bao giờ được giao.
- **CTA**: từ khóa chính + 1 từ khóa mua hàng. **Mô tả**: 2 câu đầu chứa từ khóa phụ; hashtag tối đa 5; **thẻ YouTube** đầy đủ hiện riêng để dán vào ô Thẻ.
- Khung **🔑 Đầu vào SEO cho script** cho xem trước từ khóa của từng phần (0 quota); bảng **🔎 Từ khóa đã nói** do code đếm sau khi viết (cảnh báo chưa nói / lặp nhiều); nút **🔁 Viết lại hook & CTA theo tiêu đề** chỉ gọi Groq, giữ nguyên lời bình sản phẩm (kể cả phần sửa tay).
- Chưa bấm 📌 → dùng tạm từ khóa của bước Chọn sản phẩm (`02_keywords_filtered.json`), không có tiêu đề. Không có gì → script viết như trước.

#### 🎯 Chấm điểm bằng vidIQ (tùy chọn, mục 4 trong tab)

App gọi thẳng **vidIQ MCP** (`https://mcp.vidiq.com/mcp`) từ Python (`src/vidiq_client.py`), không qua model AI trung gian, nên **điểm do code đọc trực tiếp từ kết quả vidIQ**, AI không thể bịa điểm.

Cấu hình trong `.env`:

```
VIDIQ_API_KEY=        # app.vidiq.com → Account Settings → MCP (vidIQ MCP cần gói Max)
VIDIQ_CHANNEL_ID=     # (tùy chọn) ID kênh UC... để điểm sát kênh của bạn
```

Dán key xong chỉ cần F5 trang (không phải khởi động lại). Quy trình (`src/vidiq_flow.py`):

1. Lọc từ khóa volume ≥ 750, bỏ từ khóa bị Loại → 2. chọn 3-5 từ khóa mục tiêu (volume cao, cạnh tranh thấp)
3. 8-10 tiêu đề nháp ≤ 100 ký tự từ bộ 10 công thức (mỗi tiêu đề chứa ≥ 1 từ khóa mục tiêu)
4. Chấm từng tiêu đề bằng `vidiq_score_title` (type `long`/`short`, 5 credit/lần)
5. Chưa có tiêu đề ≥ 90 → lấy mẫu điểm cao bằng `vidiq_generate_titles` (language `vi`), Groq viết lại theo mẫu (`prompts/08_vidiq_rewrite.md`), chấm lại — tối đa 4 vòng
6. Chỉ hiển thị điểm vidIQ trả về; không đạt thì hiện tiêu đề tốt nhất kèm điểm thật

**Tự học để lần sau ít vòng hơn** (`data/title_memory.json`): mọi tiêu đề vidIQ đã chấm được lưu lại và rút thành *khung* (thay từ khóa bằng `{TỪ KHÓA}`, năm bằng `{NĂM}`, vd `{TỪ KHÓA} {NĂM}: Tôi Thử 30 Ngày Và Kết Quả Thật Bất Ngờ` — 93 điểm). Lần sau, kể cả sản phẩm khác, vòng 1 thử khung điểm cao trước; Groq viết lại được xem tiêu đề điểm cao / thấp đã học; và tool **chạy tới khi có đủ N tiêu đề KHÁC NHAU đạt ≥ 90** (mặc định 3 — tiêu đề chỉ khác 1-2 từ hoặc cùng khung chỉ đổi từ khóa tính là 1) rồi dừng ngay, không chấm nốt cả vòng. Mỗi khung ghi nhớ học từ sản phẩm nào: khung của CÙNG sản phẩm luôn dùng được; khung của sản phẩm KHÁC chỉ dùng chéo khi không còn chữ riêng của sản phẩm cũ (tên sản phẩm, đối tượng — vd "{TỪ KHÓA}: Gần Như Tàng Hình Trong Tai!" của máy trợ thính không bị đem sang nồi chiên), và Groq chỉ được xem KHUNG chứ không xem tên sản phẩm khác. Xem / xóa bộ nhớ ở mục "📚 Bộ nhớ đã học".

Bảo vệ credit: có nút **Kiểm tra kết nối (0 credit)**, xem trước vòng 1 miễn phí, ô xác nhận số credit tối đa, **trần credit mỗi lần chạy** (mặc định 150), và cache điểm theo (kênh, loại, tiêu đề) trong `data/vidiq_cache.json` — không bao giờ trả tiền chấm lại cùng 1 tiêu đề. Tiêu đề đã chấm vidIQ đứng đầu danh sách chọn ở mục "Xuất & gắn vào run".

---

### 🎯 Tab "Chọn sản phẩm" — dashboard chống bịa

Trước khi gửi Groq, CODE chuẩn bị dữ liệu (`src/product_filter.py`) và hiện **📊 Dữ liệu sẽ gửi Groq — xem trước (0 quota)**:

- **Từ khóa:** file "Từ khóa đã phân loại" (tab 🔑) → chỉ gửi từ khóa đang tick Dùng; CSV gốc → tự loại tên cửa hàng / mã shop / lạc đề. Từ khóa chính đứng đầu.
- **Sản phẩm:** bỏ trùng → bỏ phụ kiện (pin, núm tai, hộp, dây sạc… "dùng cho máy…") → bỏ sản phẩm có tên không chứa tên sản phẩm của từ khóa nào → còn lại xếp theo lượt bán, lấy 30. Bảng từng sản phẩm kèm trạng thái + từ khóa có trong tên.

Sau khi chạy, **📊 Báo cáo chọn sản phẩm** (lưu ở `02b_selection_report.json` của run):

- Từ khóa Groq chọn — từ khóa **không có trong CSV bị chặn tự động** (Groq hay tự ghép, vd "máy trợ thính không dây cao cấp") và được báo rõ.
- 5 sản phẩm được chọn: code đối chiếu tên với từ khóa, cảnh báo sản phẩm không khớp. Tên / giá / link luôn lấy từ file JSON gốc theo mã sản phẩm, Groq không tự viết.
- Run cũ (chưa có báo cáo) vẫn được đối chiếu với CSV hiện có trong `data/input/`.

### 🧩 Tab "Script & Audio" — kịch bản theo mẫu kênh (1 nút, nhiều kênh)

Khung đầu tab ✍️. Đầu vào: **tiêu đề SEO** (ô nhập, mặc định lấy từ tab 🔑) + **file CSV từ khóa** (ô upload, lưu `script_keywords.csv` trong run; tool tự phân loại Chính / Kéo traffic / Phụ / Đuôi dài) + **5 sản phẩm** kèm ảnh thật và file `.md` của tab 🖼️.

- Mẫu kịch bản = file prompt trong `prompts/script_templates/*.md` (dòng đầu `# Tên mẫu`). Thêm / sửa / xóa ngay trong app (✏️ Quản lý mẫu). Có sẵn mẫu chung **AIDA 5 sản phẩm**; chỗ cần tên kênh trong mẫu ghi `{{KENH}}`, điền tên kênh ở ô "Tên kênh" lúc tạo (để trống = "kênh của mình").
- Chọn nhiều mẫu → bấm **🚀 Tạo kịch bản** → mỗi mẫu ra 1 kịch bản (`<run>/scripts/<mẫu>__<giờ>.json` + `.md`). Gemini viết từng phần (mở đầu → Top 5…Top 1 → kết, 7 lần gọi/kịch bản), lưu sau mỗi phần — hết quota thì bấm lại sẽ viết tiếp.
- **Quy tắc trung thực** luôn gắn sau mẫu và thắng mẫu khi mâu thuẫn: không bịa số liệu "đã test" (trừ khi tick đã test thật), không tự nhận đại lý uỷ quyền / mall nếu dữ liệu không ghi, không bịa voucher / flash sale / số lượng có hạn (chỉ nói ưu đãi bạn nhập), không bịa "shop dính phốt", không hứa hẹn y tế, có câu công khai link affiliate.
- **⏱️ Độ dài video mục tiêu (phút)** (thanh kéo đầu tab, mặc định 6–9 phút, lưu `script_length.json`): tool đổi ra số từ (theo ô 🗣️ Tốc độ giọng đọc — mặc định 225 từ/phút, đo lại được từ voice thật; lưu chung ở `data/voice_speed.json`: hook 8%, CTA 8%, còn lại chia đều 5 sản phẩm) và gắn vào prompt của CẢ kịch bản theo mẫu lẫn script mặc định — ưu tiên hơn độ dài ghi trong mẫu. Viết xong có bảng ⏱️ số từ / thời lượng từng phần so với mục tiêu.
- **🔊 Hiệu ứng âm thanh (SFX)** (ô tick cạnh thanh độ dài, mặc định bật): AI đặt `[Sound Effect: Swoosh / Pop / Ding / Chime / Tension / Heartbeat / Tick Tock / Win]` ở mở đầu, chuyển sản phẩm, trước nhược điểm, trước câu bấm link. Lúc sinh audio (edge-tts), `src/sfx.py` chèn **âm thanh thật** vào đúng chỗ đó (tiếng tự tổng hợp, cache ở `assets/sfx/_auto/`; muốn dùng tiếng của mình thì thả `assets/sfx/<tên>.mp3`, vd `swoosh.mp3`). Có ô nghe thử trong app. Giọng tự thu thì không chèn SFX (bạn tự thêm khi dựng).
- **🎭 Thẻ giọng đọc** (ô tick, mặc định bật — `src/voice_tones.py`): AI mở mỗi đoạn ý bằng 1 thẻ như `[Giọng trầm, nghiêm túc]`, `[Chê nhẹ / Khách quan]`, `[Năng lượng cao, bùng nổ]` (chỉ Top 1), `[Hối thúc, dứt khoát]`… Kịch bản thu âm (giọng tự thu) có ô "Giữ thẻ giọng đọc & hiệu ứng" để dán vào giọng AI đọc được thẻ (ElevenLabs v3, Gemini…). edge-tts không đọc được cảm xúc — thẻ chỉ thành nhịp ngắt ngắn.
- **⚡ Viết nhanh** (tự động): Gemini giảm "thinking" khi viết lời bình, 5 sản phẩm viết **song song** (mặc định 3 cùng lúc), ảnh > 1024px thu nhỏ trước khi gửi, viết script KHÔNG chờ upload video (chỉ dùng ảnh + file .md; video vẫn dùng khi dựng). Tùy chỉnh trong `.env`: `SCRIPT_PARALLEL=3` (giảm xuống 1–2 nếu hay gặp lỗi giới hạn request/phút), `GEMINI_FALLBACK_MODEL=` (model dự phòng khi model chính quá tải 503), `SCRIPT_USE_VIDEO=1` (gửi cả video), `GEMINI_FAST_WRITE=0` (tắt giảm thinking), `GEMINI_THINKING_LEVEL=low`.
- **🎯 Bám tiêu đề SEO**: tool phân tích tiêu đề → từ khóa CHÍNH = cụm có trong tiêu đề (câu đầu hook 0–15s, chữ trên màn hình mở đầu, nhắc lại ở kết); PHỤ → thân video từng sản phẩm; TRAFFIC (câu hỏi / mua / tốt nhất / Kéo traffic) → 3 dòng đầu mô tả, tags, chương. Liệt kê các lời hứa của tiêu đề (năm, Top N, đánh giá cao, giá, thuộc tính như “siêu nhỏ”) để AI chứng minh bằng dữ liệu; cảnh báo khi từ khóa Chính trong CSV khác tiêu đề, hoặc tiêu đề hứa “thực nghiệm” mà chưa tick đã test. Khung chat còn xin thêm phần `=== MÔ TẢ YOUTUBE ===` (3 dòng đầu mô tả, chương, tags).
- **💬 Làm qua khung chat AI** (không tốn API key): tool gom 1 prompt cho cả kịch bản (mẫu + quy tắc + độ dài + hiệu ứng + thẻ giọng + dữ liệu 5 sản phẩm) → copy dán vào Claude / Gemini / ChatGPT web (có thể tải zip ảnh để kéo vào chat) → dán câu trả lời lại, tool tách đúng 7 phần (`=== MỞ ĐẦU ===`, `=== TOP 5 ===`… `=== KẾT ===`), báo phần thiếu, lưu như kịch bản bình thường.
- Xem kịch bản / bảng chữ trên màn hình & âm thanh / chỉ lời đọc; tải `.md` hoặc `.txt`. **📌 Dùng cho Audio** chuyển kịch bản thành `04_script.txt` (bỏ chỉ dẫn [Text on screen] / [Sound Effect]) để sinh audio & ghép video như bình thường.

---

### 🎞️ Project CapCut — 2 kiểu chuyển động ảnh

- **Kiểu 1 — Keyframe Ken Burns**: zoom + trượt mượt cho mọi ảnh (như cũ, kèm Auto Keyframe Pro).
- **Kiểu 2 — Combo Animation 90% + Keyframe điểm nhấn 10%** (`src/capcut_motion.py`): ảnh thường gắn hiệu ứng Combo có sẵn của CapCut (Zoom 1, Zoom 2, Pendulum… luân phiên) — nhanh, nhịp dồn dập; ảnh ĐẦU của phần mở đầu và của mỗi Top dùng keyframe ease phóng to + trượt vào vùng bạn chọn (Giữa / Trên / Dưới-phải…, vd chỗ có giá). Auto Keyframe Pro vẫn làm lớp nền mờ nhưng không ghi đè chuyển động.
- Combo phải **học 1 lần** từ CapCut (mục 🎞️ Học hiệu ứng Combo): tạo project, gắn Combo cho vài ảnh, thoát về màn hình chính, chọn project trong app → lưu ở `data/capcut_combos.json` (chép đúng hiệu ứng CapCut đã tải trên máy, không tự bịa ID).

---

## 📥 Chuẩn bị dữ liệu đầu vào

Đặt vào `data/input/`:

- **Một hoặc nhiều file `.csv` từ khoá** (export từ tool nghiên cứu keyword)
  - Bắt buộc: cột `Keyword`
  - Nên có: `Search volume`, `Competition`, `Overall`, `Related score`
- **Một file `.json` sản phẩm Shopee**, dạng:

  ```json
  {
    "products": [
      { "name": "...", "price": 0, "imageUrl": "...", "productUrl": "...", "salesVolume": 0 }
    ]
  }
  ```

Nếu file thật có cấu trúc khác, chỉnh `src/ingestion.py`.

---

## 🚀 Chạy bằng CLI

### Bước A — Chọn 5 sản phẩm

```bash
python main.py
```

Đọc các file trong `data/input/`, chạy prompt 1-3, rồi dừng lại và hướng dẫn mở `03b_product_links.md`.

### Bước B — Lấy ảnh thật, rồi viết script + audio + video

1. Mở từng link Shopee trong `03b_product_links.md`.
2. Tải ảnh/video vào đúng thư mục `product_media/<rank>_<tên-sp>/`:
   - `main_images/` — ảnh chính
   - `description_images/` — ảnh mô tả
   - `video.mp4` — video sản phẩm (nếu có)
3. Sản phẩm nào không có ảnh có thể để trống — tool sẽ sinh prompt Google Flow trong `07_flow_prompts.md` để bạn tự tạo ảnh AI bù vào.
4. Chạy:

```bash
python main.py --script-only data/output/<run_id>
```

### Các lệnh khác

| Mục đích | Lệnh |
|---|---|
| Test nhanh không cần ảnh thật | `python main.py --auto-script` |
| Sửa tay `04_script.txt` rồi chạy lại audio + video (không tốn API) | `python main.py --media-only data/output/<run_id>` |
| Chỉ ghép lại video | `python main.py --assemble-only data/output/<run_id>` |
| Video dọc 1080x1920 cho Shorts | `python main.py --assemble-only data/output/<run_id> --vertical` |

**Tuỳ chọn thêm:**

| Flag | Ý nghĩa |
|---|---|
| `--voice vi-VN-NamMinhNeural` | Đổi sang giọng nam |
| `--rate +10%` | Đọc nhanh hơn |
| `--fps 24` | Đổi FPS video |
| `--font <path.ttf>` | Đổi font chữ |
| `--skip-media` | Dừng sau khi có script |
| `--skip-assembly` | Dừng sau khi có audio, chưa ghép video |

> 💡 Nếu chạy lại bước viết script cho một run cũ, hãy xoá `04_script_progress.json` trong thư mục run — nếu không, tool sẽ dùng lại lời bình đã sinh trước đó.

---

## 📂 Kết quả đầu ra

Tất cả nằm trong `data/output/<run_id>/`:

| File | Nội dung |
|---|---|
| `01_niche.json` | Ngách đã chọn |
| `02_keywords_filtered.json` | Từ khoá đã lọc |
| `03_products_selected.json` | 5 sản phẩm: hạng, phân khúc, tiêu chí đáp ứng/còn thiếu, điểm chê nhẹ |
| `03b_product_links.md` | Link từng sản phẩm để lấy ảnh/video |
| `04_script.txt` | Script — mỗi câu gắn 1 ảnh cụ thể + thẻ cảm xúc |
| `05_metadata.json` | Tiêu đề, hook mô tả, nhãn timestamp, tags, chữ thumbnail |
| `06_media_manifest.json` | Từng câu + ảnh + timing, dùng để ghép video |
| `07_flow_prompts.md` | Prompt Google Flow (chỉ khi có sản phẩm thiếu ảnh) |
| `08_youtube_description.txt` | Mô tả YouTube đầy đủ: mở đầu → link sản phẩm → timestamp → hashtag |
| `09_seo_keywords.json` | (tùy chọn) Từ khóa đã phân loại + tiêu đề SEO + tiêu đề đã chọn, từ tab "Từ khóa & Tiêu đề SEO" |
| `audio/*.mp3` | Mỗi đoạn một file, giọng liền mạch |
| `final_video.mp4` | Video hoàn chỉnh |

**Video `final_video.mp4` gồm:**

- Nền mờ phóng to từ chính ảnh đang hiện + ảnh sản phẩm nét ở giữa
- Badge **TOP** và **giá**
- Phụ đề hiện dần từng từ theo tiến độ đọc
- Chữ nhấn mạnh màu vàng viền đen (kiểu CapCut)
- Card "lý do chọn" ở câu đầu mỗi sản phẩm
- Zoom-in mượt theo từng frame, liên tục xuyên suốt khi nhiều câu dùng chung 1 ảnh
- Hiệu ứng flash sáng mỗi ~12 giây
- Video sản phẩm thật (nếu có) chèn vào câu dài nhất của sản phẩm đó, tắt tiếng

> ⚠️ Nhớ đổi link trong mô tả sang bản đã gắn tag affiliate trước khi đăng.

---

## 📊 Log lịch sử & hiệu suất

Mỗi lần ghép video xong, tool tự ghi vào `data/history.db` (SQLite): ngách, tiêu đề, danh sách sản phẩm, đường dẫn video.

Không có API tự động lấy YouTube Analytics / Shopee Affiliate, nên sau khi đăng video vài ngày, nhập số liệu thủ công:

```bash
python main.py --log-performance data/output/<run_id> --views 1500 --ctr 4.2 --clicks 30 --revenue 250000 --youtube-url https://youtu.be/xxxx
```

Xem toàn bộ lịch sử + hiệu suất:

```bash
python main.py --report
```

Dùng số liệu này để biết ngách/hook nào ra đơn tốt và ưu tiên làm lại kiểu tương tự.

---

## ✏️ Chỉnh prompt

Các prompt nằm trong `prompts/*.md`, dùng cú pháp `{{PLACEHOLDER}}` — sửa trực tiếp, không cần đụng code Python.

| Prompt | Ghi chú |
|---|---|
| `03_loc_san_pham.md` | Chọn 5 sản phẩm theo bậc thang giá. Code sắp lại hạng theo giá (Top 1 = đắt nhất) và cảnh báo nếu hạng trên kém tính năng hơn hạng dưới, hoặc Top 1 còn thiếu tiêu chí. |
| `04b_product_segment.md` | Prompt **duy nhất** nhận ảnh/video (Gemini). Yêu cầu đánh dấu `[[IMG:n]]` trước mỗi câu — **giữ nguyên định dạng này** vì `src/script_parser.py` và `src/video_assembly.py` dựa vào đó để khớp ảnh với câu. |
| `04d_highlight.md` | Sinh cụm chữ nhấn mạnh màu vàng (Groq, chia lô 20 câu/lần). Lô nào lỗi thì bỏ qua, không chặn audio/video. |
| `05_metadata.md` | Sinh title, tags, `hook_description`, `hook_label` / `outro_label` cho mô tả YouTube. |
| `07_seo_titles.md` | (tùy chọn) Nhờ Groq viết thêm tiêu đề SEO theo 10 công thức, ở tab "Từ khóa & Tiêu đề SEO". |
| `08_vidiq_rewrite.md` | (tùy chọn) Groq viết lại tiêu đề theo mẫu điểm cao của vidIQ khi chưa đạt ngưỡng. |

**Một số tham số hay chỉnh:**

| Muốn chỉnh | Ở đâu |
|---|---|
| Số ảnh tối đa gửi Gemini mỗi sản phẩm (mặc định 6) | `_MAX_IMAGES_FOR_SCRIPT` — `src/product_links.py` |
| Độ dài khoảng lặng theo thẻ cảm xúc | `_PAUSE_MS_RULES` — `src/tts.py` |
| Màu chữ nhấn mạnh | `HIGHLIGHT_COLOR`, `HIGHLIGHT_STROKE` — `src/video_assembly.py` |
| Màu card "lý do chọn" | `INTRO_CARD_BG` — `src/video_assembly.py` |
| Biên độ / tốc độ zoom | `_ZOOM_MAX`, `_ZOOM_SPEED`, `_ZOOM_FOCUS_Y` — `src/video_assembly.py` |

> Thẻ cảm xúc (`[cười nhẹ]`, `[thở dài]`, `[nhấn mạnh]`, `[ngập ngừng]`, `[hào hứng]`) không đổi cao độ giọng (edge-tts không hỗ trợ), chỉ tạo khoảng lặng gần đúng vị trí thẻ.

> Tên sản phẩm trong link/timestamp luôn lấy nguyên văn từ `03_products_selected.json` — không để LLM tự đặt, vì đã gặp lỗi LLM gán nhầm tính năng sản phẩm A cho sản phẩm B.

---

## 🗂 Cấu trúc code

| File | Vai trò |
|---|---|
| `app.py` | Giao diện web Streamlit |
| `main.py` | CLI, điều phối toàn bộ luồng |
| `src/pipeline.py` | Chuỗi prompt giai đoạn 1-2, xếp hạng bậc thang giá, checkpoint resume khi hết quota |
| `src/gemini_client.py`, `src/groq_client.py` | Wrapper 2 provider, retry khi bị rate-limit |
| `src/ingestion.py`, `src/product_links.py` | Đọc CSV/JSON đầu vào, quản lý `product_media/` |
| `src/script_parser.py` | Tách `04_script.txt` thành đoạn/khối theo `[[IMG:n]]` |
| `src/tts.py` | Sinh audio — cảm xúc = khoảng lặng |
| `src/media_step.py` | Nối script parser + TTS + ảnh dự phòng + mô tả YouTube |
| `src/description.py` | Ghép `08_youtube_description.txt` từ metadata + timing thật |
| `src/video_assembly.py` | Ghép MP4 bằng MoviePy + Pillow + OpenCV (xem comment đầu file) |
| `src/database.py` | SQLite log lịch sử + hiệu suất |
| `src/seo_titles.py` | Phân loại từ khóa + ghép/chấm điểm tiêu đề SEO theo 10 công thức (offline) |
| `src/seo_ui.py` | Giao diện tab "Từ khóa & Tiêu đề SEO" |
| `src/vidiq_client.py` | Client MCP (Streamable HTTP) gọi vidIQ: chấm điểm, gợi ý tiêu đề, xem credit |
| `src/vidiq_flow.py` | Quy trình 6 bước chấm/viết lại tiêu đề bằng vidIQ, trần credit + cache |
