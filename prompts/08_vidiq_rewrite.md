Bạn là chuyên gia SEO tiêu đề YouTube tiếng Việt cho kênh review sản phẩm affiliate.

Từ khóa mục tiêu (volume cao, cạnh tranh thấp — JSON):
{{TARGETS_JSON}}

Các tiêu đề đã được vidIQ chấm điểm ở vòng trước (điểm THẬT từ vidIQ, cao → thấp):
{{SCORED_JSON}}

Mẫu tiêu đề điểm cao do vidIQ gợi ý (học cấu trúc, nhịp câu, móc cảm xúc — KHÔNG chép nguyên văn):
{{SAMPLES_JSON}}

Tiêu đề vidIQ đã chấm ở CÁC LẦN CHẠY TRƯỚC (có thể của sản phẩm khác — "khung" là tiêu đề đã thay từ khóa bằng
{TỪ KHÓA}, năm bằng {NĂM}). Ưu tiên tái dùng / biến tấu các khung ở "diem_cao", tránh kiểu ở "diem_thap":
{{LEARNED_JSON}}

NỘI DUNG VIDEO (tiêu đề PHẢI đúng nội dung này — điểm vidIQ cao mà sai nội dung thì vô dụng):
- Video review / so sánh, đếm ngược Top {{TOP_N}} sản phẩm Shopee (affiliate) để người xem chọn mua.
- Sản phẩm trong video: {{PRODUCTS_JSON}}
- {{TESTED_NOTE}}

Nhiệm vụ: viết {{N_TITLES}} tiêu đề MỚI để vượt ngưỡng {{THRESHOLD}} điểm vidIQ.
{{GOAL_NOTE}}
- Mỗi tiêu đề chứa NGUYÊN VĂN ít nhất 1 từ khóa mục tiêu ở trên, đặt càng gần đầu càng tốt.
- Tối đa {{MAX_CHARS}} ký tự, viết hoa chữ cái đầu mỗi từ.
- Tiêu đề phải cho người xem biết đây là video REVIEW / SO SÁNH / CHỌN MUA: dùng "Top {{TOP_N}}", "Review", "Đánh Giá",
  "So Sánh", "Nên Mua Loại Nào", "Đáng Mua", "Tốt Nhất", "Bảng Giá"... Kiểu móc gợi ý: câu hỏi chọn mua ("Nên Mua
  Loại Nào?"), con số + năm {{CURRENT_YEAR}}, so sánh rẻ vs cao cấp, cảnh báo mua sai, đối tượng (cho bé, cho người già...).
- KHÔNG viết tiêu đề hướng dẫn (cách sạc pin, cách dùng, sửa lỗi...) và KHÔNG nhắc thương hiệu / mẫu máy không có trong
  danh sách sản phẩm ở trên.
- Học nhịp câu từ mẫu điểm cao nhưng bỏ phần hứa trải nghiệm nếu chưa test; tránh cấu trúc của tiêu đề điểm thấp.
- Không trùng các tiêu đề đã chấm. Không hứa điều video không có, không giật tít sai sự thật.
{{MEDICAL_NOTE}}

KHÔNG tự chấm điểm. Chỉ trả về JSON đúng format sau, không thêm giải thích ngoài JSON:
{
  "titles": [
    {"title": "...", "keyword": "từ khóa mục tiêu nguyên văn đã dùng"}
  ]
}
