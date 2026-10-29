export type SessionItem = {
  id: number;
  label: "A" | "B" | "C" | "L";
  planned_date: string;
  seeded_km: number;
  target_km: number;
  target_duration_min: number | null;
  target_duration_display: string;
  notes: string | null;
  status: "completed" | "planned" | "skipped";
  actual_km: number | null;
  diff_km: number | null;
  diff_duration_min: number | null;
  activity: Activity | null;
};

export type Activity = {
  id: number;
  original_filename: string;
  start_time: string;
  distance_km: number | null;
  timer_time_s: number | null;
  elapsed_time_s: number | null;
  duration_source: string;
  avg_hr: number | null;
  max_hr: number | null;
  has_hr: boolean;
  ascent_m: number | null;
  descent_m: number | null;
  elevation_source: "device" | "samples" | null;
  is_extra: boolean;
  extra_type: string | null;
  rpe: number | null;
  note: string | null;
  is_demo: boolean;
  auto_assigned: boolean;
  week_id: number | null;
  session_id: number | null;
};

export type Week = {
  id: number;
  number: number;
  start_date: string;
  end_date: string;
  notes: string | null;
  effort_context: string;
  planned_km: number;
  seeded_planned_km: number;
  actual_km: number;
  diff_km: number;
  completion_pct: number | null;
  sessions_completed: number;
  sessions_planned: number;
  actual_duration_s: number;
  sessions: SessionItem[];
  extras: Activity[];
  journal: WeekJournal;
};

/** Your own note on a week; `notes` above is the plan's text. */
export type WeekJournal = {
  note: string | null;
  reasons: string[];
  updated_at: string | null;
};

export type ListedActivity = Activity & {
  week_number: number | null;
  session_label: "A" | "B" | "C" | "L" | null;
};

export type InboxEntry = {
  filename: string;
  status: "imported" | "duplicate" | "skipped" | "failed";
  message: string | null;
  seen_at: string;
  activity_id: number | null;
  start_time: string | null;
  distance_km: number | null;
  week_number: number | null;
  session_label: "A" | "B" | "C" | "L" | "extra" | null;
  auto_assigned: boolean | null;
};

export type Inbox = {
  path: string;
  exists: boolean;
  is_default: boolean;
  poll_seconds: number;
  recent: InboxEntry[];
};

/** The garmin_sync launchd job: the app can start it and read its log, nothing more. */
export type GarminSync =
  | { installed: false }
  | {
      installed: true;
      running: boolean;
      started_at: string | null;
      lines: string[];
      exit_code: number | null;
      problem: string | null;
    };

export type DriftHalf ={ seconds: number; avg_hr: number; pace_min_km: number };

export type HrDrift = {
  drift_pct: number | null;
  reason?: string;
  warmup_excluded_s: number;
  analysed_s: number;
  first?: DriftHalf;
  second?: DriftHalf;
};

export type BlockWeek = {
  number: number;
  start_date: string;
  end_date: string;
  notes: string | null;
  journal: WeekJournal;
  started: boolean;
  current: boolean;
  planned_km: number;
  actual_km: number;
  actual_duration_s: number;
  sessions_completed: number;
  sessions_planned: number;
  ascent_m: number | null;
  long_run: {
    planned_km: number | null;
    actual_km: number | null;
    time_s: number | null;
    ascent_m: number | null;
  };
  zone_seconds: Record<string, number>;
  unknown_hr_s: number;
};

export type BlockRun = {
  id: number;
  start_time: string;
  week_number: number | null;
  label: "A" | "B" | "C" | "L" | "extra" | null;
  distance_km: number | null;
  timer_time_s: number | null;
  avg_hr: number | null;
  pace_min_km: number | null;
  ascent_m: number | null;
  hr_drift_pct: number | null;
};

export type Block = {
  weeks: BlockWeek[];
  zone_names: string[];
  runs: BlockRun[];
  totals: {
    planned_to_date_km: number;
    actual_km: number;
    planned_block_km: number;
    runs: number;
    time_s: number;
    ascent_m: number;
  };
};

