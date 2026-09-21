# Báo cáo hiện trạng AA-TripPlanner (tiếng Việt)

_Cập nhật: 14/09/2026. So sánh spec v0.3 ban đầu với sản phẩm đã build và
đang chạy trên production._

---

## 1. Sản phẩm là gì (nhắc lại ngắn gọn)

Web B2C thương hiệu Adventure Asia, **map-first**: khách mở bản đồ, xem các
điểm đến AA đã có tour, **pin** (ghim) các "trải nghiệm" (place + activity +
thời lượng) mình thích, xem lịch trình ngày-the-ngày tự dựng, rồi **gửi cho
advisor** của AA để chốt thành tour bán được. Đơn vị được pin không phải cả
tour, cũng không phải điểm đến trần — mà là **itinerary_component** (một mảnh
cắt ra từ nội dung ngày của một tour thật).

Đang chạy tại: **https://aa-tripplanner.vercel.app**

---

## 2. Kiến trúc thực tế đang chạy

```
Trình duyệt (Next.js, Vercel)
      │  chỉ gọi /api/* cùng origin
      ▼
BFF (Next.js server routes, trên Vercel) ── thêm header bí mật X-TripPlanner-Key
      │
      ▼
API Gateway (HTTP API v2, auth = NONE ở cạnh)
   ANY /browse/{proxy+}          ANY /trip/{proxy+}
      │                                │
      ▼                                ▼
Lambda A: Browse                Lambda B: Assembly
(đọc, không LLM, cache được)    (có trạng thái + gọi Bedrock)
      │                                │
      └───────────────┬────────────────┘
                      ▼
          RDS PostgreSQL (dùng CHUNG với AA-CIS, acc2, us-west-1)
          schema: tripplanner.*  +  shared.destinations
                      │
          ┌───────────┴────────────┐
          ▼                        ▼
   Cohere Embed v4          Claude (satellite: assume role
   (trực tiếp acc2)          acc3 → acc1) cho narration
```

---

## 3. Spec v0.3 ban đầu đã đặt ra những gì (tóm tắt các task)

Theo `.kiro/specs/tripplanner-mvp/tasks.md` gốc:
1. Scaffold repo.
2. Schema DB (migrations 001, 002).
3. Extraction pipeline (đọc tour_atoms → phân loại → verify → geocode →
   insert component).
4. Module Bedrock satellite (assume-role acc3/acc1, invoke, streaming).
5. Lambda A — Browse (tiles, search, destination detail).
6. Lambda B — Assembly (events, sequencing, agent compose/renarrate,
   registration, notify, handler add/remove/reorder/send).
7. Frontend (MapView, FilterChips, DestinationPopup, TripPanel, BFF).
8. Smoke test end-to-end.

Các phần spec cố tình **để ngoài phạm vi**: routing thật/thời gian di chuyển,
cộng tác thời gian thực, ngôn ngữ khác tiếng Anh, "Start Anywhere" (từ ảnh/
link), tối ưu cache sâu.

---

## 4. Khác biệt: spec v0.3 → hiện trạng

### 4.1 Những gì spec có và ĐÃ hoàn thành + chạy thật
| Hạng mục | Spec v0.3 | Hiện trạng |
|---|---|---|
| Scaffold, schema, extraction | code + test | ✅ đã chạy thật: 379 destinations, 1081 components (đều từ 31 tour active) |
| Bedrock satellite | code + test stub | ✅ chạy live: embed (Cohere, trực tiếp) + Claude (satellite acc3→acc1) |
| Lambda Browse | tiles/detail/search | ✅ live qua API Gateway |
| Lambda Assembly | add/remove/reorder/send | ✅ live, có guest-session, registration gate |
| Frontend + BFF | 4 component + proxy | ✅ live trên Vercel |
| Smoke test E2E | thủ công | ✅ đã chạy full chain trên prod |

### 4.2 Những gì spec để ngỏ / chưa reachable, NAY đã bổ sung thêm
Đây là phần **vượt** so với spec v0.3 (chi tiết trong
`docs/change-report-mvp-completion.md`):

