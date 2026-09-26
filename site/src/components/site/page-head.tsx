import { cn } from "@/lib/utils";

export function PageHead({ eyebrow, title, children, centered = false }: { eyebrow: string; title: string; children?: React.ReactNode; centered?: boolean }) {
  return (
    <header className={cn("mx-auto max-w-[1080px] px-8 pb-10 pt-16 md:pt-20", centered && "text-center")}>
      <p className="eyebrow eyebrow--section mb-3">{eyebrow}</p>
      <h1 className={cn("max-w-[26ch] text-balance font-display text-[clamp(28px,4.2vw,44px)] font-bold leading-[1.08] tracking-[-0.02em]", centered && "mx-auto")}>{title}</h1>
      {children ? <div className={cn("mt-5 max-w-[64ch] text-[15.5px] leading-[1.7] text-muted", centered && "mx-auto")}>{children}</div> : null}
    </header>
  );
}
