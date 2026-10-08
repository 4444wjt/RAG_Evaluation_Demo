# 首页设计参考与适配记录

日期：2026-10-03。范围：鉴真全域 VeriScope AI 中文比赛首页，仅 1920×1080 桌面与 1280×800 笔记本。

本记录区分实际浏览器观察、官方文档／源码核对与本项目的适配选择。参考网页的当前外观不代表其完整设计规范；单帧截图不作为完整动效机制的证据。

## 1. 保留的设计边界

- 保留现有深色表面、Gold `#F2C879`、PASS／FAIL／WARNING 语义色、字体栈、圆角与边框 tokens。
- 中文承担标题、说明、导航和操作；保留 RAG、正式产品名、完整模型名称、HTML，以及真实截图中的技术状态。不追加英文复述。
- Hero 保留原文和两行构图，桌面标题上限 64px，笔记本 48px，字重 600；本轮只对“证据检查。”使用静态浅金至 Gold 渐变，提供实色回退。
- 保留深色背景、金色轮廓线、缓慢运动、鼠标响应、隐藏暂停及主内容延伸；背景现由项目自有 Canvas 曲线实现。
- 五步流程仍为展示元素，自动播放两遍后停留质量门禁；保留离屏／隐藏暂停和减少动态效果，不增加 PASS 结论。
- 不复制参考产品品牌资产，不下载或安装新字体，不新增运行库。仅保留 MIT 许可的 Animated Beam 源码改编并附许可证；背景与预览交互为自有实现。不编造样本、指标、研究背书或客户标识。
- 仪表盘、后端、服务 API、评测数据、指标计算、门禁逻辑、`MODEL_COLORS` 与模型 IDs 保持现有实现。无移动端设计或检查，无 commit／push。

## 2. 六个产品参考：实际浏览器已核实

六站均使用独立 Playwright 浏览器上下文打开，并检查首屏截图；截图位于 `frontend/test-results/reference-{linear,anthropic,vercel,openai,huggingface,langchain}-primary.png`。

