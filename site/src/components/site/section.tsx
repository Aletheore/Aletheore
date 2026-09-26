import { cn } from "@/lib/utils";

export function Section({
  id, eyebrow, title, intro, children, className,
}: { id?: string; eyebrow?: string; title: string; intro?: React.ReactNode; children?: React.ReactNode; className?: string }) {
  const titleId = id ? `${id}-title` : undefined;
  return (
    <section id={id} aria-labelledby={titleId} className={cn("mx-auto max-w-[1080px] scroll-mt-24 px-8 py-14", className)}>
      {eyebrow ? <p className="eyebrow eyebrow--section mb-3">{eyebrow}</p> : null}
      <h2 id={titleId} className="max-w-[30ch] text-balance font-display text-[clamp(23px,3vw,32px)] font-bold leading-[1.12] tracking-[-0.015em]">{title}</h2>
      {intro ? <div className="mt-4 max-w-[68ch] text-[14.5px] leading-[1.7] text-muted">{intro}</div> : null}
      {children ? <div className="mt-8">{children}</div> : null}
    </section>
  );
}
