# Bàn giao ảnh điểm đến — TripPlanner (dành cho team Content)

Mục tiêu: mỗi **điểm đến (destination)** trên bản đồ TripPlanner có **một ảnh
đại diện** (cover image). Ảnh sẽ hiển thị trong popup điểm đến và (giai đoạn
sau) trên thẻ gợi ý.

Giai đoạn này **chỉ cần cung cấp URL ảnh** (không cần upload file cho tụi
mình). Team content dán link ảnh đã có sẵn (từ CMS/website/thư viện ảnh của
AA) vào file CSV kèm theo.

## File cần điền

`destinations_cover_images.csv` — 379 điểm đến, mỗi dòng một điểm. Các cột:

| Cột | Điền? | Ý nghĩa |
|---|---|---|
| `destination_id` | KHÔNG sửa | Khoá định danh — giữ nguyên, đừng đổi/xoá. |
| `destination_name` | KHÔNG sửa | Tên điểm đến (tham chiếu). |
| `country` | KHÔNG sửa | Nước — dùng để lọc/nhóm cho dễ tìm. |
| `current_cover_image_url` | KHÔNG sửa | Ảnh hiện tại (đang trống hết). |
| **`new_cover_image_url`** | **ĐIỀN** | Dán URL ảnh vào đây. |
| `photo_credit` | Nên điền | Nguồn/tác giả ảnh (nếu cần ghi credit). |
| `notes` | Tuỳ chọn | Ghi chú (vd: "chưa có ảnh", "dùng tạm ảnh vùng"). |

Chỉ cần điền cột **`new_cover_image_url`**. Các cột còn lại giữ nguyên.

## Yêu cầu ảnh (URL)

- **URL công khai, truy cập trực tiếp** (mở link ra thấy đúng file ảnh, không
  phải trang web bọc ngoài). Kết thúc bằng `.jpg`, `.jpeg`, `.png`, hoặc
  `.webp`.
- **HTTPS** (bắt buộc — link `http://` sẽ bị chặn khi hiển thị).
- **Tỉ lệ khuyến nghị 16:9 hoặc 4:3** (ảnh ngang). Popup hiển thị dạng ảnh
  ngang; ảnh dọc sẽ bị cắt.
- **Kích thước tối thiểu ~1200×800 px** (đủ nét trên màn retina). Không cần
  quá lớn — dưới ~1 MB/ảnh là lý tưởng cho tốc độ tải.
- **Nội dung**: ảnh thật của điểm đến (phong cảnh/địa danh tiêu biểu), hợp gu
  "Discreet Executive Adventure" — thiên nhiên, văn hoá, trải nghiệm; tránh
  ảnh có watermark hoặc logo bên thứ ba.
- **Bản quyền**: chỉ dùng ảnh AA sở hữu hoặc có quyền dùng. Ghi nguồn vào cột
  `photo_credit` nếu ảnh yêu cầu credit.

## Không có ảnh cho một điểm?

Để trống `new_cover_image_url` và ghi vào `notes` (vd: "chưa có ảnh"). Điểm đó
sẽ hiển thị không có ảnh — không sao, tụi mình bổ sung sau.

## Ưu tiên

Không cần đủ 379 ảnh ngay. Ưu tiên theo thứ tự: các điểm nổi tiếng/hay được
pin trước (Luang Prabang, Vientiane, Kandy, Sigiriya, Kathmandu, Pokhara,
Seoul, Jeju, Tokyo, Taj Mahal...). Gửi từng đợt cũng được.

## Nộp lại

Điền xong (cả bộ hoặc từng đợt) gửi lại file CSV cho team kỹ thuật. Tụi mình
sẽ nạp URL vào cột `cover_image_url` của bảng `shared.destinations` (khớp theo
`destination_id`) và ảnh sẽ tự lên app.

## Ghi chú kỹ thuật (nội bộ)

- Nguồn CSV: `shared.destinations` (cột `id, name, country, cover_image_url`).
- Nạp lại: match theo `destination_id` (UUID) — an toàn kể cả khi tên trùng.
- Giai đoạn này dùng URL trực tiếp. Khi có lượng truy cập thật, cân nhắc đưa
  ảnh lên S3 + CloudFront (một lần, Terraform/Nghiep apply) rồi thay URL —
  **không cần đổi code**, chỉ đổi giá trị `cover_image_url` trong DB.
- Xuất lại file này: chạy `docs/content-handoff/export.sh` (cần tunnel DB).
