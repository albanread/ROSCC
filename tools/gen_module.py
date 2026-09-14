#!/usr/bin/env python3
r"""Generate the RISC OS module header and entry veneers for a roscc module.

This is the job the DDE gives to CMHG: RISC OS calls a module through a
table of offsets and an assembly-level register contract that no C or Mojo
function can honour by itself — the V flag says whether a call failed, the
OS reads the header, the strings and the tables straight out of the image,
and a module's only legal state is workspace it claimed and hung off its
private word. So all of that is generated here, and everything behind it is
an ordinary C-ABI function that Mojo can `@export`.

The generated assembly puts the header and the OS-read tables in a section
called `.module`, which `roscc link --module` places at offset 0. Absolute
words are allowed there because they *are* module offsets; roscc rejects
them anywhere else, where they would be a load-address dependency.

Usage:

    gen_module.py --title MojoMod \
        --help-text "MojoMod\t1.00 (09 Sep 2026)" \
        --workspace 256 \
        --init mojomod_init --final mojomod_final \
        --command "MojoMod:mojomod_command:0:1:Runs the Mojo code" \
        --swi-chunk 0xC7000 --swi-prefix MojoMod \
        --swi "Add:mojomod_swi_add" --swi "Counter:mojomod_swi_counter" \
        --arch armv4 -o module_head.s

Veneer contract — plain C ABI throughout, so in Mojo each is an
`@export fn ... abi("C")`. Every entry is handed the module's workspace as
its last argument (zero when `--workspace` was not asked for):

    int init(void *ws)
    int final(unsigned fatal, void *ws)
    int command(const char *tail, int argc, void *ws)
    int swi(unsigned *regs, void *ws)      regs[0..9] = R0..R9, in and out

Returning zero means success. Anything else is passed back to RISC OS as an
error block pointer with V set, which is exactly the OS convention.

SWI chunk numbers (PRM 1-27): bits 19:18 say who implements the SWI —
11 is "user applications", so an unregistered module belongs at
&C0000 + 64*n. The kernel checks that the base is a multiple of 64 with a
zero top byte (PRM 1-223) and ignores the SWI fields otherwise, so this
script checks the same thing rather than letting the module load with its
SWIs silently missing.
"""
import argparse
import sys

CHUNK_SIZE = 64          # fixed by the OS (PRM 1-223)
SWI_REGS = 10            # R0-R9 are passed to and from a SWI handler
ERR_BAD_SWI = 0x1E6      # the system's own "bad SWI" error number


def c_ident(name):
    return "".join(ch if ch.isalnum() else "_" for ch in name)