| 来源 | 实际观察 | 采用的模式 |
|---|---|---|
| [Linear](https://linear.app) | 深色背景；左对齐两行实色标题、短说明与真实产品面板；观察到标题约 64px／64px、510 字重，以及下方微型编号和细线分栏。 | 字体层级、标题行距、细线分组和产品画面的优先级。不是渐变文字来源。 |
| [Anthropic](https://www.anthropic.com) | 当前为暖白页面，研究使命、编辑式内容分列、日期与分类建立出处关系。 | 可核查内容与明确来源。没有将该站描述为深色或渐变设计。 |
| [OpenAI](https://openai.com/zh-Hans-CN/) | 请求主页后跳转中文页；深色任务入口与研究主题形成清晰层级。 | 中文入口简洁、单一操作重点、研究内容与产品功能分组。 |
| [Vercel AI](https://vercel.com/ai) | 深色技术呈现、约 64px 实色标题、主实心／次描边操作、细线网格，模型轨道、操作流程与文件树等具体可视化。 | 将证据流程呈现为可读的技术仪器，而非普通功能清单。未验证其 SVG 动画实现，不声称复制其源码或动效机制。 |
| [Hugging Face](https://huggingface.co) | 深色呈现中使用模型、任务、库等具体对象；标题约 48px／48px、700 字重。 | 完整模型名称与真实评测对象建立技术可信度，避免抽象 AI 装饰。 |
| [LangChain](https://www.langchain.com) | 深色标题及 Build／Test／Deploy／Monitor／Iterate／Govern 生命周期表达。 | 能力按工作路径排列。首页仍使用已有四项中文能力，不扩充产品承诺。 |

以上字号是当前参考页面的观察值，并未照搬到项目。项目保留 64px／48px 的中文标题限制。

## 3. 组件与背景资源：核对结果

| 来源 | 核对内容 | 项目处理 |
|---|---|---|
| [Magic UI Animated Gradient Text](https://magicui.design/docs/components/animated-gradient-text) | 官方组件／注册源码与 MIT 许可；原生 CSS 文字裁切机制。 | 使用 `background-clip: text`，限定关键词和既有 Gold／文字 tokens；不使用示例紫色或持续滚动渐变。 |
| [Magic UI Animated Beam](https://magicui.design/docs/components/animated-beam) | 官方源码采用 SVG 中性轨迹与渐变光束；原组件依赖 Motion。 | 继续使用项目已有、已记录许可的 `BeamConnector`，以实测节点端点生成曲线连接错位节点；不安装原组件依赖。 |
| [Magic UI Magic Card](https://magicui.design/docs/components/magic-card)、[Aceternity Card Spotlight](https://ui.aceternity.com/components/card-spotlight) | 局部照明、边缘反馈；源码依赖需求已核对。 | 只借鉴容器层次；预览卡使用项目自有 CSS 边框与阴影反馈，没有复制卡片组件或新增 Motion。 |
| [Hover.dev Buttons](https://www.hover.dev/components/buttons)、[Cards](https://www.hover.dev/components/cards)、[Uiverse](https://uiverse.io) | 按钮与卡片交互示例；免费资源许可分别核对。 | 仅参考箭头小幅位移、边框与按压反馈，保持现有原生 CSS。 |
| [Shadcnblocks Hero 1](https://www.shadcnblocks.com/block/hero1)、[Feature 1](https://www.shadcnblocks.com/block/feature1) | 并列文字与产品图的结构。免费块许可并非 MIT。 | 仅参考布局关系，没有复制块源码。 |
| [Cool Backgrounds](https://coolbackgrounds.io)、[BGJar](https://bgjar.com)、[MeshGradient](https://meshgradient.com)、[SVG Backgrounds](https://www.svgbackgrounds.com/category/line-art/) | 细线、轮廓纹理、局部环境光；MeshGradient 的 WebGL 方式不适合本轮范围。 | 视觉对照，未下载或使用素材。BGJar 与 SVG Backgrounds 免费素材存在署名要求，不能当作无条件可复制资产。 |
| [GradientMagic](https://gradientmagic.com) | 网页访问失败，浏览器超时。 | 不作为已验证依据。 |

现有代码复用与许可见 `THIRD_PARTY_NOTICES.md`；本轮没有引入新的第三方包或素材。

## 4. 图库与字体研究：访问限制

| 来源 | 验证与限制 |
|---|---|
| [Godly／Recent](https://recent.design/?ref=godly) | `godly.website` 当前重定向至 Recent；实际浏览器确认 Typography、Motion、Editorial 分类。用作发现渠道，不复制品牌图形。 |
| [Lapa Ninja：Vercel](https://www.lapa.ninja/post/vercel-3/) | 网页内容确认黑色技术产品展示、Geist 字体与完整页面收录；实际浏览器遇到 Cloudflare 验证。不能声称完整浏览器视觉检查。 |
| [Landbook](https://land-book.com/) | 浏览器确认 Sections、Motion、Headlines 分类及 Dark Colors／Visible Borders 等筛选；Weave 条目可读，但未以其截图作为实现依据。 |
| [One Page Love：Midnight](https://onepagelove.com/midnight-2) | 已检查实际完整桌面截图：横向章节标题／说明、大幅产品演示、紧凑能力栏、环境光章节与末尾 CTA 的阅读节奏。只采用组织关系，不复制其暖白／紫色配色。 |
| [Siteinspire](https://www.siteinspire.com/) | 网页可读并确认 Typographic／Minimal／Grid Layout 分类；实际浏览器遇到安全检查。未见作品不作为视觉证据。 |
| [Awwwards](https://www.awwwards.com/) | 网页超时，浏览器导航失败。未作为已完成视觉研究。 |
| [Google Fonts：Noto Sans SC](https://fonts.google.com/noto/specimen/Noto+Sans+SC) | 浏览器确认简体中文支持与多字重。现有字体栈已包含 Noto Sans SC、微软雅黑和苹方，不增加字体加载。 |
| [Fontshare](https://www.fontshare.com/fonts/general-sans) | 浏览器只部分呈现目录与许可导航，字体详情未完整加载；没有据此更换字体。 |
| [Typewolf](https://www.typewolf.com/) | 浏览器确认字体配对与 Sans Serif／Mono 分类。采用文字角色分工，不照搬西文字体配对。 |

## 5. 模式到实现的对应关系

| 改进 | 参考模式 | 实现位置与适配 |
|---|---|---|
| Hero 精度 | Linear 的短标题／产品面板关系；Magic UI 的文字裁切机制 | `landing.css`：保留 64px／48px 和 600 字重，行高 1.2；仅关键词静态 Gold 渐变。渐变归因 Magic UI，不归因 Linear。 |
| 研究可信度 | Anthropic 的出处与编辑式信息组织 | `LandingPage.tsx`：保留真实产品图、快照 `20260930-1958`、非实时评测说明，以及单条证据与总体门禁的限制区分。 |
| 证据流程 | Vercel 的具体技术可视化；已有 Magic UI Beam 改编实现 | `EvidenceFlow.tsx`／`BeamConnector.tsx`／`landing.css`：五步错位排列、小编号、实测端点的 SVG 曲线、中性轨迹与有限 Gold 脉冲；既有两遍时钟及生命周期不变。 |
| 产品章节 | One Page Love 的章节与大幅演示组合；Shadcnblocks 的并列关系 | `LandingPage.tsx`／`landing.css`：真实图上方加功能与来源框头，保留独立出处行，用一个清楚的画面承载每章。 |
| 能力路径 | One Page Love 的紧凑能力栏；LangChain 的生命周期表达 | `LandingPage.tsx`／`landing.css`：四项既有能力组成连续条带，用序号、细分隔线与方向关系组织，不制造新功能。 |
| 收尾操作 | One Page Love 的末尾 CTA／环境光转换 | `landing.css`：已有入口采用低强度 Gold 局部照明，背景线条仍独立持续运动。 |

所有新增视觉表达均为前端呈现；流程图不是实时数据流，也不产生门禁判定或评测指标。
