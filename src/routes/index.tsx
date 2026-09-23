import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Base block — local training log" },
      {
        name: "description",
        content:
          "Single-user local app to follow the 13-week plan and compare what was planned against your Garmin FIT files.",
      },
      { property: "og:title", content: "Base block — local training log" },
      {
        property: "og:description",
        content:
          "13-week plan, manual FIT file upload and planned/actual comparison. Runs on your laptop.",
      },
    ],
  }),
  component: Index,
});

const steps = [
  {
    title: "1 · Start it",
    code: "./dev.sh",
  },
  {
    title: "2 · Open it",
    code: "http://localhost:5173",
  },
];

function Index() {
  return (
    <div className="min-h-screen bg-background px-5 py-12">
      <div className="mx-auto max-w-2xl space-y-8">
        <header className="space-y-2">
          <h1 className="text-3xl font-bold text-foreground">Base block — Big Sur</h1>
          <p className="text-muted-foreground">
            The tracking app runs on your laptop, not here. This page is only a reminder of how to
            start it.
          </p>
        </header>

        <section className="space-y-4">
          {steps.map((s) => (
            <div key={s.title} className="rounded-xl border border-border bg-card p-4">
              <h2 className="mb-2 text-sm font-semibold text-card-foreground">{s.title}</h2>
              <pre className="overflow-x-auto rounded-lg bg-muted p-3 text-xs text-muted-foreground">
                {s.code}
              </pre>
            </div>
          ))}
        </section>

        <p className="text-sm text-muted-foreground">
          Full details, backups and limitations in <code>README.md</code>. The plan is kept
          locally in <code>docs/private/plan-base-13-semanas.md</code>, which is not committed.
        </p>
      </div>
    </div>
  );
}
