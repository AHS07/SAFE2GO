/** Manual incident report (prd.md F2.8). */
import React, { useState } from "react";
import { useMachine } from "@/features/machine/MachineContext";
import { errorMessage } from "@/shared/components/ErrorNotice";
import Button from "@/shared/ui/Button";
import Notice from "@/shared/ui/Notice";
import Panel from "@/shared/ui/Panel";

const MIN_LENGTH = 3;
const MAX_LENGTH = 500;

export default function ReportIncidentForm(): React.ReactElement {
  const { reportIncident } = useMachine();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setResult(null);
    try {
      await reportIncident(text.trim());
      setText("");
      setResult({ ok: true, message: "Report recorded." });
    } catch (err) {
      setResult({ ok: false, message: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="Report an incident" icon="edit_note">
      <form className="space-y-3" onSubmit={submit}>
        <label className="block space-y-1.5">
          <span className="font-display text-xs font-bold uppercase tracking-wider text-slate-400">
            What happened
          </span>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            maxLength={MAX_LENGTH}
            rows={3}
            placeholder="For example: near miss with a haul truck at the loading point."
            className="w-full rounded border border-line bg-well p-3 text-sm text-slate-100 outline-none focus:border-brand focus:ring-1 focus:ring-brand"
          />
        </label>
        {result && (
          <Notice tone={result.ok ? "ok" : "critical"} role={result.ok ? "status" : "alert"}>
            <p>{result.message}</p>
          </Notice>
        )}
        <Button type="submit" variant="primary" disabled={busy || text.trim().length < MIN_LENGTH}>
          {busy ? "Sending" : "Submit report"}
        </Button>
      </form>
    </Panel>
  );
}
