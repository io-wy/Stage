"""Talk to the Director — inject a human message into a running orchestration.

Usage:
    uv run python talk.py <session_id> "Your message here"
    uv run python talk.py session-3e56a774 "跳过t_new_1，直接做t_new_2"
    uv run python talk.py --list

The message is written to the session's inbox file. The Director will see it
on the next check_messages call.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime


def main():
    parser = argparse.ArgumentParser(description="Send a message to the Director")
    parser.add_argument("session_id", nargs="?", help="Session ID")
    parser.add_argument("message", nargs="*", help="Message to send")
    parser.add_argument("--list", action="store_true", help="List active sessions")
    parser.add_argument("--from", dest="sender", default="human", help="Sender name")
    args = parser.parse_args()

    persist_dir = Path(__file__).parent / ".claude" / "persist"

    if args.list or not args.session_id:
        if not persist_dir.exists():
            print("No persist directory found.")
            return 1
        sessions = [d.name for d in persist_dir.iterdir() if d.is_dir()]
        if not sessions:
            print("No sessions found.")
            return 0
        print(f"\nAvailable sessions ({len(sessions)}):")
        for sid in sorted(sessions):
            inbox = persist_dir / sid / "inbox.jsonl"
            count = 0
            if inbox.exists():
                count = len(inbox.read_text().strip().splitlines()) if inbox.read_text().strip() else 0
            print(f"  {sid}  (inbox: {count} message(s))")
        return 0

    if not args.message:
        print("Error: message is required")
        return 1

    session_dir = persist_dir / args.session_id
    if not session_dir.exists():
        print(f"Error: session '{args.session_id}' not found")
        print(f"Run 'uv run python talk.py --list' to see available sessions")
        return 1

    inbox_file = session_dir / "inbox.jsonl"

    message_text = " ".join(args.message)
    entry = {
        "from": args.sender,
        "to": "director",
        "content": message_text,
        "ts": datetime.now().isoformat(),
    }

    with open(inbox_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"Message sent to Director (session: {args.session_id}):")
    print(f"  {message_text}")
    print(f"Director will see this on next check_messages.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
