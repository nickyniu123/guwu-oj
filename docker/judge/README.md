# Docker Judge Security Setup

This directory contains security profiles for the Docker judge container.

## Security Features

### Two-Layer Seccomp Approach

Docker only attaches a seccomp profile at `docker run` time — there is no
per-`docker exec` profile — and a pooled container hosts BOTH a compile and
many execute steps. The judge therefore enforces the split with two stacked
filters:

1. **Container-creation filter** (`seccomp-compile.json`):
   - The wider, compiler-friendly whitelist (default-deny otherwise). Every
     judge container starts with it, because every container may compile.
   - Allows the compiler/toolchain ABI (cc1, as, ld, javac, kotlinc, …),
     including `seccomp(2)`, which the execute launcher needs below.
   - Both phases still get the shared hard rules: `clone(CLONE_NEW*)` →
     EPERM, and a `SCMP_ACT_KILL_PROCESS` blocklist (mount/bpf/userfaultfd/
     io_uring/module loading/etc.).

2. **Execute filter** (`seccomp-execute.json` + `ojsec`):
   - A strict subset of the compile whitelist, derived empirically from
     `strace` traces of all 10 language runtimes (threads, fork, pipes,
     files, socketpair, timers, poll, SysV shm/sem, interactive shells).
   - Drops compile-only attack surface: socket-server calls
     (`bind`/`listen`/`accept4`, `recvmsg`/`recvmmsg`/`sendmmsg`), the
     `splice`/`tee`/`vmsplice`/`copy_file_range` family, inotify, xattr and
     SysV message queues. Submitted programs keep fork/execve/write/mmap —
     they run real runtimes (not a toy interpreter).
   - Applied in-container by the static launcher
     [`ojsec`](ojsec.c): `SandboxRunner._timed_command` prefixes every
     untrusted-code invocation with `/opt/oj/ojsec /opt/oj/ojrun …`. ojsec
     installs `seccomp-execute.json`'s BPF whitelist (from the generated
     [ojsec_policy.h](ojsec_policy.h)) via `seccomp(2)` and `execvp`s the
     timer; the submitted process tree inherits the filter. Stacked filters
     combine by intersection, so ojsec can only ever tighten the container
     profile, never loosen it. A container without the `/opt/oj/ojsec`
     binary transparently keeps running on the creation-time filter.

This approach ensures that:
- Compilers work normally with the wider ABI.
- Executed code runs under the narrower, trace-validated whitelist.
- A launcher bug can never weaken the container-level filter.

#### Regenerating the execute policy

After editing `seccomp-execute.json`, regenerate the BPF header and rebuild
the static launcher (on an x86_64 host with gcc + static glibc):

```bash
python3 docker/judge/gen_ojsec_policy.py
gcc -O2 -Wall -Wextra -static -s \
    -o docker/judge/ojbin/ojsec docker/judge/ojsec.c
```

Commit both `ojsec_policy.h` and `docker/judge/ojbin/ojsec` (same convention
as the committed `ojbin/ojrun`), deploy, then prune pool containers so they
recreate with the updated `/opt/oj` mount and creation profile.

### AppArmor Profile (apparmor-profile)
- Provides additional confinement beyond Docker's default security
- Restricts file access to /sandbox directory
- Denies network access
- Denies device access (except specific allowed devices)
- Denies access to sensitive system files (/etc/passwd, /etc/shadow, etc.)
- Denies ptrace and process manipulation
- Denies mount operations
- Denys module operations

### Additional Security Options in sandbox.py
- `--network none`: No network access
- `--security-opt no-new-privileges`: Prevents privilege escalation
- `--cap-drop ALL`: Drops all Linux capabilities
- `--read-only`: Read-only root filesystem
- `--tmpfs /tmp`: Temporary filesystem for /tmp
- `--device` restrictions: Only allows specific devices (/dev/null, /dev/zero, /dev/random, /dev/urandom)
- Runs as unprivileged user (nobody, uid/gid 65534)
- PIDs limit: Restricts number of processes

## Setup Instructions

### 1. Load AppArmor Profile (Linux only)

The AppArmor profile must be loaded into the kernel before use. Run the following commands as root:

```bash
# Copy the profile to AppArmor directory
sudo cp docker/judge/apparmor-profile /etc/apparmor.d/oj-judge

# Load the profile
sudo apparmor_parser -r /etc/apparmor.d/oj-judge

# Verify the profile is loaded
sudo aa-status | grep oj-judge
```

### 2. Build Docker Image

```bash
docker build -t oj-judge:latest docker/judge
```

### 3. Test the Setup

You can test if the security profiles are working by running a test container:

```bash
docker run --rm \
  --security-opt seccomp=docker/judge/seccomp-compile.json \
  --security-opt apparmor=oj-judge \
  --cap-drop ALL \
  --read-only \
  --tmpfs /tmp \
  oj-judge:latest \
  ls /sandbox
```

### 4. Disable AppArmor if Not Available (Optional)

If you're running on a system without AppArmor support (e.g., some Docker Desktop configurations), you can disable AppArmor by modifying `submissions/sandbox.py`:

Comment out or remove this line:
```python
'--security-opt', 'apparmor=oj-judge',
```

The Seccomp profile will still provide strong security even without AppArmor.

## Security Checklist

- [x] Network isolation (--network none)
- [x] No new privileges (--security-opt no-new-privileges)
- [x] Capability dropping (--cap-drop ALL)
- [x] Seccomp profile (system call filtering)
- [x] AppArmor profile (additional confinement)
- [x] Read-only root filesystem
- [x] Temporary filesystem for /tmp
- [x] Device access restrictions
- [x] Unprivileged user (nobody)
- [x] Process limit (pids-limit)
- [x] Memory limits
- [x] Time limits

## Troubleshooting

### AppArmor Profile Not Found

If you get an error about the AppArmor profile not being found, ensure:
1. You're running on Linux (AppArmor is Linux-specific)
2. AppArmor is enabled on your system
3. The profile has been loaded with `apparmor_parser`

### Seccomp Profile Not Found

If you get an error about the Seccomp profile not being found, ensure:
1. The path to `seccomp-compile.json` is correct in `sandbox.py`
   (`_seccomp_profile_path`)
2. The file exists and is readable

### Permission Denied Errors

If you encounter permission errors, ensure:
1. The work directory has proper permissions (0777)
2. Docker has access to the necessary directories
