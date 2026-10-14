import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ReferenceLine,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, type Block as BlockData, type BlockRun, type WeekJournal } from "../lib/api";
import { SESSION_NAMES, formatDate, hms, km, metres, paceMin, parseDate, signedPct } from "../lib/format";
import { Card, ErrorBox, Stat } from "../components/ui";
import {
  AXIS,
  BRAND,
  ChartCard,
  GRID,
  Keys,
  MARGIN,
  PLANNED,
  TipBox,
  type TipRow,
  zoneColors,
} from "../components/chart";


export function Block({ onOpenWeek }: { onOpenWeek: (n: number) => void }) {
  const [data, setData] = useState<BlockData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setError(null);
    api.block().then(setData).catch((e: Error) => setError(e.message));
  };
  useEffect(load, []);

  if (error) return <ErrorBox message={error} onRetry={load} />;
  if (!data) return <p className="p-4 text-sm text-muted">Loading block…</p>;

  const { weeks, totals, runs } = data;
  const current = weeks.find((w) => w.current)?.number;
  const zones = data.zone_names;
  const zoneColor = zoneColors(zones);
  const hasZoneData = weeks.some((w) => Object.values(w.zone_seconds).some((s) => s > 0));

  const rows = weeks.map((w) => ({
    week: `W${w.number}`,
    number: w.number,
    w,
    planned: w.planned_km,
    actual: w.started ? w.actual_km : null,
    longPlanned: w.long_run.planned_km,
    longActual: w.long_run.actual_km,
    climb: w.started ? w.ascent_m ?? 0 : null,
    ...Object.fromEntries(zones.map((z) => [z, w.started ? (w.zone_seconds[z] ?? 0) / 60 : null])),
  }));
  type Row = (typeof rows)[number];
  const clickWeek = (e: { activePayload?: { payload: Row }[] } | null) => {
    const n = e?.activePayload?.[0]?.payload.number;
    if (n) onOpenWeek(n);
  };
  const nowLine = current ? (
    <ReferenceLine x={`W${current}`} stroke="var(--color-muted)" strokeDasharray="3 3" label={{ value: "Now", position: "top", fontSize: 11, fill: "var(--color-muted)" }} />
  ) : null;

  const driftRuns = runs.filter((r) => r.hr_drift_pct !== null);
  const toPoint = (r: BlockRun) => ({ ...r, x: new Date(r.start_time).getTime(), y: r.hr_drift_pct });

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold">The whole block</h2>
        <p className="text-xs text-muted">
          What happened week by week. Click a week in any chart or in the table to open it.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        <Stat
          label="Km so far"
          value={km(totals.actual_km, 1)}
          hint={`${totals.planned_to_date_km} planned to date · ${totals.planned_block_km} in the block`}
        />
        <Stat label="Runs" value={totals.runs} />
        <Stat label="Running time" value={hms(totals.time_s)} />
        <Stat label="Total climb" value={metres(totals.ascent_m)} />
      </div>

      <ChartCard title="Weekly km" subtitle="Planned against what you ran. Weeks that have not started show only the plan.">
        <BarChart data={rows} barGap={2} margin={MARGIN} onClick={clickWeek}>
          <CartesianGrid {...GRID} />
          <XAxis dataKey="week" {...AXIS} />
          <YAxis {...AXIS} width={30} />
          <Tooltip cursor={{ fill: "var(--color-brand-soft)" }} content={<WeekTip render={(r: Row) => [
            [PLANNED, km(r.planned, 1), "planned"],
            [BRAND, r.actual === null ? "Not started" : km(r.actual, 1), "ran"],
          ]} />} />
          <Legend content={<Keys items={[[PLANNED, "Planned"], [BRAND, "Ran"]]} />} />
          {nowLine}
          <Bar dataKey="planned" fill={PLANNED} radius={[4, 4, 0, 0]} />
          <Bar dataKey="actual" fill={BRAND} radius={[4, 4, 0, 0]} />
        </BarChart>
      </ChartCard>

      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard title="Long run" subtitle="Planned distance against the Sunday long run you assigned.">
          <BarChart data={rows} barGap={2} margin={MARGIN} onClick={clickWeek}>
            <CartesianGrid {...GRID} />
            <XAxis dataKey="week" {...AXIS} />
            <YAxis {...AXIS} width={30} />
            <Tooltip cursor={{ fill: "var(--color-brand-soft)" }} content={<WeekTip render={(r: Row) => [
              [PLANNED, km(r.longPlanned, 1), "planned"],
              [BRAND, r.longActual === null ? "No long run yet" : km(r.longActual, 1), "ran"],
              ...(r.w.long_run.time_s ? [[null, hms(r.w.long_run.time_s), "time"] as TipRow] : []),
              ...(r.w.long_run.ascent_m !== null ? [[null, metres(r.w.long_run.ascent_m), "climb"] as TipRow] : []),
            ]} />} />
            <Legend content={<Keys items={[[PLANNED, "Planned"], [BRAND, "Ran"]]} />} />
            {nowLine}
            <Bar dataKey="longPlanned" fill={PLANNED} radius={[4, 4, 0, 0]} />
            <Bar dataKey="longActual" fill={BRAND} radius={[4, 4, 0, 0]} />
          </BarChart>
        </ChartCard>

        <ChartCard title="Climb per week (m)" subtitle="Total ascent of every run in the week, including extras.">
          <BarChart data={rows} margin={MARGIN} onClick={clickWeek}>
            <CartesianGrid {...GRID} />
            <XAxis dataKey="week" {...AXIS} />
            <YAxis {...AXIS} width={36} allowDecimals={false} />
            <Tooltip cursor={{ fill: "var(--color-brand-soft)" }} content={<WeekTip render={(r: Row) => [
              [BRAND, r.climb === null ? "Not started" : metres(r.climb), "climbed"],
            ]} />} />
            {nowLine}
            <Bar dataKey="climb" fill={BRAND} radius={[4, 4, 0, 0]} />
          </BarChart>
        </ChartCard>
      </div>

      <ChartCard
        empty={hasZoneData ? undefined : "Set your zones in Settings and recalculate to see this chart."}
        title="Time in heart-rate zones (minutes)" subtitle="Your zones from Settings, summed over every run in the week. Time without heart rate is listed in the tooltip, not drawn.">
        {hasZoneData && (
          <BarChart data={rows} margin={MARGIN} onClick={clickWeek}>
            <CartesianGrid {...GRID} />
            <XAxis dataKey="week" {...AXIS} />
            <YAxis {...AXIS} width={36} allowDecimals={false} />
            <Tooltip cursor={{ fill: "var(--color-brand-soft)" }} content={<WeekTip render={(r: Row) => [
              ...zones.slice().reverse().map((z) => [
                zoneColor[z],
                `${Math.round((r.w.zone_seconds[z] ?? 0) / 60)} min`,
                z,
              ] as TipRow),
              ...(r.w.unknown_hr_s ? [[null, `${Math.round(r.w.unknown_hr_s / 60)} min`, "without heart rate"] as TipRow] : []),
            ]} />} />
            <Legend content={<Keys items={zones.map((z) => [zoneColor[z], z])} />} />
            {nowLine}
            {zones.map((z) => (
              <Bar key={z} dataKey={z} stackId="z" fill={zoneColor[z]} stroke="var(--color-panel)" strokeWidth={2} />
            ))}
          </BarChart>
        )}
      </ChartCard>

      <ChartCard
        empty={driftRuns.length ? undefined : "No run of 30 minutes or more with heart rate yet."}
        title="Heart-rate drift per run (%)"
        subtitle="How much the heart rate rose relative to pace from the first half of a run to the second. Runs shorter than 30 minutes or without heart rate are left out. Hills, heat and wind also move it."
      >
        {driftRuns.length > 0 && (
          <ScatterChart margin={MARGIN}>
            <CartesianGrid {...GRID} />
            <XAxis
              type="number"
              dataKey="x"
              domain={[parseDate(weeks[0].start_date).getTime(), parseDate(weeks[weeks.length - 1].end_date).getTime()]}
              tickFormatter={(v) => new Date(v).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}
              {...AXIS}
            />
            <YAxis
              type="number"
              dataKey="y"
              {...AXIS}
              width={36}
              allowDecimals={false}
              domain={[(min: number) => Math.min(0, Math.floor(min)), (max: number) => Math.max(1, Math.ceil(max))]}
            />
            <ReferenceLine y={0} stroke="var(--color-muted)" />
            <Tooltip cursor={false} content={<RunTip />} />
            <Legend content={<Keys items={[[BRAND, "Long runs"], ["var(--color-other-runs)", "Other runs"]]} />} />
            <Scatter data={driftRuns.filter((r) => r.label === "L").map(toPoint)} fill={BRAND} shape={<Dot />} />
            <Scatter data={driftRuns.filter((r) => r.label !== "L").map(toPoint)} fill="var(--color-other-runs)" shape={<Dot />} />
          </ScatterChart>
        )}
      </ChartCard>

      <Card className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-muted">
            <tr>
              <th className="px-3 py-2">Week</th>
              <th>Dates</th>
              <th className="text-right">Planned</th>
              <th className="text-right">Ran</th>
              <th className="text-right">Long run</th>
              <th className="text-right">Climb</th>
              <th className="text-right">Time</th>
              <th className="px-3 text-right">Sessions</th>
              <th className="pr-3">My notes</th>
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {weeks.map((w) => (
              <tr
                key={w.number}
                onClick={() => onOpenWeek(w.number)}
                className={`cursor-pointer border-t border-line hover:bg-brand-soft ${w.current ? "font-semibold" : ""}`}
              >
                <td className="px-3 py-1.5">
                  {w.number}
                  {w.current && <span className="ml-1 text-xs text-brand">now</span>}
                </td>
                <td className="whitespace-nowrap text-xs text-muted">
                  {formatDate(w.start_date)} – {formatDate(w.end_date)}
                </td>
                <td className="text-right">{w.planned_km.toFixed(1)}</td>
                <td className="text-right">{w.started ? w.actual_km.toFixed(1) : "—"}</td>
                <td className="text-right">
                  {w.long_run.actual_km !== null ? w.long_run.actual_km.toFixed(1) : "—"}
                  <span className="text-muted"> / {w.long_run.planned_km ?? "—"}</span>
                </td>
                <td className="text-right">{w.started && w.ascent_m !== null ? metres(w.ascent_m) : "—"}</td>
                <td className="text-right">{w.started ? hms(w.actual_duration_s) : "—"}</td>
                <td className="px-3 text-right">
                  {w.sessions_completed} / {w.sessions_planned}
                </td>
                <td className="pr-3 font-normal">
                  <JournalSummary journal={w.journal} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------- pieces

/** Reason tags plus the start of the note; the full note is in the tooltip and the Week screen. */
function JournalSummary({ journal }: { journal: WeekJournal }) {
  if (!journal.note && journal.reasons.length === 0) return <span className="text-muted">—</span>;
  return (
    <div className="flex min-w-[160px] max-w-[280px] flex-wrap items-center gap-1" title={journal.note ?? undefined}>
      {journal.reasons.map((r) => (
        <span key={r} className="rounded-full bg-warn/15 px-1.5 py-0.5 text-[10px] font-medium text-warn">
          {r}
        </span>
      ))}
      {journal.note && <span className="truncate text-xs text-muted">{journal.note}</span>}
    </div>
  );
}



function WeekTip<
  R extends {
    w: { number: number; start_date: string; end_date: string; notes: string | null; journal?: WeekJournal };
  },
>({
  active,
  payload,
  render,
}: {
  active?: boolean;
  payload?: { payload: R }[];
  render: (row: R) => TipRow[];
}) {
  if (!active || !payload?.length) return null;
  const r = payload[0].payload;
  return (
    <TipBox
      title={`Week ${r.w.number} · ${formatDate(r.w.start_date)} – ${formatDate(r.w.end_date)}`}
      rows={render(r)}
      journal={r.w.journal}
    />
  );
}

function RunTip({ active, payload }: { active?: boolean; payload?: { payload: BlockRun }[] }) {
  if (!active || !payload?.length) return null;
  const r = payload[0].payload;
  const name = r.label === "extra" ? "Extra run" : r.label ? SESSION_NAMES[r.label] : "Run";
  return (
    <TipBox
      title={`${formatDate(r.start_time)} · ${name}`}
      rows={[
        [null, signedPct(r.hr_drift_pct), "drift"],
        [null, km(r.distance_km), "distance"],
        [null, `${paceMin(r.pace_min_km)} min/km`, "average pace"],
        [null, r.avg_hr ? `${r.avg_hr} bpm` : "—", "average HR"],
        [null, metres(r.ascent_m), "climb"],
      ]}
    />
  );
}


function Dot(props: { cx?: number; cy?: number; fill?: string }) {
  return <circle cx={props.cx} cy={props.cy} r={5} fill={props.fill} stroke="var(--color-panel)" strokeWidth={2} />;
}