def emit_veneer_init(out, csym, workspace, rwpi):
    """Initialisation (&04). R10 = environment, R11 = instantiation,
    R12 -> private word, R13 = SVC stack. Must preserve R7-R11 and R13;
    AAPCS callee-saves R4-R11 across the C call, but this veneer uses R4
    and R9 itself, so it saves them.

    Under RWPI this is where a module gets its static data: claim RMA, copy
    the initialised part out of the image, zero the rest, and leave the
    pointer in the private word. Every later entry reads it back into R9.
    """
    if not (workspace or rwpi):
        return out.append(f"""
	.global	__mod_init
	.type	__mod_init, %function
__mod_init:
	stmfd	sp!, {{r0-r3, r12, lr}}
	mov	r0, #0			@ no static area asked for
	bl	{csym}
	cmp	r0, #0
	bne	1f
	ldmfd	sp!, {{r0-r3, r12, lr}}
	msr	cpsr_f, #0
	mov	pc, lr
1:	add	sp, sp, #4		@ drop the saved R0; keep the error pointer
	ldmfd	sp!, {{r1-r3, r12, lr}}
	msr	cpsr_f, #(1 << 28)
	mov	pc, lr
""")

    # How much to claim: the linker's static-data figure plus any extra the
    # module asked for. Without RWPI there is no static data, so it is just
    # the extra.
    size = ("\tldr	r3, [r4, #8]		@ static-data size, from the linker\n"
            "\tadd	r3, r3, #23		@ two words below the base, plus\n"
            "\t\t\t\t\t@ room to align it to 16\n"
            if rwpi else "\tmov	r3, #0\n")
    extra = f"\tadd	r3, r3, #{workspace}\n" if workspace else ""
    copy = ""
    if rwpi:
        copy = """
	@ The module's own base address: the parameter block knows its own
	@ offset, and ADR just gave us where it really is.
	ldr	r0, [r4, #12]
	sub	r0, r4, r0		@ r0 = module base
	ldr	r1, [r4]		@ + offset of the initial static data
	add	r0, r0, r1
	ldr	r1, [r4, #4]		@ bytes to copy
	mov	r2, r9
1:	subs	r1, r1, #4
	blo	2f
	ldr	r12, [r0], #4
	str	r12, [r2], #4
	b	1b
2:	ldr	r1, [r4, #8]		@ zero the rest of the static area
	add	r1, r9, r1
	mov	r12, #0
3:	cmp	r2, r1
	strlo	r12, [r2], #4
	blo	3b
"""
    out.append(f"""
	@ Linker-filled: [0] offset of the static data's initial contents,
	@ [1] bytes to copy, [2] total static size, [3] this block's own
	@ offset in the module. Read-only, and deliberately in .text so the
	@ code below can reach it with ADR.
	@ .Lparams is the same address under a local name. ADR to a global
	@ symbol makes the assembler emit a relocation for the linker instead
	@ of resolving it here; to a local label in the same section it just
	@ does the arithmetic, which is what a position-independent module
	@ needs.
	.balign	4
	.global	__mod_params
__mod_params:
.Lparams:
	.int	0
	.int	0
	.int	0
	.int	0

	.global	__mod_init
	.type	__mod_init, %function
__mod_init:
	stmfd	sp!, {{r0-r4, r9, r12, lr}}
	adr	r4, .Lparams
{size}{extra}	mov	r0, #6			@ OS_Module 6 = Claim
	swi	0x2001E			@ XOS_Module: return errors, never take them
	ldr	r12, [sp, #24]		@ SWIs may corrupt R12; ours is on the stack
	bvs	9f			@ claim failed: R0 already -> error block
	@ OS_Module promises a word-aligned block; statics here ask for
	@ sixteen. So the static base is the block aligned up, past two words
	@ that record what the later entries need: the block itself, to give
	@ back, and the module's own workspace, which starts *after* the static
	@ data — a workspace overlapping the statics would quietly scribble on
	@ whichever one happened to be at offset zero.
	add	r9, r2, #23
	bic	r9, r9, #15
	str	r2, [r9, #-4]		@ the block, for finalisation
	ldr	r1, [r4, #8]		@ static-data size
	add	r1, r9, r1
	str	r1, [r9, #-8]		@ workspace, past the statics
	str	r9, [r12]		@ private word -> the static base
	adr	r4, .Lparams
{copy}	ldr	r0, [r9, #-8]		@ the workspace, not the static base
	bl	{csym}
	cmp	r0, #0
	bne	8f
	ldmfd	sp!, {{r0-r4, r9, r12, lr}}
	msr	cpsr_f, #0
	mov	pc, lr

	@ The C side refused. Give the static area back before reporting, or
	@ the RMA keeps it for a module that is not going to exist.
8:	mov	r4, r0
	ldr	r12, [sp, #24]
	mov	r0, #7			@ OS_Module 7 = Free
	ldr	r2, [r12]
	ldr	r2, [r2, #-4]		@ the block, not the base within it
	swi	0x2001E
	ldr	r12, [sp, #24]
	mov	r0, #0
	str	r0, [r12]
	mov	r0, r4
9:	add	sp, sp, #4		@ drop the saved R0; keep the error pointer
	ldmfd	sp!, {{r1-r4, r9, r12, lr}}
	msr	cpsr_f, #(1 << 28)
	mov	pc, lr
""")


