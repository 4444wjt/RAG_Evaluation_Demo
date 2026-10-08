# Landing page product previews

The PNGs are historical browser captures of the existing frontend using `src/fixtures/demo.json` (snapshot `20260930-1958`). They are not a new evaluation or newly calculated metrics. The landing page now presents both previews as native HTML text in `src/features/landing/ProductPreview.tsx`, preserving the exact visible snapshot content and product styling without bitmap interpolation. Neither PNG is loaded by the landing page.

- `quality-gate.png`: DeepSeek overview, `.quality-gate` region, original 1280 × 800 desktop capture.
- `evidence-trace-retina.png`: previous landing-page evidence preview. Rendered directly from `TraceView.tsx`, DeepSeek first test question, `.trace-statements-section` region, at a 1920 × 1080 desktop viewport with deviceScaleFactor 3. The region was laid out at 800 CSS pixels wide before capture; the PNG is 2400 × 726 pixels. This is a fresh browser render, not an upscaled image. Only the evidence region is captured, without navigation, browser chrome or surrounding whitespace. Original fonts, colors, text and the “有依据” verdict are unchanged.
- `evidence-trace.png`: previous low-resolution capture, retained as a historical asset; the landing page no longer references it.

Captured using Playwright on 2026-10-03. Source text, metric values and verdicts were not edited. The landing page captions identify the snapshot; enter the live workbench to inspect the full records.

## Clarity correction (2026-10-04)

Live Chrome DevTools and Playwright inspection confirmed that the quality gate bitmap was 972 × 230 pixels, rendered at approximately 833 × 197 CSS pixels on the 1920 desktop and 678 × 160 on the 1280 laptop. Its original small labels were reduced along with the whole card; the 1× asset also lacked retina density. The trace bitmap was 2400 × 726, displayed at approximately 1067 × 323 CSS pixels, resampling the original 800-CSS-pixel layout at a different scale. No image or ancestor had CSS blur, filter, opacity reduction or transform. A subtle gold hover sweep covered the images.

The native previews eliminate image resolution and resampling as display constraints. Gate labels and thresholds are 14px, metrics remain 24px, PASS remains 48px; the statement is 20px and evidence quote 18px. The hover sweep over the content is removed, while the existing Gold border and keyboard focus remain. The original displayed wording, statuses, model and numerical values are unchanged, and tests compare the preview content against the existing fixture. This is a static snapshot preview; the source captions and links to the complete workbench remain visible. No evaluation logic or runtime data import is added.

Fresh lossless PNG exports of the native previews are available in the ignored frontend `test-results/` directory: `quality-gate-native-3x.png` (2499 × 702) and `evidence-trace-native-3x.png` (3198 × 846). Playwright rendered them directly at deviceScaleFactor 3, without upscaling an old image. The landing page uses native text; these exports are presentation artifacts rather than display dependencies.
