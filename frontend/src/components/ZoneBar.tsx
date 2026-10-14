import { zoneColors } from "./chart";

/**
 * Time in heart-rate zones as one horizontal bar: each zone's share of the time
 * with measured heart rate. `names` fixes the order (and so the colors) across bars.
 */
export function ZoneBar({
  names,
  seconds,
  thick = false,
}: {
  names: string[];
  seconds: Record<string, number>;
  thick?: boolean;
}) {
  const colors = zoneColors(names);
  const total = names.reduce((t, n) => t + (seconds[n] ?? 0), 0);
  if (!total) return null;
  return (
    <div
      className={`flex w-full gap-[2px] overflow-hidden rounded ${thick ? "h-4" : "h-1.5"}`}
      role="img"
      aria-label={names
        .filter((n) => seconds[n])
        .map((n) => `${n} ${Math.round((seconds[n] / total) * 100)} %`)
        .join(", ")}
    >
      {names.map((n) =>
        seconds[n] ? (
          <div
            key={n}
            title={`${n} · ${Math.round(seconds[n] / 60)} min (${Math.round((seconds[n] / total) * 100)} %)`}
            style={{ width: `${(seconds[n] / total) * 100}%`, background: colors[n] }}
          />
        ) : null,
      )}
    </div>
  );
}

export function ZoneLegend({ names, seconds }: { names: string[]; seconds: Record<string, number> }) {
  const colors = zoneColors(names);
  const total = names.reduce((t, n) => t + (seconds[n] ?? 0), 0);
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
      {names.map((n) => (
        <span key={n} className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: colors[n] }} />
          <span className="text-muted">{n}</span>
          <span className="font-semibold tabular-nums">{Math.round((seconds[n] ?? 0) / 60)} min</span>
          {total > 0 && (
            <span className="text-muted tabular-nums">{Math.round(((seconds[n] ?? 0) / total) * 100)} %</span>
          )}
        </span>
      ))}
    </div>
  );
}
