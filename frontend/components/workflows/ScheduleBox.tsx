"use client";
// The part of a Schedule box that lives outside the workflow: the on/off switch, when it runs next and how the last scheduled run went.
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { ago } from "@/lib/workflows";

type Status = {
  has_schedule_box: boolean; enabled: boolean; next_run: number | null; upcoming: number[]; last_run: number | null; last_run_id: string | null; last_status: string | null;
  runs: number; fails: number; note: string; describe: string; schedules_enabled: boolean;
};

const when = (t: number) => new Date(t * 1000).toLocaleString();

export default function ScheduleBox({ workflowId, saved }: { workflowId: string; saved: boolean }) {
  const [st, setSt] = useState<Status | null>(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => api<Status>(`/api/workflows/${workflowId}/schedule`).then(setSt).catch((e) => setErr((e as Error).message)), [workflowId]);
  useEffect(() => { load(); }, [load]);
  const set = async (enabled: boolean) => {
    setErr("");
    try { setSt(await api<Status>(`/api/workflows/${workflowId}/schedule`, { method: "POST", body: JSON.stringify({ enabled }) })); }
    catch (e) { setErr((e as Error).message); }
  };
  return (
    <section className="card" data-testid="schedule-box" style={{ marginTop: 12 }}>
      <b className="small">Schedule</b>
      {st && !st.schedules_enabled && <div className="small" role="status" style={{ color: "var(--warn-text)" }}>Every schedule is paused in the workflow settings (the Settings button on the Flows tab).</div>}
      {!saved && <div className="small mut">Save a version first: the schedule uses the saved settings of this box.</div>}
      {st?.enabled
        ? <div className="small">On · {st.describe}. Next: <b>{st.next_run ? when(st.next_run) : "—"}</b>{st.upcoming.length > 1 && <span className="mut"> (then {st.upcoming.slice(1).map(when).join(", ")})</span>}</div>
        : <div className="small mut">Off. {st?.describe ? `When on, it runs ${st.describe}.` : ""}</div>}
      {st && st.runs > 0 && <div className="small mut">Ran {st.runs} time(s) · last {ago(st.last_run)}: {st.last_status ?? "—"}{st.fails ? ` · ${st.fails} failure(s) in a row` : ""}</div>}
      {st?.note && <div className="small" role="status" style={{ color: "var(--warn-text)" }}>{st.note}</div>}
      <div className="small mut" style={{ marginTop: 4 }}>Runs use your spend caps like any run. A schedule never starts a run while its last one is still working, and switches itself off after 3 failed runs in a row.</div>
      <div className="row" style={{ marginTop: 6 }}>
        {st?.enabled ? <button onClick={() => set(false)}>Switch off</button> : <button className="pri" onClick={() => set(true)} disabled={!saved}>Switch on</button>}
      </div>
      {err && <div className="small" role="alert" style={{ color: "var(--bad-text)", marginTop: 6 }}>{err}</div>}
    </section>
  );
}
