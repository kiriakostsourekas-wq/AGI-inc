import { AppShell } from "@/components/app-shell";
import { GoMailApp } from "./gomail-app";

type PageProps = {
  searchParams: Promise<{ run?: string }>;
};

export default async function GoMailPage({ searchParams }: PageProps) {
  const params = await searchParams;
  const runId = params.run || "demo";
  return (
    <AppShell app="gomail" runId={runId}>
      <GoMailApp runId={runId} />
    </AppShell>
  );
}
