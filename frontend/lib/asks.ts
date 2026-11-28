// Permission requests from external agents arrive with several options ("Allow once", "Always allow", "Reject"…).
// Only one of them is drawn as the main button: allowing once. "Always allow" is the bigger decision, so it stays a plain button.

export type AskOption = { id: string; kind?: string; name?: string };

/** The id of the option to draw as the main button: "allow once" when offered, else the first option that allows, else none. */
export function mainOption(options: AskOption[]): string | null {
  const allow = options.filter((o) => String(o.kind ?? "").startsWith("allow"));
  return (allow.find((o) => String(o.kind).includes("once")) ?? allow[0])?.id ?? null;
}
