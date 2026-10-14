// AA-812 — an AA catalog photo (CIS public API URL) or a neutral placeholder when there is none.
// Plain <img>: the photos come from the CIS API, already sized (?size=large|small), so
// next/image optimisation would only add a second resize hop.
import { Icon } from "./Icon";

export function Photo({
  src,
  alt,
  className = "",
  eager = false,
}: {
  src: string | null | undefined;
  alt: string;
  className?: string;
  eager?: boolean;
}) {
  if (!src) {
    return (
      <div
        role="img"
        aria-label={`${alt} (no photo yet)`}
        className={`flex items-center justify-center bg-k-accent-tint text-k-accent-border ${className}`}
      >
        <Icon name="camera" className="h-8 w-8" />
      </div>
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt={alt}
      loading={eager ? "eager" : "lazy"}
      decoding="async"
      className={`object-cover ${className}`}
    />
  );
}
