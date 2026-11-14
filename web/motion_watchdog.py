"""Independent lease watcher; survives Web process loss and never resumes movement."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    lease, script, expected_run = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    while True:
        try:
            data = json.loads(lease.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if data.get("run") != expected_run:
            return
        if time.monotonic() > data.get("expires", 0):
            env = {**os.environ, "ODIN_EXPECT_NAV_RUN": expected_run}
            result = subprocess.run(["bash", str(script), "stop"], env=env,
                                    timeout=70, check=False, capture_output=True)
            print(f"watchdog stop exit={result.returncode}", flush=True)
            if result.returncode == 0:
                lease.unlink(missing_ok=True)
                return
        time.sleep(0.5)


if __name__ == "__main__":
    main()
