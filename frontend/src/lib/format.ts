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

export const formatDate = (iso: string) =>
  new Date(iso).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });

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