export type ZoneSummary = {
  computed_at: string;
  zones_used: { name: string; min_hr: number; max_hr: number }[];
  measured_active_s: number;
  unknown_hr_s: number;
  paused_s: number;
  gap_s: number;
  buckets: { name: string; seconds: number; minutes: number; pct: number | null }[];
};

/** A run as the Week screen shows it on its card. */
export type RunSummary = Activity & {
  pace_min_km: number | null;
  zones: ZoneSummary | null;
  hr_drift_pct: number | null;
};

/** GET /api/weeks/{n}: the week plus per-run and per-day detail. */
export type WeekDetail = Omit<Week, "sessions" | "extras"> & {
  sessions: (SessionItem & { activity: RunSummary | null })[];
  extras: RunSummary[];
  avg_hr: number | null;
  avg_pace_min_km: number | null;
  ascent_m: number | null;
  zones: {
    names: string[];
    seconds: Record<string, number>;
    measured_s: number;
    unknown_hr_s: number;
  } | null;
  days: {
    date: string;
    planned_km: number | null;
    planned_labels: SessionItem["label"][];
    ran_km: number | null;
    runs: number;
  }[];
};

export type ActivityDetail = Activity & {
  series: {
    t: number;
    hr: number | null;
    pace_min_km: number | null;
    km: number | null;
    alt: number | null;
  }[];
  laps: Record<string, number | string | null>[];
  zones: ZoneSummary | null;
  hr_drift: HrDrift | null;
};

export const START_COMMAND = "./dev.sh";

export const BACKEND_HINT =
  "Can't reach the local server (http://127.0.0.1:8000). Start it in another terminal:\n" +
  START_COMMAND;

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch (e) {
    console.error("[api] network failure", url, e);
    throw new Error(BACKEND_HINT);
  }
  const text = await res.text();
  let body: any = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (!res.ok) {
    console.error("[api] error", url, res.status, text);
    // The dev proxy answers 5xx without a JSON body when it cannot reach the
    // backend; a real backend error always carries a `detail`.
    if (!body && res.status >= 500) throw new Error(BACKEND_HINT);
    throw new Error(body?.detail ?? `Error ${res.status} at ${url}`);
  }
  return body as T;
}

const json = (body: unknown, method = "POST"): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  weeks: () => req<Week[]>("/api/weeks"),
  block: () => req<Block>("/api/block"),
  week: (n: number) => req<WeekDetail>(`/api/weeks/${n}`),
  saveJournal: (n: number, body: { note: string | null; reasons: string[] }) =>
    req<WeekJournal>(`/api/weeks/${n}/journal`, json(body, "PUT")),
  activity: (id: number) => req<ActivityDetail>(`/api/activities/${id}`),
  activities: () => req<ListedActivity[]>("/api/activities"),
  inbox: () => req<Inbox>("/api/inbox"),
  scanInbox: () => req<{ handled: unknown[] }>("/api/inbox/scan", { method: "POST" }),
  garminSync: () => req<GarminSync>("/api/garmin/sync"),
  startGarminSync: () => req<GarminSync>("/api/garmin/sync", json({})),
  updateSession: (id: number, body: { override_km?: number | null; override_duration_min?: number | null }) =>
    req(`/api/sessions/${id}`, json(body, "PATCH")),
  preview: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return req<any>("/api/uploads/preview", { method: "POST", body: fd });
  },
  confirm: (body: unknown) => req<{ id: number }>("/api/activities/confirm", json(body)),
  assign: (id: number, body: unknown) => req<Activity>(`/api/activities/${id}/assign`, json(body)),
  annotate: (id: number, body: { rpe?: number | null; note?: string | null }) =>
    req<Activity>(`/api/activities/${id}`, json(body, "PATCH")),
  remove: (id: number) => req(`/api/activities/${id}?confirm=true`, { method: "DELETE" }),
  settings: () => req<{ units: { distance: string; pace: string }; zones: { name: string; min_hr: number; max_hr: number }[] }>("/api/settings"),
  saveZones: (zones: unknown[]) => req("/api/settings/zones", json({ zones }, "PUT")),
  recalculate: () => req<{ recalculated: number }>("/api/zones/recalculate", { method: "POST" }),
};
