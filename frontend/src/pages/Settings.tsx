import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Button, Card } from "../components/ui";

type Zone = { name: string; min_hr: number | string; max_hr: number | string };

export function Settings() {
  const [zones, setZones] = useState<Zone[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .settings()
      .then((s) => setZones(s.zones.length ? s.zones : [{ name: "Z1", min_hr: "", max_hr: "" }]))
      .catch((e: Error) => setError(e.message));
  }, []);

  const update = (i: number, patch: Partial<Zone>) =>
    setZones(zones.map((z, idx) => (idx === i ? { ...z, ...patch } : z)));

  return (
    <div className="space-y-4">
      <Card className="p-4">
        <h2 className="text-lg font-semibold">Units</h2>
        <p className="text-sm text-muted">Kilometres and min/km. Fixed in this version.</p>
      </Card>

      <Card className="p-4">
        <h2 className="text-lg font-semibold">Heart-rate zones</h2>
        <p className="text-sm text-muted">
          Enter your real limits, the ones you use on your watch. The app does not derive them from
          your age, a maximum heart rate or standard percentages.
        </p>

        <div className="mt-3 space-y-2">
          {zones.map((z, i) => (
            <div key={i} className="flex flex-wrap items-end gap-2">
              <label className="text-sm">
                Name
                <input
                  value={z.name}
                  onChange={(e) => update(i, { name: e.target.value })}
                  className="mt-1 block w-28 rounded-lg border border-line px-2 py-1.5"
                />
              </label>
              <label className="text-sm">
                Minimum (bpm)
                <input
                  value={z.min_hr}
                  inputMode="numeric"
                  onChange={(e) => update(i, { min_hr: e.target.value })}
                  className="mt-1 block w-28 rounded-lg border border-line px-2 py-1.5"
                />
              </label>
              <label className="text-sm">
                Maximum (bpm)
                <input
                  value={z.max_hr}
                  inputMode="numeric"
                  onChange={(e) => update(i, { max_hr: e.target.value })}
                  className="mt-1 block w-28 rounded-lg border border-line px-2 py-1.5"
                />
              </label>
              <Button variant="ghost" onClick={() => setZones(zones.filter((_, idx) => idx !== i))}>
                Remove
              </Button>
            </div>
          ))}
        </div>

        <div className="mt-3 flex flex-wrap gap-2">
          <Button
            variant="ghost"
            onClick={() => setZones([...zones, { name: `Z${zones.length + 1}`, min_hr: "", max_hr: "" }])}
          >
            Add zone
          </Button>
          <Button
            onClick={async () => {
              setError(null);
              setMsg(null);
              try {
                await api.saveZones(
                  zones
                    .filter((z) => z.name && z.min_hr !== "" && z.max_hr !== "")
                    .map((z) => ({ name: z.name, min_hr: Number(z.min_hr), max_hr: Number(z.max_hr) })),
                );
                const r = await api.recalculate();
                setMsg(`Zones saved. Recalculated ${r.recalculated} runs.`);
              } catch (e) {
                setError((e as Error).message);
              }
            }}
          >
            Save and recalculate
          </Button>
        </div>
        {msg && <p className="mt-2 text-sm text-good">{msg}</p>}
        {error && <p className="mt-2 whitespace-pre-wrap text-sm text-bad">{error}</p>}
        <p className="mt-3 text-xs text-muted">
          The zone breakdown shows minutes and percentage of the time with measured heart rate.
          There is no target number of minutes in Z2 and no compliance percentage: the plan only
          calls for easy effort (2–3/10) with Z2 as a guide.
        </p>
      </Card>

      <Card className="p-4 text-sm">
        <h2 className="text-lg font-semibold">Perceived effort</h2>
        <p className="text-muted">
          You can record an effort from 0 to 10 and a note on each run, either from the preview when
          importing it or in the session details.
        </p>
      </Card>
    </div>
  );
}
