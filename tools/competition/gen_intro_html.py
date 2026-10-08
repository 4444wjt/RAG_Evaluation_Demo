# -*- coding: utf-8 -*-
"""把 docs/competition/project-introduction.md 原样转为带排版的 HTML，供 Chrome 打印 PDF。"""
import markdown
from pathlib import Path

SRC = Path("docs/competition/project-introduction.md")
OUT = Path("docs/competition/project-introduction.html")

md_text = SRC.read_text(encoding="utf-8")

body = markdown.markdown(
    md_text,
    extensions=["tables", "sane_lists", "nl2br"],
)

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>鉴真全域 VeriScope AI 作品介绍</title>
<style>
  @page {{ size: A4; margin: 22mm 20mm; }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans SC", sans-serif;
    color: #1f2430; background: #fff;
    font-size: 14px; line-height: 1.75; margin: 0;
  }}
  @media screen {{
    body {{ padding: 56px 68px; max-width: 960px; margin: 0 auto; }}
  }}
  p, li, a, dd, dt {{ overflow-wrap: anywhere; }}
  h1 {{
    font-size: 30px; font-weight: 800; color: #1a1a1a;
    margin: 0 0 6px; line-height: 1.3;
  }}
  h1 + blockquote {{
    margin: 0 0 18px; padding: 10px 16px;
    border-left: 4px solid #F2C879; background: #faf6ec;
    color: #6b5a2e; font-size: 15px;
  }}
  h2 {{
    font-size: 21px; font-weight: 700; color: #1a1a1a;
    margin: 30px 0 12px; padding-bottom: 8px;
    border-bottom: 2px solid #F2C879;
  }}
  h3 {{ font-size: 16px; font-weight: 700; margin: 20px 0 8px; color: #2a2f3a; }}
  p {{ margin: 8px 0; }}
  ul, ol {{ margin: 8px 0; padding-left: 24px; }}
  li {{ margin: 4px 0; }}
  strong {{ color: #111; }}
  a {{ color: #b8860b; text-decoration: none; }}
  blockquote {{
    margin: 12px 0; padding: 10px 16px;
    border-left: 3px solid #e6d3a3; background: #fbf8f0; color: #5a5544;
  }}
  code {{
    font-family: "Consolas", "Courier New", monospace;
    background: #f4f4f4; padding: 1px 5px; border-radius: 3px; font-size: 13px;
  }}
  pre {{ background: #f6f6f6; padding: 12px 14px; border-radius: 6px; overflow-x: auto; }}
  pre code {{ background: none; padding: 0; }}
  table {{ border-collapse: collapse; width: 100%; margin: 14px 0; }}
  th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
  th {{ background: #faf6ec; font-weight: 700; }}
  hr {{ border: none; border-top: 1px solid #eee; margin: 20px 0; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""

OUT.write_text(html, encoding="utf-8")
print("written:", OUT.resolve())
