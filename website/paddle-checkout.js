// Paddle checkout wiring for the pricing page.
const PADDLE_ENVIRONMENT = "sandbox";
const PADDLE_CLIENT_TOKEN = "test_4c86268368fd75d088763f49248";

const TIERS = {
  indie: {
    name: "Indie",
    priceId: { month: "pri_01ky9jwz35hvj5xs6f8xqw6htt", year: "pri_01ky9jwzd6k9rhmnj8b4drbygg" },
  },
  team: {
    name: "Team",
    priceId: { month: "pri_01ky9jx0gbx02mnn4d166yp3vc", year: "pri_01ky9jx0rkkkz75atfb29me9mn" },
  },
  enterprise: {
    name: "Enterprise",
    priceId: { month: "pri_01ky9jx1bkbbkfd9zspcgzd7p8", year: "pri_01ky9jx1pbbpsexbmtbk1wfej1" },
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
