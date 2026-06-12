# Test Coverage Audit: Communication / Collaboration Stack

**Date:** 2026-06-12  
**Scope:** Mailbox, Routing, Channel Policy, Collaboration, Human Channel, Health Monitor, Integration, Tools, Resident, Message Model, Tracing, DLQ  
**Examiner:** Automated audit

---

## File Inventory (Existence Check)

| Expected File | Status | Actual File(s) |
|---|---|---|
| `tests/test_mailbox.py` | **MISSING** | Replaced by `test_mailbox_memory.py` and `test_mailbox_redis.py` |
| `tests/test_message.py` | EXISTS | 3.1 kB, 10 tests |
| `tests/test_trace.py` | EXISTS | 3.9 kB, 10 tests |
| `tests/test_routing.py` | EXISTS | 3.4 kB, 9 tests |
| `tests/test_channel_policy.py` | EXISTS | 2.7 kB, 9 tests |
| `tests/test_collaboration.py` | EXISTS | 4.9 kB, 5 tests |
| `tests/test_human_channel.py` | EXISTS | 3.3 kB, 7 tests |
| `tests/test_health_monitor.py` | EXISTS | 4.0 kB, 7 tests |
| `tests/test_integration.py` | EXISTS | 12 kB, 13 tests |
| `tests/test_tools.py` | EXISTS | 6.3 kB, 15 tests |
| `tests/test_resident.py` | EXISTS | 6.9 kB, 13 tests |

**Supporting files also found:**
- `tests/test_stateboard_mailbox.py` (StateBoard mailbox v2 integration, 12 tests)
- `tests/test_stateboard_routing.py` (routing + DLQ integration, 7 tests)
- `tests/test_check_dlq.py` (CheckDLQTool, 3 tests)

---

## 1. `tests/test_routing.py` — RoutingTable

### Covered Scenarios
- Point-to-point routing (agent A to agent B)
- Broadcast (`*` wildcard)
- Pub/Sub by topic (`topic:...`)
- Type-based multicast (`type:reviewer`)
- Pipeline routing (`"b > c"` syntax)
- Unregister agent removes from routing
- Unsubscribe removes topic subscription
- Manual `add_route` of a `RouteEntry`
- Empty pub/sub topic returns no targets

### NOT Tested (Gaps)
- **Race condition on concurrent register/unregister:** No concurrent-access tests. The `RoutingTable` has no lock in its test setup; if it uses shared state without synchronization, that is untested.
- **Duplicate registration:** What happens if an agent ID is registered twice?
- **Overlapping routes:** If both a `type:reviewer` route and a topic match the same message, which wins?
- **Pipeline with unknown agents in stages:** `resolve_pipeline_stages` is tested for "b > c" but not for malformed stage strings, empty stages, or stages referencing unregistered agents.
- **Route priority / ordering:** No test for what happens when multiple route patterns match the same message.
- **Large-scale routing:** No performance or stress tests (e.g., hundreds of agents).
- **Wildcard subscriptions** (e.g., `subscribe("a", "file_*")`).

### Fragility
- Tests use `.from_text(...)` which is a legacy convenience; the real system uses `StructuredMessage.command/signal/event`. This is fine for unit-level routing tests, but does not exercise the routing of typed messages.

---

## 2. `tests/test_channel_policy.py` — ChannelPolicy

### Covered Scenarios
- Exact sender-to-recipient match
- Wildcard sender (`*` to specific recipient)
- Wildcard recipient (specific sender to `*`)
- Prefix wildcard (`coder-*` to `reviewer-*`)
- Role-marker pattern (`*_leader`)
- `assert_allowed` raises `ChannelPolicyError` on denial
- `DEFAULT_GLOBAL_POLICY` semantics (all-to-all is allowed except agent-to-same-type)
- `DEFAULT_TEAM_POLICY` semantics (team leader can talk to anyone on team; team members cannot reach director)
- `to_dict` / `from_dict` round-trip serialization

### NOT Tested (Gaps)
- **Empty policy:** `ChannelPolicy({})` — what happens when no rules match?
- **Policy with no `*` wildcard at all:** Only the default policies are verified; a fully restrictive policy is not tested.
- **Concurrent policy checks:** No thread/async-safety testing.
- **Policy validation at construction:** What if malformed patterns (e.g., invalid glob syntax) are passed?
- **Negative patterns** (deny rules, if supported).
- **Policy merging / composition** (combining global + team policy is used in practice but not explicitly tested).
- **Serialization of edge cases:** Empty rules dict, deeply nested patterns.

