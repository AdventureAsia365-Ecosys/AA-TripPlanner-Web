# Roadmap — đưa AA-TripPlanner thành sản phẩm thị trường

_Lập 14/09/2026. Trạng thái hiện tại: MVP chạy end-to-end trên prod
(https://aa-tripplanner.vercel.app). Roadmap này để họp và quyết ưu tiên._

Ký hiệu cột "Ai làm":
- **Agent**: thuần code, không cần tài nguyên ngoài / secret dùng chung —
  Kiro làm được ngay.
- **Cần người**: cần bạn/DevOps (secret dùng chung, tài khoản bên thứ ba,
  quyết định chi phí, hoặc là dự án riêng).

---

## Nhóm 0 — Bắt buộc trước khi mở cho người dùng thật (bảo mật / vận hành)

| # | Việc | Vì sao | Ai làm |
|---|------|--------|--------|
| 0.1 | **Rotate mật khẩu admin RDS đã lộ** | Mật khẩu lộ trong log/file; dùng CHUNG với AA-CIS | Cần người (cửa sổ bảo trì) |
| 0.2 | **Email advisor thật (AWS SES)** | "Send to advisor" hiện chỉ ghi log → luồng handoff (giá trị cốt lõi) chưa khép kín | Cần người (verify domain + thoát SES sandbox) + Agent (code sender) |
| 0.3 | **Siết Mapbox token theo URL** | Token public đang lộ → rủi ro lạm dụng quota/tính tiền | Cần người (cấu hình Mapbox) |
| 0.4 | **Rate limiting + WAF** cho API Gateway/BFF | Chống abuse, scraping, spam "send to advisor" | Cần người (Infra) |

## Nhóm 1 — Dữ liệu & độ tin cậy

| # | Việc | Vì sao | Ai làm |
|---|------|--------|--------|
| 1.1 | **Auto-refresh tour mới** (scheduler chạy extraction + `run.py` idempotent) | Tour mới của AA không tự lên map → sản phẩm "chết dần" | Agent (code) + Cần người (Terraform scheduler apply) |
| 1.2 | **Sửa geocode sai** (một số điểm rơi nhầm lục địa) | Sai vị trí pin; dùng proximity bias + kiểm tọa độ trong bounding-box country | Agent |
| 1.3 | **Chuẩn hóa golden record `shared.destinations`** (Linear AA-571) | Dedupe tên gần trùng | Agent + Cần người (duyệt data) |

## Nhóm 2 — Trải nghiệm người dùng (biến MVP thành "đã dùng là thích")

| # | Việc | Vì sao | Ai làm |
|---|------|--------|--------|
| 2.1 | **Lưu trạng thái trip + link chia sẻ** (`/trip/{id}`) | Guest hiện lưu in-memory, đóng tab là mất | Agent |
| 2.2 | **Thời gian & khoảng cách di chuyển mỗi chặng** (đã có Directions) | Cảnh báo "ngày này đi 6 tiếng" | Agent |
| 2.3 | **Trip summary đẹp + export PDF/chia sẻ** | Tổng ngày, quốc gia, bản đồ tổng quan | Agent |
| 2.4 | **Ảnh thật cho component/destination** (`thumbnail_url` phần lớn null) | Ảnh là yếu tố bán hàng số 1 với phân khúc cao cấp | Cần người (nguồn ảnh) + Agent (hiển thị) |
| 2.5 | **Mobile responsive** | Layout hiện desktop-only; du lịch dùng mobile nhiều | Agent |
| 2.6 | **Onboarding / empty state** (gợi ý tìm, tour mẫu) | Tránh khách đối mặt bản đồ trống | Agent |

## Nhóm 3 — Chất lượng AI & khác biệt hóa

| # | Việc | Vì sao | Ai làm |
|---|------|--------|--------|
| 3.1 | **Narration streaming (SSE)** | Hiện buffered, chờ im lặng vài giây; stream chữ ra dần "đã" hơn | Agent (cần API GW/BFF hỗ trợ stream) |
| 3.2 | **AI gợi ý điểm kế tiếp** (embedding + địa lý) | "Đã pin Luang Prabang → gợi ý Nong Khiaw gần đó" | Agent |
| 3.3 | **Đa ngôn ngữ (i18n)** | Nếu mở rộng ngoài US/UK/AUS | Agent + Cần người (bản dịch) |

## Nhóm 4 — Vận hành & đo lường (thiết yếu để "là sản phẩm")

| # | Việc | Vì sao | Ai làm |
|---|------|--------|--------|
| 4.1 | **Analytics/telemetry funnel** (mở map → pin → send) | Không đo thì không biết tối ưu gì | Agent + Cần người (chọn công cụ) |
| 4.2 | **Error monitoring (Sentry) + structured logging + alerting** | Phát hiện lỗi prod sớm | Cần người (tài khoản) + Agent (tích hợp) |
| 4.3 | **CI/CD chặt hơn: chỉ deploy từ `main`, có staging tách dev** | Hiện deploy Lambda qua workflow_dispatch trên branch | Agent + Cần người (Infra env) |
| 4.4 | **Tối ưu tốc độ**: lazy-load Mapbox, Lambda provisioned concurrency, Vercel region gần user | Cold start + bundle nặng + cross-region latency | Agent (lazy-load) + Cần người (provisioned concurrency chi phí) |
| 4.5 | **Advisor dashboard** (xem trip draft khách gửi) | Nửa còn lại của sản phẩm B2B2C; hiện chỉ có email/log | Cần người (dự án riêng) + Agent |

---

## Đề xuất thứ tự thực tế
1. **Nhóm 0** trước (không có thì chưa mở cho user thật).
2. **Nhóm 1** (giữ dữ liệu sống).
3. **Nhóm 2** (tác động UX lớn nhất): bắt đầu 2.1 lưu trip + 2.2 thời gian
   di chuyển + 2.4 ảnh.

## Việc Agent làm được NGAY (không chờ tài nguyên ngoài)
2.1 lưu trip + share link · 2.2 duration/distance · 2.3 trip summary ·
2.5 mobile · 2.6 empty state · 3.1 narration streaming · 3.2 gợi ý điểm ·
4.4 lazy-load Mapbox · 1.2 sửa geocode.

## Việc CẦN người / tài nguyên ngoài
0.1 rotate RDS pw · 0.2 SES · 0.3 Mapbox URL restrict · 0.4 WAF ·
1.1 scheduler apply · 2.4 nguồn ảnh · 4.1 analytics tool · 4.2 Sentry ·
4.5 advisor dashboard.
