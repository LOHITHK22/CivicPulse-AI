"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { CURRENT_MILESTONE, NAV } from "@/lib/nav";

function Icon({ d }: { d: string }) {
  return (
    <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d={d} />
    </svg>
  );
}

export function Sidebar() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  const links = (
    <ul className="space-y-0.5">
      {NAV.map((item) => {
        const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
        const planned = item.milestone > CURRENT_MILESTONE;
        return (
          <li key={item.href}>
            <Link
              href={item.href}
              onClick={() => setOpen(false)}
              aria-current={active ? "page" : undefined}
              className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${
                active
                  ? "bg-surface-3 text-ink"
                  : "text-ink-2 hover:bg-surface-2 hover:text-ink"
              }`}
            >
              <span className={active ? "text-teal" : "text-ink-3"}>
                <Icon d={item.icon} />
              </span>
              <span className="flex-1">{item.label}</span>
              {planned && (
                <span className="rounded border border-border px-1.5 py-px text-[10px] text-ink-3"
                  title={`Planned for Milestone ${item.milestone}`}>
                  M{item.milestone}
                </span>
              )}
            </Link>
          </li>
        );
      })}
    </ul>
  );

  return (
    <>
      <div className="flex items-center justify-between border-b border-border bg-surface-1 px-4 py-3 lg:hidden">
        <Brand />
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-controls="mobile-nav"
          className="rounded-md border border-border px-3 py-1.5 text-sm text-ink-2"
        >
          {open ? "Close" : "Menu"}
        </button>
      </div>
      {open && (
        <nav id="mobile-nav" aria-label="Primary" className="border-b border-border bg-surface-1 p-3 lg:hidden">
          {links}
        </nav>
      )}
      <aside className="hidden w-64 shrink-0 border-r border-border bg-surface-1 lg:flex lg:flex-col">
        <div className="px-5 py-5">
          <Brand />
        </div>
        <nav aria-label="Primary" className="flex-1 px-3">
          {links}
        </nav>
        <p className="px-5 py-4 text-xs leading-relaxed text-ink-3">
          Data: NYC Open Data 311 (erm2-nwe9). Reported complaints, not incident prevalence.
        </p>
      </aside>
    </>
  );
}

function Brand() {
  return (
    <Link href="/" className="flex items-center gap-2.5">
      <span aria-hidden="true" className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-teal to-violet">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#0a1224" strokeWidth="2.4"
          strokeLinecap="round" strokeLinejoin="round"><path d="M3 12h4l3-7 4 14 3-7h4" /></svg>
      </span>
      <span className="leading-tight">
        <span className="block text-sm font-semibold text-ink">CivicPulse AI</span>
        <span className="block text-[11px] text-ink-3">City operations intelligence</span>
      </span>
    </Link>
  );
}
