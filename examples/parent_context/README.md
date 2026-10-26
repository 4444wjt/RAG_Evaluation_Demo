# 父条准备：构造样例

这五份文件是按契约人为构造的格式示例，不是真实法规、真实检索记录或真实用户案例。

- `source.txt`、`segments.json`：两条完整规则和一个标题，5段。
- `retrieval.json`：命中第七条第二段，`position:900`刻意表示召回排名，程序应展开源文位置2、3。
- `binding.json`：绑定本例真实字节；标题位置为1。
- `retrieval-invalid.json`：同时含已知段和未知段，预期整包REVIEW，不生成部分资料。

```powershell
python -B scripts/parent_context.py --source examples/parent_context/source.txt --segments examples/parent_context/segments.json --retrieval examples/parent_context/retrieval.json --binding examples/parent_context/binding.json --out work/parent-context-example
```

用`retrieval-invalid.json`替换`--retrieval`并指定另一个新输出目录，可验证未知段拒绝路径。可用自己的新资料替换全部输入；不要只修改ID或正文后沿用旧哈希。

完整说明见[`docs/integrations/parent-context.md`](../../docs/integrations/parent-context.md)。
