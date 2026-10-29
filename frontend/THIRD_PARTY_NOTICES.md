# Third-party notices

The VeriScope AI (鉴真全域) source code is offered under the repository's Apache License 2.0. Third-party code and packages keep their own licenses; the root Apache-2.0 License does not replace them.

## Adapted source included in the frontend

| Component | Local file | License and notice |
| --- | --- | --- |
| [Magic UI Animated Beam](https://github.com/magicuidesign/magicui/blob/d7207e5692d14c00dceafa8488d6d01f197fa0e4/apps/www/registry/magicui/animated-beam.tsx) | `src/features/landing/BeamConnector.tsx`; reused by `src/features/CitationVersionView.tsx` with scoped styles in `src/features/citation-version.css` | MIT; attribution and full license are included in `public/landing/third-party-licenses.txt` and `licenses/magic-ui.txt`. The local implementation uses native SVG/CSS and does not include Magic UI's Motion runtime. The citation workflow animates only real processing or report arrival, with a static reduced-motion alternative. |
| [Uiverse "modern-stingray-68" by xopc333](https://uiverse.io/xopc333/modern-stingray-68) | `src/components/SlideArrow.tsx`, `.slide-arrow` in `src/styles.css` | MIT; ring-swap and sliding-arrow motion adapted to the project's tokens. Attribution and license in `licenses/uiverse-modern-stingray-68.txt` and `public/landing/third-party-licenses.txt`. |

The landing background in `src/features/landing/ResearchField.tsx` is an original Canvas 2D contour implementation. The product preview hover state is native CSS. Neither includes React Bits source or a React Bits component. No Commons Clause component is included in the frontend source or public assets.

## Direct frontend runtime dependencies

| Package | License |
| --- | --- |
| [React](https://github.com/react/react/blob/v19.3.0/LICENSE) and React DOM | MIT; copy at `licenses/LICENSE-react-19.3.0.txt` |
| [Apache ECharts 6.1.0](https://github.com/apache/echarts/blob/6.1.0/LICENSE) | Apache-2.0; copy at `licenses/LICENSE-echarts-6.1.0.txt` |
| Apache ECharts embedded D3 components | BSD 3-Clause; copy at `licenses/LICENSE-echarts-d3-6.1.0.txt` |
| [Geist and Geist Mono 5.3.0](https://github.com/vercel/geist-font) via `@fontsource-variable/geist` and `@fontsource-variable/geist-mono` | SIL Open Font License 1.1; self-hosted font files, copies at `licenses/LICENSE-geist-5.3.0.txt` and `licenses/LICENSE-geist-mono-5.3.0.txt` |
| [Noto Sans SC Variable 5.3.0](https://github.com/notofonts/noto-cjk) via `@fontsource-variable/noto-sans-sc` | SIL Open Font License 1.1; self-hosted, split by unicode-range; copy at `licenses/LICENSE-noto-sans-sc-5.3.0.txt` |

The bundled runtime notices are copied into the production build as `public/landing/third-party-licenses.txt`. Pinned versions and the complete dependency tree are recorded in `package.json` and `package-lock.json`. Development and other transitive dependencies retain their respective upstream licenses; review their package metadata when redistributing those packages or a development environment.

## Visual research

Other websites and component galleries informed visual research only. Their assets and component source are not included unless specifically listed above.
