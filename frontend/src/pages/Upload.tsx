import { useEffect, useRef, useState } from "react";
import { api, type GarminSync, type Inbox, type ListedActivity, type Week } from "../lib/api";
import { SESSION_NAMES, durationLabel, formatDate, formatDateTime, hms, km } from "../lib/format";
import { AutoTag, Button, Card } from "../components/ui";

const EXTRA_TYPES = ["Easy run", "Run with strides", "Long run", "Road race", "Other"];

export function Upload({ onImported }: { onImported: (weekNumber: number) => void }) {
  const [weeks, setWeeks] = useState<Week[]>([]);
  const [preview, setPreview] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [weekNumber, setWeekNumber] = useState<number | null>(null);
  const [choice, setChoice] = useState<string>("");
  const [extraType, setExtraType] = useState(EXTRA_TYPES[0]);
  const [rpe, setRpe] = useState("");
  const [note, setNote] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [version, setVersion] = useState(0); // bumped when the folder import adds runs

  useEffect(() => {
    api.weeks().then(setWeeks).catch((e: Error) => setError(e.message));
  }, []);

  const week = weeks.find((w) => w.number === weekNumber);

  async function handleFile(file: File) {
    setError(null);
    setDone(null);
    setPreview(null);
    setBusy(true);
    try {
      const p = await api.preview(file);
      setPreview(p);
      if (!p.duplicate) {
        setWeekNumber(p.suggested_week ?? null);
        setChoice(p.suggested_session_id ? String(p.suggested_session_id) : "");
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!weekNumber) return;
    setError(null);
    try {
      await api.confirm({
        token: preview.token,
        filename: preview.filename,
        week_number: weekNumber,
        session_id: choice === "extra" ? null : Number(choice),
        is_extra: choice === "extra",
        extra_type: choice === "extra" ? extraType : null,
        rpe: rpe === "" ? null : Number(rpe),
        note: note || null,
      });
      setDone(`Activity assigned to week ${weekNumber}.`);
      setPreview(null);
      setConfirming(false);
      onImported(weekNumber);
    } catch (e) {
      setError((e as Error).message);
      setConfirming(false);
    }
  }

  return (
    <div className="space-y-4">
      <ImportFolder
        onImported={() => {
          api.weeks().then(setWeeks);
          setVersion((v) => v + 1);
        }}
      />

      <Card className="p-4">
        <h2 className="text-lg font-semibold">Upload activity</h2>
        <p className="mt-1 text-sm text-muted">
          An original Garmin .FIT file, or a ZIP containing a single activity.
        </p>
        <input
          type="file"
          accept=".fit,.zip,application/zip,application/octet-stream"
          onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])}
          className="mt-3 block w-full text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-brand file:px-3 file:py-2 file:text-white"
        />
        {busy && <p className="mt-2 text-sm text-muted">Decoding…</p>}
        {error && (
          <p className="mt-2 whitespace-pre-wrap rounded-lg bg-bad/10 px-3 py-2 text-sm text-bad">{error}</p>
        )}
        {done && <p className="mt-2 rounded-lg bg-good/10 px-3 py-2 text-sm text-good">{done}</p>}
      </Card>

      {preview?.duplicate && (
        <Card className="p-4 text-sm">
          <p className="font-medium">This activity has already been imported.</p>
          <p className="text-muted">
            {preview.existing_activity.original_filename} ·{" "}
            {formatDateTime(preview.existing_activity.start_time)}. You can reassign it from the
            details of the relevant week.
          </p>
        </Card>
      )}

      {preview && !preview.duplicate && (
        <Card className="space-y-4 p-4">
          <div>
            <h3 className="font-semibold">Preview</h3>
            <div className="mt-2 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
              <Pair label="Recorded date" value={formatDateTime(preview.start_time)} />
              <Pair label="Distance" value={km(preview.distance_km)} />
              <Pair
                label="Duration"
                value={hms(preview.timer_time_s)}
                hint={durationLabel[preview.duration_source]}
              />
              <Pair
                label="Heart rate"
                value={
                  preview.has_hr
                    ? `${preview.avg_hr ?? "—"} bpm average · max ${preview.max_hr ?? "—"}`
                    : "No heart-rate data"
                }
              />
            </div>
            <p className="mt-1 text-xs text-muted">
              {preview.sample_count} samples · {preview.lap_count} laps · elapsed time{" "}
              {hms(preview.elapsed_time_s)}
            </p>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-sm">
              Training week
              <select
                value={weekNumber ?? ""}
                onChange={(e) => {
                  setWeekNumber(Number(e.target.value));
                  setChoice("");
                }}
                className="mt-1 block w-full rounded-lg border border-line px-2 py-2"
              >
                <option value="">Select…</option>
                {weeks.map((w) => (
                  <option key={w.number} value={w.number}>
                    Week {w.number} ({w.start_date} – {w.end_date})
                    {w.number === preview.suggested_week ? " · suggested by date" : ""}
                  </option>
                ))}
              </select>
              <span className="text-xs text-muted">
                The recorded date does not change even if you assign the run to another week.
              </span>
            </label>

            <label className="text-sm">
              Session
              <select
                value={choice}
                onChange={(e) => setChoice(e.target.value)}
                disabled={!week}
                className="mt-1 block w-full rounded-lg border border-line px-2 py-2"
              >
                <option value="">Select…</option>
                {week?.sessions.map((s) => (
                  <option key={s.id} value={s.id} disabled={!!s.activity}>
                    {SESSION_NAMES[s.label]} — {s.target_km} km
                    {s.activity ? " (already assigned)" : ""}
                    {s.id === preview.suggested_session_id && weekNumber === preview.suggested_week
                      ? " · suggested by distance and day"
                      : ""}
                  </option>
                ))}
                <option value="extra">Extra / unplanned run</option>
              </select>
            </label>

            {choice === "extra" && (
              <label className="text-sm">
                Training type
                <select
                  value={extraType}
                  onChange={(e) => setExtraType(e.target.value)}
                  className="mt-1 block w-full rounded-lg border border-line px-2 py-2"
                >
                  {EXTRA_TYPES.map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
              </label>
            )}

            <label className="text-sm">
              Effort 0–10 (optional)
              <input
                value={rpe}
                onChange={(e) => setRpe(e.target.value)}
                inputMode="numeric"
                className="mt-1 block w-full rounded-lg border border-line px-2 py-2"
              />
            </label>
            <label className="text-sm sm:col-span-2">
              Note (optional)
              <input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                className="mt-1 block w-full rounded-lg border border-line px-2 py-2"
              />
            </label>
          </div>

          {!confirming ? (
            <Button disabled={!weekNumber || !choice} onClick={() => setConfirming(true)}>
              Continue
            </Button>
          ) : (
            <div className="rounded-lg border border-line bg-surface p-3 text-sm">
              <p>
                Confirm: assign this run to <b>week {weekNumber}</b> as{" "}
                <b>
                  {choice === "extra"
                    ? `extra (${extraType})`
                    : SESSION_NAMES[week!.sessions.find((s) => s.id === Number(choice))!.label]}
                </b>
                .
              </p>
              <div className="mt-2 flex gap-2">
                <Button onClick={confirm}>Confirm assignment</Button>
                <Button variant="ghost" onClick={() => setConfirming(false)}>
                  Back
                </Button>
              </div>
            </div>
          )}
        </Card>
      )}

      <Reassign key={version} weeks={weeks} onChanged={() => api.weeks().then(setWeeks)} />
    </div>
  );
}