### Fragility
- The default policies are tested by string assertions on specific agent pairs. If the default policy semantics change (e.g., to be more or less permissive), tests will break. This is largely by design.

---

## 3. `tests/test_collaboration.py` — Collaboration

### Covered Scenarios
- Parsing collaboration signal from text (`TASK_REVIEW_READY[task-id]: tests passed = N`)
- `task_id_from_resident_id()` extraction
- `_should_use_collaborative_mode()` with `on`/`off`/auto modes
- Approval signal processing: `REVIEW -> COMPLETED`, stops residents
- Fix-needed signal processing: `REVIEW -> FIX_NEEDED`, preserves coder, sleeps reviewer
- `iteration_history` tracking for reviewer actions
- `get_project_context()["recent_errors"]` populated on fix
- Wrong task marker is ignored (signal for non-matching task ID is skipped)

### NOT Tested (Gaps)
- **Multiple signals in flight:** Only a single signal is sent in each test; no test for concurrent approvals + fix-neededs.
- **Timeout on collaboration:** What happens when no signal arrives within a deadline?
- **Re-review after fix:** The cycle `FIX_NEEDED -> coder fixes -> REVIEW_READY -> review -> COMPLETED` is not tested end-to-end.
- **Collaboration with multiple reviewers:** Only a single reviewer is simulated.
- **Signal from unknown agent:** What happens if a non-reviewer sends "approved"?
- **Parse failures:** `parse_collaboration_message()` with malformed text (missing brackets, missing colon, etc.) is not tested.
- **Escalation:** What if the reviewer is unresponsive?
- **Task graph with multiple tasks in REVIEW at once.**

### Fragility
- `_runner_with_task` test helper creates a stub `OrchestratorRunner` with mock `_residents` dict. It relies on `_process_collaborative_messages()` being called manually. This is a white-box test that can break if the processing internals change.
- The test uses `StructuredMessage.signal(...)` but sends it via `board.send_structured(...)`. The actual orchestration path sends a text message that is then parsed by `parse_collaboration_message`. This bypasses the parsing step for the processing tests.

---

## 4. `tests/test_human_channel.py` — Human Channel

### Covered Scenarios
- `ask_human` creates a question with a generated ID
- `reply_human` records the answer and delivers it to director via mailbox
- `human_post` creates a message routed to director
- `human_post` with a specific `target_team`
- `get_human_conversation` combines asks and posts chronologically
- `snapshot()` includes human activity signals (post count, recent posts)
- Persistence round-trip (`to_dict()` -> `from_dict()`)

### NOT Tested (Gaps)
- **Multiple unanswered questions:** What happens when several agents ask questions concurrently?
- **Reply to a non-existent question ID:** `reply_human("nonexistent", "answer")` — should return False or raise.
- **Duplicate reply:** Replying twice to the same question.
- **Human channel with budget/rate limiting.**
- **Timeout on human response:** No test that a pending question expires.
- **Large human conversation:** Stress test with many questions/posts.
- **Thread safety:** Concurrent `ask_human` / `reply_human` calls.

### Fragility
- The persistence test constructs a fresh `StateBoard` from `to_dict`. If `StateBoard.from_dict` changes signature, this test breaks.
- `messages_for("director")` checks the legacy pending-mail list. If the mailbox v2 migration removes legacy delivery, this test needs updating.

---

## 5. `tests/test_health_monitor.py` — HealthMonitor

### Covered Scenarios
- Detection of long-running agent (exceeds `max_elapsed_s`)
- Detection of step count exhaustion
- Detection of consecutive tool failures
- No alert for idle agents
- Once-per-agent alert suppression (no duplicate alerts)
- Alert cleared on recovery and re-alert on new failure
- `start()` / `stop()` lifecycle

### NOT Tested (Gaps)
- **Multiple agents in alert simultaneously.**
- **Custom alert callbacks** (if the monitor supports them).
- **Graceful handling of monitor errors** (e.g., `_check` throws on a malformed board state).
- **Concurrent start/stop:** What if `start()` is called twice?
- **What if `max_elapsed_s` is zero or near-zero?** Boundary tests.
- **HealthMonitor integration with the HealthCheck endpoint** (there is a `test_health.py` but it may not use the monitor).
- **Alert message includes actionable data:** The content is checked for agent name and Chinese strings, but no test verifies the severity level or structured format.

