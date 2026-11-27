// AA-1115 (root ADR 0019 d10, S282 audit 6.3) — the one place the product's brand words live.
//
// An agency's pages (/{slug}/..., and an agency's own domain, which proxy.ts rewrites onto them) show the agency's
// brand only, never Adventure Asia's. So every sentence that names Adventure Asia lives here, in two kinds of export:
//   * AA_* — Adventure Asia's own site (the root layout's metadata, the AA bar, the AA catalogue pages). A file an
//     agency page can reach imports them only where it is allowed with a reason (lib/agencyBrand.test.ts);
//   * brandCopy(agency) — the words a component shared by both sites shows, for an agency name (its own words, no
//     Adventure Asia) or null (Adventure Asia's site and the AA-hosted demo tenant).
// The single exception on an agency page is the BO-8 badge (root ADR 0019 b8): "Powered by Adventure Asia" only when
// the agency's tp.branding flag is on (off by default), from brandCopy(...).poweredBy(true).
// lib/agencyBrand.test.ts keeps it true: no other file an agency page reaches may spell the brand, and brandCopy(name)
// never returns it. No next/* or React import, so `npm test` runs it.

const AA_NAME = "Adventure Asia";
const AA_URL = "https://adventure.asia/";

/** Adventure Asia's own site: the defaults of the root layout (app/layout.tsx). An agency's layout overrides each. */
export const AA_SITE_METADATA = {
  title: { default: `${AA_NAME} — Plan a tailor-made trip in Asia`, template: `%s · ${AA_NAME}` },
  description: `Find an ${AA_NAME} tour, shape it on the map day by day, and have an ${AA_NAME} advisor tailor it for you.`,
  openGraph: { siteName: `${AA_NAME} Trip Planner`, type: "website" as const },
};

/** The contact address adventure.asia itself publishes (same as the CIS portal's SUPPORT_EMAIL); AA's legal pages. */
export const AA_LEGAL_CONTACT_EMAIL = "info@adventure.asia";

/** Adventure Asia's company-wide privacy policy (adventure.asia, live 200 on 20/10/2026); AA's privacy page. */
export const AA_COMPANY_PRIVACY_URL = "https://adventure.asia/privacy-policy";

/** The AA bar of the planner (components/Header.tsx, AA's site only). */
export const AA_BAR = { logoAlt: AA_NAME, homeLabel: `${AA_NAME} Trip Planner — home` };

/**
 * AA-1185 (S295 security audit SA-73): the "Report AI content" link under the AI pill's explanation on Adventure Asia's
 * own pages. It goes to the contact the site's /.well-known/security.txt names (the company role mailbox, one watched
 * address for vulnerabilities and AI content concerns). An agency's pages show no such link (no Adventure Asia address
 * there). Like the other values of this file it is a code value today; moving the brand contact and wording into
 * admin-editable settings is a recorded follow-up (docs/implementation-notes/AA-1185.md).
 */
export const AA_AI_REPORT = {
  label: "Report AI content",
  href: "mailto:info@adventure.asia?subject=AI%20content%20report",
};

/** The site-wide AI pill next to the AA mark (the planner's AA bar and the site header). */
export const AA_SITE_AI = {
  label: `${AA_NAME} AI`,
  shortLabel: "AA AI",
  explanation:
    `Tour descriptions and day-by-day itineraries are written by AI from ${AA_NAME}'s own tour notes ` +
    `and checked by automated quality checks. Trip narration carries its own ${AA_NAME} AI label, ` +
    "which says how much of it AI wrote. The planner's place search and its “You might also like” suggestions use AI to match your words " +
    `and the places you picked. An ${AA_NAME} advisor reviews every trip before you book.`,
};

export interface AiPill {
  label: string;
  shortLabel?: string;
  explanation: string;
}

export type NarrationSource = "master" | "master_basic" | "mixed" | "llm";

export interface BrandCopy {
  /** The agency's trading name, or null on Adventure Asia's site. */
  agency: string | null;
  /** AI-written catalogue text (tour descriptions, map experience descriptions, a stop's activities). */
  catalogAi: AiPill;
  /** The narration badge per `source` of POST /trip/{id}/narrate (`basic` has no AI text and no badge). */
  narration: Record<NarrationSource, Required<AiPill>>;
  /** The "built from N tours" box of the trip panel (AA-674). */
  coverage: { aria: string; built: (tours: number) => string; uncovered: string; unnamedTour: string; noLink: string };
  /** A stop's activities (components/StopActivities.tsx): none left, and "on N tours" after each one. */
  activities: { none: string; onTours: (tours: number) => string };
  /** The caption of "You might also like" when the suggestions follow the tours' own routes. */
  nextStops: string;
  /** The line under the narration button before a trip is narrated. */
  narrationIntro: string;
  /** The contact consent of the hand-off form on AA's planner (an agency's planner has its own lead form). */
  consent: string;
  /** BO-8: the badge an agency may switch on; null when it is off (always null on AA's own site). */
  poweredBy: (badge: boolean) => { name: string; href: string } | null;
}

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

