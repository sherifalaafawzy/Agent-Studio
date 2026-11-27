// Backend hints quote commands and names in `backticks`. Shown raw they read as stray punctuation; this draws them as code.
import { Fragment } from "react";

export function Ticks({ text }: { text: string }) {
  const parts = text.split(/`([^`\n]+)`/);
  return <>{parts.map((p, i) => (i % 2 ? <code key={i}>{p}</code> : <Fragment key={i}>{p}</Fragment>))}</>;
}