### Fragility
- Strings like `运行` and `连续 5 次工具失败` are hardcoded Chinese messages. If the system is internationalized or the messages change, these tests break silently.
- `test_start_stop` sleeps for 150 ms and expects no failures. This is a timing-dependent test that may flake on CI under load.

---

## 6. `tests/test_integration.py` — Integration Tests

### Covered Scenarios
- **SpawnAgent full chain:** Successful spawn updates state board, sets task to COMPLETED, records artifacts, registers agents.
- **Spawn failure:** Error during delegation sets task to FAILED with error and recommendation.
- **Spawn respects dependencies:** Task with unmet dependencies raises exception and does not delegate.
- **Spawn with dependency context:** Upstream artifacts included in spawned agent input.
- **Message roundtrip:** `SendMessageTool` -> mailbox -> `CheckMessagesTool` reads and clears.
- **Broadcast message:** `*` recipient reaches all registered agents.
- **Message injection into spawn input:** Pending messages included in `_build_input`.
- **Replan tool:** Splits a task into sub-tasks, rewires dependency chain, updates board snapshot.
- **Director decision flow:** `ShowStateTool` then `FinalizeTool`.
- **Ask Human integration:** `AskHumanTool` records question in `_human_channel`.
- **Budget exhaustion and respect:** `Budget` token/time/step limits; `has_actionable` respects budget.

### NOT Tested (Gaps)
- **End-to-end resident lifecycle:** No test that spawns an agent, waits for it to complete via mailbox messages, and collects results without mocks.
- **Multiple spawns in parallel:** Concurrent task spawns.
- **DLQ recovery flow:** Integration test where a message goes to DLQ and the CheckDLQTool retries it.
- **Replan failure:** What if the LLM returns malformed JSON?
- **Cross-objective message delivery:** Two StateBoards communicating — not tested.
- **Human reply during orchestration:** The `AskHumanTool` is tested, but the full flow `agent asks -> human replies -> agent resumes` is not.
- **Budget exhaustion mid-spawn:** Task partially completed then aborted due to budget.
- **Monitor agent end-to-end:** No integration test with the monitor agent tooling.

### Fragility
- Uses `MockContext` and `MockDeps` extensively. These mocks may drift from the actual `RunContext` API.
- `test_spawn_with_dependency_context` relies on specific strings like "Upstream artifacts" in the input text.

---

## 7. `tests/test_tools.py` — Tool Unit Tests

### Covered Scenarios
- **ShowStateTool:** Schema and spec (concurrency_safe), basic invoke.
- **FinalizeTool:** Basic invoke, missing summary raises `PermanentToolError`.
- **SendMessageTool:** Basic invoke delivers via mailbox v2; collaboration signal text is parsed and delivered as structured `SIGNAL` message.
- **SpawnAgentTool:** Schema, `_build_input` with context, `_extract_artifacts`.
- **ReplanTool:** Schema only (no invoke).
- **AskHumanTool:** Basic invoke records question.
- **CheckMessagesTool:** No messages returns "no new messages"; with messages returns correct count; `clear=False` leaves messages in mailbox; `messages_for` isolation by recipient.

### NOT Tested (Gaps)
- **FinalizeTool on already-finalized board:** What happens when finalize is called twice?
- **SendMessageTool to non-existent agent:** No test for routing to an unregistered agent ID.
- **SendMessageTool with `topic:` or `type:` target.**
- **Spam/large messages:** What happens with oversized payloads?
- **ReplanTool invoke:** Only schema is tested, not the actual replan logic.
- **AskHumanTool with invalid context (missing board).**
- **All tools with invalid/malformed `ctx.deps`.**

### Fragility
- `MockContext` assigns arbitrary attributes; if `RunContext` validation changes, these tests will not catch it.
- The collaboration signal test (`test_collaboration_signal_delivered_as_structured_message`) does NOT go through `parse_collaboration_message`; it sends raw text that happens to match the signal format.

---

## 8. `tests/test_resident.py` — ResidentAgent

