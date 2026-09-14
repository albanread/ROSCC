/* rwpi_check — does the static-base model actually work?
 *
 * Two statics, deliberately of different kinds:
 *
 *   counter  lives in .bss, so it exists only because initialisation claimed
 *            a static area and zeroed it;
 *   seed     lives in .data, so it exists only because the linker carried its
 *            initial value in the image and initialisation copied it in.
 *
 * Both are reached as offsets from r9. If either comes back wrong, the
 * static-base model is broken somewhere between the compiler, the linker's
 * second address space and the veneer that establishes r9 — and it is worth
 * knowing that before blaming anything more interesting.
 */

static int counter;             /* .bss: zeroed at initialisation */
static int seed = 0x5A5A;       /* .data: copied at initialisation */

int rwpi_check(int *out_seed)
{
    if (out_seed)
        *out_seed = seed;
    return ++counter;
}
