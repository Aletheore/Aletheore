# Website

The marketing site, deployed at [aletheore.com](https://aletheore.com). Static
HTML/CSS/vanilla JS, no build step, deployed via Vercel on push to `master`.

- `index.html`, `pricing.html`, `developers.html` — main pages.
- `paddle-checkout.js` — Paddle checkout integration for the pricing page.
- `status.js` — the public status widget, calling `/v1/health/{org}/{repo}` on
  `app.aletheore.com` (see `../github-app/app_server/dashboard.py`).
