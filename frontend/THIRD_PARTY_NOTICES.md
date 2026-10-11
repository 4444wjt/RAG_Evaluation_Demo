# Third-party notices

The VeriScope AI (鉴真全域) source code is offered under the repository's MIT License. Third-party code and packages keep their own licenses; the root MIT License does not replace them.

## Adapted source included in the landing page

| Component | Local file | License and notice |
| --- | --- | --- |
| [Magic UI Animated Beam](https://github.com/magicuidesign/magicui/blob/d7207e5692d14c00dceafa8488d6d01f197fa0e4/apps/www/registry/magicui/animated-beam.tsx) | `src/features/landing/BeamConnector.tsx` | MIT; attribution and full license are included in `public/landing/third-party-licenses.txt` and `licenses/magic-ui.txt`. The local implementation uses native SVG/CSS and does not include Magic UI's Motion runtime. |

The landing background in `src/features/landing/ResearchField.tsx` is an original Canvas 2D contour implementation. The product preview hover state is native CSS. Neither includes React Bits source or a React Bits component. No Commons Clause component is included in the frontend source or public assets.

## Direct frontend runtime dependencies

| Package | License |
| --- | --- |
| [React](https://github.com/react/react/blob/v19.3.0/LICENSE) and React DOM | MIT; copy at `licenses/LICENSE-react-19.3.0.txt` |
| [Apache ECharts 6.1.0](https://github.com/apache/echarts/blob/6.1.0/LICENSE) | Apache-2.0; copy at `licenses/LICENSE-echarts-6.1.0.txt` |
| Apache ECharts embedded D3 components | BSD 3-Clause; copy at `licenses/LICENSE-echarts-d3-6.1.0.txt` |
| [Inter Variable 5.3.0](https://github.com/rsms/inter) via `@fontsource-variable/inter` | SIL Open Font License 1.1; self-hosted font files, copy at `licenses/LICENSE-inter-5.3.0.txt` |

The bundled runtime notices are copied into the production build as `public/landing/third-party-licenses.txt`. Pinned versions and the complete dependency tree are recorded in `package.json` and `package-lock.json`. Development and other transitive dependencies retain their respective upstream licenses; review their package metadata when redistributing those packages or a development environment.

## Visual research

Other websites and component galleries informed visual research only. Their assets and component source are not included unless specifically listed above.
