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

/* ---------------- heap ----------------
 *
 * This was a bump allocator with a no-op free, which is fine for the globals
 * it was written for - created once, live for the whole program - and quietly
 * fatal for anything else. Mojo's String and List allocate and release
 * constantly, so `s += ...` in a loop leaks every intermediate, and 32K goes
 * in a few dozen iterations. What you see first is not an out-of-memory
 * message but wrong output, because the failing allocation returns into code
 * that carries on regardless.
 *
 * RISC OS supplies a real heap manager, so use it: OS_Heap (SWI &1D) does
 * first-fit allocation with coalescing over a block we own. Reason 0
 * initialises, 2 claims, 3 frees. R1 must be word-aligned, R3 a multiple of
 * four (PRM 1-355).
 *
 * Globals keep the bump path deliberately. They are allocated before the
 * heap is worth having, they are never freed by design, and keeping them out
 * of the heap keeps a long-lived allocation from fragmenting it.
 */
#define HEAP_BYTES 262144

static unsigned char heap_area[HEAP_BYTES] __attribute__((aligned(16)));
static int heap_ready;

static void heap_init(void)
{
    register int r0 __asm("r0") = 0;             /* initialise */
    register void *r1 __asm("r1") = heap_area;
    register u32 r3 __asm("r3") = HEAP_BYTES;
    __asm__ volatile("swi 0x2001D"               /* X form: no error trap */
                     : "+r"(r0), "+r"(r1), "+r"(r3)
                     : : "r2", "r12", "memory");
    heap_ready = 1;
}

static void *heap_alloc(u64 size)
{
    if (!heap_ready)
        heap_init();
    register int r0 __asm("r0") = 2;             /* claim block */
    register void *r1 __asm("r1") = heap_area;
    register void *r2 __asm("r2");
    register u32 r3 __asm("r3") = ((u32)size + 3) & ~3u;
    __asm__ volatile("swi 0x2001D"
                     : "+r"(r0), "+r"(r1), "=r"(r2), "+r"(r3)
                     : : "r12", "memory");
    return r2;
}

static void heap_free(void *p)
{
    if (!p || !heap_ready)
        return;
    register int r0 __asm("r0") = 3;             /* free block */
    register void *r1 __asm("r1") = heap_area;
    register void *r2 __asm("r2") = p;
    __asm__ volatile("swi 0x2001D"
                     : "+r"(r0), "+r"(r1), "+r"(r2)
                     : : "r3", "r12", "memory");
}

/* Bump arena, now only for globals and Wimp-lifetime structures. */
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
    for (unsigned i = 0; i < count; i++) {
        /* Mojo emits a bare LF for a newline, as everything POSIX does. To
         * the RISC OS VDU drivers LF means exactly "down one line" and
         * nothing else, so without a CR the next line starts in whatever
         * column the last one ended - output walks diagonally across the
         * screen and looks like the program is corrupting its own strings.
         * Send the pair, and let a CR the caller wrote itself pass through
         * without doubling it. */
        if (p[i] == '\n' && (i == 0 || p[i - 1] != '\r'))
            os_writec('\r');
        os_writec(p[i]);
    }
    return (long)count;
}

/* Long-lived bump allocation for Wimp structures (indirected buffers must
 * outlive the creating function's frame). Never freed. */
void *rostrt_alloc(unsigned size)
{
    return alloc_arena(size);
}

/* Allocator + support surface pulled in by stdlib print/format. */
/* The alignment argument is not advisory. OS_Heap promises word alignment and
 * nothing more (PRM 1-355), so honouring it means over-allocating and rounding
 * up ourselves. Ignoring it cost an afternoon: Mojo's String keeps short text
 * inline and spills to the heap past 24 bytes, and the spilled buffer is
 * declared over-aligned. Given a merely word-aligned block, the copy into it
 * stores wider than the address allows - and an ARMv4 does not fault on that,
 * it rotates - so a string of 25 characters came back with a hole in the
 * middle while its length stayed correct. The base pointer is stashed in the
 * word below the aligned one so free still knows what to hand back. */
void *KGEN_CompilerRT_AlignedAlloc(u64 alignment, u64 size)
{
    u32 a = (u32)alignment;
    if (a < 8)
        a = 8;
    unsigned char *raw = heap_alloc(size + a + sizeof(void *));
    if (!raw) {
        puts_ro("rostrt: out of heap\n");
        os_exit(1, 0);
    }
    unsigned char *p = raw + sizeof(void *);
    u32 off = (u32)(unsigned long)p & (a - 1);
    if (off)
        p += a - off;
    ((void **)p)[-1] = raw;
    return p;
}

void KGEN_CompilerRT_AlignedFree(void *p)
{
    if (p)
        heap_free(((void **)p)[-1]);
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

/* ---------------- AEABI memory helpers ----------------
 *
 * LLVM lowers struct copies and array initialisation to these rather than to
 * the C names, and picks the 4/8 suffixed variants when it can prove the
 * pointers are that aligned. They are the same operations; the suffix is a
 * promise about alignment, not a different contract, so one byte-wise body
 * serves all of them correctly (just not as quickly as a word loop would).
 *
 * The one trap: __aeabi_memset takes (dest, n, c) - length before value -
 * where C's memset takes (dest, c, n). Getting that backwards fills memory
 * with the length, which looks like corruption rather than a bad call.
 */

void *memset(void *dst, int c, unsigned n)
{
    unsigned char *d = dst;
    for (unsigned i = 0; i < n; i++)
        d[i] = (unsigned char)c;
    return dst;
}

void *memmove(void *dst, const void *src, unsigned n)
{
    unsigned char *d = dst;
    const unsigned char *s = src;
    if (d == s || n == 0)
        return dst;
    if (d < s) {
        for (unsigned i = 0; i < n; i++)
            d[i] = s[i];
    } else {
        for (unsigned i = n; i-- > 0;)
            d[i] = s[i];
    }
    return dst;
}

int memcmp(const void *a, const void *b, unsigned n)
{
    const unsigned char *x = a, *y = b;
    for (unsigned i = 0; i < n; i++)
        if (x[i] != y[i])
            return (int)x[i] - (int)y[i];
    return 0;
}

void __aeabi_memcpy(void *d, const void *s, unsigned n) { memcpy(d, s, n); }
void __aeabi_memcpy4(void *d, const void *s, unsigned n) { memcpy(d, s, n); }
void __aeabi_memcpy8(void *d, const void *s, unsigned n) { memcpy(d, s, n); }

void __aeabi_memmove(void *d, const void *s, unsigned n) { memmove(d, s, n); }
void __aeabi_memmove4(void *d, const void *s, unsigned n) { memmove(d, s, n); }
void __aeabi_memmove8(void *d, const void *s, unsigned n) { memmove(d, s, n); }

/* (dest, n, c): length first, value second - not the C argument order. */
void __aeabi_memset(void *d, unsigned n, int c) { memset(d, c, n); }
void __aeabi_memset4(void *d, unsigned n, int c) { memset(d, c, n); }
void __aeabi_memset8(void *d, unsigned n, int c) { memset(d, c, n); }

void __aeabi_memclr(void *d, unsigned n) { memset(d, 0, n); }
void __aeabi_memclr4(void *d, unsigned n) { memset(d, 0, n); }
void __aeabi_memclr8(void *d, unsigned n) { memset(d, 0, n); }
