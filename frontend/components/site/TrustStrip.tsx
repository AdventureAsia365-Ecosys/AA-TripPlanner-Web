import { Icon } from "@/ui";

const ITEMS = [
  { icon: "compass", title: "Designed by Adventure Asia", body: "Every day comes from a real Adventure Asia tour." },
  { icon: "sparkle", title: "Adventure Asia AI itineraries, confirmed by advisors", body: "AI helps you explore; a person checks every trip." },
  { icon: "chat", title: "Tailor-made with an advisor", body: "No instant checkout: an advisor shapes the trip with you." },
];

// AA-995: tone "band" = inside the navy footer (light text on the band, gold icons, white hairlines).
export default function TrustStrip({ tone = "default" }: { tone?: "default" | "band" }) {
  const band = tone === "band";
  return (
    <ul className="grid gap-3 sm:grid-cols-3" aria-label="Why plan with Adventure Asia">
      {ITEMS.map((it) => (
        <li
          key={it.title}
          className={`flex gap-3 rounded-card border p-4 ${band ? "border-k-band-line" : "border-k-line bg-k-card"}`}
        >
          <Icon name={it.icon} className={`mt-0.5 h-5 w-5 shrink-0 ${band ? "text-k-band-accent" : "text-k-accent"}`} />
          <div>
            <p className={`text-small font-semibold ${band ? "text-k-on-band" : "text-k-ink"}`}>{it.title}</p>
            <p className={`mt-0.5 text-caption font-normal ${band ? "text-k-on-band-muted" : "text-k-muted"}`}>{it.body}</p>
          </div>
        </li>
      ))}
    </ul>
  );
}
