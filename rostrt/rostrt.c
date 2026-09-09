/* rostrt — RISC OS runtime for Mojo/LLVM-generated code.
 *
 * Implements the KGEN_CompilerRT surface and the OS entry points on
 * RISC OS SWIs. Compiled freestanding (-ffreestanding -nostdlib); the
 * only calls it makes are SWIs.
 */

typedef unsigned int u32;
typedef unsigned long long u64;

/* ---------------- OS SWI wrappers ----------------
 * RISC OS SWIs corrupt r0-r3 and r12 per the PRM; models reflect that.
 * Inputs ride in the named registers via explicit operands. */

__attribute__((noinline)) void os_writec(int c)
{
    register int r0 __asm("r0") = c;
    __asm__ volatile("swi 0x00" : "+r"(r0) : : "r1", "r2", "r3", "r12", "memory");
}

__attribute__((noinline)) void os_write0(const char *s)
{
    register const char *r0 __asm("r0") = s;
    __asm__ volatile("swi 0x02" : : "r"(r0) : "r1", "r2", "r3", "r12", "memory");
}

__attribute__((noreturn, noinline)) void os_exit(int code, int reason)
{
    register int r0 __asm("r0") = reason;
    register int r2 __asm("r2") = code;
    __asm__ volatile("swi 0x11" : : "r"(r0), "r"(r2) : "r1", "r3", "r12", "memory");
    __builtin_unreachable();
}

/* OS_GetEnv (SWI &10): R0 -> "<command tail>\0<env strings>", R1 = top of
 * usable memory (not used yet). */
__attribute__((noinline)) const char *os_getenv(void)
{
    register const char *r0 __asm("r0");
    register u32 r1 __asm("r1");
    __asm__ volatile("swi 0x10"
                     : "=r"(r0), "=r"(r1)
                     :
                     : "r2", "r3", "r12", "memory");
    return r0;
}

/* ---------------- argv from the command tail ---------------- */

static char argbuf[1024];
static const char *argvec[64];
static int argc_v;
static const char **argv_v;
__attribute__((used)) const char **rostrt_argv_ptr;

int rostrt_init(void)
{
    const char *env = os_getenv();
    int argc = 0;
    char *out = argbuf;

    /* Quoting is minimal: whitespace separates; "..." groups. */
    for (;;) {
        char c = *env;
        while (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
            env++;
            c = *env;
        }
        if (c == '\0' || argc >= 63)
            break;
        int quoted = 0;
        if (c == '"') {
            quoted = 1;
            env++;
            c = *env;
        }
        const char *start = out;
        while (c && (quoted || (c != ' ' && c != '\t'))) {
            if (quoted && c == '"') {
                env++;
                break;
            }
            *out++ = c;
            env++;
            c = *env;
        }
        *out++ = '\0';
        argvec[argc++] = start;
    }
    argvec[argc] = 0;
    argc_v = argc;
    argv_v = argvec;
    rostrt_argv_ptr = argvec;
    return argc;
}

int rostrt_argc(void) { return argc_v; }
const char **rostrt_argv(void) { return argv_v; }

/* ---------------- tiny output for the demo/runtime ---------------- */

static void puts_ro(const char *s) { os_write0(s); }

/* ---------------- KGEN_CompilerRT surface ---------------- */

/* Mojo's runtime consults a "CPU device"; a single static handle is fine. */
u64 KGEN_CompilerRT_AsyncRT_GetOrCreateCPUDevice(void)
{
    return 1;
}

void KGEN_CompilerRT_AsyncRT_ReleaseCPUDevice(u64 device)
{
    (void)device;
}

u64 KGEN_CompilerRT_AsyncRT_GetCurrentCPUDevice(void)
{
    return 1;
}

/* Static arena for Mojo globals: created-once objects with optional
 * constructor. A bump allocator over a fixed array keeps this SWI-cheap
 * and dependency-free; OS_Heap can replace it later. */
