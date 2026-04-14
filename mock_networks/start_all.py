"""
Start all 6 mock payment network servers concurrently.
Run from project root: python mock_networks/start_all.py
"""
import subprocess
import sys
import os
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SERVERS = [
    {"name": "1LINK/IBFT",   "module": "onelink.main",   "port": 9000},
    {"name": "JazzCash",    "module": "jazzcash.main",   "port": 9001},
    {"name": "Easypaisa",   "module": "easypaisa.main",  "port": 9002},
    {"name": "NayaPay",     "module": "nayapay.main",    "port": 9003},
    {"name": "UPay",        "module": "upay.main",       "port": 9004},
    {"name": "SadaPay",     "module": "sadapay.main",    "port": 9005},
    {"name": "Utility Bills","module": "bills.main",     "port": 9006},
]

processes: list[subprocess.Popen] = []


def start_servers():
    for server in SERVERS:
        server_dir = os.path.join(BASE_DIR, server["module"].split(".")[0])
        proc = subprocess.Popen(
            [sys.executable, "main.py"],
            cwd=server_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        processes.append(proc)
        print(f"[+] Started {server['name']} on port {server['port']}  (PID {proc.pid})")

    print("\nAll 6 mock servers running. Press Ctrl+C to stop all.\n")

    try:
        while True:
            time.sleep(1)
            for i, proc in enumerate(processes):
                if proc.poll() is not None:
                    s = SERVERS[i]
                    print(f"[!] {s['name']} (port {s['port']}) exited unexpectedly — restarting…")
                    server_dir = os.path.join(BASE_DIR, s["module"].split(".")[0])
                    new_proc = subprocess.Popen(
                        [sys.executable, "main.py"],
                        cwd=server_dir,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                    )
                    processes[i] = new_proc
                    print(f"    Restarted on port {s['port']}  (PID {new_proc.pid})")
    except KeyboardInterrupt:
        print("\nShutting down all servers…")
        for proc in processes:
            proc.terminate()
        for proc in processes:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        print("All servers stopped.")


if __name__ == "__main__":
    start_servers()
