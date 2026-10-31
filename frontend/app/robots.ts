// TL-24 (S262): robots.txt lists the sitemap only on the live site (SITE_INDEXING=on); a preview keeps the crawl rules so
// crawlers can read each page's noindex. Rules: lib/siteMode.ts.
import type { MetadataRoute } from "next";
import { siteUrl } from "@/lib/catalog";
import { isLiveSite, robotsFor } from "@/lib/siteMode";

export default function robots(): MetadataRoute.Robots {
  return robotsFor(isLiveSite(), siteUrl());
}
