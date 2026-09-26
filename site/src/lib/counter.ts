export type CounterSpec = { target: number; decimals: number; grouped: boolean; suffix: string };

const PATTERN = /^(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(%?)$/;

/** Parses "1,463,552" or "45.5%". Returns null for values that are not a single number ("4m 20s"). */
export function parseCounter(text: string): CounterSpec | null {
  const m = PATTERN.exec(text.trim());
  if (!m) return null;
  const [, whole, fraction = "", suffix] = m;
  return {
    target: Number(`${whole.replace(/,/g, "")}${fraction ? `.${fraction}` : ""}`),
    decimals: fraction.length,
    grouped: whole.includes(","),
    suffix,
  };
}

export function formatCounter(value: number, spec: CounterSpec): string {
  const fixed = value.toFixed(spec.decimals);
  if (!spec.grouped) return `${fixed}${spec.suffix}`;
  const [whole, fraction] = fixed.split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${grouped}${fraction ? `.${fraction}` : ""}${spec.suffix}`;
}

export function easeOutCubic(t: number): number {
  const c = Math.min(1, Math.max(0, t));
  return 1 - (1 - c) ** 3;
}
