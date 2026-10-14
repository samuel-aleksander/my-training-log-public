import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, Tooltip, XAxis, YAxis } from "recharts";
import { api, type RunSummary, type SessionItem, type WeekDetail } from "../lib/api";
import {
  SESSION_NAMES,
  formatDate,
  hms,
  km,
  localDay,
  metres,
  paceMin,
  parseDate,
  signedKm,
  signedPct,
} from "../lib/format";
import { ActivityDetail } from "../components/ActivityDetail";
import { WeekNotes } from "../components/WeekNotes";
import { AXIS, BRAND, ChartCard, GRID, Keys, PLANNED, TipBox, type TipRow } from "../components/chart";
import { ZoneBar, ZoneLegend } from "../components/ZoneBar";
import { AutoTag, Badge, Button, Card, ErrorBox, Stat } from "../components/ui";

export function WeekView({ initialWeek }: { initialWeek: number }) {
  const [number, setNumber] = useState(initialWeek);
  const [week, setWeek] = useState<WeekDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openSession, setOpenSession] = useState<number | null>(null);
  const [editing, setEditing] = useState<number | null>(null);

  const load = () => {
    setError(null);
    return api
      .week(number)
      .then(setWeek)
      .catch((e: Error) => setError(e.message));
  };
  useEffect(() => {
    load();
  }, [number]);

  if (error) return <ErrorBox message={error} onRetry={load} />;
  if (!week) return <p className="p-4 text-sm text-muted">Loading week…</p>;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <Button variant="ghost" disabled={number <= 1} onClick={() => setNumber(number - 1)}>
          ← Previous
        </Button>
        <div className="text-center">
          <h2 className="text-lg font-semibold">Week {week.number} of 13</h2>
          <p className="text-xs text-muted">
            {formatDate(week.start_date)} – {formatDate(week.end_date)}
          </p>
        </div>
        <Button variant="ghost" disabled={number >= 13} onClick={() => setNumber(number + 1)}>
          Next →
        </Button>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <Stat
          label="Km ran"
          value={km(week.actual_km, 1)}
          hint={
            `of ${week.planned_km} planned · ${week.diff_km > 0 ? "+" : ""}${week.diff_km.toFixed(1)} km` +
            (week.completion_pct === null ? "" : ` (${week.completion_pct} %)`) +
            (week.planned_km !== week.seeded_planned_km ? ` · original plan ${week.seeded_planned_km}` : "")
          }
        />
        <Stat
          label="Sessions"
          value={`${week.sessions_completed} / ${week.sessions_planned}`}
          hint={week.extras.length ? `+ ${week.extras.length} extra` : undefined}
        />
        <Stat label="Running time" value={hms(week.actual_duration_s)} />
        <Stat label="Average pace" value={week.avg_pace_min_km ? `${paceMin(week.avg_pace_min_km)} /km` : "—"} />
        <Stat
          label="Average heart rate"
          value={week.avg_hr ? `${week.avg_hr} bpm` : "—"}
          hint={week.avg_hr ? "weighted by each run's time" : undefined}
        />
        <Stat label="Climb" value={metres(week.ascent_m)} />
      </div>

      <WeekNotes
        key={week.number}
        weekNumber={week.number}
        journal={week.journal}
        onSaved={(journal) => setWeek({ ...week, journal })}
      />

      <div className="grid gap-4 lg:grid-cols-2">
        <DayChart week={week} />
        <WeekZones week={week} />
      </div>

      {week.notes && (
        <Card className="p-3 text-sm">
          <span className="font-medium">Complementary work: </span>
          {week.notes}
        </Card>
      )}
      <Card className="p-3 text-xs text-muted">{week.effort_context}</Card>

      <div className="space-y-2">
        {week.sessions.map((s) => (
          <Card key={s.id}>
            <div className="flex flex-wrap items-center justify-between gap-2 p-3">
              <div className="w-full sm:w-auto sm:min-w-[200px]">
                <div className="flex items-center gap-2">
                  <span className="font-semibold">{SESSION_NAMES[s.label]}</span>
                  <Badge status={s.status} />
                  {s.activity?.auto_assigned && <AutoTag />}
                </div>
                <p className="text-xs text-muted">
                  {s.activity && localDay(s.activity.start_time) !== s.planned_date ? (
                    <>
                      Ran {formatDate(s.activity.start_time)}
                      <span className="opacity-70"> · planned {formatDate(s.planned_date)}</span>
                    </>
                  ) : (
                    formatDate(s.planned_date)
                  )}
                </p>
              </div>
              <div className="grid w-full grid-cols-2 gap-2 sm:w-auto sm:flex-1 sm:grid-cols-4">
                <Mini label="Target" value={km(s.target_km)} hint={
                  s.target_km !== s.seeded_km ? `Original ${s.seeded_km} km` : undefined
                } />
                <Mini label="Actual" value={km(s.actual_km)} />
                <Mini label="Difference" value={signedKm(s.diff_km)} />
                <Mini
                  label="Target duration"
                  value={s.target_duration_display}
                  hint={
                    s.diff_duration_min !== null
                      ? `Difference ${s.diff_duration_min > 0 ? "+" : ""}${s.diff_duration_min} min`
                      : undefined
                  }
                />
              </div>
              <div className="flex gap-2">
                <Button variant="ghost" onClick={() => setEditing(editing === s.id ? null : s.id)}>
                  Edit target
                </Button>
                {s.activity && (
                  <Button
                    variant="ghost"
                    onClick={() => setOpenSession(openSession === s.id ? null : s.id)}
                  >
                    {openSession === s.id ? "Hide" : "Details"}
                  </Button>
                )}
              </div>
            </div>
            {s.activity && <RunLine run={s.activity} zoneNames={week.zones?.names} />}
            {s.notes && <p className="px-3 pb-2 text-xs text-muted">{s.notes}</p>}
            {editing === s.id && <TargetEditor s={s} onDone={() => { setEditing(null); load(); }} />}
            {openSession === s.id && s.activity && (
              <ActivityDetail activityId={s.activity.id} onChanged={load} />
            )}
          </Card>
        ))}
      </div>

      <section>
        <h3 className="mb-2 text-sm font-semibold">Extra runs this week</h3>
        {week.extras.length === 0 ? (
          <p className="text-sm text-muted">None.</p>
        ) : (
          week.extras.map((e) => (
            <Card key={e.id} className="mb-2">
              <div className="flex flex-wrap items-center justify-between gap-2 p-3">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-semibold">{e.extra_type || "Extra run"}</span>
                    <Badge status="extra" />
                    {e.auto_assigned && <AutoTag />}
                  </div>
                  <p className="text-xs text-muted">{formatDate(e.start_time)}</p>
                </div>
                <Mini label="Distance" value={km(e.distance_km)} />
                <Button
                  variant="ghost"
                  onClick={() => setOpenSession(openSession === -e.id ? null : -e.id)}
                >
                  {openSession === -e.id ? "Hide" : "Details"}
                </Button>
              </div>
              <RunLine run={e} zoneNames={week.zones?.names} />
              {openSession === -e.id && <ActivityDetail activityId={e.id} onChanged={load} />}
            </Card>
          ))
        )}
      </section>
    </div>
  );
}

