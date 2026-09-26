import { Counter } from "@/components/site/counter";

export function StatRow({ stats }: { stats: readonly { value: string; label: string; sub?: string }[] }) {
  return (
    <dl className="grid gap-px border border-line bg-line sm:grid-cols-3">
      {stats.map((s) => (
        <div key={s.label} className="bg-raised p-5">
          <dd className="font-display text-[34px] font-bold leading-none tabular-nums text-stamp"><Counter value={s.value} /></dd>
          <dt className="mt-2 text-[13px] font-semibold">{s.label}</dt>
          {s.sub ? <p className="mt-0.5 text-[12px] text-faint">{s.sub}</p> : null}
        </div>
      ))}
    </dl>
  );
}