### Covered Scenarios
- `ResidentState.to_dict()` serialization
- `ResidentAgent._build_input()` with full and minimal input dicts
- **Lifecycle:** start -> send -> process -> stop -> reply via mailbox v2
- **Idle timeout:** Agent stops after `max_idle_s`
- **Multiple messages:** Resident processes two messages in sequence, accumulates transcript
- **Error handling:** `RuntimeError` from runner is recorded in `error_count`, status set to "stopped", error reply sent
- **StateBoard resident management:** register, get, update, list by type, snapshot, report

### NOT Tested (Gaps)
- **Wake from sleep:** `_should_wake` (or similar) is not tested; there is a `sleep()` / `wake()` API on the stub but no test that simulates the sleep-wake cycle on a real resident.
- **Concurrent message delivery:** `send()` called while processing another message.
- **Resident recovery:** What happens after an error — can a resident be restarted?
- **Name collision:** Registering two residents with the same `resident_id`.
- **Max retry on task for a resident** (the error handling test does one failure and gives up).
- **Serialization round-trip of `ResidentState` with complex fields** (e.g., transcripts).
- **Memory growth:** Accumulating transcripts is tested to be non-zero, but no cap/threshold test.
- **Budget integration:** Resident respecting `max_steps` or token budget.

### Fragility
- The lifecycle test `await asyncio.sleep(0.5)` is a timing-dependent sleep. Under CI load, 0.5s may not be enough or may be far too much.
- `MockRunner` returns a `MagicMock` for `final_output` and `metadata`. If the runner's result shape changes, this test silently passes with wrong data.
- `test_error_handling` patches `runner._run_resident_single` after construction, which works but is fragile if the constructor does eager setup.

---

## 9. `tests/test_mailbox_memory.py` — InMemoryMailbox

### Covered Scenarios
- Basic enqueue/dequeue
- Priority ordering (HIGH > NORMAL > LOW)
- Back-pressure (queue full returns False)
- Ack prevents redelivery
- Nack (3x) moves to DLQ after the third nack
- Peek does not consume
- Expired messages dropped at dequeue time
- `clear_expired` removes messages that expired after enqueue
- Size tracking (0 -> 1 -> 0)

### NOT Tested (Gaps)
- **Nack with fewer than 3 attempts:** After 1 and 2 nacks, message is re-delivered — this IS tested for 1 and 2 but the max-retry boundary is implicitly 3 (hardcoded in the mailbox implementation). If max_retries changes, tests break.
- **Concurrent enqueue/dequeue:** Async race conditions.
- **DLQ peek with multiple messages:** Only single-message DLQ tested.
- **DLQ purge / retry from DLQ.**
- **Enqueue after mailbox is destroyed / stopped.**
- **Mixed priority with expired messages.**
- **Large batch sizes.**
- **TTL = 0 or negative.**

### Fragility
- Max retry count (3) is hardcoded in the test expectations; this is acceptable for a unit test but should be documented.

---

## 10. `tests/test_mailbox_redis.py` — RedisMailbox

### Covered Scenarios
- Basic enqueue (verifies `xadd` is called)
- Back-pressure via `xlen` check
- Dequeue returns empty from empty stream
- Dequeue with mocked message data (verifies JSON deserialization, delivery_count increment)
- Ack via `xack`
- Peek via `xrevrange`
- Expired messages silently dropped at enqueue

### NOT Tested (Gaps)
- **Redis connection failure:** What happens when Redis is unreachable?
- **Nack / DLQ:** `RedisMailbox` may not implement DLQ the same way as `InMemoryMailbox`.
- **Consumer group creation:** `xgroup_create` is mocked but no test verifies it is called at startup.
- **Stream trimming / memory management.**
- **Message re-delivery on disconnect** (pending entries in consumer group).
- **TTL-based cleanup via Redis native expiry:** No test that messages with TTL are cleaned by Redis itself.
- **Concurrent consumer groups** (multiple agents on same stream).
- **Serialization failure:** What if a message's `to_dict()` produces non-JSON-serializable data?
- **`RedisMailbox` does not implement `ack`/`nack` parity with `InMemoryMailbox`:** The Redis tests do not test nack or DLQ at all — a gap vs. the in-memory variant.

### Fragility
- Entire suite is `@pytest.mark.skipif(not REDIS_AVAILABLE, ...)` but tests use a mock (`fake_redis`), so they would never actually need a real Redis server. The skip guard is misleading.
- The global `__import__` calls in `test_expired_enqueue_dropped` and `test_dequeue_with_message` are unusual and fragile.

