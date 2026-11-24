"use client";
// The live-mode avatar: a simple animated face drawn in SVG, entirely in the browser (nothing is downloaded or sent anywhere).
// The mouth follows the loudness and brightness of the voice that is playing, the eyes blink, and the ring shows what Agent Studio is doing.
import { useEffect, useRef } from "react";
import { LiveState, mouthShape, readVoice } from "@/lib/live";

const RING: Record<LiveState, string> = { idle: "var(--mut)", connecting: "var(--warn)", loading: "var(--warn)", listening: "var(--ok)", hearing: "var(--acc)", thinking: "var(--warn)", speaking: "var(--acc)" };
export const STATE_TEXT: Record<LiveState, string> = { idle: "Not connected", connecting: "Connecting…", loading: "Getting ready…", listening: "Listening", hearing: "Hearing you…", thinking: "Thinking…", speaking: "Speaking" };

export default function Avatar({ analyser, state }: { analyser: AnalyserNode | null; state: LiveState }) {
  const mouth = useRef<SVGPathElement>(null);
  const shape = useRef({ open: 0, wide: 0.5 });
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      let target = { open: 0, wide: 0.5 };
      if (analyser && state === "speaking") { const v = readVoice(analyser); target = mouthShape(v.level, v.bright); }
      const s = shape.current;
      s.open += (target.open - s.open) * (target.open > s.open ? 0.6 : 0.25);          // opens fast, closes a little slower: looks like speech
      s.wide += (target.wide - s.wide) * 0.3;
      const w = 34 + s.wide * 22, h = 3 + s.open * 26;
      mouth.current?.setAttribute("d", `M ${100 - w / 2} 138 Q 100 ${138 + h * 1.25} ${100 + w / 2} 138 Q 100 ${138 + (h > 6 ? h * 0.3 : -1)} ${100 - w / 2} 138 Z`);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [analyser, state]);
  return (
    <svg viewBox="0 0 200 200" role="img" aria-label={`Avatar: ${STATE_TEXT[state]}`} className={`live-avatar st-${state}`} data-testid="live-avatar">
      <circle cx="100" cy="100" r="92" fill="none" stroke={RING[state]} strokeWidth="5" className="ring" />
      <circle cx="100" cy="100" r="80" fill="var(--panel)" stroke="var(--line)" strokeWidth="2" />
      <g className="eyes"><ellipse cx="72" cy="88" rx="9" ry="12" fill="var(--fg)" className="eye" /><ellipse cx="128" cy="88" rx="9" ry="12" fill="var(--fg)" className="eye" /></g>
      <path ref={mouth} d="M 83 138 Q 100 141 117 138 Q 100 137 83 138 Z" fill="var(--fg)" stroke="var(--fg)" strokeWidth="3" strokeLinejoin="round" />
      {state === "thinking" && <g className="dots" fill="var(--fg2)"><circle cx="84" cy="168" r="4" /><circle cx="100" cy="168" r="4" /><circle cx="116" cy="168" r="4" /></g>}
    </svg>
  );
}

/** Voice-only view: a pulsing orb with level bars, driven by the same analyser. */
export function Orb({ analyser, state }: { analyser: AnalyserNode | null; state: LiveState }) {
  const bars = useRef<(SVGRectElement | null)[]>([]);
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const v = analyser && state === "speaking" ? readVoice(analyser).level : 0;
      bars.current.forEach((b, i) => {
        const h = 6 + Math.min(1, v * 6) * (60 - Math.abs(i - 3) * 12) + (state === "hearing" ? 8 : 0);
        b?.setAttribute("height", String(h));
        b?.setAttribute("y", String(100 - h / 2));
      });
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [analyser, state]);
  return (
    <svg viewBox="0 0 200 200" role="img" aria-label={`Voice only: ${STATE_TEXT[state]}`} className={`live-avatar live-orb st-${state}`} data-testid="live-orb">
      <circle cx="100" cy="100" r="92" fill="none" stroke={RING[state]} strokeWidth="5" className="ring" />
      <circle cx="100" cy="100" r="80" fill="var(--panel)" stroke="var(--line)" strokeWidth="2" />
      {[0, 1, 2, 3, 4, 5, 6].map((i) => <rect key={i} ref={(el) => { bars.current[i] = el; }} x={64 + i * 12} y={100 - 3} width="7" height="6" rx="3.5" fill="var(--fg)" />)}
    </svg>
  );
}
