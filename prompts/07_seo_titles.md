Bối cảnh: kênh YouTube review sản phẩm làm affiliate (video dạng Top {{TOP_N}} sản phẩm, giọng gần gũi).
Năm hiện tại: {{CURRENT_YEAR}} (chỉ dùng đúng năm này nếu nhắc năm).

Từ khóa đã phân loại (JSON — main = từ khóa chính, traffic = kéo traffic, secondary = phụ, longtail = đuôi dài):
{{KEYWORDS_JSON}}

Lợi ích nổi bật của sản phẩm (người dùng cung cấp): {{BENEFITS}}
Người nhận quà hay gặp: {{GIFT_TO}}

Các công thức tiêu đề, xếp theo ưu tiên CTR từ cao xuống thấp:
{{FORMULAS}}

Nhiệm vụ: viết 12 tiêu đề YouTube tiếng Việt tối ưu SEO + CTR, ưu tiên 5 công thức đầu, có thể ghép 2 công thức
top trong 1 tiêu đề (vd "CT2+CT1").

Quy tắc bắt buộc:
- Mỗi tiêu đề tối đa {{MAX_CHARS}} ký tự, viết hoa chữ cái đầu mỗi từ.
- Từ khóa (chính, kéo traffic hoặc phụ) phải nằm trong 5 từ đầu, giữ NGUYÊN VĂN từ khóa đó.
- Mỗi tiêu đề chỉ 1 móc cảm xúc (1 câu hỏi HOẶC 1 lời hứa), ưu tiên có số cụ thể hoặc năm.
- Không giật tít sai sự thật, không hứa điều video không có.
{{MEDICAL_NOTE}}

Chỉ trả về JSON đúng format sau, không thêm giải thích ngoài JSON:
{
  "titles": [
    {"title": "...", "formula": "CT2", "keyword": "từ khóa nguyên văn đã dùng"}
  ]
}