---

## 11. `tests/test_message.py` — StructuredMessage

### Covered Scenarios
- `MessageHeader` default creation (msg_id, type, priority, delivery_count, expiry check)
- `with_delivery()` increments delivery count immutably
- Expiry with TTL (past `created_at + ttl_s` is expired)
- No expiry when TTL is unset
- `StructuredMessage.from_text()` back-compat factory
- `StructuredMessage.command()` factory (HIGH priority, action/params payload)
- `StructuredMessage.signal()` factory (SIGNAL type, signal/payload fields)
- `StructuredMessage.event()` factory (EVENT type, broadcast)
- Round-trip serialization (`to_dict()` / `from_dict()`)
- Broadcast detection based on recipient `*`

### NOT Tested (Gaps)
- **`MessageType` validation:** What if an invalid `MessageType` string is passed?
- **`Priority` edge values:** `URGENT` (if it exists) or `LOW`.
- **`MessageHeader.from_dict` with missing/extra fields** — deserialization robustness.
- **`StructuredMessage` with empty sender/recipient.**
- **`StructuredMessage` with special characters in text/payload.**
- **Serialization of `TraceContext` inside header.**
- **Deep copy / immutability guarantees.**
- **Large payload (message size limits).**

### Fragility
- None of significance.

---

## 12. `tests/test_trace.py` — TraceContext

### Covered Scenarios
- `TraceContext` default creation (trace_id, span_id, parent_span_id)
- Child span preserves trace_id, sets parent_span_id, includes name in span_id
- Round-trip dict serialization (equality check)
- Inject into message (trace_id and parent_span_id set on header)
- StateBoard `start_trace()` and `get_trace()`
- Child trace from parent
- Trace propagated into message via `send_structured()`
- Trace propagated from task to agent span
- Trace logged in event payload
- `propagate_trace` does not override existing trace_id on message

### NOT Tested (Gaps)
- **Nested span hierarchy:** Three or more levels of child spans.
- **End-to-end trace flow through mailbox:** Trace context propagated through enqueue/dequeue.
- **Trace in DLQ messages.**
- **Trace serialization in event persistence.**
- **Missing/malformed trace context on inbound messages** (resilience).
- **Concurrent trace creation** (race conditions on trace_id generation).
- **TraceContext `to_dict()` / `from_dict()` with non-default values** (child spans, named span_ids).

### Fragility
- Tests use `board.start_trace("t1")` and then check `board.get_trace("t1")`. This is a white-box test.
- `test_propagate_trace_does_not_override_existing` uses `object.__setattr__` to bypass the property — this is fragile if the header implementation changes.

---

## 13. Additional Related Files

### `tests/test_stateboard_mailbox.py` (12 tests)
Covers mailbox v2 integration at the StateBoard level.

| Covered | NOT Covered |
|---|---|
| send/claim/ack/nack/peek | Partial re-read (tombstone handling) |
| Broadcast via mailbox | Concurrent mailbox access |
| Legacy backward compat | Malformed messages |
| Back-pressure on full queue | Per-agent mailbox isolation at scale |
| DLQ monitoring (nack 3x, dlq_size, dlq_peek) | DLQ purge, DLQ retry |
| Mailbox isolation (agent A/B) | Mixing mailbox v1 and v2 |

### `tests/test_stateboard_routing.py` (7 tests)
Covers routing through StateBoard.

| Covered | NOT Covered |
|---|---|
| Broadcast via routing table | Routing with mixed v1/v2 |
| Pub/Sub via routing table | Pipeline routing through StateBoard |
| Type multicast | Multiple subscriber topics |
| Unsubscribe | Large routing tables |
| Unregister agent | Overlapping route types |
| DLQ inspect (with and without messages) | DLQ retry/resend |

### `tests/test_check_dlq.py` (3 tests)
Covers the CheckDLQTool.

| Covered | NOT Covered |
|---|---|
| Empty DLQ | DLQ with multiple agents |
| Single dead letter found | Retry from DLQ |
| Limit parameter | Purge/clear DLQ |
| | DLQ alerting / notification |

---

## 14. Cross-Cutting Coverage Gaps

### Concurrency and Thread Safety
No test in any file exercises concurrent mailbox operations, concurrent routing lookups, or concurrent resident access. All tests run sequentially in a single event loop thread.

