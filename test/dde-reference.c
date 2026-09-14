/* ddetest — the DDE reference client for the binding differential:
 * the same stateful path (registration, _kernel_init, malloc/stdio)
 * that roclib drives, built the way the library's own clients build. */
#include <stdio.h>
#include <string.h>

int main(void)
{
    printf("dde: strlen=%d\n", (int)strlen("abcdef"));
    FILE *f = fopen("HostFS:$.Farm.Build.c.ddetest", "rb");
    if (f) {
        fseek(f, 0, SEEK_END);
        printf("dde: size=%ld\n", ftell(f));
        fclose(f);
    } else {
        printf("dde: fopen failed\n");
    }
    return 0;
}
