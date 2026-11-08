// AA-812 — sitemap.xml: home, the destinations index, every listed tour and destination from the CIS catalog (ISR).
import type { MetadataRoute } from "next";
import { connection } from "next/server";
import { rendersOnRequest } from "@/lib/renderMode";
import { getSitemap, siteUrl } from "@/lib/catalog";

export const revalidate = 3600;

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  // The ECS image has no catalog at build: render on request there (lib/renderMode.ts).
  if (rendersOnRequest()) await connection();
  const base = siteUrl();
  const out: MetadataRoute.Sitemap = [
    { url: `${base}/`, changeFrequency: "daily", priority: 1 },
    // AA-995: the destinations index (every country with tours).
    { url: `${base}/destinations`, changeFrequency: "weekly", priority: 0.8 },
  ];
  const res = await getSitemap();
  if (!res.ok) return out; // the home is always listed; the rest returns on the next revalidate
  for (const c of res.data.countries) {
    out.push({
      url: `${base}/destinations/${c.slug}`,
      lastModified: c.updated_at ?? undefined,
      changeFrequency: "weekly",
      priority: 0.8,
    });
  }
  for (const t of res.data.tours) {
    out.push({ url: `${base}/tours/${t.slug}`, lastModified: t.updated_at ?? undefined, changeFrequency: "weekly", priority: 0.7 });
  }
  return out;
}
