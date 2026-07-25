# Website

The marketing site and live demo, deployed at [aletheore.com](https://aletheore.com). Static
HTML/CSS/vanilla JS, no build step, deployed via Vercel on push to `master`.

- `index.html`, `pricing.html`, `developers.html` — main pages.
- `live-demo.js` — the paste-a-repo demo, calling the isolated `/v1/demo-scan` API on
  `app.aletheore.com` (see `../github-app/app_server/demo_scan_api.py`).
- `paddle-checkout.js` — Paddle checkout integration for the pricing page.
