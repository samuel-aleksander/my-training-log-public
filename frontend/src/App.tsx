import { useEffect, useState } from "react";
import { START_COMMAND, api } from "./lib/api";
import { localDay } from "./lib/format";
import { WeekView } from "./pages/WeekView";
import { Upload } from "./pages/Upload";
import { Settings } from "./pages/Settings";
import { Block } from "./pages/Block";

type Tab = "week" | "block" | "upload" | "settings";

export default function App() {
  const [tab, setTab] = useState<Tab>("week");
  const [week, setWeek] = useState<number | null>(null);
  const [key, setKey] = useState(0);
  const [offline, setOffline] = useState(false);

  // Open on the week that contains today (the last one once the block is over).
  useEffect(() => {
    const today = localDay(new Date().toISOString());
    api
      .weeks()
      .then((ws) => {
        const current = ws.find((w) => w.start_date <= today && today <= w.end_date);
        const over = ws.length && today > ws[ws.length - 1].end_date;
        setWeek((n) => n ?? current?.number ?? (over ? ws[ws.length - 1].number : 1));
      })
      .catch(() => setWeek((n) => n ?? 1));
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/health")
      .then((r) => {
        if (!cancelled) setOffline(!r.ok);
      })
      .catch((e) => {
        console.error("[api] /api/health did not respond", e);
        if (!cancelled) setOffline(true);
      });
    return () => {
      cancelled = true;
    };
  }, [key]);

  const tabs: [Tab, string][] = [
    ["week", "Week"],
    ["block", "Block"],
    ["upload", "Upload"],
    ["settings", "Settings"],
  ];

  return (
    <div className="mx-auto max-w-5xl px-3 py-4 pb-24 sm:px-6 sm:pb-8">
      <header className="mb-4">
        <h1 className="text-xl font-bold">Base block — Big Sur</h1>
        <p className="text-xs text-muted">13 weeks · 21 Sep – 20 Dec 2026 · km and min/km</p>
      </header>

      {offline && (
        <div className="mb-4 rounded-xl border border-warn/40 bg-warn/10 p-3 text-sm text-warn">
          <p className="font-medium">The local server is not running</p>
          <p className="mt-1 text-xs">
            Open another terminal and run:
            <code className="mt-1 block font-mono">{START_COMMAND}</code>
          </p>
          <button
            type="button"
            onClick={() => setKey((k) => k + 1)}
            className="mt-2 rounded-lg border border-warn/40 px-3 py-1.5 text-xs font-medium"
          >
            Retry
          </button>
        </div>
      )}

      <nav className="fixed inset-x-0 bottom-0 z-10 flex border-t border-line bg-panel sm:static sm:mb-4 sm:w-fit sm:rounded-xl sm:border">
        {tabs.map(([id, label]) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`flex-1 px-4 py-3 text-sm font-medium sm:flex-none sm:py-2 ${
              tab === id ? "bg-brand-soft text-brand" : "text-muted"
            }`}
          >
            {label}
          </button>
        ))}
      </nav>

      <main>
        {tab === "week" &&
          (week === null ? (
            <p className="p-4 text-sm text-muted">Loading…</p>
          ) : (
            <WeekView key={`${week}-${key}`} initialWeek={week} />
          ))}
        {tab === "block" && (
          <Block
            key={key}
            onOpenWeek={(n) => {
              setWeek(n);
              setTab("week");
            }}
          />
        )}
        {tab === "upload" && (
          <Upload
            key={key}
            onImported={(n) => {
              setWeek(n);
              setKey((k) => k + 1);
            }}
          />
        )}
        {tab === "settings" && <Settings key={key} />}
      </main>
    </div>
  );
}
