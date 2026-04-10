"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"

const NAV_ITEMS = [
  { href: "/", label: "Dashboard" },
  { href: "/teleop", label: "Teleop" },
] as const;

export function NavBar() {
  const pathname = usePathname();

  return (
    <header className="border-b border-border bg-background px-4 py-2">
      <div className="mx-auto flex max-w-[1400px] items-center justify-between">
        <h1 className="font-mono text-sm font-medium tracking-tight text-foreground">
          Robot Task Dashboard
        </h1>
        <nav className="flex gap-1">
          {NAV_ITEMS.map((item) => {
            const isActive = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`rounded-md px-3 py-1 font-mono text-xs transition-colors ${
                  isActive
                    ? "bg-foreground/10 text-foreground"
                    : "text-muted-foreground hover:bg-foreground/5 hover:text-foreground"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
