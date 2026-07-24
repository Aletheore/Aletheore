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

function generateClaimToken() {
  const bytes = new Uint8Array(24);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
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

async function subscribe(tierKey) {
  const paddle = await initPaddle();
  const tier = TIERS[tierKey];
  const claimToken = generateClaimToken();

  document.cookie = `claim_token=${claimToken}; domain=.aletheore.com; path=/; max-age=3600; secure; samesite=lax`;

  paddle.Checkout.open({
    items: [{ priceId: tier.priceId[billingInterval], quantity: 1 }],
    customData: { claim_token: claimToken },
    settings: {
      displayMode: "overlay",
      variant: "one-page",
      successUrl: "https://app.aletheore.com/subscribe/claim",
    },
  });
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
