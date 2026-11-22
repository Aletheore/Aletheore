import { TIERS } from "@/lib/pricing";

export type SubscribedPlan = "lite" | "flash";

/** What the confirmation notice calls each plan a customer can return from: the same names checkout uses (TIERS), narrowed to the plans
 * the hosted checkout sends back to the website. */
export const SUBSCRIBED_PLAN_NAMES: Record<SubscribedPlan, string> = {
  lite: TIERS.lite.name,
  flash: TIERS.flash.name,
};

/** The hosted checkout sends a paying Flash or Flash Lite customer back to /?subscribed=<plan> (the hosted application success URL). */
export function subscribedPlan(search: string): SubscribedPlan | null {
  const value = new URLSearchParams(search).get("subscribed");
  return value === "flash" || value === "lite" ? value : null;
}
