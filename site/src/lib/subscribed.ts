/** The hosted checkout sends a paying Flash customer back to /?subscribed=flash (the hosted application success URL). */
export function subscribedPlan(search: string): "flash" | null {
  const value = new URLSearchParams(search).get("subscribed");
  return value === "flash" ? "flash" : null;
}
