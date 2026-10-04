"""Compile/execute seccomp split invariants.

* seccomp-execute.json is a strict subset of the container-creation compile
  profile and drops the compile-only attack surface (socket server calls,
  inotify, splice family, SysV message queues, xattr).
* The committed ojsec launcher binary + generated policy header exist and
  the header stays in sync with the JSON policy.
"""
import json
import re
from pathlib import Path

from django.test import SimpleTestCase

JUDGE_DIR = Path(__file__).resolve().parent.parent / "docker" / "judge"

# Removed from the execute phase on purpose (compiler/toolchain ABI only).
EXECUTE_REMOVED = {
    "accept4", "bind", "listen",
    "recvmsg", "recvmmsg", "sendmmsg",
    "copy_file_range", "splice", "tee", "vmsplice",
    "getxattr", "lgetxattr",
    "inotify_add_watch", "inotify_init1", "inotify_rm_watch",
    "msgctl", "msgget", "msgrcv", "msgsnd",
}

# Must always remain reachable by submitted programs: process/threading,
# execution, files, outbound socket attempt, timing, JVM pkey guard.
EXECUTE_REQUIRED = {
    "read", "write", "openat", "close", "mmap", "munmap", "mprotect",
    "brk", "rt_sigaction", "rt_sigreturn", "ioctl", "lseek", "newfstatat",
    "clone", "clone3", "vfork", "execve", "wait4", "waitid", "exit_group",
    "kill", "tgkill", "futex", "epoll_pwait", "eventfd2", "pipe2",
    "socket", "connect", "sendto", "recvfrom", "setsockopt", "getsockopt",
    "clock_gettime", "clock_nanosleep", "nanosleep", "getrandom",
    "pkey_mprotect", "rseq", "prctl", "fcntl", "unlinkat", "renameat",
    "mknodat", "timerfd_create", "fallocate", "sendfile", "statfs",
}

KILL_SYSCALLS = {
    "bpf", "mount", "umount2", "pivot_root", "perf_event_open",
    "init_module", "finit_module", "delete_module",
    "io_uring_setup", "io_uring_enter", "io_uring_register",
    "reboot", "kexec_load", "userfaultfd",
}


def _load(name):
    return json.loads((JUDGE_DIR / name).read_text())


def _allow_block(profile):
    for block in profile["syscalls"]:
        if block.get("action") == "SCMP_ACT_ALLOW":
            return set(block["names"])
    raise AssertionError("no SCMP_ACT_ALLOW block")


class SeccompProfileSplitTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.compile_p = _load("seccomp-compile.json")
        cls.execute_p = _load("seccomp-execute.json")
        cls.compile = _allow_block(cls.compile_p)
        cls.execute = _allow_block(cls.execute_p)

    def test_default_deny_eprem(self):
        for p in (self.compile_p, self.execute_p):
            self.assertEqual(p["defaultAction"], "SCMP_ACT_ERRNO")
            self.assertEqual(p["defaultErrnoRet"], 1)

    def test_execute_is_strict_subset_of_compile(self):
        self.assertTrue(self.execute.issubset(self.compile))
        self.assertLess(len(self.execute), len(self.compile))

    def test_compile_allows_seccomp_for_ojsec(self):
        # ojsec installs its stacked filter via seccomp(2); that must be
        # allowed at container creation, and must NOT leak into execute
        # (a submitted program never needs to add filters).
        self.assertIn("seccomp", self.compile)
        self.assertNotIn("seccomp", self.execute)

    def test_execute_drops_compile_only_surface(self):
        self.assertEqual(self.execute & EXECUTE_REMOVED, set())
        self.assertTrue(EXECUTE_REMOVED.issubset(self.compile))

    def test_execute_keeps_required_runtime_abi(self):
        missing = EXECUTE_REQUIRED - self.execute
        self.assertFalse(missing, f"execute whitelist missing: {missing}")

    def test_dangerous_syscalls_kill_process_in_both(self):
        for p in (self.compile_p, self.execute_p):
            killed = set()
            for block in p["syscalls"]:
                if block.get("action") == "SCMP_ACT_KILL_PROCESS":
                    killed.update(block["names"])
            self.assertTrue(KILL_SYSCALLS.issubset(killed))

    def test_clone_namespace_mask_still_eprem(self):
        for p in (self.compile_p, self.execute_p):
            masked = [
                b for b in p["syscalls"]
                if b.get("action") == "SCMP_ACT_ERRNO"
                and set(b["names"]) == {"clone", "clone3"}
            ]
            self.assertEqual(len(masked), 1)
            self.assertEqual(masked[0]["args"][0]["value"], 2080505856)

    def test_policy_size_fits_unprivileged_bpf(self):
        # 2 BPF insns per entry + 5 fixed; classic unprivileged cap 4096.
        self.assertLessEqual(5 + 2 * len(self.execute), 4096)


class OjsecBinaryAndHeaderTests(SimpleTestCase):
    def test_launcher_binary_committed(self):
        binary = JUDGE_DIR / "ojbin" / "ojsec"
        self.assertTrue(binary.is_file())
        self.assertTrue(binary.stat().st_mode & 0o111)

    def test_generated_header_matches_execute_json(self):
        header = (JUDGE_DIR / "ojsec_policy.h").read_text()
        m = re.search(r"#define OJSEC_POLICY_COUNT (\d+)", header)
        self.assertIsNotNone(m)
        count = int(m.group(1))
        execute = _allow_block(_load("seccomp-execute.json"))
        self.assertEqual(count, len(execute))

        body = re.search(r"ojsec_policy_nrs\[[^\]]*\]\s*=\s*\{(.*?)\}",
                         header, re.S).group(1)
        numbers = [int(x) for x in re.findall(r"-?\d+", body)]
        self.assertEqual(len(numbers), count)
        self.assertEqual(numbers, sorted(numbers))
        self.assertEqual(len(set(numbers)), count)  # no duplicates
        # x86_64 number range sanity (read == 0 is legitimate).
        self.assertTrue(all(0 <= n < 512 for n in numbers))

    def test_generator_script_present(self):
        self.assertTrue((JUDGE_DIR / "gen_ojsec_policy.py").is_file())
