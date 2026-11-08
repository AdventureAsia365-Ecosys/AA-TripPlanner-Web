// AA-1038 R5 hosting (review S272) — where the catalog pages render in the ECS image.
//
// `next build` prerenders a route with ISR config and no request API (the home, /destinations, sitemap.xml). On Vercel
// the build has the catalog (BROWSE_API_URL, the edge key), so the prerendered page is real. The ECS image builds with
// no server value at all (frontend/Dockerfile: every server value is task env), so the catalog read fails at build and
// the image would ship the "We couldn't load ..." page and a two-line sitemap, served until the first revalidation
// (30 / 60 minutes after each deploy). So in the image (NEXT_OUTPUT=standalone, set in both Dockerfile stages) these
// routes call `connection()` and render on request. Their catalog reads stay cached by the tagged fetch cache
// (lib/catalog.ts) and the CIS purge still drops them; Vercel, CI and local builds keep the prerendered ISR page.
//
// Each such route does: `if (rendersOnRequest()) await connection();` (lib/renderMode.test.ts checks the routes).
// No next/* import: `npm test` runs it.

export function rendersOnRequest(env: Record<string, string | undefined> = process.env): boolean {
  return (env.NEXT_OUTPUT ?? "").trim() === "standalone";
}
