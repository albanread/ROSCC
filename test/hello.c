/* hello — the roscc smoke test: compile with clang (macOS host),
 * link with roscc, run on a bigmacfarm guest. */

extern void OS_Write0(const char *);

int main(int argc, char **argv)
{
    OS_Write0("Hello from clang + roscc, built on the Mac Pro!\n");
    for (int i = 1; i < argc; i++) {
        OS_Write0("arg: ");
        OS_Write0(argv[i]);
        OS_Write0("\n");
    }
    return 0;
}
