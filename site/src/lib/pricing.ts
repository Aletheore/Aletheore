export type Interval = "month" | "year";
export type TierKey = "lite" | "flash" | "air";

// Live Paddle price ids. These are public identifiers (they appear in Paddle.js calls on the page); the
// hosted backend maintains the corresponding server-side mapping.
export const TIERS: Record<TierKey, { name: string; priceId: Partial<Record<Interval, string>> }> = {
  lite: { name: "Aletheore Flash Lite", priceId: { month: "pri_01m7zbvbsck8s6rnxpv66652cw" } },
  flash: { name: "Aletheore Flash", priceId: { month: "pri_01m7d0qgwddyg8rgbcj2ppm1ek" } },
  air: { name: "Aletheore AIR", priceId: { month: "pri_01m7d0qgz9jv3dfye9jxvcv5n9", year: "pri_01m7d0qh2fgmr0nt92cj77nqva" } },
};

/** Tiers that can be priced for this interval. Flash Lite and Flash have no yearly price, so they are left out of a yearly preview. */
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
