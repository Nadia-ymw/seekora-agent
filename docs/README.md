# 开发文档索引

文档按“先了解项目，再理解设计，然后开发和测试”的顺序组织。新增文档应放入对应分类，避免所有内容堆积在单一文件中。

## 00 概览

- [三层模型搜索推荐 Agent 重构主控执行规划](00-overview/three-layer-agent-refactoring-execution-plan.md)：当前生效的重构阶段、核心契约、过滤式 HNSW、模型职责、质量门禁、回滚和防漂移规则。
- [单实例本地 MVP 后续开发规划](00-overview/local-mvp-development-plan.md)：M1/M2 历史基线、千问 Embedding 选型和原 MVP 范围；原 M3/M4 排期已由重构主控规划取代。
- [实施任务规划](00-overview/implementation-plan.md)：阶段目标、数据获取、里程碑和发布门禁。
- [技术路线实现状态与后续开发规划](00-overview/technical-route-status-and-roadmap.md)：逐项对照技术路线，区分代码完成度、门禁状态和后续增量。

## 01 架构

- [源码目录与依赖规范](01-architecture/source-layout.md)：分层目录、模块职责和允许的依赖方向。
- [运行时与请求链路](01-architecture/runtime-flow.md)：HTTP、SSE、Runtime、Tool、Session 和 Receipt 的协作关系。
- [Fast Path 设计与文件说明](01-architecture/fast-path.md)：意图解析、多源召回、RRF、约束和目录复核。
- [Grounded Deep Path 首个增量](01-architecture/deep-path.md)：复杂度路由、Retrieval Probe、结构化计划和多查询执行。
- [Deep Path DAG 执行设计](01-architecture/dag-execution.md)：节点依赖、并发限制、停止条件、故障降级和执行凭据。
- [Session Intent、Profile 与 Consent](01-architecture/profile-consent.md)：短期意图、长期画像、显式授权和隐私边界。
- [多轮 Session 约束上下文](01-architecture/session-context.md)：约束修改、追加、删除、清空与新任务隔离。
- [Constraint 生命周期与最小放宽](01-architecture/constraint-lifecycle.md)：约束元数据、继承、过期、挂起、冲突与确认式放宽。
- [Embedding 索引与语义复核](01-architecture/semantic-retrieval.md)：向量端口、版本化索引、Embedding/Cross-Encoder Challenger 和降级边界。
- [SQLite Session 与 Receipt](01-architecture/sqlite-session-receipt.md)：短期状态、执行回执、TTL、裁剪与并发版本控制。
- [行为反馈与授权召回](01-architecture/behavior-feedback.md)：反馈事件、幂等写入、双重授权和行为召回边界。
- [服务端曝光清单与反馈归因](01-architecture/exposure-validation.md)：曝光生成、身份关联、服务端归因和删除传播。
- [行为事件持久化队列](01-architecture/event-pipeline.md)：SQLite 队列、迟到策略、机器人过滤和事件重放。
- [Item Detail 与证据解释链路](01-architecture/item-detail-evidence.md)：详情补全、ACL 复核、证据组合和降级。
- [请求幂等与 SSE 回放](01-architecture/request-idempotency.md)：请求指纹、执行占用、冲突保护和跨重启回放。
- [曝光行为排序评测样本与特征契约](01-architecture/ltr-training.md)：分级标签、成熟窗口、防泄漏特征和时间切分；不进行 LTR 模型训练。
- [LangChain 迁移说明](01-architecture/langchain-migration.md)：StateGraph、`@tool`、ToolNode、ToolRuntime 和扩展方式。

## 02 开发

- [本地开发指南](02-development/getting-started.md)：环境安装、CLI、API 和常用命令。
- [本地测试账户](02-development/demo-account.md)：预置测试身份、初始 Profile、调用方式和安全边界。
- [KuaiSearch-Lite 电子产品数据处理](02-development/kuaisearch-data.md)：外部数据目录、流式转换、真实统计和字段映射。
- [LLM 配置与新增文件说明](02-development/llm-configuration.md)：API Key、Provider 选择、降级路径和本增量文件职责。
- [本地语义模型配置](02-development/semantic-models.md)：可选依赖、向量索引构建和 Challenger 启用方式。
- [Web 测试台使用说明](02-development/frontend-testing.md)：聊天页面、SSE 事件、模型调用识别和文件职责。
- [Deep Path 测试指南](02-development/deep-path-testing.md)：Fast/Deep 路由、SSE、Receipt 和复杂约束验收用例。
- [编码与扩展规范](02-development/coding-standards.md)：新增模块、工具和基础设施适配器的约定。

## 03 测试

- [测试策略](03-testing/test-strategy.md)：测试分层、当前覆盖和发布前检查。
- [本地并行召回与 SQLite 向量索引性能报告](03-testing/local-recall-performance-report.md)：5 万商品暖态微基准、并行关键路径、旧 JSON 方案提速原因和后续性能门禁。
- [M3 搜索推荐检索策略与测试说明报告](03-testing/m3-search-retrieval-development-summary.md)：完整检索链路、Active/Challenger/降级策略、测试过程、通过与失败指标及后续建议。
- [M3 本地混合召回质量门禁报告](03-testing/m3-recall-quality-report.md)：固定查询集、BM25+TF-IDF 与 BM25+Qwen 对照以及加权 RRF 实验结果。

## 文档维护规则

1. 文档使用英文文件名和中文正文，保证链接、脚本和跨平台工具稳定；
2. 架构决策写入 `01-architecture`，不要埋在开发教程中；
3. 可执行命令集中维护在 `02-development/getting-started.md`；
4. 测试门禁集中维护在 `03-testing/test-strategy.md`；
5. 代码路径变化时必须同步更新文档并运行链接/导入检查；
6. 根 README 只保留项目入口，详细内容链接到本目录。
