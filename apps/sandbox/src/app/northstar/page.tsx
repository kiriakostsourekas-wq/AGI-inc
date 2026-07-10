import { AppShell } from "@/components/app-shell";
import { NorthstarApp } from "./northstar-app";

type PageProps = {
  searchParams: Promise<{ run?: string }>;
};

export default async function NorthstarPage({ searchParams }: PageProps) {
  const params = await searchParams;
  const runId = params.run || "demo";
  return (
    <AppShell app="northstar" runId={runId}>
      <NorthstarApp runId={runId} />
    </AppShell>
  );
}
