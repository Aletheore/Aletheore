"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { plans, type FeatureText, type Plan } from "@/data/pricing";
import { effectiveInterval, previewItems, subscribeUrl, type Interval, type TierKey } from "@/lib/pricing";
import { cn } from "@/lib/utils";

// Paddle client-side token: public by design, the same one the current site ships.
const PADDLE_TOKEN = "live_e7aef6edd9b215cd9059dab0c3d";
const PADDLE_SRC = "https://cdn.paddle.com/paddle/v2/paddle.js";

type PaddleGlobal = {
  Environment: { set: (env: string) => void };
  Initialize: (opts: { token: string }) => void;
  PricePreview: (req: { items: { priceId: string; quantity: number }[] }) => Promise<{
    data: { details: { lineItems: { formattedTotals: { total: string } }[] } };
  }>;
};

let paddleLoad: Promise<PaddleGlobal> | null = null;
function loadPaddle(): Promise<PaddleGlobal> {
  if (paddleLoad) return paddleLoad;
  paddleLoad = new Promise<PaddleGlobal>((resolve, reject) => {
    const ready = () => {
      const p = (window as unknown as { Paddle?: PaddleGlobal }).Paddle;
      if (!p) return reject(new Error("Paddle.js did not load"));
      p.Environment.set("production");
      p.Initialize({ token: PADDLE_TOKEN });
      resolve(p);
    };
    const s = document.createElement("script");
    s.src = PADDLE_SRC;
    s.async = true;
    s.onload = ready;
    s.onerror = () => reject(new Error("Paddle.js failed to load"));
    document.head.appendChild(s);
  });
  paddleLoad.catch(() => { paddleLoad = null; });
  return paddleLoad;
}

type Prices = Partial<Record<Interval, Partial<Record<TierKey, string>>>>;

function Feature({ f }: { f: FeatureText }) {
  if (typeof f === "string") return <>{f}</>;
  return (
    <>
      {f.parts.map((p, i) =>
        typeof p === "string" ? <span key={i}>{p}</span> : <Link key={i} href={p.href} className="text-ink underline">{p.label}</Link>,
      )}
      {f.mark ? <sup className="ml-0.5 text-stamp">{f.mark}</sup> : null}
    </>
  );
}

function PlanCard({ plan, price, priceNote, interval }: { plan: Plan; price: string; priceNote: string; interval: Interval }) {
  const { cta } = plan;
  return (
    <article
      data-tier={plan.id === "community" ? undefined : plan.id}
      className={cn("flex flex-col border bg-raised p-6", plan.flagship ? "border-ink shadow-[6px_6px_0_0_var(--color-ink)]" : "border-line")}
    >
      <h3 className="font-display text-[13px] font-semibold uppercase tracking-[0.04em] text-stamp">{plan.name}</h3>
      <p className="mt-3 flex items-baseline gap-1.5 font-display">
        <span className="price-now text-[38px] font-bold tabular-nums leading-none">{price}</span>
        <span className="text-[13px] text-faint">{priceNote}</span>
      </p>
      <p className="mt-3 text-[13.5px] leading-[1.6] text-muted">{plan.desc}</p>
      <ul className="mt-5 flex flex-1 flex-col gap-2.5 border-t border-dashed border-line-strong pt-5 text-[13px] leading-[1.6]">
        {plan.features.map((f, i) => (
          <li key={i} className="flex gap-2.5">
            <span className="mt-[7px] size-1.5 flex-none bg-stamp" aria-hidden="true" />
            <span><Feature f={f} /></span>
          </li>
        ))}
      </ul>
      <div className="mt-6">
        {cta.href ? (
          <Button asChild variant="secondary" className="w-full"><a href={cta.href} rel="noopener">{cta.label}</a></Button>
        ) : (
          <Button asChild variant={plan.flagship ? "primary" : "secondary"} className="w-full">
            <a href={subscribeUrl(cta.subscribe!, effectiveInterval(cta.subscribe!, interval))} data-subscribe={cta.subscribe}>{cta.label}</a>
          </Button>
        )}
      </div>
    </article>
  );
}

export function PlanCards() {
  const [interval, setInterval] = useState<Interval>("month");
  const [prices, setPrices] = useState<Prices>({});
  const [failed, setFailed] = useState<Interval | null>(null);

  useEffect(() => {
    let cancelled = false;
    const items = previewItems(interval);
    (async () => {
      try {
        const paddle = await loadPaddle();
        const res = await paddle.PricePreview({ items: items.map((i) => ({ priceId: i.priceId, quantity: 1 })) });
        if (cancelled) return;
        const line = res.data.details.lineItems;
        setPrices((prev) => ({ ...prev, [interval]: Object.fromEntries(items.map((it, idx) => [it.tier, line[idx].formattedTotals.total])) }));
        setFailed(null);
      } catch {
        if (!cancelled) setFailed(interval);
      }
    })();
    return () => { cancelled = true; };
  }, [interval]);

  const shown = (plan: Plan): { price: string; note: string } => {
    if (plan.id === "community") return { price: plan.price, note: plan.priceNote };
    const eff = effectiveInterval(plan.id, interval);
    const live = prices[eff]?.[plan.id];
    // Monthly falls back to the published price; a yearly price is never guessed.
    const price = live ?? (eff === "month" ? plan.price : "…");
    return { price, note: eff === "month" ? "/month" : "/year" };
  };

  return (
    <>
      <div className="mb-8 flex flex-col items-center gap-2">
        <div role="group" aria-label="Billing interval" className="inline-flex border border-line-strong bg-raised p-0.5 font-display text-[12.5px]">
          {(["month", "year"] as const).map((v) => (
            <button
              key={v}
              type="button"
              aria-pressed={interval === v}
              onClick={() => setInterval(v)}
              className={cn(
                "px-4 py-1.5 font-semibold transition-colors focus-visible:outline-2 focus-visible:outline-stamp",
                interval === v ? "bg-ink text-paper" : "text-muted hover:text-ink",
              )}
            >
              {v === "month" ? "Monthly" : (<>Yearly <span className={cn("ml-1", interval === v ? "text-paper/80" : "text-stamp")}>2 months free</span></>)}
            </button>
          ))}
        </div>
        <p className="min-h-4 text-[12px] text-faint" role="status">
          {failed === "year" ? "Yearly prices could not be loaded right now. Monthly prices are shown." : interval === "year" ? "Flash is monthly only." : ""}
        </p>
      </div>
      <div className="grid gap-6 md:grid-cols-3">
        {plans.map((p) => {
          const s = shown(p);
          return <PlanCard key={p.id} plan={p} price={s.price} priceNote={s.note} interval={interval} />;
        })}
      </div>
    </>
  );
}

