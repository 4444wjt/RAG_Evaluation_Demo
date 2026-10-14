/** 品牌标志：一对直角引号「」框住三行文字，中间一行以句号「。」收尾，取「每一句回答都引得出原文」之意。
 *  与 public/favicon.svg 同形；颜色取 currentColor，其余两行用半透明表示尚未核对的句子。 */
export function BrandMark({ size = 28, className = '' }: { size?: number; className?: string }) {
  return <svg className={`brand-glyph ${className}`.trim()} width={size} height={size} viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path className="brand-glyph-open" d="M5 12.5V5h7.5" strokeWidth="2.6"/>
    <path className="brand-glyph-close" d="M27 19.5V27h-7.5" strokeWidth="2.6"/>
    <path className="brand-glyph-line" d="M10.5 10.5h11" strokeOpacity=".42" strokeWidth="2.4" pathLength={1}/>
    <path className="brand-glyph-line brand-glyph-cited" d="M10.5 16h7" strokeWidth="2.4" pathLength={1}/>
    <path className="brand-glyph-line" d="M10.5 21.5h6.5" strokeOpacity=".42" strokeWidth="2.4" pathLength={1}/>
    <circle className="brand-glyph-stop" cx="21.2" cy="17.4" r="1.75" strokeWidth="1.6"/>
  </svg>;
}
