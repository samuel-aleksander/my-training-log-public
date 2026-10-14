export const km = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined ? "—" : `${v.toFixed(digits)} km`;

export const signedKm = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)} km`;

export function hms(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h > 0 ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}:${String(sec).padStart(2, "0")}`;
}

export function pace(distanceKm: number | null, seconds: number | null): string {
  if (!distanceKm || !seconds) return "—";
  const secPerKm = seconds / distanceKm;
  const m = Math.floor(secPerKm / 60);
  const s = Math.round(secPerKm % 60);
  return `${m}:${String(s).padStart(2, "0")} min/km`;
}

/** Decimal minutes per km (6.97) as "6:58". */
export function paceMin(minPerKm: number | null | undefined): string {
  if (minPerKm === null || minPerKm === undefined) return "—";
  let m = Math.floor(minPerKm);
  let s = Math.round((minPerKm - m) * 60);
  if (s === 60) {
    m += 1;
    s = 0;
  }
  return `${m}:${String(s).padStart(2, "0")}`;
}

export const metres = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : `${Math.round(v)} m`;

export const signedPct = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(1)} %`;

/** Plan dates ("2026-09-21") are calendar days, not instants: read them as local
 * dates, or `new Date` takes them as UTC midnight and west of UTC shows the day before. */
export function parseDate(iso: string): Date {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date(iso);
}

export const formatDate = (iso: string) =>
  parseDate(iso).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });

export const formatDateTime = (iso: string) => new Date(iso).toLocaleString("en-GB");

export const durationLabel: Record<string, string> = {
  timer: "Activity time (timer)",
  fallback_samples: "Estimated from the samples (no timer in the file)",
  fallback_elapsed: "Elapsed time (no timer in the file)",
};

export const SESSION_NAMES: Record<string, string> = {
  A: "A · Easy run (Tuesday)",
  B: "B · Medium run (Thursday)",
  C: "C · Short run (Saturday)",
  L: "L · Long run (Sunday)",
};

export const elevationLabel: Record<string, string> = {
  device: "Totals recorded by the watch",
  samples: "Computed from the altitude samples",
};