/** What a run looked like, on its card: time, pace, heart rate, climb, drift and zones. */
function RunLine({ run, zoneNames }: { run: RunSummary; zoneNames?: string[] }) {
  const zoneSecs = Object.fromEntries((run.zones?.buckets ?? []).map((b) => [b.name, b.seconds]));
  return (
    <div className="space-y-2 border-t border-line px-3 py-2">
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
        <Mini label="Time" value={hms(run.timer_time_s)} />
        <Mini label="Pace" value={run.pace_min_km ? `${paceMin(run.pace_min_km)} /km` : "—"} />
        <Mini
          label="Heart rate"
          value={run.avg_hr ? `${run.avg_hr} bpm` : "No data"}
          hint={run.max_hr ? `max ${run.max_hr}` : undefined}
        />
        <Mini label="Climb" value={metres(run.ascent_m)} />
        <Mini label="HR drift" value={signedPct(run.hr_drift_pct)} hint={run.hr_drift_pct === null ? "run too short or no HR" : undefined} />
        <Mini label="Effort" value={run.rpe === null ? "—" : `${run.rpe}/10`} />
      </div>
      {zoneNames && run.zones && (
        <div className="flex items-center gap-2">
          <span className="shrink-0 text-[11px] uppercase tracking-wide text-muted">HR zones</span>
          <ZoneBar names={zoneNames} seconds={zoneSecs} />
        </div>
      )}
      {run.note && <p className="text-xs italic text-muted">“{run.note}”</p>}
    </div>
  );
}

