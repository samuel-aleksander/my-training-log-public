import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, type ActivityDetail as Detail } from "../lib/api";
import { durationLabel, formatDateTime, hms, km, pace } from "../lib/format";
import { Button } from "./ui";

function downsample<T>(rows: T[], max = 400): T[] {
  if (rows.length <= max) return rows;
  const step = Math.ceil(rows.length / max);
  return rows.filter((_, i) => i % step === 0);
}

export function ActivityDetail({
  activityId,
  onChanged,
}: {
  activityId: number;
  onChanged: () => void;
}) {
  const [d, setD] = useState<Detail | null>(null);
  const [rpe, setRpe] = useState<string>("");
  const [note, setNote] = useState<string>("");
  const [confirmDelete, setConfirmDelete] = useState(false);

  const load = () =>
    api.activity(activityId).then((x) => {
      setD(x);
      setRpe(x.rpe === null ? "" : String(x.rpe));
      setNote(x.note ?? "");
    });

  useEffect(() => {
    load();
  }, [activityId]);

  if (!d) return <div className="p-4 text-sm text-muted">Loading…</div>;

  const series = downsample(d.series);
  const hasHr = d.has_hr;

  return (
    <div className="space-y-4 border-t border-line bg-surface/60 p-4">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Field label="Distance" value={km(d.distance_km)} />
        <Field label="Duration" value={hms(d.timer_time_s)} hint={durationLabel[d.duration_source]} />
        <Field label="Elapsed time" value={hms(d.elapsed_time_s)} />
        <Field label="Average pace" value={pace(d.distance_km, d.timer_time_s)} />
      </div>

      {hasHr ? (
        <div className="grid gap-4 lg:grid-cols-2">
          <Chart title="Heart rate (bpm)">
            <LineChart data={series}>
              <CartesianGrid strokeOpacity={0.15} />
              <XAxis dataKey="t" tickFormatter={(v) => hms(v)} fontSize={11} />
              <YAxis domain={["dataMin - 5", "dataMax + 5"]} fontSize={11} width={34} />
              <Tooltip labelFormatter={(v) => hms(Number(v))} />
              <Line type="monotone" dataKey="hr" dot={false} stroke="var(--color-bad)" strokeWidth={1.5} connectNulls={false} />
            </LineChart>
          </Chart>
          <Chart title="Pace (min/km)">
            <LineChart data={series}>
              <CartesianGrid strokeOpacity={0.15} />
              <XAxis dataKey="t" tickFormatter={(v) => hms(v)} fontSize={11} />
              <YAxis reversed domain={["dataMin - 0.3", "dataMax + 0.3"]} fontSize={11} width={34} />
              <Tooltip labelFormatter={(v) => hms(Number(v))} />
              <Line type="monotone" dataKey="pace_min_km" dot={false} stroke="var(--color-brand)" strokeWidth={1.5} connectNulls={false} />
            </LineChart>
          </Chart>
        </div>
      ) : (
        <p className="rounded-lg bg-warn/10 px-3 py-2 text-sm">
          This activity has no heart-rate data. The effort is unknown, not low.
        </p>
      )}

      <section>
        <h4 className="mb-1 text-sm font-semibold">Time in zone</h4>
        {d.zones ? (
          <div className="space-y-1">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted">
                <tr>
                  <th className="py-1">Zone</th>
                  <th>Minutes</th>
                  <th>% of measured time</th>
                </tr>
              </thead>
              <tbody>
                {d.zones.buckets.map((b) => (
                  <tr key={b.name} className="border-t border-line">
                    <td className="py-1">{b.name}</td>
                    <td className="tabular-nums">{b.minutes}</td>
                    <td className="tabular-nums">{b.pct === null ? "—" : `${b.pct} %`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="text-xs text-muted">
              Time measured with heart rate: {hms(d.zones.measured_active_s)} · Without heart rate:{" "}
              {hms(d.zones.unknown_hr_s)} · Pauses: {hms(d.zones.paused_s)} · Recording gaps:{" "}
              {hms(d.zones.gap_s)}. Calculated on {formatDateTime(d.zones.computed_at)} with the
              zones: {d.zones.zones_used.map((z) => `${z.name} ${z.min_hr}–${z.max_hr}`).join(", ")}.
            </p>
          </div>
        ) : (
          <p className="text-sm text-muted">
            Set your real zones in Settings and recalculate to see this breakdown.
          </p>
        )}
      </section>

      {d.laps.length > 0 && (
        <section>
          <h4 className="mb-1 text-sm font-semibold">Laps</h4>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted">
                <tr>
                  <th className="py-1">#</th>
                  <th>Distance</th>
                  <th>Time</th>
                  <th>Avg HR</th>
                  <th>Max HR</th>
                </tr>
              </thead>
              <tbody>
                {d.laps.map((l, i) => (
                  <tr key={i} className="border-t border-line">
                    <td className="py-1">{String(l.idx)}</td>
                    <td className="tabular-nums">
                      {l.distance_m ? `${(Number(l.distance_m) / 1000).toFixed(2)} km` : "—"}
                    </td>
                    <td className="tabular-nums">{hms(Number(l.timer_time_s))}</td>
                    <td className="tabular-nums">{l.avg_hr ?? "—"}</td>
                    <td className="tabular-nums">{l.max_hr ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="grid gap-2 sm:grid-cols-[120px_1fr_auto] sm:items-end">
        <label className="text-sm">
          Effort 0–10
          <input
            value={rpe}
            onChange={(e) => setRpe(e.target.value)}
            inputMode="numeric"
            placeholder="optional"
            className="mt-1 w-full rounded-lg border border-line px-2 py-1.5"
          />
        </label>
        <label className="text-sm">
          Note
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="How it felt, terrain, niggles…"
            className="mt-1 w-full rounded-lg border border-line px-2 py-1.5"
          />
        </label>
        <Button
          onClick={async () => {
            await api.annotate(d.id, { rpe: rpe === "" ? null : Number(rpe), note: note || null });
            await load();
            onChanged();
          }}
        >
          Save
        </Button>
      </section>

      <div className="flex flex-wrap items-center gap-2">
        {!confirmDelete ? (
          <Button variant="danger" onClick={() => setConfirmDelete(true)}>
            Delete this run
          </Button>
        ) : (
          <>
            <span className="text-sm">Delete the imported run? The planned session stays.</span>
            <Button
              variant="danger"
              onClick={async () => {
                await api.remove(d.id);
                onChanged();
              }}
            >
              Yes, delete
            </Button>
            <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
              Cancel
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

function Field({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-muted">{label}</div>
      <div className="font-semibold tabular-nums">{value}</div>
      {hint && <div className="text-[11px] text-muted">{hint}</div>}
    </div>
  );
}

function Chart({ title, children }: { title: string; children: React.ReactElement }) {
  return (
    <div className="rounded-lg border border-line bg-panel p-2">
      <div className="mb-1 text-xs font-medium text-muted">{title}</div>
      <div className="h-48">
        <ResponsiveContainer width="100%" height="100%">
          {children}
        </ResponsiveContainer>
      </div>
    </div>
  );
}
