import { useState } from "react";
import { api, type WeekJournal } from "../lib/api";
import { Button, Card } from "./ui";

/** Quick tags for why a week went differently. Anything else goes in the note. */
export const REASONS = [
  "Illness",
  "Niggle / pain",
  "Fatigue",
  "Poor sleep",
  "Work",
  "Travel",
  "Weather",
  "Life / family",
];

export function WeekNotes({
  weekNumber,
  journal,
  onSaved,
}: {
  weekNumber: number;
  journal: WeekJournal;
  onSaved: (j: WeekJournal) => void;
}) {
  const [note, setNote] = useState(journal.note ?? "");
  const [reasons, setReasons] = useState<string[]>(journal.reasons);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dirty =
    note.trim() !== (journal.note ?? "") || reasons.join("|") !== journal.reasons.join("|");
  // Tags saved earlier that are no longer in the list still show, so they can be removed.
  const options = [...REASONS, ...journal.reasons.filter((r) => !REASONS.includes(r))];

  const toggle = (r: string) =>
    setReasons((cur) => (cur.includes(r) ? cur.filter((x) => x !== r) : [...cur, r]));

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const saved = await api.saveJournal(weekNumber, { note: note.trim() || null, reasons });
      setNote(saved.note ?? "");
      setReasons(saved.reasons);
      onSaved(saved);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card className="space-y-2 p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold">My notes</h3>
        <span className="text-[11px] text-muted">
          {journal.updated_at
            ? `Saved ${new Date(journal.updated_at).toLocaleString()}`
            : "How did this week go? Anything that got in the way?"}
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Reasons">
        {options.map((r) => {
          const on = reasons.includes(r);
          return (
            <button
              key={r}
              type="button"
              aria-pressed={on}
              onClick={() => toggle(r)}
              className={`rounded-full border px-2.5 py-1 text-xs transition ${
                on
                  ? "border-warn/50 bg-warn/15 font-medium text-warn"
                  : "border-line text-muted hover:bg-brand-soft"
              }`}
            >
              {r}
            </button>
          );
        })}
      </div>
      <textarea
        value={note}
        onChange={(e) => setNote(e.target.value)}
        rows={3}
        maxLength={4000}
        placeholder="e.g. Cold from Wednesday, skipped B and kept the long run short."
        className="block w-full rounded-lg border border-line bg-panel px-2 py-1.5 text-sm"
      />
      {error && <p className="text-xs text-bad">{error}</p>}
      <div className="flex items-center gap-2">
        <Button onClick={save} disabled={!dirty || saving}>
          {saving ? "Saving…" : "Save notes"}
        </Button>
        {dirty && !saving && (
          <Button
            variant="ghost"
            onClick={() => {
              setNote(journal.note ?? "");
              setReasons(journal.reasons);
            }}
          >
            Discard changes
          </Button>
        )}
      </div>
    </Card>
  );
}
