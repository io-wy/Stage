"""Tests for the persistence layer (EventRecorder, StateSnapshotter, SessionResumer)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openagents_orchestration.persistence.event_recorder import EventRecorder
from openagents_orchestration.persistence.state_snapshotter import StateSnapshotter
from openagents_orchestration.persistence.session_resumer import SessionResumer
from openagents_orchestration.state_board import StateBoard, Budget


class TestEventRecorder:
    def test_append_and_flush(self, tmp_path: Path):
        recorder = EventRecorder(tmp_path, "test-session")
        seq = recorder.append("task.updated", task_id="t1", status="running")
        assert seq == 1
        recorder.flush()

        events_file = tmp_path / "events.jsonl"
        assert events_file.exists()
        lines = events_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["seq"] == 1
        assert entry["type"] == "task.updated"
        assert entry["task_id"] == "t1"

    def test_buffer_threshold(self, tmp_path: Path):
        recorder = EventRecorder(tmp_path, "test", flush_threshold=3)
        recorder.append("a")
        recorder.append("b")
        # Not flushed yet
        assert not (tmp_path / "events.jsonl").exists()
        recorder.append("c")
        # Flushed after threshold
        assert (tmp_path / "events.jsonl").exists()

    def test_events_after(self, tmp_path: Path):
        recorder = EventRecorder(tmp_path, "test")
        recorder.append("first")
        recorder.append("second")
        recorder.append("third")
        recorder.flush()

        after = recorder.events_after(1)
        assert len(after) == 2
        assert after[0]["seq"] == 2
        assert after[1]["seq"] == 3

    def test_all_events(self, tmp_path: Path):
        recorder = EventRecorder(tmp_path, "test")
        recorder.append("event", data="x")
        recorder.flush()

        all_events = recorder.all_events()
        assert len(all_events) == 1
        assert all_events[0]["data"] == "x"

    def test_seq_monotonic(self, tmp_path: Path):
        recorder = EventRecorder(tmp_path, "test")
        s1 = recorder.append("a")
        s2 = recorder.append("b")
        s3 = recorder.append("c")
        assert s1 < s2 < s3

    def test_reload_max_seq(self, tmp_path: Path):
        recorder = EventRecorder(tmp_path, "test")
        recorder.append("a")
        recorder.append("b")
        recorder.flush()

        # New recorder reading same file
        recorder2 = EventRecorder(tmp_path, "test")
        s = recorder2.append("c")
        assert s == 3


class TestStateSnapshotter:
    def test_on_mutation_interval(self, tmp_path: Path):
        board = StateBoard("test")
        snapper = StateSnapshotter(tmp_path, interval=3)

        # First 2 mutations don't trigger
        assert snapper.on_mutation(board) is None
        assert snapper.on_mutation(board) is None
        # 3rd triggers
        info = snapper.on_mutation(board)
        assert info is not None
        assert "path" in info
        assert "seq" in info

    def test_force_snapshot(self, tmp_path: Path):
        board = StateBoard("test", budget=Budget(token_limit=100))
        board.add_tokens(50)
        snapper = StateSnapshotter(tmp_path, interval=100)

        info = snapper.force_snapshot(board, seq=42)
        assert info["seq"] == 42

        snapshot_path = Path(info["path"])
        assert snapshot_path.exists()
        data = json.loads(snapshot_path.read_text(encoding="utf-8"))
        assert data["objective"] == "test"
        assert data["budget"]["token_used"] == 50

    def test_latest_snapshot(self, tmp_path: Path):
        board = StateBoard("test")
        snapper = StateSnapshotter(tmp_path, interval=1)
        snapper.on_mutation(board)
        snapper.on_mutation(board)

        latest = snapper.latest_snapshot()
        assert latest is not None
        assert "stateboard-" in latest.name

    def test_load_snapshot(self, tmp_path: Path):
        board = StateBoard("test", budget=Budget(token_limit=999))
        snapper = StateSnapshotter(tmp_path, interval=1)
        snapper.on_mutation(board)

        latest = snapper.latest_snapshot()
        data = snapper.load_snapshot(latest)
        assert data is not None
        assert data["objective"] == "test"

    def test_cleanup_old_snapshots(self, tmp_path: Path):
        board = StateBoard("test")
        snapper = StateSnapshotter(tmp_path, interval=1, keep_last=2)
        for _ in range(5):
            snapper.on_mutation(board)

        snapshots = list(tmp_path.glob("stateboard-*.json"))
        # Should keep only last 2 (plus meta files)
        json_files = [s for s in snapshots if not s.name.endswith(".meta.json")]
        assert len(json_files) <= 2


class TestSessionResumer:
    def test_load_empty_session(self, tmp_path: Path):
        resumer = SessionResumer(tmp_path)
        result = resumer.load("nonexistent")
        assert result.session_id == "nonexistent"
        assert result.snapshot is None
        assert result.events_after == []

    def test_load_with_snapshot_and_events(self, tmp_path: Path):
        session_dir = tmp_path / "s1"
        session_dir.mkdir()

        # Write a snapshot
        snapshots_dir = session_dir / "snapshots"
        snapshots_dir.mkdir()
        snapshot_data = {"objective": "test obj", "budget": {"token_used": 10}}
        snapshot_path = snapshots_dir / "stateboard-5.json"
        snapshot_path.write_text(json.dumps(snapshot_data), encoding="utf-8")

        # Write events
        events_file = session_dir / "events.jsonl"
        events_file.write_text(
            json.dumps({"seq": 1, "type": "a"}) + "\n" +
            json.dumps({"seq": 6, "type": "b"}) + "\n" +
            json.dumps({"seq": 7, "type": "c"}) + "\n",
            encoding="utf-8",
        )

        resumer = SessionResumer(tmp_path)
        result = resumer.load("s1")
        assert result.snapshot is not None
        assert result.snapshot["objective"] == "test obj"
        assert result.snapshot_seq == 5
        # Only events after seq 5
        assert len(result.events_after) == 2
        assert result.events_after[0]["seq"] == 6
        assert result.events_after[1]["seq"] == 7

    def test_list_sessions(self, tmp_path: Path):
        # Create two sessions
        for sid in ["s1", "s2"]:
            sdir = tmp_path / sid
            sdir.mkdir()
            (sdir / "events.jsonl").write_text("", encoding="utf-8")

        resumer = SessionResumer(tmp_path)
        sessions = resumer.list_sessions()
        assert len(sessions) == 2
        session_ids = {s["session_id"] for s in sessions}
        assert session_ids == {"s1", "s2"}

    def test_detect_interruption_completed(self, tmp_path: Path):
        resumer = SessionResumer(tmp_path)
        events = [
            {"seq": 1, "type": "task.completed"},
        ]
        result = resumer._detect_interruption(events)
        assert result == "completed"

    def test_detect_interruption_human_asked(self, tmp_path: Path):
        resumer = SessionResumer(tmp_path)
        events = [
            {"seq": 1, "type": "human.asked"},
        ]
        result = resumer._detect_interruption(events)
        assert result == "interrupted_prompt"

    def test_detect_interruption_running(self, tmp_path: Path):
        resumer = SessionResumer(tmp_path)
        events = [
            {"seq": 1, "type": "task.running"},
        ]
        result = resumer._detect_interruption(events)
        assert result == "mid_tool"


class TestStateBoardPersistence:
    def test_log_event_writes_to_recorder(self, tmp_path: Path):
        recorder = EventRecorder(tmp_path, "test")
        board = StateBoard("obj", recorder=recorder)
        board.log_event("test.event", message="hello")
        recorder.flush()

        events = recorder.all_events()
        assert len(events) == 1
        assert events[0]["type"] == "test.event"
        assert events[0]["message"] == "hello"

    def test_mutation_triggers_snapshot(self, tmp_path: Path):
        recorder = EventRecorder(tmp_path, "test")
        snapshots_dir = tmp_path / "snapshots"
        snapper = StateSnapshotter(snapshots_dir, interval=2)
        board = StateBoard("obj", recorder=recorder, snapshotter=snapper)

        # First mutation — no snapshot
        board.log_event("a")
        assert snapper.latest_snapshot() is None

        # Second mutation — triggers snapshot
        board.log_event("b")
        assert snapper.latest_snapshot() is not None

    def test_add_tasks_with_recorder(self, tmp_path: Path):
        from openagents_orchestration.models.task import TaskGraph, TaskNode

        recorder = EventRecorder(tmp_path, "test")
        board = StateBoard("obj", recorder=recorder)
        graph = TaskGraph(objective="obj", tasks=[TaskNode("t1", "task", "coder")])
        board.add_tasks(graph)
        recorder.flush()

        events = recorder.all_events()
        assert len(events) == 1
        assert events[0]["type"] == "tasks.imported"

    def test_register_agent_with_recorder(self, tmp_path: Path):
        recorder = EventRecorder(tmp_path, "test")
        board = StateBoard("obj", recorder=recorder)
        board.register_agent("a1", "coder")
        recorder.flush()

        events = recorder.all_events()
        assert events[0]["type"] == "agent.registered"


class TestStateBoardRoundtrip:
    def test_snapshot_to_dict_roundtrip(self, tmp_path: Path):
        from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus

        board = StateBoard("Build API", budget=Budget(token_limit=1000, max_steps=10))
        graph = TaskGraph(objective="Build API", tasks=[
            TaskNode("t1", "scaffold", "coder", input_context="create dirs"),
            TaskNode("t2", "implement", "coder", dependencies=["t1"]),
        ])
        board.add_tasks(graph)
        board.register_agent("coder-t1", "coder")
        board.update_task("t1", status="completed", result_output="done")
        board.update_agent("coder-t1", status="done")
        board.add_tokens(500)
        board.add_steps(2)

        # Serialize and deserialize
        data = board.to_dict()
        board2 = StateBoard.from_dict(data, echo=False)

        assert board2.objective == "Build API"
        assert len(board2.tasks) == 2
        assert board2.tasks["t1"].status == TaskStatus.COMPLETED
        assert board2.tasks["t1"].result_output == "done"
        assert board2.tasks["t1"].input_context == "create dirs"
        assert board2.tasks["t2"].status == TaskStatus.PENDING
        assert len(board2.agents) == 1
        assert board2.agents["coder-t1"].status.value == "done"
        assert board2.budget.token_used == 500
        assert board2.budget.steps_taken == 2
        assert board2.budget.max_steps == 10
        assert len(board2.events) == len(board.events)

    def test_event_replay_after_snapshot(self, tmp_path: Path):
        from openagents_orchestration.models.task import TaskGraph, TaskNode, TaskStatus
        from openagents_orchestration.persistence.event_replayer import EventReplayer

        recorder = EventRecorder(tmp_path, "test")
        snapper = StateSnapshotter(tmp_path / "snapshots", interval=100)
        board = StateBoard("obj", recorder=recorder, snapshotter=snapper)

        graph = TaskGraph(objective="obj", tasks=[TaskNode("t1", "task", "coder")])
        board.add_tasks(graph)
        board.register_agent("a1", "coder")

        # Take snapshot here — record seq before more mutations
        snapshot_seq = recorder._seq
        snapper.force_snapshot(board, seq=snapshot_seq)

        # After snapshot: more mutations
        board.update_task("t1", status="running")
        board.update_agent("a1", status="running", current_task="t1")
        board.update_task("t1", status="completed")
        board.update_agent("a1", status="done")
        board.add_tokens(100)
        board.add_steps(1)
        recorder.close()

        # Load snapshot
        loaded = snapper.load_snapshot(snapper.latest_snapshot())
        board2 = StateBoard.from_dict(loaded, echo=False)

        # Verify: snapshot has initial state only
        assert board2.tasks["t1"].status == TaskStatus.PENDING
        assert board2.agents["a1"].status.value == "idle"
        assert board2.budget.token_used == 0

        # Replay events after snapshot
        events_after = recorder.events_after(snapshot_seq)
        EventReplayer().replay(board2, events_after)

        # Verify: replayed to final state
        assert board2.tasks["t1"].status == TaskStatus.COMPLETED
        assert board2.agents["a1"].status.value == "done"
        assert board2.budget.token_used == 100
        assert board2.budget.steps_taken == 1