function aaCopy(): BrandCopy {
  const itinerary =
    `${AA_NAME}'s own itinerary text for these tour days, written by AI from our tour notes and ` +
    "checked by automated quality checks.";
  return {
    agency: null,
    catalogAi: {
      label: `${AA_NAME} AI`,
      shortLabel: "AA AI",
      explanation:
        `Written by an AI model from ${AA_NAME}'s own tour notes and checked by automated quality checks; ` +
        "tours the checks flag are reviewed by our team. Your advisor confirms every detail before you book.",
    },
    narration: {
      master: {
        label: `AA itinerary · ${AA_NAME} AI`,
        shortLabel: "AA itinerary · AA AI",
        explanation: `${itinerary} Your advisor confirms every detail.`,
      },
      master_basic: {
        label: `AA itinerary · ${AA_NAME} AI`,
        shortLabel: "AA itinerary · AA AI",
        explanation:
          `${itinerary} AI writing is not available right now (paused, or today's limit is reached), ` +
          "so the days it does not cover show only their places. Your advisor confirms every detail.",
      },
      mixed: {
        label: `AA itinerary + ${AA_NAME} AI`,
        shortLabel: "AA itinerary + AA AI",
        explanation:
          `Mostly ${AA_NAME}'s own itinerary text, written by AI from our tour notes and checked by ` +
          "automated quality checks; AI also wrote the days it did not cover. Your advisor confirms every detail.",
      },
      llm: {
        label: `${AA_NAME} AI narration`,
        shortLabel: "AA AI narration",
        explanation: "Written by AI from the tour descriptions. Your advisor confirms every detail.",
      },
    },
    coverage: {
      aria: `Built from ${AA_NAME} tours`,
      built: (n) => `Built from ${n} ${AA_NAME} ${plural(n, "tour", "tours")}`,
      uncovered: "Part of this trip isn't covered by an AA tour",
      unnamedTour: "AA tour",
      noLink: "no AA link",
    },
    activities: {
      none: "No other AA activities here.",
      onTours: (n) => `on ${n} AA ${plural(n, "tour", "tours")}`,
    },
    nextStops: `Where ${AA_NAME} tours go next from your last stop.`,
    narrationIntro: `We'll pull each day's story from ${AA_NAME}'s own tour itineraries, in your chosen order.`,
    consent: `An ${AA_NAME} advisor may contact me about this trip.`,
    poweredBy: () => null,
  };
}

function agencyCopy(agency: string): BrandCopy {
  const itinerary =
    "The tour's own itinerary text for these days, written by AI from the tour notes and checked by automated " +
    "quality checks.";
  return {
    agency,
    catalogAi: {
      label: "AI",
      shortLabel: "AI",
      explanation:
        "Written by an AI model from the tour notes and checked by automated quality checks. " +
        `${agency} confirms every detail before you book.`,
    },
    narration: {
      master: {
        label: "Tour itinerary · AI",
        shortLabel: "Itinerary · AI",
        explanation: `${itinerary} ${agency} confirms every detail.`,
      },
      master_basic: {
        label: "Tour itinerary · AI",
        shortLabel: "Itinerary · AI",
        explanation:
          `${itinerary} AI writing is not available right now (paused, or today's limit is reached), so the days ` +
          `it does not cover show only their places. ${agency} confirms every detail.`,
      },
      mixed: {
        label: "Tour itinerary + AI",
        shortLabel: "Itinerary + AI",
        explanation:
          "Mostly the tours' own itinerary text, written by AI from the tour notes and checked by automated quality " +
          `checks; AI also wrote the days it did not cover. ${agency} confirms every detail.`,
      },
      llm: {
        label: "AI narration",
        shortLabel: "AI narration",
        explanation: `Written by AI from the tour descriptions. ${agency} confirms every detail.`,
      },
    },
    coverage: {
      aria: "Built from tours",
      built: (n) => `Built from ${n} ${plural(n, "tour", "tours")}`,
      uncovered: "Part of this trip isn't covered by a tour",
      unnamedTour: "Tour",
      noLink: "no tour link",
    },
    activities: {
      none: "No other activities here.",
      onTours: (n) => `on ${n} ${plural(n, "tour", "tours")}`,
    },
    nextStops: "Where the tours go next from your last stop.",
    narrationIntro: "We'll pull each day's story from the tours' own itineraries, in your chosen order.",
    consent: `${agency} may contact me about this trip.`,
    poweredBy: (badge) => (badge ? { name: AA_NAME, href: AA_URL } : null),
  };
}

