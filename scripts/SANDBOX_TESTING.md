# Sandbox protection testing

The judge runs user code with the same flags as `submissions/sandbox.py`:

- `--network none`
- `--memory` / `--memory-swap` (from problem `memory_limit`)
- `--user 65534:65534` (or configured uid/gid)
- `--pids-limit 64`
- `--security-opt no-new-privileges`
- `--cap-drop ALL`
- Code only in a temp dir mounted at `/sandbox`

## Prerequisites

```bash
./scripts/build-judge-image.sh
docker images | grep oj-judge
python manage.py runserver
```

Create a problem with ≥3 test cases (any trivial I/O is fine).

## 1. Automated script

```bash
chmod +x scripts/test-sandbox.sh
./scripts/test-sandbox.sh
```

## 2. Manual Docker (same as production)

```bash
WORKDIR=$(mktemp -d)
chmod 777 "$WORKDIR"
docker run --rm -i --network none --memory 64m --memory-swap 64m \
  --user 65534:65534 --pids-limit 64 --security-opt no-new-privileges --cap-drop ALL \
  -v "$WORKDIR:/sandbox:rw" -w /sandbox oj-judge:latest \
  python3 -c "import socket; socket.create_connection(('8.8.8.8', 53), 3)"
# Expect: OSError / timeout — no connection
```

## 3. Submit via OJ UI (Python)

Use a problem with dummy test input `1` and expected output `ok` (or accept WA/TLE/RE).

### Network should fail → Runtime Error

```python
import urllib.request
print(urllib.request.urlopen('https://example.com', timeout=5).read()[:10])
```

### Infinite loop → Time Limit Exceeded

Lower problem time limit to `1000` ms in admin/upload, then:

```python
while True:
    pass
```

### Memory bomb → Memory Limit Exceeded (or RE)

Set problem memory limit to `32` MB, then:

```python
a = []
while True:
    a.append(' ' * 10**7)
```

### Fork stress → Runtime Error / TLE (pids-limit)

```python
import os
while True:
    os.fork()
```

## 4. What “pass” looks like

| Attack              | Expected on OJ                          |
|---------------------|-----------------------------------------|
| HTTP / socket out   | Runtime Error (no network)              |
| `while True`        | Time Limit Exceeded                     |
| Huge allocation     | Memory Limit Exceeded or Runtime Error  |
| Many forks          | Runtime Error / TLE, judge still alive  |

After each test, **Django must keep running** and other users can still submit — the bad code must not take down the host process.

## 5. Defense layers and scope

- Containers run default-deny seccomp (`seccomp-compile.json`) **stacked with
  the tighter execute whitelist** installed by the `ojsec` launcher for every
  untrusted-code invocation (`seccomp-execute.json`), plus the `oj-judge`
  AppArmor profile, `--network none`, dropped caps, read-only rootfs and
  cgroup/PID limits.
- After changing `seccomp-execute.json`, regenerate and rebuild
  (`python3 docker/judge/gen_ojsec_policy.py && gcc -O2 -static -s -o
  docker/judge/ojbin/ojsec docker/judge/ojsec.c`), redeploy `ojbin/ojsec`,
  then prune pool containers; `tests/test_seccomp_phase_split.py` guards the
  subset invariants and keeps the generated header in sync with the JSON.
- Submissions can still **burn CPU/time** until timeout; file writes are
  confined to `/sandbox` (quota-mounted) and tmpfs `/tmp`.
- This is hardening, not a complete security audit.
