"""Bộ THẺ CẢM XÚC / GIỌNG ĐỌC (voice tones) gắn đầu mỗi đoạn ý trong script.

AI viết script đặt 1 thẻ chuẩn tiếng Anh (vd: [Deep and serious], [Objective and critical], [Energetic and peak])
ở ĐẦU mỗi đoạn 1–3 câu để giọng AI (ElevenLabs v3, Gemini TTS, Google Vids...) bắt chuẩn sắc thái chuyên gia review.

- Script / kịch bản thu âm GIỮ nguyên thẻ (các mô hình TTS cao cấp nhận diện thẻ tiếng Anh trực tiếp làm context prompt).
- edge-tts KHÔNG đọc được cảm xúc: thẻ chỉ tạo khoảng ngắt ngắn (xem src/tts.py), không đọc thành lời.
"""
from __future__ import annotations

TONE_GROUPS = {
    "Thu hút & đánh vào tâm lý (Hook & Pain point)": [
        ("[Deep and serious]", "nêu vấn đề, nỗi đau của người mua (lạc vào ma trận giá, mua nhầm hàng)"),
        ("[Cautious and alerting]", "lời khuyên tránh mất tiền oan, rủi ro khi mua (rủi ro CHUNG, có thật)"),
        ("[Authoritative and emphatic]", "chốt 1 giá trị cốt lõi / thông tin không thể bỏ qua"),
        ("[Surprised and intrigued]", "công bố tính năng vượt trội so với mức giá"),
    ],
    "Review khách quan & Anti-sell": [
        ("[Objective and critical]", "chân thành, hạ nhẹ giọng khi chỉ ra nhược điểm thật"),
        ("[Steady and informative]", "đọc thông số kỹ thuật, liệt kê phụ kiện kiện trong hộp"),
        ("[Clear and detailed]", "miêu tả mạch lạc, từ tốn về hoàn thiện, chất liệu, ngoại hình"),
    ],
    "Năng lượng & tích cực": [
        ("[Excited and upbeat]", "chuyển sang sản phẩm tốt hơn / deal/ưu đãi THẬT"),
        ("[Warm and optimistic]", "nhẹ nhàng, mang lại giải pháp khi sản phẩm giải quyết được vấn đề"),
        ("[Professional and reassuring]", "giải thích công nghệ lõi (chip DSP, lọc ồn, cảm biến...) — tạo niềm tin kỹ thuật"),
        ("[Energetic and peak]", "CHỈ dùng cho Top 1 (quán quân, lựa chọn tốt nhất)"),
    ],
    "Chốt sale & kêu gọi (CTA)": [
        ("[Confident and reassuring]", "chắc nịch, khẳng định chất lượng dựa trên dữ liệu/trải nghiệm thực tế"),
        ("[Consultative and persuasive]", "như chuyên gia/người thân tư vấn giải pháp tối ưu"),
        ("[Urgent and decisive]", "câu kêu gọi cuối: dứt khoát, nhịp nhanh (Bấm ngay, kiểm tra ngay) — KHÔNG bịa khan hiếm"),
    ],
}

ALL_TONES = [t for items in TONE_GROUPS.values() for t, _ in items] + [
    "[Detailed description]", 
    "[Bright and cheerful]", 
    "[In-depth technical]", 
    "[Trustworthy and assured]", 
    "[Direct CTA]",
]

_FLOW = {
    "hook": (
        "[Deep and serious] khi nêu nỗi đau → [Authoritative and emphatic] khi hứa giá trị của video → "
        "[Surprised and intrigued] hoặc [Excited and upbeat] khi hé lộ sản phẩm đáng chú ý → "
        "[Consultative and persuasive] khi nhắc link ghim."
    ),
    "product": (
        "Mở đoạn: [Excited and upbeat] hoặc [Bright and cheerful] (Top 1: [Energetic and peak]) → "
        "thông số / đồ trong hộp: [Steady and informative] hoặc [Clear and detailed] → "
        "công nghệ: [Professional and reassuring] → "
        "nhược điểm: [Objective and critical] → "
        "rủi ro khi mua: [Cautious and alerting] → "
        "câu bấm link: [Consultative and persuasive]."
    ),
    "cta": (
        "[Consultative and persuasive] khi chốt ma trận lựa chọn → "
        "[Confident and reassuring] khi khẳng định → "
        "[Urgent and decisive] cho câu kêu gọi bấm link cuối cùng."
    ),
}


def tone_rule(part: str) -> str:
    part = {"intro": "hook", "outro": "cta"}.get(part, part)
    menu = "\n".join(f"  {t} — {use}" for items in TONE_GROUPS.values() for t, use in items)
    return (
        "\nTHẺ GIỌNG ĐỌC (bắt buộc — chuẩn tiếng Anh cho TTS model): mỗi đoạn ý 1–3 câu BẮT ĐẦU bằng đúng 1 thẻ, "
        "đặt ngay đầu dòng, cùng dòng với câu (vd: \"[Objective and critical] Điểm trừ đáng tiếc nhất là...\"). "
        "Chỉ dùng thẻ trong danh sách, viết đúng format tiếng Anh, thay thế hoàn toàn các thẻ hành động thừa "
        "([cười nhẹ], [thở dài]...):\n"
        f"{menu}\nNhịp gợi ý cho đoạn này: {_FLOW[part]} Không đặt 2 thẻ giọng liền nhau; thẻ giọng không thay "
        "đổi quy tắc trung thực (không bịa ưu đãi / khan hiếm ảo).\n"
    )