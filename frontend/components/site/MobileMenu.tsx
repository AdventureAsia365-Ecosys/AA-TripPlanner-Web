"use client";
// AA-995 (immersive graft, judges 1–3) — below md the header links live in a native <dialog> sheet: a 44 px
// button opens it with showModal() (focus trap and Esc from the browser), and focus returns to the button
// when it closes. Following a link closes it.
import Link from "next/link";
import { useRef } from "react";
import { Icon } from "@/ui";
import { ThemeToggle } from "@/ui/ThemeToggle";
import AccountLink from "@/components/AccountLink";

export default function MobileMenu({ links }: { links: { href: string; label: string }[] }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const close = () => dialog.current?.close();

  return (
    <>
      <button
        ref={button}
        type="button"
        aria-haspopup="dialog"
        aria-label="Open menu"
        onClick={() => dialog.current?.showModal()}
        className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-full border border-k-band-line text-k-on-band transition-colors duration-fast ease-std hover:border-k-band-accent md:hidden"
      >
        <Icon name="menu" className="h-5 w-5" />
      </button>
      <dialog
        ref={dialog}
        aria-label="Menu"
        onClose={() => button.current?.focus()}
        className="tp-menu tp-on-dark m-0 w-full max-w-none rounded-b-panel border-0 bg-k-band p-0 text-k-on-band shadow-e3"
      >
        <div className="flex h-header items-center justify-between px-gutter">
          <span className="font-script text-script-sm leading-none text-k-band-accent">Menu</span>
          <button
            type="button"
            onClick={close}
            aria-label="Close menu"
            className="inline-flex h-11 w-11 items-center justify-center rounded-full border border-k-band-line text-k-on-band"
          >
            <Icon name="close" className="h-5 w-5" />
          </button>
        </div>
        <nav aria-label="Menu" className="px-gutter pb-6">
          <ul className="divide-y divide-k-band-line">
            {links.map((l) => (
              <li key={l.href}>
                <Link href={l.href} onClick={close} className="flex min-h-12 items-center text-lead font-medium text-k-on-band">
                  {l.label}
                </Link>
              </li>
            ))}
            <li className="flex min-h-12 items-center">
              <AccountLink tone="band" className="!px-0 !text-lead" />
            </li>
          </ul>
          <div className="mt-4 flex items-center gap-3 text-small text-k-on-band-muted">
            <ThemeToggle tone="band" /> Theme
          </div>
        </nav>
      </dialog>
    </>
  );
}
