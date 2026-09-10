# roscc — urgent next steps (2026-09-10)

From the review of 2026-09-10. Everything here was checked against the tree
as it stood that day: roscc built clean, the demo module was rebuilt from the
current sources and passed its 48 live checks on RPCEmu, a Mojo application
ran on RPCEmu, and the probes quoted below were run against the built binary.

One decision, taken the same day, shapes the order: **the Pi 4 profile
(`riscos-a72`) is tested on the QEMU fork, not on RPCEmu.** The plan to teach
RPCEmu's interpreter A72 instructions (`rpcemu\A72-MODE-PLAN.md`) is
superseded. RPCEmu stays the `riscos-sa` (StrongARM, ARMv4) sandbox.

What roscc is today: an LLVM-IR ingester, a static linker to AIF and to
relocatable modules, the module header and veneer generator, and a
freestanding runtime, aimed at Mojo output. It is not yet the clang-fronted C
compiler `docs\TOOLCHAIN-DESIGN.md` describes; that is Track B work and not on
this list.

## 0. Commit the module work

The whole module path is untracked: `src/module.rs`, `rostrt/modrt.c`,
`tools/gen_module.py`, `tools/build-module.sh`, `tools/module_test.py`,
`tests/`. It is the best work in the crate and it passes its test. Commit it
before anything below touches it, with `docs/MODULES.md` as its description.

## 1. Pi 4 profile: make it link, then give it a runner

Blocks everything `riscos-a72`. Today only the StrongARM build of the module
has ever run (`docs/MODULES.md` §8), and the Pi 4 build does not link:

    TRIPLE=armv8a-none-eabi CPU=cortex-a72 ARCH=armv8-a RT=a72 bash tools/build-module.sh
    roscc: module link failed: rostrt.o: argbuf is in writable data but is
    reached by relocation type 84, which cannot express a static-base offset.

- **Four relocation types.** With `-fropi -frwpi`, clang for ARMv8 reaches
  statics through MOVW/MOVT pairs, not literal pools. `rostrt.c` built for
  `cortex-a72` carries 22 `R_ARM_MOVW_BREL_NC` (84), 22 `R_ARM_MOVT_BREL` (85),
  4 `R_ARM_MOVW_PREL_NC` (45) and 4 `R_ARM_MOVT_PREL` (46); the same file for
  `strongarm110` carries 18 `R_ARM_SBREL32` and 4 `R_ARM_REL32`, which is all
  `src/module.rs` and `src/aif.rs` know. Add the four (and `R_ARM_MOVW_BREL`,
  86, for completeness): same immediate packing as the existing MOVW/MOVT ABS
  cases, with S the static-base offset for BREL and S + A − P per half for
  PREL. Acceptance: the command above links, and `roscc link --module`
  refuses nothing it should accept.
- **A runner on the QEMU fork.** `tools/module_test.py` drives RPCEmu through
  its portal; nothing drives the Pi 4. The delivery path is the fork's HostFS
  over the vmchannel doorbell (`qemu\riscos-pi4\FSDESIGN.md`, sprint 6B):
  drop `,ff8` and `,ffa` files into the HostFS root, `*RMLoad` and `*Run`
  them with typed keys over QMP, read the screen back with `screendump` or
  `pmemsave`. Until 6B lands there is no a72 runner, which puts 6B on this
  team's critical path too. Acceptance: `module_test.py --emu qemu` passes
  the same 48 checks with the a72 module on the Pi 4 machine.
- **Runtime on the Pi 4.** `rostrt/atomics_ldrex.s` and the A72 runtime
  objects exist and have never executed on an A72. The first QEMU run will
  say whether `bx`, `movw/movt` and LDREX behave under the real ROM.

## 2. Linker correctness, both profiles

