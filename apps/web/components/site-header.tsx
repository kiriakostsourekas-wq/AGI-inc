import Link from "next/link";

import { Badge, buttonClassName } from "@trust/ui";

const navItems = [
  { href: "/demo", label: "Demo" },
  { href: "/evals", label: "Evaluation" },
  { href: "/methodology", label: "Methodology" },
];

function BrandMark() {
  return (
    <span className="brand-mark" aria-hidden="true">
      <span />
      <span />
      <span />
    </span>
  );
}

export function SiteHeader() {
  return (
    <header className="site-header">
      <div className="site-header__inner page-shell">
        <Link className="site-brand" href="/" aria-label="Trust Runtime home">
          <BrandMark />
          <span>Trust Runtime</span>
          <Badge tone="neutral" className="site-brand__version">
            v0.1
          </Badge>
        </Link>

        <nav className="desktop-nav" aria-label="Primary navigation">
          {navItems.map((item) => (
            <Link href={item.href} key={item.href}>
              {item.label}
            </Link>
          ))}
        </nav>

        <Link
          className={buttonClassName({ variant: "secondary", size: "sm", className: "header-cta" })}
          href="/demo"
        >
          Run demo
          <span aria-hidden="true">→</span>
        </Link>

        <details className="mobile-nav">
          <summary aria-label="Open navigation">
            <span />
            <span />
          </summary>
          <nav aria-label="Mobile navigation">
            {navItems.map((item) => (
              <Link href={item.href} key={item.href}>
                {item.label}
              </Link>
            ))}
          </nav>
        </details>
      </div>
    </header>
  );
}