### Timeouts and Expiry
- `HealthMonitor` tests time-based alerting with a fixed `time.time() - 400` but do not test what happens when an agent is right at the boundary (299s vs 300s).
- `test_mailbox_memory` tests TTL-based message expiry only for the in-memory variant.
- No test verifies the system behavior when a collaboration signal times out.

### DLQ (Dead Letter Queue)
- Nack pattern is exercised in `test_mailbox_memory`, `test_stateboard_mailbox`, `test_stateboard_routing`, and `test_check_dlq`.
- **Missing:** DLQ purge, DLQ retry (re-enqueue from DLQ), DLQ alerting to director/monitor.
- **Missing:** DLQ for the Redis variant.
- **Missing:** DLQ with different max-retry configurations.

### Retry
- Nack (up to 3 retries) is the only retry mechanism tested.
- **Missing:** Exponential backoff, retry with delay, retry budget, retry with different priorities.
- **Missing:** `SpawnAgentTool` `RetryableToolError` is tested once in integration, but the actual retry loop on the orchestrator side is not.

### Expiry
- StructuredMessage TTL tested in `test_message.py` and `test_mailbox_memory.py`.
- **Missing:** Expiry at the routing layer (should expired messages still be routed?).
- **Missing:** Expiry of human questions.
- **Missing:** Expiry of collaboration signals.

### Resilience and Error Paths
- **Missing:** What happens when `StateBoard.send_structured` gets an unregistered recipient?
- **Missing:** What happens when `InMemoryMailbox.nack` is called on a non-existent msg_id?
- **Missing:** Redis connection loss mid-operation.
- **Missing:** Invalid/envelopes parsing failures in mailbox dequeue.
- **Missing:** Corrupt message data in the mailbox.

### Monitoring and Observability
- HealthMonitor checks specific conditions but no test verifies integration with the orchestration loop (e.g., director reacting to health alerts).
- No test for metrics emission during mailbox operations.
- No test for structured logging during routing decisions.

### Collaboration
- No end-to-end test for the full collaborative review cycle (submit -> review -> approve/reject -> fix -> resubmit -> complete).
- No test for timeout of collaboration signals.
- No test for collaboration with more than one reviewer.

---

## 15. Fragility Summary

| Test File | Fragile Element |
|---|---|
| `test_collaboration.py` | Uses `_process_collaborative_messages()` directly (white-box); bypasses parsing step |
| `test_health_monitor.py` | Hardcoded Chinese alert strings (`"运行"`, `"连续 5 次工具失败"`); timing-dependent `asyncio.sleep(0.15)` |
| `test_integration.py` | `MockContext`/`MockDeps` may drift from `RunContext`; string-based matching ("Upstream artifacts") |
| `test_tools.py` | Same `MockContext` drift issue; collaboration signal test bypasses parser |
| `test_resident.py` | Timing-dependent `asyncio.sleep(0.3/0.5)`; `MockRunner` returns `MagicMock.result` |
| `test_mailbox_redis.py` | Skip guard (`@skipif(not REDIS_AVAILABLE)`) is misleading since tests use mocks; `__import__()` calls in test body |
| `test_trace.py` | `object.__setattr__` to bypass header immutability |

---

## 16. Summary

### Overall Coverage: MODERATE

The communication/collaboration stack has broad unit-level coverage across individual components (mailbox, routing, policy, messages, tracing). Integration tests cover the main happy paths (spawn agent, message round-trip, replan, finalize, health alerts, DLQ inspect).

### Critical Gaps (Highest Priority to Address)

1. **No concurrency tests anywhere** -- single-threaded sequential tests do not reflect real async orchestration.
2. **RedisMailbox lacks DLQ parity** with InMemoryMailbox -- no nack, no DLQ tests at all.
3. **Collaboration end-to-end cycle not tested** -- the review-approve-fix-resubmit-complete flow is only partially tested.
4. **No retry budget or backoff tests** -- the 3-nack DLQ threshold is hardcoded and untested for configurability.
5. **No timeout tests for collaboration or human channel** -- what happens when an agent never responds or a human never replies?
6. **DLQ recovery (retry/purge/re-enqueue) is untested** -- the DLQ can be inspected but cannot be acted upon in tests.
7. **`MockContext` in integration/tool tests** -- these mocks will silently break if the production context API changes.
