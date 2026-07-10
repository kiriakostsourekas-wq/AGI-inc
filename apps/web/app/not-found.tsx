import Link from "next/link";

import { Badge, buttonClassName } from "@trust/ui";

export default function NotFound() {
  return (
    <section className="page-shell not-found-page">
      <Badge tone="warning">404 · Unknown resource</Badge>
      <h1>This evidence trail does not exist.</h1>
      <p>The run may be expired, deleted, or the URL may be incomplete.</p>
      <div className="button-row">
        <Link className={buttonClassName()} href="/demo">
          Start with the demo
        </Link>
        <Link className={buttonClassName({ variant: "secondary" })} href="/">
          Return home
        </Link>
      </div>
    </section>
  );
}