- **The AIF linker ignores section alignment.** `src/aif.rs` lays sections
  out at 4-byte boundaries; `src/module.rs` honours `align`. Probe: a global
  declared `aligned(16)` linked through the AIF path landed at `0x8098`. On a
  Cortex-A72 that is a fault the first time NEON or an alignment-checked
  load touches it. Use `align_to` in the AIF layout, and make the module
  sidecar's symbol pass reuse the layout from `build()` instead of
  recomputing it with `align4`, or its symbol addresses drift from the image.
- **Duplicate definitions are accepted silently.** Linking the same object
  twice succeeds, last definition wins. Make it an error naming both objects.
- **Weak symbols are not handled.** `STB_WEAK` is treated as local, so a
  `linkonce_odr` function referenced from another object reports undefined.
  Mojo emits those for templates; the demos survive only because their
  references stay within one object. Strong overrides weak, several weak
  definitions are fine, an undefined weak resolves to zero.
- **Branch addends are discarded.** `R_ARM_CALL` and `R_ARM_JUMP24` assume
  the standard minus-eight. A branch to a section symbol plus offset, which
  LLVM produces for cross-section jumps, lands in the wrong place. Use
  S + A − P with A the sign-extended in-place immediate times four.
- **Malformed input panics.** The ELF reader slices without bounds checks.
  A bad object should be an error message, not a Rust panic.
- **No unit tests.** The crate has none; the only test is the emulator
  harness. Add synthetic-object tests for every relocation type, for the
  duplicate, weak and alignment cases above, and a layout test per profile.

## 3. Runtime and shims

- **X-form SWI shims.** `tools/gen_riscos_pkg.py` emits 77 shims; 12 use the
  X form and none reads V, so an error from a plain-form SWI goes to the
  system error handler and kills the caller. Inside a module that is the
  caller's context, not the module's. Generate an `XName` variant per SWI
  returning the error block pointer or null, as `tests/module/swicall.c`
  does by hand.
- **UTF-8 into a Latin-1 console.** The application's em dash printed as
  `â`. Transcode in the runtime's `write()`, or document that output is
  Latin-1 and strings must be plain ASCII.
- **`print()` with formatted arguments inside a module** still interleaves
  its fragments (`docs/MODULES.md` §8). The SVC stack is the open suspect;
  measure the depth Mojo's formatter uses before guessing further.

## 4. Tooling hygiene

- `tools/run_on_emu.py` is stale: it calls a `cli` RPC method the current
  RPCEmu no longer has (`-32601`). The portal path in `module_test.py` is the
  working one. Fix it to use the portal, or delete it; the QEMU runner in §1
  replaces it for a72.
- `TRIPLE_SA` in `src/main.rs` is never used: `--cpu strongarm110` compiles
  under the ARMv8 triple and only the CPU's feature set keeps the output
  ARMv4 (it does, today). Pass the triple that matches the CPU so the ELF
  attributes and default features agree with what `tools/build-rt.sh` builds.
- Remove the hard-coded log path behind `ROSCC_ARGLOG`, or make it a proper
  option.
- `--rt` finds the runtime three directories above the executable, which
  breaks for an installed binary. Take an install-relative default and a
  `ROSCC_RT` override.
- Clear the four build warnings: an unused `resolve_sym`, fields never read.

## 5. Verified working, so nobody re-verifies it

| Check | Result |
|---|---|
| `cargo build --release` | clean, 4 warnings |
| Demo module rebuilt from the tree, live on RPCEmu | 48 of 48 checks pass, cold boot 8 s |
| `natural,ff8`, a Mojo application, on RPCEmu | prints through Mojo `print` and `os.write0`, computes |
| `--cpu strongarm110` output | literal-pool loads and `mov pc, lr`, no `movw`, no `bx` |
| Pi 4 application path | links (MOVW/MOVT ABS handled); never executed |
| Pi 4 module path | fails at link, relocation 84 |

Order: 0, then the relocations in 1, then the alignment and duplicate fixes
in 2, then the QEMU runner as soon as HostFS 6B lands, then 3 and 4.
