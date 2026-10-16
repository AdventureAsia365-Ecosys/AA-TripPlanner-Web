import { Icon } from "@/ui";

const ITEMS = [
  { icon: "compass", title: "Designed by Adventure Asia", body: "Every day comes from a real Adventure Asia tour." },
  { icon: "sparkle", title: "Adventure Asia AI itineraries, confirmed by advisors", body: "AI helps you explore; a person checks every trip." },
  { icon: "chat", title: "Tailor-made with an advisor", body: "No instant checkout: an advisor shapes the trip with you." },
];

export default function TrustStrip() {
  return (
    <ul className="grid gap-3 sm:grid-cols-3" aria-label="Why plan with Adventure Asia">
      {ITEMS.map((it) => (
        <li key={it.title} className="flex gap-3 rounded-2xl border border-k-line bg-k-card p-4">
          <Icon name={it.icon} className="mt-0.5 h-5 w-5 shrink-0 text-k-accent" />
          <div>
            <p className="text-[13px] font-semibold text-k-ink">{it.title}</p>
            <p className="mt-0.5 text-[12px] leading-relaxed text-k-muted">{it.body}</p>
          </div>
        </li>
      ))}
    </ul>
  );
}
