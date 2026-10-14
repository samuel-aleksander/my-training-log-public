import { useEffect, useState } from "react";
import { api, type SessionItem, type Week } from "../lib/api";
import { SESSION_NAMES, formatDate, hms, km, localDay, signedKm } from "../lib/format";
import { ActivityDetail } from "../components/ActivityDetail";
import { WeekNotes } from "../components/WeekNotes";
import { AutoTag, Badge, Button, Card, ErrorBox, Stat } from "../components/ui";

export function WeekView({ initialWeek }: { initialWeek: number }) {
  const [number, setNumber] = useState(initialWeek);
  const [week, setWeek] = useState<Week | null>(null);
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
          label="Planned km"
          value={km(week.planned_km)}
          hint={
            week.planned_km !== week.seeded_planned_km
              ? `Original plan: ${week.seeded_planned_km} km`
              : undefined
          }
        />
        <Stat label="Actual km" value={km(week.actual_km)} />
        <Stat label="Difference" value={signedKm(week.diff_km)} />
        <Stat
          label="Completed"
          value={week.completion_pct === null ? "—" : `${week.completion_pct} %`}
        />
        <Stat
          label="Sessions"
          value={`${week.sessions_completed} / ${week.sessions_planned}`}
        />
        <Stat label="Running time" value={hms(week.actual_duration_s)} />
      </div>

      <WeekNotes
        key={week.number}
        weekNumber={week.number}
        journal={week.journal}
        onSaved={(journal) => setWeek({ ...week, journal })}
      />

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
              <div className="min-w-[200px]">
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
              <div className="grid flex-1 grid-cols-2 gap-2 sm:grid-cols-4">
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
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                  <Mini label="Distance" value={km(e.distance_km)} />
                  <Mini label="Duration" value={hms(e.timer_time_s)} />
                  <Mini label="Avg HR" value={e.avg_hr ? `${e.avg_hr} bpm` : "No data"} />
                </div>
                <Button
                  variant="ghost"
                  onClick={() => setOpenSession(openSession === -e.id ? null : -e.id)}
                >
                  {openSession === -e.id ? "Hide" : "Details"}
                </Button>
              </div>
              {openSession === -e.id && <ActivityDetail activityId={e.id} onChanged={load} />}
            </Card>
          ))
        )}
      </section>
    </div>
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
