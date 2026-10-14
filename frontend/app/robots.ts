import type { MetadataRoute } from "next";
import { siteUrl } from "@/lib/catalog";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [{ userAgent: "*", allow: "/", disallow: ["/api/", "/plan"] }],
    sitemap: `${siteUrl()}/sitemap.xml`,
  };
}
