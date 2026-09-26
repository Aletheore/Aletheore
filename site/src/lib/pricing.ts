export type Interval = "month" | "year";
export type TierKey = "flash" | "air";

// Live Paddle price ids. These are public identifiers (they appear in Paddle.js calls on the page); the
// authoritative copy for the backend is github-app/app_server/paddle_pricing.py.
export const TIERS: Record<TierKey, { name: string; priceId: Partial<Record<Interval, string>> }> = {
  flash: { name: "Aletheore Flash", priceId: { month: "pri_01m1dj0m1netz6ze1mmckz73nm" } },
  air: { name: "Aletheore AIR", priceId: { month: "pri_01kyhevc8bkcghfpwjymz16y2h", year: "pri_01kyhevc9xn6z2nghmy8057jvp" } },
};

/** Tiers that can be priced for this interval. Flash has no yearly price, so it is left out of a yearly preview. */
export function previewItems(interval: Interval): { tier: TierKey; priceId: string }[] {
  return (Object.keys(TIERS) as TierKey[]).flatMap((tier) => {
    const priceId = TIERS[tier].priceId[interval];
    return priceId ? [{ tier, priceId }] : [];
  });
}

/** Checkout happens on app.aletheore.com, which knows the signed-in user and installation. */
export function subscribeUrl(tier: TierKey, interval: Interval): string {
  return `https://app.aletheore.com/subscribe?plan=${tier}&interval=${interval}`;
}

/** Flash is monthly only, so a yearly request for it falls back to monthly. */
export function effectiveInterval(tier: TierKey, interval: Interval): Interval {
  return TIERS[tier].priceId[interval] ? interval : "month";
}
