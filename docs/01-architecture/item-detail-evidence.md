# Item Detail 与证据解释链路

## 目标

召回结果只携带候选 ID、分数和来源，不直接作为可展示事实。本增量在硬约束和目录校验通过后，批量调用只读 `item_detail` 工具，补全标题、描述、类目和结构化属性，并为每个结果生成可追溯解释。

```text
约束校验通过
→ item_detail 批量读取最终候选
→ 再次校验 tenant、status 与 ACL
→ EvidenceComposer 组合目录事实和约束证据
→ result 返回详情、explanation 与 evidence
```

解释链路不调用 LLM。`EvidenceComposer` 只能组合目录工具和 Constraint Engine 已验证的事实，不能增加来源中不存在的卖点。合成测试字段标记为 `synthetic`，不能伪装成权威事实。详情工具临时失败时，系统保留已经通过约束校验的基础结果并在 Receipt 中记录失败。

## 新增文件职责

- `src/seekora_agent/infrastructure/tools/item_detail.py`：使用 `@tool` 定义批量详情工具，通过 `ToolRuntime` 获取可信租户和 ACL，并返回结构化 Artifact。
- `src/seekora_agent/application/evidence.py`：把已验证候选、目录详情和约束证据组合为确定性结果解释。
- `tests/test_item_detail.py`：验证工具 Schema、批量读取、租户隔离和 ACL。
- `docs/01-architecture/item-detail-evidence.md`：记录详情与证据链路的边界和降级行为。
