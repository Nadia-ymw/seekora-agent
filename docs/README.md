# 开发文档索引

文档按“先了解项目，再理解设计，然后开发和测试”的顺序组织。新增文档应放入对应分类，避免所有内容堆积在单一文件中。

## 00 概览

- [实施任务规划](00-overview/implementation-plan.md)：阶段目标、数据获取、里程碑和发布门禁。

## 01 架构

- [源码目录与依赖规范](01-architecture/source-layout.md)：分层目录、模块职责和允许的依赖方向。
- [运行时与请求链路](01-architecture/runtime-flow.md)：HTTP、SSE、Runtime、Tool、Session 和 Receipt 的协作关系。
- [Fast Path 设计与文件说明](01-architecture/fast-path.md)：意图解析、双路召回、RRF、约束和目录复核。
- [Grounded Deep Path 首个增量](01-architecture/deep-path.md)：复杂度路由、Retrieval Probe、结构化计划和多查询执行。
- [LangChain 迁移说明](01-architecture/langchain-migration.md)：StateGraph、StructuredTool、环境和扩展方式。

## 02 开发

- [本地开发指南](02-development/getting-started.md)：环境安装、CLI、API 和常用命令。
- [LLM 配置与新增文件说明](02-development/llm-configuration.md)：API Key、Provider 选择、降级路径和本增量文件职责。
- [Web 测试台使用说明](02-development/frontend-testing.md)：聊天页面、SSE 事件、模型调用识别和文件职责。
- [Deep Path 测试指南](02-development/deep-path-testing.md)：Fast/Deep 路由、SSE、Receipt 和复杂约束验收用例。
- [编码与扩展规范](02-development/coding-standards.md)：新增模块、工具和基础设施适配器的约定。

## 03 测试

- [测试策略](03-testing/test-strategy.md)：测试分层、当前覆盖和发布前检查。

## 文档维护规则

1. 文档使用英文文件名和中文正文，保证链接、脚本和跨平台工具稳定；
2. 架构决策写入 `01-architecture`，不要埋在开发教程中；
3. 可执行命令集中维护在 `02-development/getting-started.md`；
4. 测试门禁集中维护在 `03-testing/test-strategy.md`；
5. 代码路径变化时必须同步更新文档并运行链接/导入检查；
6. 根 README 只保留项目入口，详细内容链接到本目录。
