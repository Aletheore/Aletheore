# Marketing website redesign — mockup, not live code

Static prototype only. Nothing in this directory is wired into the real `website/` files or deployed anywhere — it exists so the design direction can be reviewed in a browser before any real port, and so it isn't lost the way the earlier dashboard mockups (which lived only in `/tmp` and were never checked in) nearly were.

Serve locally to view:

```
cd mockups/marketing-website-redesign
python3 -m http.server 8822
```

Then open `http://localhost:8822/index.html`, `pricing.html`, `benchmarks.html`, or `developers.html`.

## Status as of this commit

- Design direction (flat "evidence-native" identity: IBM Plex Mono display headlines, cool paper palette, one restrained "stamp red" accent, no gradients/glass/pills) approved by the user.
- Real content ported in from the live site for `index.html`, `pricing.html`, `benchmarks.html`, `developers.html`, including the Linux-kernel stress-test stats, the RepoWise head-to-head numbers, the "where we lose" honesty section, and a live interactive dependency-graph demo (same force-directed engine as the AIRview dashboard feature) on the homepage.
- Benchmark chart numbers and the TOON-vs-JSON token comparison are verified against real data (a fresh `aletheore scan` of this repo, and the existing published benchmark table), not invented.
- Not yet done: `dogfooding.html`, `status.html`, `security.html`, `terms.html`, `refund.html` were never touched. The live showcase cards (Django/Express/Kubernetes, populated by `showcase-data.js` on the real site) were not wired up with real fetched data here.
- Not yet ported to the real `website/` files — this is stored here deliberately so the work survives between sessions, not as a request to merge or deploy it.
