/* qsortmin — a MINIMAL stateful program that hangs BEFORE main runs:
 * not a qsort bug (the call is never reached — zero output, and a live
 * capture shows the spin in kernel XOS_ReadVarVal loops during CLib
 * init, with r9 = our statics, so our client context).  clibstate —
 * a BIGGER binary with the same objects and flow — is green 6/6 on a
 * boot where this hangs 6/6: binary-layout dependent, boot-state
 * dependent.  Prime suspects: the module's 4 K stack carve colliding
 * with a page boundary as __image_end moves, or the RTSK region
 * landing somewhere the variable machinery mishandles.  The lldb
 * recipe: break at real_main; if never hit, sample with the dbg.py
 * client and read the spin's module context via r9. */

extern void OS_Write0(const char *);
extern void roclib_run(int (*)(int, char **)) __attribute__((noreturn));
extern void qsort(void *, unsigned long, unsigned long,
                  int (*)(const void *, const void *));

extern void OS_WriteC(int);

static int ncmp;

static int cmp_int(const void *a, const void *b)
{
    extern void OS_WriteI(void);
    ncmp++;
    OS_WriteC(46);                     /* '.' per callback */
    int x = *(const int *)a, y = *(const int *)b;
    return (x > y) - (x < y);
}

static int real_main(int argc, char **argv)
{
    static int arr[9] = { 5, 3, 9, 1, 7, 2, 8, 6, 4 };
    OS_Write0("calling qsort...\n");
    qsort(arr, 9, sizeof(int), cmp_int);
    OS_Write0("\nback from qsort, callbacks: ");
    OS_Write0(ncmp ? "some\n" : "ZERO\n");
    int sorted = 1;
    for (int i = 0; i < 9; i++)
        sorted &= arr[i] == i + 1;
    OS_Write0(sorted ? "qsortmin: sorted ok\n" : "qsortmin: SORT FAIL\n");
    return !sorted;
}

int main(void) { roclib_run(real_main); }