static unsigned char global_arena[32768] __attribute__((aligned(16)));
static u64 arena_used;

static void *alloc_arena(u64 size)
{
    u64 sz = (size + 15) & ~(u64)15;
    if (arena_used + sz > sizeof(global_arena)) {
        puts_ro("rostrt: arena exhausted\n");
        os_exit(1, 0);
    }
    void *p = &global_arena[arena_used];
    arena_used += sz;
    return p;
}

void *KGEN_CompilerRT_GetOrCreateGlobal(u64 hash, u64 size, void *ctor,
                                        void *dtor)
{
    (void)hash;
    (void)dtor;
    u64 sz = (size + 7) & ~(u64)7;
    if (arena_used + sz > sizeof(global_arena)) {
        puts_ro("rostrt: global arena exhausted\n");
        os_exit(1, 0);
    }
    void *p = &global_arena[arena_used];
    for (u64 i = 0; i < size; i++)
        ((unsigned char *)p)[i] = 0;
    arena_used += sz;
    if (ctor) {
        typedef void (*fn_t)(void *);
        ((fn_t)ctor)(p);
    }
    return p;
}

void KGEN_CompilerRT_SetArgV(int argc, void *argv)
{
    (void)argc;
    (void)argv;
}

/* Mojo calls this once during start-up to install a fault handler, not when a
 * fault happens - so printing here made every program announce "rostrt: fault"
 * before it had done anything, which reads as a crash in a program that is
 * fine. There is nothing to install: RISC OS reports aborts itself, and the
 * emulator's fault trap catches them with the register file intact, which is
 * more than a stack trace would give us. Silence is the honest stub. */
void KGEN_CompilerRT_PrintStackTraceOnFault(void) {}

void KGEN_CompilerRT_DestroyGlobals(void) {}

/* ------------- POSIX-ish bottom layer for Mojo's stdlib -------------
 * print() bottoms at exactly one symbol on the CPU path:
 *   ssize_t write(int fd, const void *buf, size_t count)
 * (std/io/file_descriptor.mojo). fd 1/2 -> all RISC OS output streams.
 * Returning the full count is required (the caller asserts on it). */

long write(int fd, const void *buf, unsigned count)
{
    (void)fd; /* 1=stdout, 2=stderr: same stream on RISC OS for now */
    const unsigned char *p = buf;
    for (unsigned i = 0; i < count; i++)
        os_writec(p[i]);
    return (long)count;
}

/* Long-lived bump allocation for Wimp structures (indirected buffers must
 * outlive the creating function's frame). Never freed. */
void *rostrt_alloc(unsigned size)
{
    return alloc_arena(size);
}

/* Allocator + support surface pulled in by stdlib print/format. */
void *KGEN_CompilerRT_AlignedAlloc(u64 alignment, u64 size)
{
    (void)alignment; /* arena is 8-aligned; larger alignments rare in print */
    return alloc_arena(size);
}

void KGEN_CompilerRT_AlignedFree(void *p)
{
    (void)p; /* bump allocator: no-op */
}

int KGEN_CompilerRT_fprintf(void *stream, const char *fmt, ...)
{
    (void)stream; (void)fmt;
    return 0; /* only used by interpreter/overflow paths */
}

void *__aeabi_unwind_cpp_pr0 = 0; /* never called: exidx stripped by roscc */

void *memcpy(void *dst, const void *src, unsigned n)
{
    unsigned char *d = dst;
    const unsigned char *s = src;
    for (unsigned i = 0; i < n; i++)
        d[i] = s[i];
    return dst;
}

/* Stubs for the flush=True path (std/io/io.mojo _fdopen dance). */
void *fdopen(int fd, const char *mode)
{
    (void)fd; (void)mode;
    return (void *)0;
}
int dup(int fd) { return fd; }
int fclose(void *f) { (void)f; return 0; }
int fflush(void *f) { (void)f; return 0; }
