"""Повторяемая нагрузка на одноразовый стенд: python -m scripts.run_lab10_load."""
import argparse
import csv
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

import psutil
from sqlalchemy import text

from scripts.lab10_environment import ROOT, isolated_stack, running_api


def sample(context, processes):
    with context["engine"].connect() as db:
        activity = db.execute(text(
            "SELECT count(*) FILTER (WHERE state='active') AS active, "
            "count(*) FILTER (WHERE wait_event_type='Lock') AS locks "
            "FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()"
        )).mappings().one()
    parent = processes[context["api"].pid]
    for child in parent.children(recursive=True):
        if child.pid not in processes:
            processes[child.pid] = child
            child.cpu_percent()
    return {
        "elapsed_utc": time.time(),
        "api_cpu_percent": sum(process.cpu_percent() for process in processes.values()),
        "api_rss_mb": round(sum(process.memory_info().rss for process in processes.values()) / 1024**2, 2),
        "db_active": activity["active"], "db_lock_waits": activity["locks"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web", action="store_true", help="Keep disposable API and Locust UI alive until Ctrl+C")
    parser.add_argument("--stress", action="store_true", help="Short 0.01-0.05 second think time to find saturation")
    parser.add_argument("--workers", type=int, default=1, help="API process count for a bottleneck comparison")
    parser.add_argument("--users", nargs="+", type=int, default=[50, 200])
    parser.add_argument("--seconds", type=int, default=40)
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "lab10" / "load")
    args = parser.parse_args()
    if any(users <= 0 for users in args.users) or args.seconds < 10 or args.workers < 1:
        parser.error("users must be positive and seconds must be at least 10")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    temp = ROOT / ".pytest_cache" / "lab10-load"
    try:
        with isolated_stack(temp) as context:
            users_file = temp / "users.json"
            subprocess.run(
                [sys.executable, "-m", "scripts.lab10_seed", str(users_file), str(max(1000, max(args.users)))],
                cwd=ROOT, env=context["env"], check=True,
            )
            env = {**context["env"], "LAB10_USERS_FILE": str(users_file),
                   "LAB10_STRESS": "1" if args.stress else "0"}
            with running_api(context, workers=args.workers):
                base = [sys.executable, "-m", "locust", "-f", str(ROOT / "loadtests" / "locustfile.py"),
                        "--host", context["url"], "--only-summary"]
                if args.web:
                    print(f"Disposable API: {context['url']}; Locust: http://127.0.0.1:8089", flush=True)
                    process = subprocess.Popen([*base, "--web-host", "127.0.0.1"], cwd=ROOT, env=env)
                    try:
                        process.wait()
                    finally:
                        process.terminate()
                        process.wait(timeout=10)
                    return
                summaries = []
                for users in args.users:
                    prefix = output / f"users_{users}"
                    command = [
                        *base, "--headless", "-u", str(users), "-r", "10",
                        "-t", f"{args.seconds}s", "--csv", str(prefix), "--html", str(prefix)+".html",
                        "--exit-code-on-error", "1",
                    ]
                    observations = []
                    api_process = psutil.Process(context["api"].pid)
                    api_process.cpu_percent()
                    processes = {api_process.pid: api_process}
                    with (output / f"users_{users}.log").open("w", encoding="utf-8") as log:
                        load = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
                        try:
                            while load.poll() is None:
                                observations.append(sample(context, processes))
                                time.sleep(1)
                        finally:
                            if load.poll() is None:
                                load.terminate()
                                load.wait(timeout=10)
                    (output / f"users_{users}_resources.json").write_text(
                        json.dumps(observations, indent=2), encoding="utf-8"
                    )
                    if load.returncode != 0:
                        raise RuntimeError(f"Locust failed; see {prefix}.log and CSV failures")
                    with Path(str(prefix)+"_stats.csv").open(encoding="utf-8-sig", newline="") as file:
                        rows = list(csv.DictReader(file))
                    summaries.append({
                        "users": users, "seconds": args.seconds, "stress": args.stress,
                        "workers": args.workers, "stats": rows,
                        "api_cpu_percent_mean": round(statistics.mean(o["api_cpu_percent"] for o in observations), 2),
                        "api_cpu_percent_max": max(o["api_cpu_percent"] for o in observations),
                        "db_lock_waits_max": max(o["db_lock_waits"] for o in observations),
                        "db_active_max": max(o["db_active"] for o in observations),
                    })
                    print(f"PASS {users} users; reports: {prefix}", flush=True)
                (output / "summary.json").write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    finally:
        # Короткоживущие JWT не должны попадать в отчёты или репозиторий.
        users_file = temp / "users.json"
        users_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
