# Production Communication Hardening Plan

Goal: 世界级 生产级 完备的 agent调度通信协作机制

## Audit Summary

已审计组件：
- Mailbox v2 (InMemory/Redis) ✅ 基础功能完整
- StructuredMessage + MessageHeader ✅ 类型化消息
- RoutingTable (p2p/broadcast/pubsub/pipeline) ✅ 4拓扑
- ChannelPolicy (fnmatch ACL) ✅ 基础ACL
- CollaborationSignal (REVIEW_READY/APPROVED/FIX_NEEDED) ✅ 
- HumanChannel (ask/answer/post) ✅ 基本功能
- HealthMonitor ✅ 基本告警
- TraceContext ✅ 基础 tracing
- SendMessage + CheckMessages tools ✅ 
- DLQ (InMemory/Redis) ✅ 基本DLQ

测试：46个测试文件，393 passed，7 skipped

---

## P0 — 生产必备（安全性/可靠性/持久性）

### 1. Message Payload Size Validation
- **问题**: StructuredMessage.payload 无大小限制，1MB payload 可OOM
- **修复**: MessageHeader 加 max_payload_bytes，enqueue 时校验
- **文件**: `models/message.py`, `mailbox/memory.py`, `mailbox/redis.py`

### 2. InMemoryMailbox Persistence (Checkpoint)
- **问题**: 进程重启全部消息丢失
- **修复**: 可选的 JSONL checkpoint dump/load
- **文件**: `mailbox/memory.py`

### 3. Message Idempotency
- **问题**: 重试send可能产生重复消息
- **修复**: idempotency_key in MessageHeader，mailbox.enqueue 去重
- **文件**: `models/message.py`, `mailbox/memory.py`

### 4. DLQ Auto-Replay & Monitoring API
- **问题**: DLQ消息只能手动检查，无自动重放
- **修复**: dlq_replay() 方法 + dlq_summary() for Director
- **文件**: `mailbox/memory.py`, `mailbox/base.py`, `core/state_board.py`

### 5. HumanChannel Redis Backend
- **问题**: 多实例部署时 human 问答状态不共享
- **修复**: RedisHumanChannel 子类
- **文件**: `enterprise/human_channel.py`

---

## P1 — 重要（韧性/安全/可观测）

### 6. Per-Agent Rate Limiting
- **问题**: 无发送频率限制
- **修复**: Token bucket rate limiter on enqueue
- **文件**: `mailbox/memory.py` 或新 `mailbox/rate_limit.py`

### 7. ChannelPolicy Deny Rules
- **问题**: 只能allow-list，不能 "除X外全部允许"
- **修复**: 支持 `!prefix` deny 语法
- **文件**: `transport/channel_policy.py`

### 8. Structured Concurrency Guards
- **问题**: dequeue/ack 之间无锁保护
- **修复**: asyncio.Lock per mailbox
- **文件**: `mailbox/memory.py`

### 9. Observability Metrics
- **问题**: 无消息计数器/延迟直方图
- **修复**: MailboxMetrics dataclass + 导出接口
- **文件**: 新 `observability/mailbox_metrics.py`

### 10. Redelivery Backoff
- **问题**: nack 后立即重新入队，无延迟
- **修复**: nack_count → 指数退避 `retry_after` 时间戳
- **文件**: `mailbox/memory.py`

### 11. Circuit Breaker for Mailbox
- **问题**: 满邮箱持续返回False，调用方可能死循环
- **修复**: 连续满N次 → open circuit，冷却后 half-open
- **文件**: 新 `mailbox/circuit_breaker.py`

---

## P2 — 锦上添花（完整度/体验）

### 12. Message Filter/Query API
- peek(limit, msg_type=..., sender=...)
- **文件**: `mailbox/base.py`, `mailbox/memory.py`

### 13. Pipeline Causality Test Coverage
- 测试 pipeline 消息链的 parent_id/causality
- **文件**: `tests/test_stateboard_routing.py`

### 14. Message Priority Bump on Retry
- nack 3次 → priority += 1 (更紧急)
- **文件**: `mailbox/memory.py`

### 15. Redis Sentinel/Cluster Config
- **文件**: `mailbox/redis.py`

### 16. Comprehensive Integration Tests
- 端到端: send → route → claim → ack 完整流程
- Multi-agent collaboration 场景
- **文件**: `tests/test_integration.py`

---

## 执行顺序

Phase 1: P0.1 + P0.3 + P0.4 (消息安全基础)  
Phase 2: P0.2 + P1.6 + P1.7 (持久化+限流+ACL)  
Phase 3: P1.8 + P1.9 + P1.10 + P1.11 (韧性+可观测)  
Phase 4: P0.5 + P1.12 (HumanChannel Redis + 过滤器)  
Phase 5: P2 items (完善+测试)
