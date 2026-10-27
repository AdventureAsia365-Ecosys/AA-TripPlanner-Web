// AA-995 (spec §3.8) — the navy call-to-action band: Satisfy title, one line, three reassurances we can
// back up (no response times, no prices), gold "Plan my trip" and the outline "Ask an advisor" (opens the
// planner's trip panel, where the draft goes to an advisor). Dark mode adds a hairline top and bottom so
// the band reads apart from the page (--tp-band-edge).
import { ButtonLink, Icon } from "@/ui";

export const CTA_REASSURANCES = [
  "Real Adventure Asia tours",
  "Change days and stops yourself",
  "No payment to send a draft",
];

export default function CtaBand({
  id = "advisor",
  title = "Talk it through with an advisor",
  planHref = "/plan",
  askHref = "/plan?ask=1",
}: {
  id?: string;
  title?: string;
  // Destination and tour pages open the planner on their own country / tour.
  planHref?: string;
  askHref?: string;
}) {
  return (
    <section aria-labelledby={`${id}-title`} className="tp-on-dark border-y border-k-band-edge bg-k-band py-section text-center">
      <div className="mx-auto max-w-page px-gutter" data-reveal="">
        <h2 id={`${id}-title`} className="font-script text-script text-k-band-accent">{title}</h2>
        <p className="mx-auto mt-4 max-w-[60ch] text-lead text-k-on-band-muted">
          Start from a real tour, change what you like on the map, and an Adventure Asia advisor prices and confirms it
          with you.
        </p>
        <ul className="mx-auto mt-6 flex max-w-3xl flex-col items-center gap-3 text-small text-k-on-band md:flex-row md:justify-center md:gap-8">
          {CTA_REASSURANCES.map((r) => (
            <li key={r} className="inline-flex items-center gap-2">
              <Icon name="check" className="h-4 w-4 shrink-0 text-k-band-accent" />
              {r}
            </li>
          ))}
        </ul>
        <div className="mx-auto mt-8 flex max-w-md flex-col gap-3 md:max-w-none md:flex-row md:justify-center">
          <ButtonLink href={planHref} variant="primary" size="lg" className="min-h-12">
            Plan my trip
          </ButtonLink>
          <ButtonLink href={askHref} variant="on-dark" size="lg" className="min-h-12" prefetch={false}>
            Ask an advisor
          </ButtonLink>
        </div>
      </div>
    </section>
  );
}
