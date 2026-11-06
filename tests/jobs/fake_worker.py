"""Standalone fake worker for JobManager tests: modes ok / crash / hang / partial."""

import os
import sys
import time

from toystudio.jobs.protocol import Done, Progress, encode


def _emit(msg) -> None:
    sys.stdout.buffer.write(encode(msg))
    sys.stdout.buffer.flush()


def main() -> None:
    mode = sys.argv[1]
    if mode == "ok":
        sys.stdout.write("garbage line\n")
        sys.stdout.flush()
        for i in range(10):
            _emit(Progress("fake", i + 1, 10, None))
        _emit(Done())
    elif mode == "crash":
        _emit(Progress("fake", 1, 10, None))
        _emit(Progress("fake", 2, 10, None))
        sys.stderr.write("boom stderr\n")
        sys.stderr.flush()
        os._exit(3)
    elif mode == "hang":
        time.sleep(600)
    else:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
