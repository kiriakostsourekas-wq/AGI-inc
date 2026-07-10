import { AppShell } from "@/components/app-shell";
import { DayPlanApp } from "./dayplan-app";

type PageProps = {
  searchParams: Promise<{ run?: string }>;
};

export default async function DayPlanPage({ searchParams }: PageProps) {
  const params = await searchParams;
  const runId = params.run || "demo";
  return (
    <AppShell app="dayplan" runId={runId}>
      <DayPlanApp runId={runId} />
    </AppShell>
  );
}
