import type { Metadata } from "next";

import { RunConsole } from "@/components/run-console";

export const metadata: Metadata = { title: "Run console" };

export default async function RunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  return (
    <div className="run-page">
      <RunConsole runId={runId} />
    </div>
  );
}
