Ngách (JSON):
{{NICHE_JSON}}

Từ khóa đã lọc (JSON):
{{FILTERED_KEYWORDS_JSON}}

Danh sách sản phẩm Shopee thu thập được (JSON rút gọn — "id" là mã sản phẩm, "price" tính bằng đồng,
"sold" là lượt bán):
{{PRODUCTS_JSON}}

Nhiệm vụ: Chọn ra ĐÚNG 5 sản phẩm cho video review "Top 5", xếp thành một BẬC THANG GIÁ từ
BÌNH DÂN lên CAO CẤP. Video đếm ngược Top 5 -> Top 1, nên:
- TOP 5 = sản phẩm RẺ NHẤT (bình dân, đủ dùng cơ bản).
- TOP 4, TOP 3, TOP 2 = giá tăng dần, mỗi bậc phải XỊN HƠN RÕ RÀNG bậc ngay dưới nó (thêm tính
  năng, thông số mạnh hơn, chất lượng tốt hơn).
- TOP 1 = sản phẩm ĐẮT NHẤT, CAO CẤP NHẤT — phải đáp ứng được TẤT CẢ các nhu cầu chính, tức là
  bù được HẾT những điểm mà Top 5 -> Top 2 còn thiếu.
Người xem phải cảm thấy: càng lên hạng cao giá càng cao NHƯNG đáng tiền hơn, không có chuyện sản
phẩm hạng cao lại kém hơn sản phẩm hạng thấp ở một tính năng chính nào.

Bước 1 — Xác định 4-6 nhu cầu chính của người mua trong ngách này, suy ra từ ngách + từ khóa
(ví dụ với nồi chiên không dầu: dung tích, công suất, chế độ nấu cài sẵn, lòng nồi chống dính
an toàn, dễ vệ sinh, màn hình/điều khiển, độ bền thương hiệu...).

Bước 2 — Đọc kỹ tên từng sản phẩm (tên Shopee thường chứa thông số — dung tích, công suất, pin
mAh, chuẩn kết nối...) và rating/lượt bán, rồi chọn 5 sản phẩm trải đều các phân khúc giá
(tránh 5 sản phẩm cùng một tầm giá).
ƯU TIÊN SỐ 1 khi chọn trong mỗi phân khúc: sản phẩm có LƯỢT BÁN ("sold") CAO NHẤT, RATING CAO (≥ 4.5 càng tốt)
và NHIỀU LƯỢT ĐÁNH GIÁ ("reviews", nếu có). Giữa 2 sản phẩm cùng tầm giá, LUÔN chọn sản phẩm bán chạy hơn.
Sản phẩm không có "sold" hoặc chỉ vài lượt bán chỉ được chọn khi phân khúc đó không còn sản phẩm nào khác.

TUYỆT ĐỐI KHÔNG CHỌN TRÙNG: 5 sản phẩm phải là 5 MẪU KHÁC NHAU — không chọn 2 tin cùng mã model, cùng 1 sản phẩm
đăng ở 2 shop, hoặc 2 tin tên gần giống nhau cùng hãng + cùng công suất / dung tích (vd "Bếp Từ Cảm Ứng SUNHOUSE
2200W" và "[TẶNG NỒI] Bếp Từ Cảm Ứng SUN HOUSE 2200W" là CÙNG 1 mẫu). Mỗi "id" chỉ xuất hiện 1 lần.

Bước 3 — Với mỗi sản phẩm, ghi rõ nó đáp ứng nhu cầu nào ("needs_met") và còn thiếu nhu cầu nào
("needs_missing") trong danh sách ở Bước 1. Kiểm tra lại toàn bộ bậc thang:
- needs_met của hạng cao hơn phải BAO GỒM (hoặc tốt hơn) needs_met của hạng thấp hơn.
- Top 1 có "needs_missing" RỖNG [].
- Giá: Top 1 > Top 2 > Top 3 > Top 4 > Top 5.
Nếu chưa thỏa, đổi sản phẩm khác cho tới khi thỏa.

Bước 4 — Chấm điểm 0-10 cho 4 tiêu chí để tham khảo: "tinh_nang" (đáp ứng nhu cầu + thông số),
"danh_gia" (rating + lượt bán), "gia_tri" (đáng đồng tiền so với phân khúc của nó), "lien_quan"
(khớp ngách/từ khóa). Điểm tính_năng phải TĂNG DẦN từ Top 5 lên Top 1.

Với mỗi sản phẩm:
- "tier": "bình dân" | "tầm trung" | "cận cao cấp" | "cao cấp".
- "reason_selected": 1 câu NGẮN vì sao nó đứng ĐÚNG hạng đó — Top 5 -> Top 2 nói điểm hơn hạng
  dưới và điểm còn thiếu so với hạng trên; Top 1 nói nó bù được hết những gì các hạng dưới thiếu.
- "weakness": điểm còn thiếu so với hạng trên (Top 1 ghi "" vì không thiếu nhu cầu nào).
- "nitpick": 1 điểm CHÊ NHẸ, lặt vặt, có thể bỏ qua được (ví dụ: vỏ dễ bám vân tay, dây nguồn hơi
  ngắn, hướng dẫn chưa có tiếng Việt, hơi chiếm chỗ...). BẮT BUỘC có cho CẢ 5 sản phẩm, kể cả Top 1.
  KHÔNG chê lỗi nghiêm trọng (hỏng hóc, kém an toàn, lừa đảo...).

Mỗi sản phẩm chọn ghi đúng "id" của nó trong danh sách trên (KHÔNG cần chép lại tên, giá hay link — hệ
thống tự điền). Viết các câu "reason_selected", "weakness", "nitpick" ngắn gọn.

Chỉ trả về JSON đúng theo format sau, không thêm giải thích ngoài JSON:
{
  "buyer_needs": ["...", "..."],
  "selected_products": [
    {
      "rank": 1,
      "id": 0,
      "tier": "cao cấp",
      "needs_met": ["..."],
      "needs_missing": [],
      "scores": {"tinh_nang": 0, "danh_gia": 0, "gia_tri": 0, "lien_quan": 0},
      "score": 0,
      "reason_selected": "...",
      "weakness": "",
      "nitpick": "..."
    }
  ]
}
