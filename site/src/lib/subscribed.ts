export type SubscribedPlan = "lite" | "flash";

/** What the confirmation notice calls each plan a customer can return from. */
export const SUBSCRIBED_PLAN_NAMES: Record<SubscribedPlan, string> = {
  lite: "Aletheore Flash Lite",
  flash: "Aletheore Flash",
};

/** The hosted checkout sends a paying Flash or Flash Lite customer back to /?subscribed=<plan> (the hosted application success URL). */
export function subscribedPlan(search: string): SubscribedPlan | null {
  const value = new URLSearchParams(search).get("subscribed");
  return value === "flash" || value === "lite" ? value : null;
}
