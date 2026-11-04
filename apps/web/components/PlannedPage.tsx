import { Card, PageHeader } from "@/components/ui";

export function PlannedPage({ title, milestone, purpose, scope }: {
  title: string; milestone: number; purpose: string; scope: string[];
}) {
  return (
    <>
      <PageHeader title={title} subtitle={purpose} />
      <Card title={`Planned: Milestone ${milestone}`}>
        <p className="text-sm text-ink-2">
          This page is not built yet. It shows no sample, placeholder or simulated results. It will
          become functional once the underlying work is implemented and evaluated:
        </p>
        <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-ink-2">
          {scope.map((s) => <li key={s}>{s}</li>)}
        </ul>
        <p className="mt-4 text-xs text-ink-3">Implementation status is tracked in docs/PROJECT_STATUS.md.</p>
      </Card>
    </>
  );
}
