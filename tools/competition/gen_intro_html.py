# -*- coding: utf-8 -*-
"""把 docs/competition/project-introduction.md 原样转为带排版的 HTML，供 Chrome 打印 PDF。"""
import posixpath
import re

import markdown
from pathlib import Path

SRC = Path("docs/competition/project-introduction.md")
OUT = Path("docs/competition/project-introduction.html")
# PDF 里的站内链接指向 Gitee 主仓库，避免打印成本机 file: 地址；图片仍用本地文件渲染。
REPO = "https://gitee.com/kkbushishou/rag-hallucination-evaluator/blob/master/"

md_text = SRC.read_text(encoding="utf-8")

body = markdown.markdown(
    md_text,
    extensions=["tables", "sane_lists", "nl2br"],
)


def repo_link(match):
    href = match.group(1)
    if re.match(r"^(https?:|mailto:|#)", href):
        return match.group(0)
    path, _, anchor = href.partition("#")
    resolved = posixpath.normpath(posixpath.join(SRC.parent.as_posix(), path))
    return 'href="' + REPO + resolved + ("#" + anchor if anchor else "") + '"'


body = re.sub(r'href="([^"]+)"', repo_link, body)


# 列宽沿用 pandoc 的约定：表头分隔行各列的横线数之比就是列宽之比；横线数相同的表仍按内容自动排。
separators = [
    [len(cell.strip().strip(":")) for cell in line.strip().strip("|").split("|")]
    for line in md_text.splitlines()
    if re.fullmatch(r"\|?(\s*:?-+:?\s*\|)+\s*:?-+:?\s*\|?", line.strip())
]
widths = iter(separators)


def size_columns(match):
    dashes = next(widths)
    if len(set(dashes)) == 1:
        return match.group(0)
    cols = "".join(f'<col style="width:{100 * d / sum(dashes):.1f}%">' for d in dashes)
    return f'<table class="sized"><colgroup>{cols}</colgroup>'


body = re.sub(r"<table>", size_columns, body)
assert next(widths, None) is None, "表格数与分隔行数不一致"

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>鉴真全域 VeriScope AI 作品介绍</title>
<style>
  @page {{
    size: A4; margin: 22mm 20mm;
    @top-left {{ content: "鉴真全域 VeriScope AI 作品介绍"; font: 9pt "Microsoft YaHei", sans-serif; color: #aaa; }}
    @bottom-right {{ content: counter(page) " / " counter(pages); font: 9pt "Microsoft YaHei", sans-serif; color: #888; }}
  }}
  @page :first {{ @top-left {{ content: none; }} }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans SC", sans-serif;
    color: #1f2430; background: #fff;
    font-size: 15px; line-height: 1.7; margin: 0;
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
  table {{ border-collapse: collapse; width: 100%; margin: 14px 0; font-size: 13.5px; line-height: 1.6; }}
  th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: left; vertical-align: top; }}
  th {{ background: #faf6ec; font-weight: 700; }}
  thead {{ display: table-header-group; }}
  td:first-child, th:first-child {{ white-space: nowrap; }}
  table.sized {{ table-layout: fixed; }}
  table.sized td, table.sized th {{ white-space: normal; }}
  td a {{ overflow-wrap: normal; }}
  h1, h2, h3 {{ break-after: avoid; }}
  hr {{ border: none; border-top: 1px solid #eee; margin: 20px 0; }}
  img {{ display: block; max-width: 100%; height: auto; margin: 12px auto; }}
  tr, img, blockquote {{ break-inside: avoid; }}
  p:has(+ p > img) {{ break-after: avoid; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""

OUT.write_text(html, encoding="utf-8")
print("written:", OUT.resolve())
