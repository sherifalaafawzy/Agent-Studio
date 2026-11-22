"use client";
// Form fields drawn from a server-side description (a guard kind, an identity provider): text, secret, switch, choice, several-of, number, long text.
// Secrets are write-only: the box is empty and says when one is already stored.
import InfoTip from "./InfoTip";

export type KField = { name: string; label: string; type?: string; required?: boolean; default?: unknown; placeholder?: string; choices?: unknown[]; help?: string };

export default function KindFields({ fields, params, onParams, secrets, onSecrets, secretsSet }:
  { fields: KField[]; params: Record<string, any>; onParams: (p: Record<string, any>) => void; secrets: Record<string, string>; onSecrets: (s: Record<string, string>) => void; secretsSet: string[] }) {
  const val = (f: KField) => (f.name in params ? params[f.name] : f.default ?? "");
  const set = (name: string, v: unknown) => onParams({ ...params, [name]: v });
  return (
    <div className="grid">
      {fields.map((f) => {
        const label = `${f.label}${f.required ? "" : " (optional)"}`;
        if (f.type === "secret") {
          return (<label key={f.name}>{label}
            <input type="password" autoComplete="off" value={secrets[f.name] ?? ""} placeholder={secretsSet.includes(f.name) ? "(stored: leave empty to keep)" : "paste value"}
              onChange={(e) => onSecrets({ ...secrets, [f.name]: e.target.value })} />
            {f.help && <span className="small mut">{f.help}</span>}</label>);
        }
        if (f.type === "bool") {
          return (<label key={f.name} style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
            <input type="checkbox" checked={!!val(f)} onChange={(e) => set(f.name, e.target.checked)} />{f.label}
            {f.help && <InfoTip text={f.help} />}</label>);
        }
        if (f.type === "choice" && Array.isArray(f.default)) {
          const on = (val(f) as string[]) ?? [];
          return (<fieldset key={f.name} className="kf-set"><legend className="lbl">{f.label}</legend>
            {(f.choices ?? []).map((c) => (
              <label key={String(c)} style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                <input type="checkbox" checked={on.includes(String(c))} onChange={(e) => set(f.name, e.target.checked ? [...on, String(c)] : on.filter((x) => x !== String(c)))} />{String(c)}</label>))}
            {f.help && <span className="small mut">{f.help}</span>}</fieldset>);
        }
        if (f.type === "choice") {
          return (<label key={f.name}>{label}
            <select value={String(val(f))} onChange={(e) => set(f.name, typeof f.choices?.[0] === "number" ? Number(e.target.value) : e.target.value)}>
              {(f.choices ?? []).map((c) => <option key={String(c)} value={String(c)}>{String(c)}</option>)}</select>
            {f.help && <span className="small mut">{f.help}</span>}</label>);
        }
        if (f.type === "textarea") {
          return (<label key={f.name}>{label}
            <textarea rows={3} value={String(val(f))} placeholder={f.placeholder} onChange={(e) => set(f.name, e.target.value)} />
            {f.help && <span className="small mut">{f.help}</span>}</label>);
        }
        return (<label key={f.name}>{label}
          <input type={f.type === "number" ? "number" : "text"} value={String(val(f))} placeholder={f.placeholder}
            onChange={(e) => set(f.name, f.type === "number" ? (e.target.value === "" ? "" : Number(e.target.value)) : e.target.value)} />
          {f.help && <span className="small mut">{f.help}</span>}</label>);
      })}
    </div>
  );
}
