"""Local observation command for a host tool that wakes its AI on stdout.

This process alone cannot wake a terminal. It never claims work on behalf of AI.
"""

import argparse
import json
import time
from pathlib import Path

from herald.config import load_config
from herald.inbox import Inbox
from herald.watch import WatchStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("duty_id")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    config = load_config(args.config)
    if not config.watch.enabled:
        parser.error("Watch is disabled")
    inbox = Inbox(config.capture.database, config.capture.files_dir)
    inbox.prepare()
    store = WatchStore(inbox)
    store.prepare()
    previous = None
    emitted = 0.0
    while True:
        try:
            event = store.monitor_probe(args.duty_id)
        except ValueError:
            return
        current = tuple(event["pending"])
        if args.once or (
            current and (current != previous or time.monotonic() - emitted >= 60)
        ):
            print(json.dumps(event), flush=True)
            emitted = time.monotonic()
        previous = current
        if args.once:
            return
        time.sleep(5)


if __name__ == "__main__":
    main()
