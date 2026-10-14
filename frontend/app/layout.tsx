import type { Metadata, Viewport } from "next";
import { Fahkwang, Inter, Poppins } from "next/font/google";
import "../ui/tokens.css";
import "./globals.css";
import { RESTORE_THEME_SCRIPT } from "@/ui/restoreTheme";
import { siteUrl } from "@/lib/catalog";

// Inter: the map planner (/plan). Poppins + Fahkwang: the brand type of the kit pages, the same
// fonts as CIS admin/portal and adventure.asia (CIS _brand/tokens.ts).
const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });
const poppins = Poppins({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-poppins",
  display: "swap",
});
const fahkwang = Fahkwang({
  subsets: ["latin"],
  weight: ["500", "600"],
  variable: "--font-fahkwang",
  display: "swap",
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
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#F8F6F2" },
    { media: "(prefers-color-scheme: dark)", color: "#0F141B" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // data-theme is set before paint by the restore script, so the server value may differ.
    <html
      lang="en"
      data-theme="light"
      suppressHydrationWarning
      className={`${inter.variable} ${poppins.variable} ${fahkwang.variable}`}
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: RESTORE_THEME_SCRIPT }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