1. **UI redesign** thương hiệu AA (gold #DB9628 / ink #1F2933 / Inter),
   phong cách TripAdvisor light theme. Spec gốc chỉ có UI placeholder.
2. **Backfill embeddings**: seeder để `embedding = NULL` nên search luôn
   rỗng. Đã embed đủ **1081/1081** component (Cohere Embed v4) → search
   ngữ nghĩa trả kết quả thật.
3. **Wire ô search vào semantic search**: trước đây ô tìm chỉ lọc theo
   `country`; nay gọi thẳng endpoint `/browse/search`, map hiện kết quả
   xếp hạng theo độ liên quan.
4. **Route narration reachable**: `agent.compose/renarrate` đã có sẵn
   nhưng chưa có HTTP route. Nay thêm `POST /trip/{id}/narrate`, wire vào
   BFF + UI (nút Compose/Regenerate). Dùng buffered invoke (không stream).
5. **Auth cạnh (shared-secret)**: API Gateway để NONE; hai Lambda tự kiểm
   header `X-TripPlanner-Key` mà chỉ BFF biết. Verify: gọi thẳng không
   header → 401; qua BFF → 200.
6. **Đường đi thật trên bản đồ**: spec nói "không routing thật". Nay đã
   thêm **Mapbox Directions** (driving) vẽ đường bộ thật, fallback đường
   thẳng khi 2 điểm không nối được bằng đường (đảo/qua sông). Kèm marker
   số ngày.
7. **Narration hiển thị đẹp**: parse thành block "Day N" + đoạn văn,
   bỏ markdown.
8. **Hệ màu phân tầng**: ink = "đang duyệt" (cluster + pin đơn), gold =
   "đã vào trip" (điểm số ngày + route) — tránh 3 vai trò cùng màu.

### 4.3 Sửa lỗi/cấu hình phát hiện dọc đường
- **Model Claude**: `us.anthropic.claude-sonnet-4-6` → `global.anthropic.
  claude-sonnet-4-6` (role invoker chỉ cho profile `global.`, `us.` bị
  AccessDenied).
- **CSS logo mất màu**: bản build cũ trên Vercel chưa có palette `aa-*` →
  rebuild force, đã có lại màu thương hiệu.

### 4.4 Những gì VẪN chưa làm (defer, có chủ đích)
- **Auto-refresh tour mới**: khi mastercontent thêm tour → atomize vào
  `acp_contract.tour_atoms`, dữ liệu này **KHÔNG tự** chảy vào
  `tripplanner.itinerary_components`. Pipeline extraction hiện chạy tay.
  Cần scheduler + làm `run.py` idempotent.
- Email advisor thật (SES + verify domain) — hiện là stub ghi log.
- CloudFront trước route đọc (đã đo: Vercel Edge đang cache tile 30 phút,
  nên chưa cấp thiết).
- Siết Mapbox token theo URL.
- Sửa số ít destination bị geocode sai (Mapbox limit=1 rơi vào nơi trùng
  tên ở nơi khác) — chỉ ảnh hưởng vị trí pin, không ảnh hưởng search.
- **Rotate mật khẩu admin RDS đã lộ** (dùng chung với CIS — cần chủ động).

---

## 5. Hạ tầng dùng CHUNG với AA-CIS (không tự dựng riêng)

- Toàn bộ resource TripPlanner nằm trong file **`AA-CIS-Infra/accounts/
  aa365/tripplanner.tf`** (2 Lambda, API Gateway, S3 artifacts + OIDC
  deploy role, secret DB + secret api-key). Chỉ **đọc** output VPC/RDS
  của AA-CIS, KHÔNG tạo VPC/RDS mới.
- Trust cross-account Bedrock ở `accounts/acc3-bedrock` + `acc1-bedrock`
  (thêm role Lambda TripPlanner làm principal tin cậy).
- Dùng chung: cùng RDS instance, cùng GitHub OIDC provider. KHÔNG đụng
  REST API riêng của AA-CIS (cái đó VPC-Link tới ECS/FastAPI, tách biệt).

---

## 6. Luồng chạy end-to-end (khi khách dùng)

1. Mở trang → tải Next.js từ Vercel Edge → khởi tạo bản đồ Mapbox.
2. Map pan/zoom → BFF `/api/browse?resource=tiles` → API GW → Lambda
   Browse → RDS → trả pin theo ô 1°. (Vercel Edge cache 30 phút.)
3. Gõ ô search → BFF `/api/browse?resource=search&q=...` → Lambda embed
   câu hỏi bằng Cohere → so cosine với embedding component → trả điểm xếp
   hạng.
4. Click pin → popup liệt kê component, mỗi cái có nút "+ Add to trip".
5. Add → BFF `POST /api/trip` (op=add) → Lambda Assembly ghi trip_events
   (append-only) + dựng projection trip_drafts (nearest-neighbor theo địa
   lý) → trả itinerary có lat/lng → map vẽ route (Mapbox Directions) +
   marker số ngày.
6. Kéo thả đổi thứ tự ngày → op=reorder (giữ đúng thứ tự khách chọn).
7. Bấm Compose → op=narrate → Lambda gọi Claude (satellite) sinh lời dẫn
   từng ngày.
8. Send to advisor → nếu chưa đăng ký → 422 yêu cầu name + phone/email →
   đăng ký → gửi (hiện stub ghi log; email thật để sau).

---

## 7. Trạng thái mã nguồn / triển khai

- App repo (`AA-TripPlanner-Web`): PR #27, #28, #29 đã merge vào `main`;
  prod khớp `main`. Deploy Lambda qua workflow `deploy-lambdas.yml`; deploy
  FE qua Vercel.
- Infra repo (`AA-CIS-Infra`): PR #58 đã merge + apply (dev).
- Test: 59 pytest pass. Frontend lint + build sạch.
