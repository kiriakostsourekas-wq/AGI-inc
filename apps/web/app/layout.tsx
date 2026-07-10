import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";

import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "Trust Runtime — Prove the outcome",
    template: "%s · Trust Runtime",
  },
  description:
    "An open-source trust runtime for inspectable, authorized, verified, and recoverable browser-agent actions.",
  applicationName: "Trust Runtime",
  openGraph: {
    title: "Trust Runtime — Action agents should prove the outcome",
    description:
      "A disrupted-trip reference application for authority, verification, recovery, and reproducible evaluation.",
    type: "website",
  },
};

export const viewport: Viewport = {
  colorScheme: "dark",
  themeColor: "#0b0d10",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <a className="skip-link" href="#main-content">
          Skip to content
        </a>
        <div className="site-frame">
          <div className="honesty-strip">
            <span className="honesty-strip__pulse" aria-hidden="true" />
            Portfolio MVP · Synthetic apps and money · Evaluation claims pending raw runs
          </div>
          <SiteHeader />
          <main id="main-content">{children}</main>
          <SiteFooter />
        </div>
      </body>
    </html>
  );
}
