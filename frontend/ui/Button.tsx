// AA-812 — the brand button (CIS kit primitives.tsx Button, AA-752): pill radius; primary = gold
// pill, uppercase with light tracking on md/lg; secondary = gold outline; ghost = line outline.
import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";

// AA-995: "on-dark" = the outline button on the navy band and on photos (light text, white hairline that
// turns gold on hover).
export type ButtonVariant = "primary" | "secondary" | "ghost" | "on-dark";
export type ButtonSize = "sm" | "md" | "lg";

export function buttonClass(variant: ButtonVariant = "primary", size: ButtonSize = "md", extra = ""): string {
  const base =
    "inline-flex items-center justify-center gap-2 rounded-full font-semibold transition disabled:cursor-not-allowed disabled:opacity-50";
  const sizes: Record<ButtonSize, string> = {
    sm: "px-3 py-1.5 text-xs",
    md: "px-4 py-2 text-[13px]",
    lg: "px-6 py-3 text-sm",
  };
  const variants: Record<ButtonVariant, string> = {
    primary: "border border-k-accent bg-k-accent text-k-on-accent hover:brightness-95",
    secondary: "border border-k-accent-border bg-transparent text-k-accent-deep hover:bg-k-accent-tint",
    ghost: "border border-k-line bg-transparent text-k-muted hover:border-k-accent-border hover:text-k-ink",
    "on-dark": "border border-k-band-line bg-transparent text-k-on-band hover:border-k-band-accent hover:text-k-band-accent",
  };
  const caps = variant !== "ghost" && size !== "sm" ? "uppercase tracking-[0.06em]" : "";
  return `${base} ${sizes[size]} ${variants[variant]} ${caps} ${extra}`.trim();
}

export function Button({
  variant = "primary",
  size = "md",
  className = "",
  ...rest
}: { variant?: ButtonVariant; size?: ButtonSize } & ComponentProps<"button">) {
  return <button type="button" className={buttonClass(variant, size, className)} {...rest} />;
}

export function ButtonLink({
  href,
  variant = "primary",
  size = "md",
  className = "",
  children,
  ...rest
}: { href: string; variant?: ButtonVariant; size?: ButtonSize; children: ReactNode } & Omit<
  ComponentProps<typeof Link>,
  "href"
>) {
  return (
    <Link href={href} className={buttonClass(variant, size, className)} {...rest}>
      {children}
    </Link>
  );
}
