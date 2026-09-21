# Đồng bộ thương hiệu với adventure.asia — đề xuất (chờ duyệt)

_Lập 14/09/2026. Khảo sát trực tiếp trang chính thức https://adventure.asia
(đọc HTML/CSS thật). Đây là DANH SÁCH ĐỀ XUẤT — chưa làm gì, chờ anh/chị
quyết từng mục._

---

## 1. Phát hiện từ site chính thức (dữ liệu thật)

### Fonts (site chính đang dùng)
| Font | Vai trò trên adventure.asia | TripPlanner hiện tại |
|------|------------------------------|----------------------|
| **Poppins** (300–700) | Font chính, body + heading | Đang dùng **Inter** → KHÁC |
| **Satisfy** | Chữ viết tay (script) cho điểm nhấn trang trí | Không có |
| **Fahkwang** | Font phụ, có nét Á Đông | Không có |

### Bảng màu (hex, theo tần suất xuất hiện thật)
| Màu | Vai trò trên site chính | TripPlanner |
|-----|-------------------------|-------------|
| `#ffffff` | Nền trắng chủ đạo | ✅ giống |
| `#db9628` | **GOLD thương hiệu** | ✅ **KHỚP CHÍNH XÁC** |
| `#9e6e16` | Gold đậm (hover/nhấn) | ~ gần `#B87A1A` |
| `#c6932d`, `#dda64e`, `#e3bd7f` | Thang gold (đậm→pastel) | Chưa có nhiều sắc độ |
| `#111827` | Ink/near-black (heading) | Đang dùng `#1F2933` (nhạt hơn 1 chút) |
| `#474a50`, `#35495e`, `#6b7280` | Xám / xanh-xám (text phụ) | `#6B7280` ✅ giống muted |
| `#f8f6f2` | Off-white | ✅ giống |
| `#c20101`, `#ff6a6a` | Đỏ (accent/CTA phụ) | Không dùng |

**Kết luận nền tảng:** thương hiệu ĐÃ đúng hướng — gold `#db9628` và
off-white `#f8f6f2` khớp y hệt site chính. Khác biệt chính là **font
(Inter vs Poppins)** và **thiếu font script Satisfy** làm điểm nhấn.

---

## 2. Các thứ CÓ THỂ đồng bộ (đề xuất, chờ quyết)

### A. Font — tác động thị giác lớn nhất
- **A1. Đổi font chính Inter → Poppins.** Khớp 100% site chính. Rủi ro
  thấp (chỉ đổi `next/font` + biến `--font-*`). Poppins tròn/thân thiện
  hơn Inter.
- **A2. Thêm Satisfy cho điểm nhấn** (vd wordmark phụ, tiêu đề "Your trip",
  tagline). Tạo cảm giác "du lịch/thư giãn" đúng chất AA. Dùng tiết chế.
- **A3. (Tùy chọn) Fahkwang** cho heading lớn nếu muốn nét Á Đông.

### B. Màu — tinh chỉnh cho khớp tuyệt đối
- **B1. Đổi ink `#1F2933` → `#111827`** để khớp near-black của site chính.
- **B2. Bổ sung thang gold** (`#9e6e16` đậm, `#e3bd7f`/`#dda64e` pastel)
  làm token phụ — dùng cho hover, badge, nền nhạt.
- **B3. Thêm màu đỏ accent** `#c20101` cho các nhãn "nổi bật/ưu đãi" nếu
  sau này có (không bắt buộc cho MVP).

### C. Phong cách/thành phần (tham khảo bố cục site chính)
- **C1. Wordmark**: site chính dùng chữ + có thể logo ảnh. Cân nhắc lấy
  **logo AA thật** (file ảnh) thay cho chấm-tròn tự vẽ hiện tại — cần
  anh/chị cấp file logo chính thức.
- **C2. Ảnh hero/điểm đến chất lượng cao** — site chính rất "ảnh-nặng"
  (phân khúc cao cấp). TripPlanner đang thiếu ảnh (thumbnail_url null).
  Liên quan mục 2.4 trong roadmap.
- **C3. Giọng điệu chữ (copy)**: site chính dùng các cụm như "A Journey
  That Follows Your Own Path", "Discover An Asia That Few Ever Experience".
  Có thể mượn tông này cho tagline/empty-state của TripPlanner.
- **C4. Cấu trúc điểm đến theo vùng** (Southeast/Central/East/South Asia →
  country → place). TripPlanner có thể dùng cùng phân cấp này cho filter/
  breadcrumb để khách quen thuộc.

---

## 3. Đề xuất ưu tiên (nếu duyệt)
1. **A1 (Poppins) + B1 (ink #111827)** — đồng bộ nhanh, rủi ro thấp, tác
   động thị giác rõ. Làm cùng lúc, 1 lần deploy.
2. **A2 (Satisfy accent)** — thêm nét thương hiệu, tiết chế.
3. **C1 (logo thật)** — cần file logo từ anh/chị.
4. **B2 (thang gold)** — tinh chỉnh dần.

Các mục cần tài nguyên ngoài: **C1** (file logo chính thức), **C2** (nguồn
ảnh điểm đến). Còn lại (font, màu, copy) Agent làm được ngay khi duyệt.

> Lưu ý bản quyền: chỉ nên mượn **hệ thống thiết kế** (font, thang màu, tông
> chữ). Ảnh/logo phải là tài sản chính thức của AA cấp cho, không tự lấy
> từ site về.
