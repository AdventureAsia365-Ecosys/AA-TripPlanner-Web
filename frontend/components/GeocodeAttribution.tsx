// AA-847 (ADR 0007): the attribution the Geoapify Free plan and the OpenStreetMap licence (ODbL) require wherever
// a Geoapify-located point is shown (map pins and areas, static maps, place cards).
import { GEOCODE_ATTRIBUTION } from "@/lib/types";

export default function GeocodeAttribution({ className = "" }: { className?: string }) {
  const { geoapify, osm } = GEOCODE_ATTRIBUTION;
  return (
    <p className={`rounded bg-white/85 px-1.5 py-0.5 text-[10px] leading-tight text-aa-muted ${className}`}>
      <a href={geoapify.href} target="_blank" rel="noopener noreferrer" className="underline">
        {geoapify.label}
      </a>
      {" · "}
      <a href={osm.href} target="_blank" rel="noopener noreferrer" className="underline">
        {osm.label}
      </a>
    </p>
  );
}
