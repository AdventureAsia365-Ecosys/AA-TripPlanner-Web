# Changelog

## [0.15.0](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/compare/v0.14.0...v0.15.0) (2026-11-14)


### Features

* /{slug} tenant planner path with a signed BFF tenant assertion ([#228](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/228)) ([b54421f](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/b54421f2074f14e587271f2a3befc4e9ee23c936))
* agency planners list the agency's own tours and open its own tour pages ([#255](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/255)) ([0fe3890](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/0fe3890d1a7deaebfd58272b101ba75a5ec0e1f7))
* assembly binds the tenant context on every request (shadow RLS) ([#227](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/227)) ([954b925](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/954b9257c58c0017c4fd6b13c81d049ebb92f392))
* bot check (Vercel BotID Basic) before search and narration; AA-text narration answered first ([#201](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/201)) ([4fd7dca](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/4fd7dcabba569ce489084ccdcb08f6546fd0365b))
* browse catalog JSON carries the machine-readable AI mark of the Master text ([#223](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/223)) ([ad1fcec](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/ad1fcec339949a703f29af4cbae19088c6ad23a4))
* dedicated tenant assertion key with rotation grace (Lambdas) ([#256](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/256)) ([37a1618](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/37a16189df88412631a0e8ea8d16823bdfb0ca84))
* Dev and preview deployments are noindex and show a "Preview site" footer line ([#200](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/200)) ([25d1803](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/25d1803c4f488776ac4dee4edd818150b6366505))
* drop tripplanner.llm_spend_daily, the tp-010 spend counter (migration tp-024, destructive) ([#216](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/216)) ([fe42833](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/fe42833939b1a96e373085cdc5ceebb75f878237))
* enforce tenant row-level security on the trip tables (tp-028) ([#249](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/249)) ([0d6b5eb](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/0d6b5eb8be4ee92c3a3616f63cf3a1bcff383056))
* gateway dispatches by deployment with a breaker per connection and core pricing ([#211](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/211)) ([d24808e](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/d24808e6d32dd58d62b01d265ef95a017a161689))
* gateway ledger rows carry prompt_version, prompt_hash and request_id, with a prompt bump gate ([#207](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/207)) ([78f3255](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/78f3255a1ce4b7eef28162f3c14fbdcc8ed55cf2))
* gateway on the shared core aa_llm (CIS 323 views with fallback, ledger v2 rows) (AA-642 S8) ([#203](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/203)) ([c845b62](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/c845b623030ed42bfcb8dede46b5f889486fe8d8))
* gateway reads the CIS views only (fail closed) and applies the stage x model temperature ([#215](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/215)) ([184ba85](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/184ba857430233a3ad82a3040b6df14fc25fdfa6))
* jobs op erasure_feed reads the CIS erasure requests every 5 minutes ([#265](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/265)) ([d5103a7](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/d5103a739da56324e9699960e2e11fcfed9996c9))
* lead.submitted.v1 for agency tenants, CRM pull over M2M, per-tenant AI cap, tenant brand on the /{slug} planner ([#239](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/239)) ([ffca93c](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/ffca93c61486b2ca1c6c70ea3ef1db061a54887e))
* migration numbers check (main max + 1, one open PR per tp-NNN); migration refs read non-UTF-8 lines ([#234](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/234)) ([759ec03](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/759ec0396d251a263164189672c1ca46fc1b30a5))
* narration AI notice variant and machine mark in the trip planner and tour pages ([#222](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/222)) ([c3e6acc](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/c3e6acce40ec82437762871a4328750fd3ab9421))
* narration gateway treats a truncated reply as a failure and stops at a route deadline ([#206](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/206)) ([0da18a8](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/0da18a84b40885fc5e1013688080b4e4de74826f))
* per-session and per-IP AI limits on narration and search, stage caps and 80% spend alerts ([#199](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/199)) ([4c6b341](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/4c6b341483d7c4249d39a908b73bae4c257ffc14))
* planner stop photos, icons, map sync, reorder, share view and kit states ([#250](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/250)) ([0391f7b](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/0391f7b7aafd83bed8166fd1ef37b356d84521fc))
* planning chat bound to AA tours and places, monthly caps per app and tenant (tp-029) ([#252](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/252)) ([8cb38fc](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/8cb38fc9b4a80eaa40d434e4e40c7697ca0afdf2))
* read TripPlanner's admin-editable settings from the CIS registry view ([#264](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/264)) ([fa64dcd](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/fa64dcdc11fd3fc217b89506866387017aded905))
* refuse migration references left behind by a renumber, and a push mid-rebase ([#233](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/233)) ([91d0608](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/91d0608b48b5ab84375933888c41ce42977bcda0))
* retire My Payments on /trips with the Booking B2C drop ([#230](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/230)) ([cdb5b11](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/cdb5b11ea159e16673268882dbd04e8f266b321a))
* search embeds the query only in the embedding space of the searched column ([#208](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/208)) ([69e4b2b](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/69e4b2bea452d05b3e8c410ec01aa17ce8a87731))
* Send to an advisor shows a B2B-only demo notice and the hand-off answers 410 b2b_only ([#218](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/218)) ([537b0fd](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/537b0fd5e4f725d1c1063952c77373cde9468398))
* tenant erase, backup and restore drill jobs in the jobs Lambda ([#260](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/260)) ([ab78656](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/ab78656452993ebc27eaaa1a7381eae485bcac65))
* tenant erasure log, erase function and report view (tp-030) ([#259](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/259)) ([02669eb](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/02669eb31f0f2ad1cf7862cb8ae9f986dbd30d90))
* tenant erasure log, erase function and report view to the tenant.erasure.v1 contract (tp-031) ([#262](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/262)) ([7221c45](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/7221c45ff8c583265dae0658034c0d7c1f1b741f))
* tenant_id and shadow RLS on the trip tables (tp-025) ([#226](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/226)) ([0656822](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/065682220d3fed1fd3b3fa0fe03f88f5ff0cc8da))
* the BFF signs tenant assertions with the dedicated key ([#257](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/257)) ([566dee6](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/566dee62ca41a1d25fdfb689fe2b347dc77f9544))
* tour page shows the from price from Booking price.from.v1 ([#224](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/224)) ([cf6faf9](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/cf6faf9a99193bf2564ff5d8cade0fabb9946f63))
* tp_tenant_erase speaks tenant.erasure.v1 (request rows, rounds, append-only reports, verify census) ([#263](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/263)) ([60eaf4a](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/60eaf4a07f6d8d8ca660060c67449f05572f2ea2))
* tp-027 revokes the booking read of the inquiry outbox and schema USAGE ([#244](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/244)) ([85d7ec2](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/85d7ec2c0312edbde168ad5bd6d8b07b9e8e51e5))
* traveller AI notice (tour page, planner, footer) and terms section Use of AI ([#210](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/210)) ([8e59223](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/8e59223a2dcbd81ee492507c0ef9a25e5f4e7503))
* TripPlanner acts on its app profile (AI switch, daily cap over every stage) ([#217](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/217)) ([9bc3d7f](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/9bc3d7f044d0e0efa26dde09967e072f381229f8))
* TripPlanner web image and ECS deploy behind CloudFront (edge gate, viewer address, WAF token) ([#236](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/236)) ([17a8662](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/17a8662b51830aef6b7e30425e2e4200877f08c4))


### Bug Fixes

* deploy verify reads the sitemap from a file (pipefail) ([#240](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/240)) ([fb7c657](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/fb7c657d3eafaa1f4ce4cd5104e6e52dd8093249))
* destination detail hides suspect points and redirects merged ids ([#195](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/195)) ([26c2c97](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/26c2c976f40ca8b2452e353b3537bef55abc0d2c))
* Docs lint takes the lychee binary from a cached, hash-checked copy ([#245](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/245)) ([cca049a](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/cca049a1c2bcabda2f155c4efea9deee0688d37d))
* fail closed and degrade narration and search when AI cannot run ([#198](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/198)) ([0f2fcb8](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/0f2fcb8581319030ae97bcf19444317b092db7a1))
* planner reorder spec holds answers with gates, saver drops superseded queued saves ([#251](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/251)) ([73694f0](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/73694f0c98b56b9700b94e9a8b76a7778d58bb68))
* planning chat proposes a route option, grounding checks whole names, AI label per AI reply, refusals name every country ([#253](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/253)) ([60e5db1](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/60e5db15734bfe7f9a6c5b7dbac35f5ed7566b73))
* serve the AA catalogue to the AA-hosted tenant only, not every kind demo tenant ([#258](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/258)) ([9c3c213](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/9c3c213421d50ba401c511ff6d85be6552daa004))
* unknown single-segment paths render the themed not-found page ([#229](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/229)) ([e9033c5](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/e9033c5e226e3f3d679258c85cc5506e75bdf0a5))
* visual run accepts the WAF challenge on the planner's first document load ([#243](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/243)) ([4485ee3](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/4485ee33878164def56d91b2084196f854fd97b7))


### Performance Improvements

* planner loads Mapbox after first paint, chat on first open, hover sync in its own context ([#254](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/254)) ([63a2aa2](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/63a2aa2a56f843aa2a603a6abfbf5a740f821563))

## [0.14.0](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/compare/v0.13.0...v0.14.0) (2026-10-29)


### Features

* inquiry key contract view for the CIS TP-03 handoff check (tp-022) ([#193](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/193)) ([01a72c1](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/01a72c16c97f4eaef685c91f0bb0083257aeed2d))


### Bug Fixes

* keep below-the-fold images out of the destination page load window ([#188](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/188)) ([25e8064](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/25e80647fd7438ab8e88571593fb3ee4efe012df))

## [0.13.0](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/compare/v0.12.0...v0.13.0) (2026-10-29)


### Features

* jobs Lambda runner and outbox PII redaction after 90 days (tp_retention_outbox_redact) ([#185](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/185)) ([a694a88](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/a694a88aaf862675463cb29e211cf7a94b1934e0))


### Bug Fixes

* make the HNSW top-k test compare distances, not exact order ([#187](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/187)) ([84df8e0](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/84df8e0f4e413db09879702dd9b6d1f44b4f4e4b))

## [0.12.0](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/compare/v0.11.1...v0.12.0) (2026-10-29)


### Features

* migration tp-021 (tripplanner.job from the job template, outbox.redacted_at) ([#183](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/183)) ([35e294b](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/35e294b7216f8d4954b6d61e1ee1939b6635ab9d))


### Bug Fixes

* faster catalog place reads and same-origin load-time photos on destination pages ([#181](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/181)) ([7118a92](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/7118a92d9bfe654d51cdf27280ea2edbff3c8081))

## [0.11.1](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/compare/v0.11.0...v0.11.1) (2026-10-29)


### Bug Fixes

* mobile LCP on the public pages (same-origin LCP photo, no cross-page hero preloads) ([#178](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/178)) ([c9d84bc](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/c9d84bc3b9058f7ea9b44703a2ee9ba968001238))

## [0.11.0](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/compare/v0.10.0...v0.11.0) (2026-10-28)


### Features

* remove the acc1 Bedrock fallback, Claude on acc3 only ([#174](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/174)) ([8327bb0](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/8327bb0e8b34939ca8778a8fb8adeaa88f972abc))


### Bug Fixes

* final Golden Hour polish (console errors, /trips CLS, LCP image, search card layout) ([#172](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/172)) ([6917392](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/691739230f62fa713d5a8c60a9ea8a9579441071))
* remove internal issue IDs from user-visible text ([#176](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/176)) ([e71a7f6](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/e71a7f667e99dca9390f157c61d41cd6228e6f18))

## [0.10.0](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/compare/v0.9.0...v0.10.0) (2026-10-28)


### Features

* Golden Hour destination, tour and account pages ([#168](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/168)) ([bb7d259](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/bb7d259b7c893013f07ea771cbfae0caad30bdeb))
* My Payments section on /trips (Booking customer payments via BFF) ([#167](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/167)) ([23e8b2a](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/23e8b2a4258e7cc339e5e2f7658234bb2ba3e83d))
* planner Poppins, reduce-motion toggle, content-visibility, expand all days ([#171](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/171)) ([be5cbc4](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/be5cbc417b919df66e0e9e83501e82b0146bfe6b))


### Bug Fixes

* dedupe browse photos by path (destination gallery, tour detail) ([#165](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/165)) ([1073dbc](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/1073dbc278b6639fe868144bb6a815dc13965a65))
* places grid keeps the phone column width on destination pages ([#169](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/169)) ([eae6986](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/eae69868c2a810b92ec73e992e922c1ec40f3bb1))
* places list keeps the phone column width (truncated names) ([#170](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/170)) ([882f7b5](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/882f7b5d3655d210377227bb68f4f404acce8a4c))

## [0.9.0](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/compare/v0.8.2...v0.9.0) (2026-10-27)


### Features

* Golden Hour home page, tokens and motion gate ([#163](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/163)) ([545514a](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/545514a0498c6328ff92a91a7def23d67af413c7))
* photo ribbon reads a longer featured list ([#164](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/164)) ([3d0840b](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/3d0840b871146935e82bd71da2cb65cbba030400))
* saved pins follow CIS component-id redirects across rebuilds ([#154](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/154)) ([5418cd5](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/5418cd53cf2ca508471e3998b488b32f238405e2))
* tp-020 count-only ops views for the CIS data coverage page ([#156](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/156)) ([2f56adc](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/2f56adc3f868ddd68f52b96ad6cad95a7c47646b))


### Bug Fixes

* single-pin static map drops padding so Mapbox stops answering 422 ([#158](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/158)) ([3f6228a](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/3f6228a51631636cb6ca790fd58dd1ebccce9612))
* tour-graph routes cover every tour day and follow the half-pinned rule ([#161](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/161)) ([f61a748](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/f61a74853ef1454a22f3c119b90ab0d361166a23))

## [0.8.2](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/compare/v0.8.1...v0.8.2) (2026-10-25)


### Bug Fixes

* linear slug suffix check (CodeQL ReDoS) and zod 4.6.7 under @vercel/cli-config ([#152](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/152)) ([f024a39](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/f024a3963cf4564d1f007e04199d0e06b88a3a40))

## [0.8.1](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/compare/v0.8.0...v0.8.1) (2026-10-25)


### Bug Fixes

* sign-out in the My Trips error state and email_verified in the first session after sign-up ([#149](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/149)) ([9443a34](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/9443a3435a87f452d8ad1f6d7e612f60f6708bd1))

## [0.8.0](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/compare/v0.7.0...v0.8.0) (2026-10-25)


### Features

* migration tp-019 (traveller accounts and sessions.account_id) ([#145](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/145)) ([ce4bd3b](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/ce4bd3b7a3e3c51344d548ad5c365d1790436164))
* traveller access token, guest-trip claim, My Trips API and v1.4 hand-off in the assembly Lambda ([#147](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/147)) ([90ea21f](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/90ea21f746e23af16b48abecac49d8304ddbdf9b))
* traveller sign-in by email code through the BFF, session cookies and My Trips page ([#148](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/148)) ([49738c6](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/49738c6fd8bef69ae77525b7bac79c4aad40f2e8))

## [0.7.0](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/compare/v0.6.0...v0.7.0) (2026-10-23)


### Features

* purge TripPlanner caches on a signed CIS tour lifecycle notice ([#143](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/143)) ([d14805f](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/d14805fb2f230ddc4989b2892ebcf89102720316))

## [0.6.0](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/compare/v0.5.0...v0.6.0) (2026-10-23)


### Features

* saved trips flag stops AA no longer serves, retired tour pages answer 410 ([#142](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/142)) ([e6f5c4a](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/e6f5c4a86b1b872f36ca06e468bb7b2b4e7fc267))
* tp-018 drops the frozen place tables (needs approval after the DRY RUN) ([#139](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/139)) ([46f3bfb](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/46f3bfb9d0bdfb5d17e25332e95ed2a80fab9a29))


### Bug Fixes

* Lambda smoke checks the BFF itinerary and the Lambda 404 separately ([#136](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/136)) ([dfbe785](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/dfbe78514cb170af863b87a7bc7ca2d1de5c007c))

## [0.5.0](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/compare/v0.4.0...v0.5.0) (2026-10-23)


### Features

* upgrade TripPlanner frontend to Next.js 16 ([#132](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/132)) ([f601c94](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/f601c9478115e90125034dc033ef043bba7f4172))


### Bug Fixes

* /plan WCAG AA contrast in light and dark, clean Customise console, "1 day" labels ([#134](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/134)) ([408375f](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/408375feac797d2b14fc06c1a11989361822f8c9))

## [0.4.0](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/compare/v0.3.0...v0.4.0) (2026-10-22)


### Features

* release-please and the release version on /health ([#130](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/issues/130)) ([81e2fa9](https://github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web/commit/81e2fa9f0a309d6a669ae1cf5178167b3a020e28))
