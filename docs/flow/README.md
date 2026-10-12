# docs/flow — the multi-agent loop (animated)

A self-contained animated walkthrough of this kit's end-to-end workflow:
KB grounding → preflight → plan → delegate to specialist developer agents →
build+test → review → address/loop → learn → commit → push, with all **three
guard hooks** (`shell-guard`, `slop-guard`, `push-guard`) and the skills firing
at their trigger points.

Everything here is generic — it uses the kit's placeholder names
(`feature-orchestrator`, `backend-developer`, `serviceA/B/C`, …). Adapt it to your
own stack the same way you adapt the agents and KB.

## Files

| File | What it is |
|------|------------|
| `flow.html` | The animation. Fully self-contained (inline CSS/JS, no assets). Open in a browser; it autoplays. |
| `flow.mp4`  | Rendered 1080p H.264 video (≈1:45, 30fps) for sharing where HTML won't run. |

## Watch

- **HTML:** open `flow.html` in any browser — it autoplays with on-screen captions.
- **MP4:** open `flow.mp4` in any player.

## Re-render after editing `flow.html`

`flow.mp4` is produced from `flow.html` by recording the page headlessly and
transcoding to MP4. `flow.html` follows a tiny deterministic protocol
(`window.__ready` / `window.__start()` / `window.__done`) and a single timeline
driver, so any headless-browser recorder can capture it. A minimal approach with
Playwright + ffmpeg:

```js
// record.mjs — node record.mjs flow.html flow.mp4
import { chromium } from 'playwright';
import { execFileSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
const [, , inp, out] = process.argv;
const browser = await chromium.launch({ headless: true });               // or channel: 'msedge'
const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 },
  recordVideo: { dir: '.take', size: { width: 1920, height: 1080 } } });
const page = await ctx.newPage();
const t0 = Date.now();
await page.goto(pathToFileURL(inp).href + '?record');
await page.waitForFunction(() => window.__ready === true);
const lead = (Date.now() - t0) / 1000;
await page.evaluate(() => window.__start());
await page.waitForFunction(() => window.__done === true, null, { timeout: 180000 });
const webm = await page.video().path();
await ctx.close(); await browser.close();
execFileSync('ffmpeg', ['-y', '-ss', lead.toFixed(2), '-i', webm, '-c:v', 'libx264',
  '-preset', 'slow', '-crf', '20', '-pix_fmt', 'yuv420p', '-r', '30',
  '-movflags', '+faststart', out], { stdio: 'inherit' });
```

Edit scenes (the `.scene` blocks with `data-start`/`data-end`/`data-cap` and
`data-at` reveals), then re-record to regenerate `flow.mp4`. The clip is silent by
design — narration is on-screen captions.