function Pair({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-muted">{label}</div>
      <div className="font-semibold tabular-nums">{value}</div>
      {hint && <div className="text-[11px] text-muted">{hint}</div>}
    </div>
  );
}

function Reassign({ weeks, onChanged }: { weeks: Week[]; onChanged: () => void }) {
  const [list, setList] = useState<ListedActivity[]>([]);
  const [target, setTarget] = useState<Record<number, { week: string; session: string }>>({});
  const [msg, setMsg] = useState<string | null>(null);

  const load = () => api.activities().then(setList);
  useEffect(() => {
    load();
  }, []);

  return (
    <Card className="p-4">
      <h3 className="font-semibold">Imported runs</h3>
      <p className="text-sm text-muted">Reassign a run to another week or session whenever you like.</p>
      {msg && <p className="mt-2 text-sm text-good">{msg}</p>}
      <div className="mt-3 space-y-2">
        {list.length === 0 && <p className="text-sm text-muted">No runs imported yet.</p>}
        {list.map((a) => {
          const t = target[a.id] ?? { week: "", session: "" };
          const w = weeks.find((x) => x.number === Number(t.week));
          return (
            <div key={a.id} className="flex flex-wrap items-end gap-2 border-t border-line pt-2 text-sm">
              <div className="min-w-[200px] flex-1">
                <div className="flex items-center gap-2 font-medium">
                  {a.original_filename}
                  {a.auto_assigned && <AutoTag />}
                </div>
                <div className="text-xs text-muted">
                  {formatDateTime(a.start_time)} · {km(a.distance_km)} · week {a.week_number ?? "—"},{" "}
                  {a.is_extra ? `extra (${a.extra_type ?? "—"})` : a.session_label ? SESSION_NAMES[a.session_label] : "—"}
                </div>
              </div>
              <select
                value={t.week}
                onChange={(e) => setTarget({ ...target, [a.id]: { week: e.target.value, session: "" } })}
                className="rounded-lg border border-line px-2 py-1.5"
              >
                <option value="">Week…</option>
                {weeks.map((x) => (
                  <option key={x.number} value={x.number}>
                    Week {x.number}
                  </option>
                ))}
              </select>
              <select
                value={t.session}
                disabled={!w}
                onChange={(e) => setTarget({ ...target, [a.id]: { ...t, session: e.target.value } })}
                className="rounded-lg border border-line px-2 py-1.5"
              >
                <option value="">Session…</option>
                {w?.sessions.map((s) => (
                  <option key={s.id} value={s.id} disabled={!!s.activity && s.activity.id !== a.id}>
                    {s.label} — {s.target_km} km{s.activity && s.activity.id !== a.id ? " (taken)" : ""}
                  </option>
                ))}
                <option value="extra">Extra</option>
              </select>
              <Button
                variant="ghost"
                disabled={!t.week || !t.session}
                onClick={async () => {
                  try {
                    await api.assign(a.id, {
                      week_number: Number(t.week),
                      session_id: t.session === "extra" ? null : Number(t.session),
                      is_extra: t.session === "extra",
                      extra_type: t.session === "extra" ? "Extra run" : null,
                    });
                    setMsg("Assignment updated.");
                    load();
                    onChanged();
                  } catch (e) {
                    setMsg((e as Error).message);
                  }
                }}
              >
                Reassign
              </Button>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

const STATUS_STYLE: Record<string, string> = {
  imported: "text-good",
  duplicate: "text-muted",
  skipped: "text-warn",
  failed: "text-bad",
};

function ImportFolder({ onImported }: { onImported: () => void }) {
  const [inbox, setInbox] = useState<Inbox | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [garmin, setGarmin] = useState<GarminSync | null>(null);
  const [syncing, setSyncing] = useState(false);

  const newest = useRef<string | null | undefined>(undefined);
  const load = () =>
    api
      .inbox()
      .then((x) => {
        const top = x.recent[0]?.seen_at ?? null;
        // A file handled by the background watcher since the last look: refresh the runs below.
        if (newest.current !== undefined && top !== newest.current) onImported();
        newest.current = top;
        setInbox(x);
      })
      .catch((e: Error) => setError(e.message));
  useEffect(() => {
    load();
    api.garminSync().then(setGarmin).catch(() => setGarmin(null));
    // The server checks the folder on its own; refresh the list while this screen is open.
    const t = setInterval(load, 10_000);
    return () => clearInterval(t);
  }, []);

  const syncGarmin = async () => {
    setSyncing(true);
    setError(null);
    try {
      let s = await api.startGarminSync();
      setGarmin(s);
      while (s.installed && s.running) {
        await sleep(2000);
        s = await api.garminSync();
        setGarmin(s);
      }
      // The folder leaves files alone for 3 s after they are written; then import them
      // straight away instead of waiting for the next background look.
      await sleep(3500);
      await api.scanInbox();
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSyncing(false);
    }
  };

  if (!inbox) return error ? <Card className="p-4 text-sm text-bad">{error}</Card> : null;

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold">Import folder</h2>
          <p className="mt-1 text-sm text-muted">
            Save Garmin exports (Export original → a .zip, or a .fit) into this folder and they are imported
            {inbox.poll_seconds > 0 ? ` within ${inbox.poll_seconds} s` : " when you check"}. Each run goes to the
            free session of its week that best fits its distance and day, so a run done a day early or late still
            lands on its session. Files are only read, never moved or deleted.
            {garmin?.installed && " Sync from Garmin fetches new runs now instead of waiting for the next scheduled sync."}
          </p>
        </div>
        <div className="flex gap-2">
          {garmin?.installed && (
            <Button disabled={syncing || busy} onClick={syncGarmin}>
              {syncing ? "Syncing…" : "Sync from Garmin"}
            </Button>
          )}
          <Button
            variant="ghost"
            disabled={busy || syncing}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                await api.scanInbox();
                await load();
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "Checking…" : "Check now"}
          </Button>
        </div>
      </div>

      <code className="mt-2 block break-all rounded-lg bg-surface px-2 py-1.5 font-mono text-xs">{inbox.path}</code>
      {!inbox.exists && (
        <p className="mt-2 rounded-lg bg-warn/10 px-3 py-2 text-sm text-warn">
          This folder does not exist. Create it, or start the app with MARATHON_INBOX_DIR pointing at another one.
        </p>
      )}
      {inbox.is_default && (
        <p className="mt-1 text-xs text-muted">
          To watch your Downloads folder instead, start the app with{" "}
          <code className="font-mono">MARATHON_INBOX_DIR=~/Downloads ./dev.sh</code>.
        </p>
      )}
      {error && <p className="mt-2 rounded-lg bg-bad/10 px-3 py-2 text-sm text-bad">{error}</p>}
      {garmin?.installed && garmin.started_at && (garmin.lines.length > 0 || garmin.problem) && (
        <div className="mt-2 rounded-lg bg-surface px-3 py-2 text-xs">
          {garmin.lines.map((l, i) => (
            <p key={i}>{l}</p>
          ))}
          {garmin.problem && <p className="mt-1 font-medium text-warn">{garmin.problem}</p>}
        </div>
      )}

      <div className="mt-3">
        <h3 className="text-sm font-semibold">Latest files</h3>
        {inbox.recent.length === 0 ? (
          <p className="text-sm text-muted">Nothing imported from the folder yet.</p>
        ) : (
          <ul className="mt-1 divide-y divide-line text-sm">
            {inbox.recent.map((r, i) => (
              <li key={i} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 py-1.5">
                <span className={`w-20 shrink-0 text-xs font-medium capitalize ${STATUS_STYLE[r.status]}`}>
                  {r.status}
                </span>
                <span className="min-w-0 flex-1 break-all">{r.filename}</span>
                <span className="text-xs text-muted">
                  {r.start_time && r.status === "imported"
                    ? `${formatDate(r.start_time)} · ${km(r.distance_km)} → week ${r.week_number}, ${
                        r.session_label === "extra" ? "extra" : `session ${r.session_label}`
                      }${r.auto_assigned === false ? " (moved by you)" : ""}`
                    : r.message}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
