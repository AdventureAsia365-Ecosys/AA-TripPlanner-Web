# AA-TripPlanner-Web — Claude Code Context
# Created: 15/09/2026 (AA-Ecosys restructure)

## ⚠️ RESTRUCTURE 15/09/2026 — ĐỌC TRƯỚC
- **Workspace:** repo này ở `~/projects/AA-Ecosys/apps/AA-TripPlanner-Web`. Multi-repo, `.git` riêng.
- **Org GitHub:** `AdventureAsia365-Ecosys` (đổi tên từ `AdventureAsia365-CIS`; redirect còn chạy).
  Tên repo giữ nguyên. Remote origin đã trỏ org mới.
- **Hệ sinh thái 3 repo:** `AA-CIS-App` (CIS core + ACPv2 + Admin/Tenant FE), `AA-TripPlanner-Web`
  (repo này), `AA-CIS-Infra` (Terraform). Sắp có `AA-Booking` (AAA) — bàn giao: `~/projects/AA-Ecosys/docs/tripplanner-to-aaa-handoff.md`.
- **CI/CD:** `deploy-lambdas.yml` đã nâng `configure-aws-credentials` `@v4`→`@v6` (Node 24). Deploy
  Lambda qua OIDC role `aa-tripplanner-dev-app-deploy` (định nghĩa ở AA-CIS-Infra, KHÔNG ở repo này).
- Sơ đồ tổng hệ: `~/projects/AA-Ecosys/docs/ecosystem-architecture.md`.

## SẢN PHẨM
B2C map-first trip planner (Adventure Asia). Khách vô danh duyệt điểm đến → ghim component
(place+activity) → ráp itinerary theo ngày → "Send to advisor" + đăng ký. Kết thúc = draft trip +
customer, KHÔNG phải booking hoàn chỉnh (booking thuộc AAA sau này).

## KIẾN TRÚC (tóm tắt — chi tiết ở docs/architecture-overview.md)
```
Browser (Next.js, Vercel) → BFF /api/* (server-side, giữ TRIPPLANNER_API_KEY)
  → API Gateway HTTP v2 (auth=NONE)  → Lambda A Browse (stateless, đọc, no LLM)
                                      → Lambda B Assembly (stateful, Bedrock narration)
  → RDS Postgres acc2 (schema tripplanner.* + shared.destinations, pgvector)
  → Bedrock: Cohere Embed v4 (direct acc2) / Claude (satellite assume acc3→acc1)
```
- **3 đơn vị deploy:** Frontend (Vercel), Lambda A Browse, Lambda B Assembly. + pipeline extraction offline.
- **Auth MVP:** edge shared-secret `X-TripPlanner-Key` (BFF ↔ Lambda), chưa per-user.

## DB (dùng CHUNG RDS acc2 với AA-CIS)
- **Owns:** schema `tripplanner.*` (`itinerary_components`, `customers`, `sessions`, `trip_events`,
  `trip_drafts`) — migration `001_tripplanner_schema.sql` trong repo NÀY. Bật pgvector.
- **Dùng chung:** `shared.destinations` (golden record, lat/lng geocode).
- **Chỉ đọc (không ghi):** `acp_contract.tour_atoms`, `gold_aa_internal.published_tours` (của AA-CIS).
- Phối hợp đánh số migration `shared` với AA-CIS (`002_shared_destinations.sql` trước vì FK).

## RANH GIỚI APP/INFRA
- Repo này OWNS Lambda **code**; `AA-CIS-Infra/accounts/aa365/tripplanner.tf` OWNS **resource**
  (2 Lambda, API GW, S3 artifacts, OIDC role, secret DB+api-key). Terraform `ignore_changes` code
  attrs → `deploy-lambdas.yml` (`lambda:UpdateFunctionCode`) không bị Terraform ghi đè.

## MODEL IDs (verified live)
- Embed: `us.cohere.embed-v4:0` (direct acc2, 1536-dim).
- Compose/renarrate: `global.anthropic.claude-sonnet-4-6` (via satellite; role cho phép `global.` không phải `us.`).

## DEFERRED (tracked)
Auto-refresh extraction khi có tour mới; advisor email thật (đang stub); CloudFront trước Browse;
Mapbox token URL-restriction; fix điểm geocode sai; rotate RDS dev admin password.
