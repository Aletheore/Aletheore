// Paddle checkout wiring for the pricing page.
const PADDLE_ENVIRONMENT = "production";
const PADDLE_CLIENT_TOKEN = "live_e7aef6edd9b215cd9059dab0c3d";

const TIERS = {
  flash: {
    // Monthly only - no annual price exists yet in Paddle for this plan
    // (see app_server/paddle_pricing.py). refreshPrices() below skips a
    // tier entirely for an interval it has no priceId for, leaving its
    // card's static HTML price as the fallback rather than fetching an
    // undefined priceId.
    name: "Aletheore Flash",
    priceId: { month: "pri_01m1754jr5msg62grry49kjhw5" },
  },
  air: {
    name: "Aletheore AIR",
    priceId: { month: "pri_01kyhevc8bkcghfpwjymz16y2h", year: "pri_01kyhevc9xn6z2nghmy8057jvp" },
  },
};

let billingInterval = "month";
let paddleReady = null;

function initPaddle() {
  if (paddleReady) return paddleReady;
  paddleReady = new Promise((resolve, reject) => {
    if (typeof Paddle === "undefined") {
      reject(new Error("Paddle.js failed to load"));
      return;
    }
    Paddle.Environment.set(PADDLE_ENVIRONMENT);
    Paddle.Initialize({ token: PADDLE_CLIENT_TOKEN });
    resolve(Paddle);
  });
  return paddleReady;
}

async function refreshPrices() {
  const paddle = await initPaddle();
  // Only tiers that actually have a price for this interval - Flash has no
  // "year" priceId, so toggling to Yearly must skip it entirely rather than
  // send Paddle an undefined priceId (which fails the whole preview call
  // for every tier, not just the one missing it). Its card keeps the
  // static "$6/month" from the HTML in that case.
  const activeTiers = Object.entries(TIERS).filter(([, tier]) => tier.priceId[billingInterval]);
  if (activeTiers.length === 0) return;
  const items = activeTiers.map(([, tier]) => ({ priceId: tier.priceId[billingInterval], quantity: 1 }));

  let preview;
  try {
    preview = await paddle.PricePreview({ items });
  } catch (err) {
    console.error("Paddle price preview failed", err);
    return;
  }

  const lineItems = preview.data.details.lineItems;
  activeTiers.forEach(([key], index) => {
    const card = document.querySelector(`[data-tier="${key}"]`);
    if (!card) return;
    const lineItem = lineItems[index];
    card.querySelector(".price-now").textContent = lineItem.formattedTotals.total;
    const subEl = card.querySelector(".price-sub-interval");
    if (subEl) subEl.textContent = billingInterval === "month" ? "/month" : "/year";
  });
}

function setBillingInterval(interval) {
  billingInterval = interval;
  document.querySelectorAll(".billing-toggle button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.interval === interval);
  });
  refreshPrices();
}

function subscribe(tierKey) {
  // Checkout itself now happens on app.aletheore.com, not here - this page
  // has no session and no way to know who's paying or which installation to
  // apply the plan to. app.aletheore.com already knows both (or can ask the
  // visitor to sign in / install the GitHub App) before any money moves.
  window.location.href = `https://app.aletheore.com/subscribe?plan=${tierKey}&interval=${billingInterval}`;
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".billing-toggle button").forEach((btn) => {
    btn.addEventListener("click", () => setBillingInterval(btn.dataset.interval));
  });
  document.querySelectorAll("[data-subscribe]").forEach((btn) => {
    btn.addEventListener("click", () => subscribe(btn.dataset.subscribe));
  });
  refreshPrices();
});
