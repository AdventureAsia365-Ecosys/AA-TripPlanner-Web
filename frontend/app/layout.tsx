import type { Metadata, Viewport } from "next";
import { Poppins, Satisfy } from "next/font/google";
// AA-994 S5a: the kit core token set first (every --aa-* value), then the TripPlanner-only --tp-* values.
import "../ui/core/tokens.css";
import "../ui/tokens.css";
import "./globals.css";
import { RESTORE_THEME_SCRIPT } from "@/ui/restoreTheme";
import { siteUrl } from "@/lib/catalog";
import { isLiveSite, layoutRobots } from "@/lib/siteMode";

// AA-995: Poppins for everything + Satisfy for section titles, the two faces adventure.asia uses.
// Fahkwang is gone (the live brand does not use it); Inter is gone since the planner moved to Poppins (spec R10).
const poppins = Poppins({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-poppins",
  display: "swap",
});
// `optional`: a slow first view keeps the Poppins fallback instead of swapping a large script face
// on screen (no layout shift); preloaded, so a normal view gets Satisfy.
const satisfy = Satisfy({
  subsets: ["latin"],
  weight: "400",
  variable: "--font-satisfy",
  display: "optional",
  preload: true,
});

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl()),
  title: {
    default: "Adventure Asia — Plan a tailor-made trip in Asia",
    template: "%s · Adventure Asia",
  },
  description:
    "Find an Adventure Asia tour, shape it on the map day by day, and have an Adventure Asia advisor tailor it for you.",
  openGraph: { siteName: "Adventure Asia Trip Planner", type: "website" },
  // TL-24 (S262): noindex, nofollow on every deployment until the live site sets SITE_INDEXING=on (lib/siteMode.ts).
  // A page's own `robots` replaces this one; every page that sets one today sets index: false.
  robots: layoutRobots(isLiveSite()),
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#F8F6F2" },
    { media: "(prefers-color-scheme: dark)", color: "#0F141B" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // data-theme and data-motion are set before paint by the restore script, so the server value may differ.
    <html
      lang="en"
      data-theme="light"
      suppressHydrationWarning
      className={`${poppins.variable} ${satisfy.variable}`}
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: RESTORE_THEME_SCRIPT }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
