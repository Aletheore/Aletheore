// Paddle checkout wiring for the pricing page.
const PADDLE_ENVIRONMENT = "production";
const PADDLE_CLIENT_TOKEN = "live_e7aef6edd9b215cd9059dab0c3d";

const TIERS = {
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
  const items = Object.values(TIERS).map((tier) => ({ priceId: tier.priceId[billingInterval], quantity: 1 }));

  let preview;
  try {
    preview = await paddle.PricePreview({ items });
  } catch (err) {
    console.error("Paddle price preview failed", err);
    return;
  }

  const lineItems = preview.data.details.lineItems;
  Object.keys(TIERS).forEach((key, index) => {
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
