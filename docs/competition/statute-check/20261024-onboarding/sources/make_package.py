"""新法规接入：离线题包（不调用模型、不联网）。

按 登记.md（已锁定）从作者整理好的节选生成题包：四类款级陈述 × 四臂。
出题、补标签、规则与字面基线参照、费用估算都由 tools/competition/statute_onboarding.py 的同一套函数推出，
运行器在 template、run、analyze 前会再重建一遍逐项核对。本脚本只做入口检查和写出，节选规则始终强制执行。
只读节选、登记、复现归档与阶段 3 字面基线，不改任何文件。

用法：python -I work/statute-injection/onboarding-20261023/make_package.py --source <仓库内节选> --out <新的空目录>
      --chapter-two-first-article <官方文本第二章第一条的条号，不分章的填 1>
"""
import argparse
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.competition import statute_onboarding as so  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--chapter-two-first-article', required=True, type=int,
                        help='官方文本第二章第一条的条号（阿拉伯数字）；不分章的填 1')
    args = parser.parse_args()
    so.si.require(so.si.load(ROOT / so.JUDGE_SOURCE)['judges']['deepseek'] == so.JUDGE, '裁判配置与复现题包不一致')
    package = so.write_package(args.source, args.out, args.chapter_two_first_article)
    estimate = so.si.load(args.out / 'estimates.json')
    print(f"{package['statute']['title']}：陈述 {len(package['items'])} 条（错误 {package['totals']['errors']}，"
          f"对照 {package['totals']['controls']}），剔除 {len(package['excluded'])} 条，待发请求 {package['maxRequests']} 个")
    print(f"估算 ¥{estimate['expectedCostCny']}，预留上限 ¥{estimate['reservationUpperCny']}（按复现单价，运行当天须重新核价）")


if __name__ == '__main__':
    main()