def emit_veneer_final(out, csym, has_area):
    """Finalisation (&08). R10 = 0 non-fatal / 1 fatal, R12 -> private word.
    An error here refuses the kill, so only the C side may raise one."""
    ws = ("\tldr	r9, [r12]\n\tldr	r1, [r9, #-8]\n" if has_area
          else "\tmov	r1, #0\n")
    free = ""
    if has_area:
        free = """
	@ Give the static area back, once the module has agreed to go. R12 is
	@ reloaded first: the C call was free to corrupt it, and so is the SWI.
	ldr	r12, [sp, #16]
	mov	r0, #7			@ OS_Module 7 = Free
	ldr	r2, [r12]
	ldr	r2, [r2, #-4]		@ the block, not the base within it
	swi	0x2001E
	ldr	r12, [sp, #16]
	mov	r0, #0
	str	r0, [r12]		@ a freed pointer is worse than none
"""
    out.append(f"""
	.global	__mod_final
	.type	__mod_final, %function
__mod_final:
	stmfd	sp!, {{r1-r3, r9, r12, lr}}
	mov	r0, r10			@ the fatal flag, as the C argument
{ws}	bl	{csym}
	cmp	r0, #0
	bne	1f
{free}	ldmfd	sp!, {{r1-r3, r9, r12, lr}}
	msr	cpsr_f, #0
	mov	pc, lr
1:	ldmfd	sp!, {{r1-r3, r9, r12, lr}}
	msr	cpsr_f, #(1 << 28)
	mov	pc, lr
""")


def emit_veneer_command(out, label, csym, has_area):
    """A * command. R0 -> tail and R1 = argument count are already the first
    two C argument registers, so only the static base needs fetching.

    Branch on the comparison rather than conditionalising the MSR after the
    stack pop: the first MSR would rewrite the very flags the second one
    tests, and both would fire.
    """
    ws = ("\tldr	r9, [r12]\n\tldr	r2, [r9, #-8]\n" if has_area
          else "\tmov	r2, #0\n")
    out.append(f"""
	@ *{label[6:]} — SVC mode, interrupts enabled (PRM 1-219).
	.global	{label}
	.type	{label}, %function
{label}:
	stmfd	sp!, {{r4, r9, lr}}
{ws}	bl	{csym}
	cmp	r0, #0
	bne	1f
	ldmfd	sp!, {{r4, r9, lr}}
	msr	cpsr_f, #0		@ V clear: handled
	mov	pc, lr
1:	ldmfd	sp!, {{r4, r9, lr}}
	msr	cpsr_f, #(1 << 28)	@ V set: R0 -> error block
	mov	pc, lr
""")


