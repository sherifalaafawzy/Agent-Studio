"use client";
// Mounted once in the layout. A first-time visitor is sent to the set-up guide; someone who stopped half-way gets a small
// "Continue set-up" reminder. People who already use the app are never interrupted.
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { markFirstRunDone } from "@/lib/tour";

const CHECKED = "as-gate-checked";
const PENDING = "as-gate-pending";

export default function OnboardingGate() {
  const path = usePathname() ?? "/";
  const router = useRouter();
  const [resume, setResume] = useState<{ step: number; of: number } | null>(null);
  const [hidden, setHidden] = useState(false);

  useEffect(() => {
    try { if (sessionStorage.getItem(CHECKED) === "1") return; } catch { /* storage blocked: check anyway */ }
    const pending = (on: boolean) => { try { if (on) sessionStorage.setItem(PENDING, "1"); else sessionStorage.removeItem(PENDING); } catch { /* ignore */ } };
    pending(true);                                          // tells the welcome tour to wait for our answer
    api<{ show: boolean; resume: boolean; steps: string[]; record: { step: string } }>("/api/easy/onboarding").then((o) => {
      try { sessionStorage.setItem(CHECKED, "1"); } catch { /* ignore */ }
      if (o.show && !path.startsWith("/setup")) { markFirstRunDone(); router.push("/setup/"); }
      else if (o.resume) setResume({ step: Math.max(0, o.steps.indexOf(o.record.step)) + 1, of: o.steps.length });
    }).catch(() => { /* backend offline: nothing to do */ }).finally(() => pending(false));
  }, [path, router]);

  if (!resume || hidden || path.startsWith("/setup")) return null;
  return (
    <div className="setup-banner" role="status">
      <span>Finish setting up Agent Studio · step {resume.step} of {resume.of}</span>
      <Link href="/setup/" className="chip">Continue</Link>
      <button className="icon-btn" aria-label="Hide this reminder" onClick={() => setHidden(true)}>✕</button>
    </div>
  );
}
