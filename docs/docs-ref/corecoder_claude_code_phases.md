# CoreCoder 单 Agent 对齐 Claude Code：分阶段详细设计（目录）

由于内容较长，本文档拆分为两部分：

- **[Part 1 — Phase 0~2](corecoder_claude_code_phases_part1.md)**
  - Phase 0：工具补齐（已完成，需测试加固）
  - Phase 1：执行可信（编辑降级链、自动验证闭环、Bash 安全、失败恢复）
  - Phase 2：上下文工程 + 流式基础（动态片段分类、四层压缩、spill-to-file、流式输出、tool pre-execution）

- **[Part 2 — Phase 3~4](corecoder_claude_code_phases_part2.md)**
  - Phase 3：扩展架构（MCP 客户端、coder 级 sub-agent、Hooks）
  - Phase 4：产品化体验（权限模式、Plan Mode、多模态）
  - 总体验收清单与涉及文件总览

方法论与总体架构见 **[corecoder_claude_code_method.md](corecoder_claude_code_method.md)**。
能力对照表见 **[claude_code_reference.md](claude_code_reference.md)**。
