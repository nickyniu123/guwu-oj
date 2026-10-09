/*
 * ojsec — execute-phase seccomp launcher for judge containers.
 *
 * WHY THIS EXISTS
 * ---------------
 * Docker applies a container's ``--security-opt seccomp=...`` filter at
 * ``docker run`` time and inherits it by every ``docker exec``; there is no
 * per-exec profile. A pooled judge container is born once (with the compile
 * superset, because the compiler toolchain needs the wider ABI) and then
 * serves both the compile and the execute phases. ojsec closes that gap:
 * SandboxRunner prefixes every untrusted-code invocation with ojsec, which
 * installs the execute-phase filter from ojsec_policy.h (generated from
 * seccomp-execute.json) and then execvp()s the real command (ojrun/time and,
 * through them, the submitted program and all its descendants).
 *
 * Stacked seccomp filters combine by intersection: the execute filter can
 * only ever be STRICTER than the container profile, so a bug here cannot
 * loosen the sandbox. A non-x86_64 audit architecture kills the process;
 * any whitelisted-but-not-allowed syscall returns EPERM.
 *
 * Failure mode is FAIL-CLOSED: ojsec exits 126/127 without exec'ing the
 * program whenever the filter cannot be installed.
 *
 * Build (static, runs in any judge image regardless of libc):
 *     python3 docker/judge/gen_ojsec_policy.py
 *     gcc -O2 -static -s -o docker/judge/ojbin/ojsec docker/judge/ojsec.c
 * The committed binary is bind-mounted read-only at /opt/oj alongside ojrun
 * (see submissions/sandbox.py); a missing binary transparently skips the
 * extra layer (old containers recycle and pick it up on redeploy).
 */
#define _GNU_SOURCE
#include <errno.h>
#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <unistd.h>

#include "ojsec_policy.h"

#ifndef AUDIT_ARCH_X86_64
#define AUDIT_ARCH_X86_64 0xC000003E
#endif

/* Unprivileged seccomp filters are capped at BPF_MAXINSNS (4096) classic
 * instructions. 2 per whitelist entry + 5 fixed slots stays well under it
 * for the current ~150-entry policy; reject growth past the cap instead of
 * letting the kernel refuse installation at runtime. */
#define FILTER_INSNS (5 + 2 * OJSEC_POLICY_COUNT)
#if FILTER_INSNS > 4096
#error "execute seccomp policy too large for an unprivileged BPF filter"
#endif

#define RET_ALLOW  SECCOMP_RET_ALLOW
#define RET_EPERM  (SECCOMP_RET_ERRNO | (EPERM & 0xffff))
#define RET_KILL   SECCOMP_RET_KILL_PROCESS

static int install_filter(void)
{
    struct sock_filter code[FILTER_INSNS];
    unsigned int i = 0;

    /* Reject anything that is not x86_64 up front (x32, compat 32-bit). */
    code[i++] = (struct sock_filter)BPF_STMT(
        BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, arch));
    code[i++] = (struct sock_filter)BPF_JUMP(
        BPF_JMP | BPF_JEQ | BPF_K, AUDIT_ARCH_X86_64, 1, 0);
    code[i++] = (struct sock_filter)BPF_STMT(BPF_RET | BPF_K, RET_KILL);

    code[i++] = (struct sock_filter)BPF_STMT(
        BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr));

    /* Per number: the classic BPF interpreter adds the offset to the jump
     * instruction's index and THEN advances one more slot. So an equal
     * result with jt=0 lands on the adjacent ALLOW; jf=1 skips that ALLOW
     * and continues with the next test. After the last pair a miss falls
     * through to EPERM. */
    for (unsigned int k = 0; k < OJSEC_POLICY_COUNT; ++k) {
        code[i++] = (struct sock_filter)BPF_JUMP(
            BPF_JMP | BPF_JEQ | BPF_K, (unsigned int)ojsec_policy_nrs[k],
            0, 1);
        code[i++] = (struct sock_filter)BPF_STMT(BPF_RET | BPF_K, RET_ALLOW);
    }
    code[i++] = (struct sock_filter)BPF_STMT(BPF_RET | BPF_K, RET_EPERM);

    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) < 0) {
        /* Container already runs with no-new-privileges; EPERM here means
         * something is structurally wrong. Do not run unfiltered. */
        fprintf(stderr, "ojsec: PR_SET_NO_NEW_PRIVS: %s\n", strerror(errno));
        return -1;
    }

    struct sock_fprog prog = {
        .len = (unsigned short)i,
        .filter = code,
    };
    if (syscall(SYS_seccomp, SECCOMP_SET_MODE_FILTER,
                SECCOMP_FILTER_FLAG_TSYNC, &prog) == 0) {
        return 0;
    }
    int saved = errno;
    /* Older kernels / libc fallback. */
    if (prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &prog) == 0)
        return 0;
    fprintf(stderr, "ojsec: seccomp filter install: %s\n", strerror(saved));
    return -1;
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr, "usage: ojsec <command> [args...]\n");
        return 127;
    }
    if (install_filter() < 0)
        return 126;
    execvp(argv[1], argv + 1);
    fprintf(stderr, "ojsec: exec %s: %s\n", argv[1], strerror(errno));
    return 127;
}
