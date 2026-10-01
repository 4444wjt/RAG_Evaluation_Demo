# Phase 4-B CSV 空值修复记录

日期：2026-10-01

Phase 3-B-10 冻结基线：`872d959`，标签 `phase-3-b-10-freeze-20261001`。

执行基线：Phase 4-A 提交 `a9c2a4995bb7009e6f041307a8196a5c5ecbc5fa`。

## 修改范围

- `backend/services/testsets.py`、`main.py`：仅在 CSV/TXT 分支的 `pd.read_csv(...)` 后增加 `.fillna("")`。
- `tests/test_testsets.py`：仅移除原 CSV 空值缺陷测试的 xfail 标记，保留原断言；新增 CSV/TXT 参数化回归，直接调用 backend 加载函数，并与 main 加载函数比较完整返回结构，不 mock 加载结果。
- `README.md`：说明空值规则，区分历史验收基线与本轮结果。
- `PHASE_4_B_RECORD.md`：记录范围、行为变化与验证结果。

未修改 JSON、benchmark、Ragas、门禁、模型调用、pipeline 架构、requirements 或依赖版本；未调整其他 xfail/skip 或已有测试逻辑。

## 行为变化

CSV/TXT 中被 pandas 识别的缺失值不再变为字面字符串 `"nan"`。空 question 沿用原规则过滤，空 ground_truth 为 `""`，空 case_type 默认 positive；返回结构与行顺序保持不变。

指标定义、score 公式及门禁规则不变。原含缺失值的输入重跑后，实际样本或样本类型修正可能改变结果；已有历史数据不重算、不覆盖。正常输入行为保持不变。

## 本地验证

```powershell
.\venv\Scripts\python.exe -m compileall -q main.py backend tests
.\venv\Scripts\python.exe -m pytest -q tests
```

- 编译结果：退出码 `0`，无错误输出。
- pytest 结果：退出码 `0`，`179 passed in 13.93s`，无失败、xfail 或 skip。
- 数量变化：原基线 `176 passed + 1 xfailed` 中的缺陷测试转为通过，另新增 CSV/TXT 两项 backend 回归，共 179 项通过；原缺陷断言保持不变。

本轮不调用真实 API，不暂存、不提交、不推送，不移动冻结标签；既有未跟踪文件保持原样。
