import Link from "next/link";

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="page-shell site-footer__grid">
        <div>
          <p className="site-footer__brand">Trust Runtime</p>
          <p className="site-footer__note">
            An open-source portfolio prototype for inspectable, authorized, and verifiable
            browser-agent actions.
          </p>
        </div>
        <div className="site-footer__links" aria-label="Footer links">
          <Link href="/demo">Demo</Link>
          <Link href="/evals">Evaluation</Link>
          <Link href="/methodology">Methodology</Link>
        </div>
      </div>
      <div className="page-shell site-footer__bottom">
        <span>Apache-2.0 · Synthetic applications, identities, reservations, and money.</span>
        <span>Synthetic data only; no sponsorship or endorsement is claimed.</span>
      </div>
    </footer>
  );
}
