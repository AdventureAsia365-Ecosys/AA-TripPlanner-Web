# Changelog

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
