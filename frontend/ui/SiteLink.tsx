// AA-1038 R5 hosting (S272) — the site's internal link. A link to a planner page (/plan, /{tenant}/plan and below) is
// a plain <a>: the browser loads the planner as a document, which the web ACL challenges and so hands the planner its
// AWS WAF token (lib/plannerNav.ts). Every other href is a next/link (client-side navigation, prefetch as given).
import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";
import { isPlannerHref } from "@/lib/plannerNav";

export type SiteLinkProps = {
  href: string;
  prefetch?: boolean | null;
  children?: ReactNode;
} & Omit<ComponentProps<"a">, "href">;

export function SiteLink({ href, prefetch, children, ...rest }: SiteLinkProps) {
  if (isPlannerHref(href)) {
    // A document load on purpose (see above).
    return (
      <a href={href} {...rest}>
        {children}
      </a>
    );
  }
  return (
    <Link href={href} prefetch={prefetch} {...rest}>
      {children}
    </Link>
  );
}
