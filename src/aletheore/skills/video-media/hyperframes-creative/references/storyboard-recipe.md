# Storyboard recipe — plan a video as stills the person approves

Applies wherever a run plans on a storyboard (`storyboard: yes` in `brief-contract.md`), and whenever a run makes or changes a storyboard, a sketch sheet or a board of frames, whatever skill or workflow asked for one. This file owns **how to make the board**. `review-loop.md` owns when the person is asked; `storyboard-format.md` owns the `STORYBOARD.md` file the scripts parse; `story-spine.md` owns the order of the story.

The person sees the plan as stills on the board, comments or draws on a frame, and says go. The board is `boards/storyboard.html`, and tools that show it find its frames by the classes in § 2; boxes marked any other way (`cell` boxes, `id="frame-NN"` markers) are not found.

## 1. The run

1. **Pose each shot** in the composition the project renders, the root `index.html`, at its key moment: real words, real fonts, real colors, the focal point big enough to read on a phone. A shot posed in its own file (`compositions/frames/NN-*.html`) shows only once it is mounted in `index.html` (`data-composition-src`) at its planned start; `snapshot` renders `index.html` and nothing else.
2. **Snapshot every still in one run** with your host's snapshot tool when one is offered (each shot's seconds, out `boards/stills`); otherwise `npx hyperframes snapshot --at <every shot's seconds, in film order> --no-end --describe false -o boards/stills`. Each still is named `frame-NN-at-<seconds>s.png`, NN its place in the film. Every `snapshot` run clears its `-o` folder and numbers from `00`, so never snapshot a single moment into `boards/stills/` itself (step 4). Look at every still yourself; fix and snapshot again until each one holds.
3. **Write `boards/storyboard.html`** (§ 2), styled as a finished board: Inter or the system sans-serif, a light page, each frame a rounded card with its still above its words, even gaps.
4. **Stop.** Hand the person the board and wait for their go. Notes on frames change only those frames. For each one: re-pose it, snapshot just its moment into `boards/stills/redo/` (`--at <its seconds>`), move that one file over the frame's old still under the old name (its new time in the name if the time changed, and the page's `src` with it), then rewrite the page and stop again. The other stills stay as approved.
5. **Once they say go**, animate from the approved stills.

## 2. The page

One `class="frame"` box per still, in film order, holding its image, a `class="title"` line "<two-digit number>: <name>" and a `class="time"` range in seconds. Optional: a `class="words"` line under it, and a `class="act"` heading before the first frame of each act.

```html
<section class="act">Act 1: The hook</section>
<div class="frame">
  <img src="stills/frame-00-at-1.5s.png" alt="" />
  <div class="title">01: The hook</div>
  <div class="time">0.0 to 3.0s</div>
  <p class="words">The headline pushes in on a dark canvas.</p>
</div>
```

Stills are written next to the page (`boards/stills/`), so `src` is relative to `boards/` and names the file `snapshot` wrote for that shot. A still written after the page shows by itself; never reopen or rename the page for it.

## 3. The plan beside it

`STORYBOARD.md` at the project root (format: `hyperframes/references/storyboard-format.md`) names the same frames in the same order, and its words win over the page's: one heading per frame like `## Frame 1: The hook (0-3s)`, a `- act:` bullet for its act, and `- status: outline` until its still is posed.