/** The catalogue text label on Adventure Asia's own pages (AA tour pages); a planner reads it from brandCopy. */
export const AA_CATALOG_AI: AiPill = aaCopy().catalogAi;

/** The shared components' words for an agency (its name) or for Adventure Asia's own site (null). */
export function brandCopy(agency: string | null, lang: "en" | "vi" = "en"): BrandCopy {
  const name = typeof agency === "string" ? agency.trim() : "";
  if (!name) return aaCopy();
  // AA-1160: an agency's planner in Vietnamese (its CIS locale); Adventure Asia's own site stays English.
  return lang === "vi" ? agencyCopyVi(name) : agencyCopy(name);
}

export interface TenantMetaSite {
  name: string;
  locale: string;
}

/**
 * The metadata of every page under /{slug} (app/[tenant]/layout.tsx): the agency's name replaces each brand-bearing
 * key of AA_SITE_METADATA (title default and template, description, og:site_name), so no tab title, description or
 * link preview of an agency's page names Adventure Asia. `site` null: a slug that does not resolve (the 404 page), a
 * neutral title with no brand at all.
 * The agency's own title is `absolute`, not `default`: Next applies the root layout's template ("%s · Adventure Asia")
 * to a layout's `default` when that layout's title is the page's (the agency 404 under /{slug}, where no page sets a
 * title), so the tab read "Trip planner · <agency> · Adventure Asia". `template` still names the agency on every page.
 */
export function tenantMetadata(site: TenantMetaSite | null) {
  if (!site) {
    return {
      title: { default: "Trip planner", template: "%s" },
      description: "Plan a trip day by day on the map.",
      openGraph: { siteName: "Trip planner", type: "website" as const },
    };
  }
  return {
    title: { absolute: `Trip planner · ${site.name}`, template: `%s · ${site.name}` },
    description: `Plan your trip with ${site.name}: shape it on the map day by day and send it to ${site.name}.`,
    openGraph: {
      siteName: site.name,
      type: "website" as const,
      locale: site.locale === "vi" ? "vi_VN" : "en_US",
    },
  };
}

// AA-1160: the agency's words in Vietnamese (the same keys as agencyCopy; lib/plannerI18n.test.ts compares them).
function agencyCopyVi(agency: string): BrandCopy {
  const itinerary =
    "Lịch trình riêng của tour cho những ngày này, do AI viết từ ghi chú của tour và được kiểm tra chất lượng tự động.";
  return {
    agency,
    catalogAi: {
      label: "AI",
      shortLabel: "AI",
      explanation:
        "Do một mô hình AI viết từ ghi chú của tour và được kiểm tra chất lượng tự động. " +
        `${agency} xác nhận mọi chi tiết trước khi bạn đặt.`,
    },
    narration: {
      master: {
        label: "Lịch trình tour · AI",
        shortLabel: "Lịch trình · AI",
        explanation: `${itinerary} ${agency} xác nhận mọi chi tiết.`,
      },
      master_basic: {
        label: "Lịch trình tour · AI",
        shortLabel: "Lịch trình · AI",
        explanation:
          `${itinerary} Tính năng viết bằng AI hiện chưa dùng được (đang tạm dừng, hoặc đã hết hạn mức hôm nay), nên ` +
          `những ngày chưa có lịch trình chỉ hiện tên địa điểm. ${agency} xác nhận mọi chi tiết.`,
      },
      mixed: {
        label: "Lịch trình tour + AI",
        shortLabel: "Lịch trình + AI",
        explanation:
          "Phần lớn là lịch trình riêng của các tour, do AI viết từ ghi chú của tour và được kiểm tra chất lượng tự " +
          `động; AI cũng viết những ngày còn thiếu. ${agency} xác nhận mọi chi tiết.`,
      },
      llm: {
        label: "Mô tả bằng AI",
        shortLabel: "Mô tả bằng AI",
        explanation: `Do AI viết từ mô tả của các tour. ${agency} xác nhận mọi chi tiết.`,
      },
    },
    coverage: {
      aria: "Dựng từ các tour",
      built: (n) => `Dựng từ ${n} tour`,
      uncovered: "Một phần chuyến đi chưa có tour nào đi qua",
      unnamedTour: "Tour",
      noLink: "chưa có tour nối",
    },
    activities: {
      none: "Không có hoạt động nào khác ở đây.",
      onTours: (n) => `trong ${n} tour`,
    },
    nextStops: "Nơi các tour đi tiếp từ điểm dừng cuối của bạn.",
    narrationIntro: "Chúng tôi sẽ lấy câu chuyện từng ngày từ lịch trình riêng của các tour, theo thứ tự bạn chọn.",
    consent: `${agency} có thể liên hệ với tôi về chuyến đi này.`,
    poweredBy: (badge) => (badge ? { name: AA_NAME, href: AA_URL } : null),
  };
}