/** Planned km by the day it was planned; ran km by the day you actually ran. */
function DayChart({ week }: { week: WeekDetail }) {
  const today = localDay(new Date().toISOString());
  const rows = week.days.map((d) => ({
    ...d,
    day: parseDate(d.date).toLocaleDateString("en-GB", { weekday: "short" }),
    // a future day shows the plan only; a past day with no run shows 0
    ran: d.ran_km ?? (d.date <= today ? 0 : null),
  }));
  type Row = (typeof rows)[number];
  return (
    <ChartCard title="Day by day (km)" subtitle="Planned on the day it was planned, ran on the day you ran it.">
      <BarChart data={rows} barGap={2} margin={{ top: 8, right: 4, left: 0, bottom: 0 }}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey="day" {...AXIS} />
        <YAxis {...AXIS} width={30} />
        <Tooltip
          cursor={{ fill: "var(--color-brand-soft)" }}
          content={({ active, payload }) => {
            if (!active || !payload?.length) return null;
            const r = payload[0].payload as Row;
            const tipRows: TipRow[] = [
              [PLANNED, r.planned_km === null ? "Rest day" : km(r.planned_km, 1),
                r.planned_labels.map((l) => SESSION_NAMES[l].split(" (")[0]).join(", ") || "planned"],
              [BRAND, r.ran_km === null ? (r.date <= today ? "No run" : "—") : km(r.ran_km, 1),
                r.runs > 1 ? `ran, ${r.runs} runs` : "ran"],
            ];
            return <TipBox title={formatDate(r.date)} rows={tipRows} />;
          }}
        />
        <Legend content={<Keys items={[[PLANNED, "Planned"], [BRAND, "Ran"]]} />} />
        <Bar dataKey="planned_km" fill={PLANNED} radius={[4, 4, 0, 0]} />
        <Bar dataKey="ran" fill={BRAND} radius={[4, 4, 0, 0]} />
      </BarChart>
    </ChartCard>
  );
}

function WeekZones({ week }: { week: WeekDetail }) {
  const z = week.zones;
  return (
    <Card className="p-3">
      <h3 className="text-sm font-semibold">Time in heart-rate zones</h3>
      <p className="mb-3 text-xs text-muted">
        Share of the time with measured heart rate, over every run this week. A description, not a target.
      </p>
      {!z ? (
        <p className="text-sm text-muted">Set your zones in Settings to see this.</p>
      ) : z.measured_s === 0 ? (
        <p className="text-sm text-muted">No heart-rate data this week yet.</p>
      ) : (
        <div className="space-y-3">
          <ZoneBar names={z.names} seconds={z.seconds} thick />
          <ZoneLegend names={z.names} seconds={z.seconds} />
          <p className="text-xs text-muted">
            Measured with heart rate: {hms(z.measured_s)}
            {z.unknown_hr_s > 0 && ` · without heart rate: ${hms(z.unknown_hr_s)} (not counted)`}
          </p>
        </div>
      )}
    </Card>
  );
}

function Mini({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-muted">{label}</div>
      <div className="text-sm font-semibold tabular-nums">{value}</div>
      {hint && <div className="text-[10px] text-muted">{hint}</div>}
    </div>
  );
}

function TargetEditor({ s, onDone }: { s: SessionItem; onDone: () => void }) {
  const [kmv, setKmv] = useState(String(s.target_km));
  const [dur, setDur] = useState(s.target_duration_min === null ? "" : String(s.target_duration_min));
  return (
    <div className="flex flex-wrap items-end gap-2 border-t border-line bg-surface/60 p-3">
      <label className="text-sm">
        Target distance (km)
        <input
          value={kmv}
          onChange={(e) => setKmv(e.target.value)}
          className="mt-1 block w-32 rounded-lg border border-line px-2 py-1.5"
        />
      </label>
      <label className="text-sm">
        Target duration (min, optional)
        <input
          value={dur}
          onChange={(e) => setDur(e.target.value)}
          placeholder="Not set"
          className="mt-1 block w-48 rounded-lg border border-line px-2 py-1.5"
        />
      </label>
      <Button
        onClick={async () => {
          await api.updateSession(s.id, {
            override_km: Number(kmv) === s.seeded_km ? null : Number(kmv),
            override_duration_min: dur === "" ? null : Number(dur),
          });
          onDone();
        }}
      >
        Save
      </Button>
      <Button
        variant="ghost"
        onClick={async () => {
          await api.updateSession(s.id, { override_km: null, override_duration_min: null });
          onDone();
        }}
      >
        Restore original plan ({s.seeded_km} km)
      </Button>
    </div>
  );
}
