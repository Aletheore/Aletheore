"use client";
import { X } from "lucide-react";
import { useState, useSyncExternalStore } from "react";
import { subscribedPlan } from "@/lib/subscribed";

// Nothing to subscribe to on purpose: the customer arrives by a full page load from the hosted checkout (an external redirect), so the
// query string is fixed for the life of the page. Client-side navigation to /?subscribed=flash is not a supported entry.
const subscribe = () => () => {};

/** Confirms a Flash checkout. The old site showed nothing after payment, so customers landed on the homepage with no sign it worked. */
export function SubscribedNotice() {
  const plan = useSyncExternalStore(subscribe, () => subscribedPlan(window.location.search), () => null);
  const [dismissed, setDismissed] = useState(false);
  if (plan !== "flash" || dismissed) return null;
  return (
    <div role="status" className="border-b border-success/40 bg-[#EAF1EA]">
      <div className="mx-auto flex max-w-[1080px] items-start gap-4 px-8 py-4">
        <div className="flex-1 text-[13.5px] leading-[1.6]">
          <p className="font-display font-bold">Thanks for subscribing to Aletheore Flash.</p>
          <p className="text-muted">
            It can take a moment for the payment to be confirmed. Reviews then run on your next push to an open pull request in any repository where
            the Aletheore GitHub App is installed. Questions: <a className="text-ink underline" href="mailto:support@aletheore.com">support@aletheore.com</a>.
          </p>
        </div>
        <button
          type="button"
          onClick={() => {
            setDismissed(true);
            // Drop the parameter so a refresh or a bookmarked link does not show the notice again.
            const url = new URL(window.location.href);
            url.searchParams.delete("subscribed");
            window.history.replaceState(null, "", url.pathname + url.search + url.hash);
          }}
          aria-label="Dismiss"
          className="inline-flex size-8 flex-none items-center justify-center rounded-[4px] text-muted hover:text-ink focus-visible:outline-2 focus-visible:outline-stamp"
        >
          <X className="size-4" aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}
