"""角色 prompt 积木 —— 每角色一个可组合的 system prompt 片段。

戏台「一文件一 agent」下，``agents/<role>.json`` 的 ``prompts`` 字段按序引用本包
的符号（如 ``prompts.roles.coder:ROLE``），由 CoreCoderPattern 解析拼接成角色层
system prompt，叠加在 pattern 共享的 ``CORE_PRINCIPLES`` 之上。

设计：CORE 是 5 个 CoreCoder 角色共享的工作底座（探索/编辑/验证纪律）；ROLE 是
每角色专属的定位与侧重，让角色从「同一份 CORE」中分化出各自的行为。约束类硬规则
（文件落盘、审查取证）放 ``prompts.constraints``，可被任意角色组合引用。
"""

from __future__ import annotations