def emit_swi_handler(out, swis, has_area, title):
    """SWI handler (&20). R11 = SWI number within the chunk, R12 -> private
    word, R14 = return address.

    The caller's R0-R9 are pushed as a block the handler can read and write,
    which is what makes a SWI expressible as a C function at all. Dispatch is
    the PRM's own idiom (1-223): a bounds check, then PC-relative addition
    into a branch table — position independent, which a table of addresses
    would not be.

    Note the 26-bit `ORRS PC, R14, #V` idiom in the PRM does not apply on
    RISC OS 5: in 32-bit mode R14 is an address, not address-plus-flags, so
    the flags are set with MSR exactly as in the other veneers."""
    n = len(swis)
    frame = 4 * SWI_REGS                # r0-r9; lr is popped separately
    # R9 is part of the block just pushed, so the caller's value is already
    # safe and the register is free to become the static base.
    ws_load = ("\tldr	r9, [r12]\n\tldr	r1, [r9, #-8]\n" if has_area
               else "\tmov	r1, #0\n")
    table = "".join(f"\tb	__swi_v{i}\n" for i in range(n))
    veneers = "".join(
        f"""__swi_v{i}:
	bl	{s['csym']}		@ {title}_{s['name']}
	b	__swi_ret
""" for i, s in enumerate(swis))

    out.append(f"""
	.global	__mod_swi
	.type	__mod_swi, %function
__mod_swi:
	stmfd	sp!, {{r0-r9, lr}}	@ the caller's registers, as a block
	mov	r0, sp			@ -> that block
{ws_load}	cmp	r11, #{n}
	addlo	pc, pc, r11, lsl #2	@ exactly one instruction below this
	b	__swi_unknown
{table}{veneers}
__swi_ret:
	cmp	r0, #0
	bne	1f
	ldmfd	sp!, {{r0-r9, lr}}	@ hand back whatever the handler wrote
	msr	cpsr_f, #0
	mov	pc, lr
1:	mov	r11, r0			@ the error pointer, parked where the
	ldmfd	sp!, {{r0-r9, lr}}	@ unwind cannot reach it: R10-R12 are
	mov	r0, r11			@ ours to corrupt here (PRM 1-222)
	msr	cpsr_f, #(1 << 28)
	mov	pc, lr

__swi_unknown:
	add	sp, sp, #{frame}		@ drop the register block, unread
	ldmfd	sp!, {{lr}}
	adr	r0, __swi_bad_error	@ PC-relative: a module has no fixed address
	msr	cpsr_f, #(1 << 28)
	mov	pc, lr

	.balign	4
__swi_bad_error:
	.int	{ERR_BAD_SWI}
	.asciz	"Unknown {title} SWI"
	.balign	4
""")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--title", required=True,
                    help="module name as *Modules / *RMKill see it")
    ap.add_argument("--help-text", default=None,
                    help=r"*Help line, conventionally 'Name\t1.00 (date)'")
    ap.add_argument("--rwpi", action="store_true",
                    help="the module has static data: claim a static area at "
                         "init, copy the initialised part in, and establish "
                         "the static base (r9) on every entry. Build C with "
                         "-fropi -frwpi and Mojo with "
                         "--target-features +reserve-r9")
    ap.add_argument("--workspace", type=int, default=0, metavar="BYTES",
                    help="claim this much RMA at init on top of any static "
                         "data, and pass the pointer to every entry point")
    ap.add_argument("--init", default=None, metavar="CSYM")
    ap.add_argument("--final", default=None, metavar="CSYM")
    ap.add_argument("--command", action="append", default=[], metavar="SPEC",
                    help="NAME:CSYM[:MIN:MAX[:HELP]] — repeatable")
    ap.add_argument("--swi", action="append", default=[], metavar="SPEC",
                    help="NAME:CSYM — repeatable, in chunk order from 0")
    ap.add_argument("--swi-chunk", default=None,
                    help="chunk base, e.g. 0xC7000 (user-application range)")
    ap.add_argument("--swi-prefix", default=None,
                    help="SWI group prefix; defaults to --title")
    ap.add_argument("--arch", default="armv4",
                    help="armv4 (StrongARM/RPCEmu) or armv8-a (Pi 4)")
    ap.add_argument("-o", "--output", required=True)
    a = ap.parse_args()

    help_text = a.help_text or (a.title + r"\t1.00")

    commands = []
    for spec in a.command:
        parts = spec.split(":", 4)
        if len(parts) < 2:
            sys.exit(f"gen_module: --command needs NAME:CSYM, got {spec!r}")
        name, csym = parts[0], parts[1]
        commands.append(dict(
            name=name, csym=csym,
            min=int(parts[2]) if len(parts) > 2 and parts[2] else 0,
            max=int(parts[3]) if len(parts) > 3 and parts[3] else 255,
            help=parts[4] if len(parts) > 4 else f"*{name}",
            label="__cmd_" + c_ident(name)))

    swis = []
    for spec in a.swi:
        parts = spec.split(":", 1)
        if len(parts) != 2:
            sys.exit(f"gen_module: --swi needs NAME:CSYM, got {spec!r}")
        swis.append(dict(name=parts[0], csym=parts[1]))

    chunk = 0
    if swis or a.swi_chunk:
        if not a.swi_chunk:
            sys.exit("gen_module: --swi needs --swi-chunk")
        chunk = int(a.swi_chunk, 0)
        # The kernel's own checks (PRM 1-223). It ignores the SWI fields
        # rather than complaining, so a module with a bad chunk loads and
        # then has no SWIs at all — worth catching here instead.
        if chunk % CHUNK_SIZE:
            sys.exit(f"gen_module: SWI chunk {chunk:#x} is not a multiple "
                     f"of {CHUNK_SIZE}; the kernel would ignore it")
        if chunk >> 24:
            sys.exit(f"gen_module: SWI chunk {chunk:#x} has a non-zero top "
                     "byte; the kernel would ignore it")
        if len(swis) > CHUNK_SIZE:
            sys.exit(f"gen_module: {len(swis)} SWIs but a chunk holds "
                     f"{CHUNK_SIZE}")
        owner = (chunk >> 18) & 3
        if owner != 3:
            who = ["the operating system", "OS extension modules",
                   "third-party resident applications"][owner]
            print(f"gen_module: warning: chunk {chunk:#x} is in the range "
                  f"reserved for {who} (PRM 1-27); unregistered modules "
                  f"belong at &C0000 + 64*n", file=sys.stderr)

    prefix = a.swi_prefix or a.title
    o = []
    o.append(f"""@ Generated by tools/gen_module.py — do not edit.
@ RISC OS relocatable module header and entry veneers for "{a.title}".
@ Link with: roscc link --module

	.syntax unified
	.arch	{a.arch}
	.code	32

	@ The header and everything RISC OS reads out of the image. roscc
	@ places this section at offset 0; the absolute words below are
	@ module offsets, which is why they are legal only here.
	.section .module, "a", %progbits
	.balign	4
	.global	module_header
module_header:
	.int	0			@ &00 start code (not an application)
	.int	{"__mod_init" if a.init else "0"}			@ &04 initialisation
	.int	{"__mod_final" if a.final else "0"}			@ &08 finalisation
	.int	0			@ &0C service call handler
	.int	__mod_title		@ &10 title string
	.int	__mod_help		@ &14 help string
	.int	{"__mod_commands" if commands else "0"}		@ &18 help and command table
	.int	{f"{chunk:#x}" if swis else "0"}			@ &1C SWI chunk base
	.int	{"__mod_swi" if swis else "0"}		@ &20 SWI handler
	.int	{"__mod_swi_names" if swis else "0"}	@ &24 SWI decoding table
	.int	0			@ &28 SWI decoding code
	.int	0			@ &2C messages file
	.int	__mod_flags		@ &30 module flags

__mod_flags:
	.int	1			@ bit 0: 32-bit compatible. RISC OS 5
					@ refuses the module without it.

__mod_title:
	.asciz	"{a.title}"
	.balign	4
__mod_help:
	.asciz	"{help_text}"
	.balign	4
""")

    if commands:
        o.append("""
	@ Help and command keyword table (PRM 1-219): per command a matched
	@ string, then code offset, information word, invalid-syntax offset
	@ and help offset. A zero byte ends the table.
__mod_commands:
""")
        for c in commands:
            info = (c["min"] & 0xFF) | ((c["max"] & 0xFF) << 16)
            o.append(f"""	.asciz	"{c['name']}"
	.balign	4
	.int	{c['label']}		@ code offset
	.int	{info:#010x}		@ min {c['min']}, max {c['max']} parameters
	.int	0			@ invalid syntax: default message
	.int	{c['label']}_help	@ help text
""")
        o.append("""	.byte	0			@ end of table
	.balign	4
""")
        for c in commands:
            o.append(f"""{c['label']}_help:
	.asciz	"{c['help']}"
	.balign	4
""")

    if swis:
        names = "".join(f'\t.asciz\t"{s["name"]}"\n' for s in swis)
        o.append(f"""
	@ SWI decoding table (PRM 1-225): the group prefix, then one name per
	@ SWI in chunk order, then a zero byte. This is what turns
	@ SYS "{prefix}_{swis[0]['name']}" in BASIC into {chunk:#x}, and what
	@ *ShowRegs-style tools print instead of a bare number.
__mod_swi_names:
	.asciz	"{prefix}"
{names}	.byte	0
	.balign	4
""")

    o.append("""
	@ Veneers. RISC OS's register and V-flag contract on one side, the
	@ plain C ABI Mojo emits on the other.
	.text
""")
    # One question decides all four veneers: does this module have an area
    # of its own, and therefore a pointer in the private word to fetch?
    has_area = bool(a.rwpi or a.workspace)
    if a.init:
        emit_veneer_init(o, a.init, a.workspace, a.rwpi)
    elif has_area:
        sys.exit("gen_module: --rwpi/--workspace need --init: something has "
                 "to claim the area")
    if a.final:
        emit_veneer_final(o, a.final, has_area)
    elif has_area:
        sys.exit("gen_module: --rwpi/--workspace need --final: something has "
                 "to give the area back")
    for c in commands:
        emit_veneer_command(o, c["label"], c["csym"], has_area)
    if swis:
        emit_swi_handler(o, swis, has_area, prefix)

    with open(a.output, "w", newline="\n") as f:
        f.write("".join(o))

    swi_note = ""
    if swis:
        swi_note = (f", {len(swis)} SWI(s) at {chunk:#x}-"
                    f"{chunk + len(swis) - 1:#x}")
    area = "static data + " if a.rwpi else ""
    print(f"gen_module: {a.output} — {a.title}, {len(commands)} command(s)"
          f"{swi_note}, area {area}{a.workspace} bytes, "
          f"init={a.init or 'none'}, final={a.final or 'none'}")


if __name__ == "__main__":
    main()
