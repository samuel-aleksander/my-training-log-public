/** Pieces shared by the Block and Week screens' charts. */
import type { ReactNode } from "react";
import { ResponsiveContainer } from "recharts";
import type { WeekJournal } from "../lib/api";
import { Card } from "./ui";

export const BRAND = "var(--color-brand)";
export const PLANNED = "var(--color-planned)";
export const GRID = { strokeOpacity: 0.15, vertical: false };
export const AXIS = { fontSize: 11, tickLine: false, axisLine: false };
export const MARGIN = { top: 16, right: 4, left: 0, bottom: 0 }; // room for the "Now" label

export const OUTSIDE = "Outside zones";

/** Your zones in Settings order take Garmin's colors by number: Z1 gray … Z5 red.
 * A sixth zone or more stays red rather than inventing a color. */
export function zoneColors(names: string[]): Record<string, string> {
  const named = names.filter((n) => n !== OUTSIDE);
  const out: Record<string, string> = { [OUTSIDE]: "var(--color-zone-outside)" };
  named.forEach((n, i) => {
    out[n] = `var(--color-zone-${Math.min(i + 1, 5)})`;
  });
  return out;
}

export function ChartCard({
  title,
  subtitle,
  empty,
  children,
}: {
  title: string;
  subtitle: string;
  empty?: string;
  children: ReactNode;
}) {
  return (
    <Card className="p-3">
      <h3 className="text-sm font-semibold">{title}</h3>
      <p className="mb-2 text-xs text-muted">{subtitle}</p>
      <div className="h-60">
        {empty ? (
          <div className="flex h-full items-center justify-center text-sm text-muted">{empty}</div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            {children as React.ReactElement}
          </ResponsiveContainer>
        )}
      </div>
    </Card>
  );
}

/** [series color or null, value, label]. */
export type TipRow = [string | null, string, string];

export function TipBox({ title, rows, journal }: { title: string; rows: TipRow[]; journal?: WeekJournal }) {
  const hasJournal = journal && (journal.note || journal.reasons.length > 0);
  return (
    <div className="max-w-[260px] rounded-lg border border-line bg-panel px-3 py-2 text-xs shadow-sm">
      <div className="mb-1 font-medium">{title}</div>
      {rows.map(([color, value, label], i) => (
        <div key={i} className="flex items-center gap-2">
          <span className="inline-block h-0.5 w-3" style={{ background: color ?? "transparent" }} />
          <span className="font-semibold tabular-nums">{value}</span>
          <span className="text-muted">{label}</span>
        </div>
      ))}
      {hasJournal && (
        <div className="mt-1.5 border-t border-line pt-1.5">
          {journal.reasons.length > 0 && <div className="font-medium text-warn">{journal.reasons.join(" · ")}</div>}
          {journal.note && <div className="line-clamp-3 text-muted">{journal.note}</div>}
        </div>
      )}
    </div>
  );
}

export function Keys({ items }: { items: [string, string][] }) {
  return (
    <div className="flex flex-wrap justify-center gap-x-4 gap-y-1 pt-1 text-xs text-muted">
      {items.map(([color, label]) => (
        <span key={label} className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: color }} />
          {label}
        </span>
      ))}
    </div>
  );
}
