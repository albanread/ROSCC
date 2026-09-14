/* rostrt — hand Wimp shims for the two SWIs whose contracts carry the
 * 'TASK' magic constant (&4B534154) on a register that is ALSO an output.
 * Everything else is generated: see tools/gen_riscos_pkg.py -> swis_*.c */

/* Wimp_Initialise (SWI &400C0):
 * in:  r0 = last known Wimp version x100, r1 = 'TASK', r2 = task name,
 *      r3 = pointer to a 0-terminated list of wanted message numbers
 * out: r0 = actual version x100, r1 = task handle
 *
 * r3 is read whenever r0 >= 300 (PRM 3-114), and we pass 310. It used to be
 * named only in the clobber list, which told the compiler r3 was free scratch
 * - so LLVM staged the 'TASK' constant through it (ldr r3, =TASK; mov r1, r3)
 * and left it live at the SWI. The Wimp then followed &4B534154 as a message
 * list and took a data abort inside the ROM. A register the SWI reads is an
 * input, never a clobber. 0 means every message matters to this task. */
int Wimp_Initialise(int version, const char *name, int *out_version)
{
    register int r0 __asm("r0") = version;
    register int r1 __asm("r1") = 0x4B534154; /* 'TASK' */
    register const char *r2 __asm("r2") = name;
    register const int *r3 __asm("r3") = 0; /* all messages */
    __asm__ volatile("swi 0x400C0"
                     : "+r"(r0), "+r"(r1)
                     : "r"(r2), "r"(r3)
                     : "r12", "lr", "memory");
    if (out_version)
        *out_version = r0;
    return r1; /* task handle */
}

/* Wimp_CloseDown (SWI &400DD): in r0 = task handle, r1 = 'TASK' */
void Wimp_CloseDown(int task)
{
    register int r0 __asm("r0") = task;
    register int r1 __asm("r1") = 0x4B534154;
    __asm__ volatile("swi 0x400DD"
                     :
                     : "r"(r0), "r"(r1)
                     : "r2", "r3", "r12", "lr", "memory");
}
