// AA-995 (spec §3.3) — centred Satisfy h2, optional one-line lead, a 40 x 2 px gold rule, and an optional
// action ("See all →", carousel buttons) that sits right of the title from md and under the lead below.
import type { ReactNode } from "react";

export default function SectionTitle({
  id,
  title,
  lead,
  action,
  onDark = false,
}: {
  id: string;
  title: string;
  lead?: ReactNode;
  action?: ReactNode;
  onDark?: boolean;
}) {
  return (
    <div className="relative flex flex-col items-center text-center md:block">
      <h2 id={id} className={`font-script text-script ${onDark ? "text-k-band-accent" : "text-k-ink"}`}>
        {title}
      </h2>
      {lead && (
        <p className={`mx-auto mt-3 max-w-[56ch] text-lead ${onDark ? "text-k-on-band-muted" : "text-k-muted"}`}>{lead}</p>
      )}
      <span aria-hidden className="mx-auto mt-4 block h-0.5 w-10 rounded-pill bg-k-accent" />
      {action && (
        <div className="mt-4 flex items-center justify-center gap-2 md:absolute md:bottom-0 md:right-0 md:mt-0">{action}</div>
      )}
    </div>
  );
}
