import { cn } from "@/lib/utils";

export function Prose({ children, className }: { children: React.ReactNode; className?: string }) {
  return <p className={cn("max-w-[72ch] text-[14px] leading-[1.7] text-muted", className)}>{children}</p>;
}

export function Callout({ title, children, tone = "plain" }: { title: string; children: React.ReactNode; tone?: "plain" | "warn" }) {
  return (
    <aside className={cn("border p-5", tone === "warn" ? "border-stamp/40 bg-stamp-soft" : "border-line bg-raised")}>
      <h3 className="font-display text-[13.5px] font-bold">{title}</h3>
      <div className="mt-2 max-w-[74ch] text-[13.5px] leading-[1.7] text-muted">{children}</div>
    </aside>
  );
}

export function FreshBadge() {
  return <span className="ml-2 inline-block rounded-[3px] bg-stamp px-1.5 py-[1px] align-middle font-display text-[10px] font-semibold uppercase tracking-[0.05em] text-paper">New</span>;
}
