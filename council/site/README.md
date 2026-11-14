# aletheorecouncil.com

A static site: one scroll-driven page (`index.html`, eight pinned scenes, GSAP ScrollTrigger from cdnjs, Geist and IBM Plex Mono from Google
Fonts), a privacy page, a terms page, and one function, `api/slots.js`, that reads the free-tunnel count from
`https://registry.aletheorecouncil.com/v1/status`. If the count cannot be read the page says so; it never shows an invented number.

```
node check.mjs              # checks on the copy, links, layout rules and the counter route (exits 1 on any failure)
node preview.mjs            # http://127.0.0.1:4180/ with the live counter route
node preview.mjs --lan      # also on your local network, to open on a real phone (stop it when done)
```

Deploy it as its own Vercel project with this folder as the root. `vercel.json` sets the security headers. `.vercelignore` keeps `design/`,
`transcripts/` and the dev scripts out of the deploy.

## Rules the checks enforce

- The demo window is an illustration and says so, except scene 08, which shows recorded runs of real agents (transcripts in `transcripts/`)
  with the invitation link left out. Add another agent to scene 08 only from a real recorded run
  (`../acceptance/claude_codex_control_test.py` records one for Claude Code or Codex).
- Scene 08 names only the six real control tools, and no invitation link appears anywhere.
- No em or en dashes, no placeholder wording, no dead links.
- Phones: the camera zoom is off below 900 px wide, the stage uses the small viewport height so Safari's toolbars do not cover it, and
  below 620 px of height (or sideways on a phone) the page shows a note instead of a walkthrough that cannot fit.
  Check changes by jumping the timeline to several points at 393x659, 390x844, 768x1024, 1024x768, 1280x720 and 1440x900, including
  the zoomed frames.
- Open to search engines: no `noindex` anywhere, `robots.txt` allows crawling and names `sitemap.xml`, and aletheore.com links here from its footer and its Council page. To hide the site again, add the `noindex` meta tag to every page, `Disallow: /` to `robots.txt` and an `X-Robots-Tag` header in `vercel.json`.
- The Install section gives the real command, `pip install 'aletheore[council]'` (Council shipped in Aletheore 0.13.0).

## Before launch

- Have a lawyer read `privacy.html` and `terms.html`. They state facts and plain terms; they are not legal advice.
- A host's device id and public keys stay in the registry until someone asks for removal. Decide whether to add automatic deletion, then update the privacy page.
- The page loads Google Fonts and cdnjs, and the privacy page says so. Self-hosting both would remove that disclosure.
