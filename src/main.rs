//! roscc — RISC OS 5 compiler toolchain (scaffold).
//!
//! Subcommands:
//!   demo                          emit Cortex-A72 smoke-test objects
//!   ingest <file.ll> [-o out.o]   parse LLVM IR (e.g. Mojo's --emit llvm),
//!                                 retarget to RISC OS ARM, emit an object
//!   link [opts] <objs...>         link ELF objects at &8000 into a RISC OS
//!                                 executable AIF (plus .elf sidecar)
//!   link --module <objs...>       link at base 0 into a RISC OS relocatable
//!                                 module (&FFA), header from the `.module`
//!                                 section (plus .elf sidecar for symbols)
//!
//! Targets: riscos-a72 (cortex-a72, Pi 4) and riscos-sa (strongarm110,
//! RPCEmu sandbox). Default -mcpu follows --cpu.

mod aif;
mod elf;
mod module;

#[cfg(feature = "llvm")]
use std::ffi::{c_char, CStr, CString};

#[cfg(feature = "llvm")]
use llvm_sys::analysis::{LLVMVerifierFailureAction, LLVMVerifyModule};
#[cfg(feature = "llvm")]
use llvm_sys::core::*;
#[cfg(feature = "llvm")]
use llvm_sys::ir_reader::LLVMParseIRInContext2;
#[cfg(feature = "llvm")]
use llvm_sys::prelude::*;
#[cfg(feature = "llvm")]
use llvm_sys::target_machine::LLVMTargetMachineRef;
#[cfg(feature = "llvm")]
use llvm_sys::target_machine::{
    LLVMCodeModel, LLVMCodeGenFileType, LLVMCodeGenOptLevel, LLVMCreateTargetMachine,
    LLVMDisposeTargetMachine, LLVMGetTargetFromTriple, LLVMRelocMode,
    LLVMTargetMachineEmitToFile, LLVMCreateTargetDataLayout,
};

#[cfg(feature = "llvm")]
const TRIPLE_A72: &str = "armv8a-none-eabi";
#[cfg(feature = "llvm")]
const TRIPLE_SA: &str = "armv4-none-eabi";

#[cfg(feature = "llvm")]
extern "C" {
    fn LLVMInitializeARMTargetInfo();
    fn LLVMInitializeARMTarget();
    fn LLVMInitializeARMTargetMC();
    fn LLVMInitializeARMAsmParser();
    fn LLVMInitializeARMAsmPrinter();
}

#[cfg(feature = "llvm")]
fn cstr(s: &str) -> CString {
    CString::new(s).expect("no NUL in string")
}

#[cfg(feature = "llvm")]
unsafe fn make_target_machine(cpu: &str) -> (CString, LLVMTargetMachineRef) {
    let triple = cstr(TRIPLE_A72);
    let mut target = std::ptr::null_mut();
    let mut terr: *mut c_char = std::ptr::null_mut();
    if LLVMGetTargetFromTriple(triple.as_ptr(), &mut target, &mut terr) != 0 {
        eprintln!(
            "roscc: ARM target unavailable: {}",
            CStr::from_ptr(terr).to_string_lossy()
        );
        std::process::exit(1);
    }
    let feats = cstr("");
    let tm = LLVMCreateTargetMachine(
        target,
        triple.as_ptr(),
        cstr(cpu).as_ptr(),
        feats.as_ptr(),
        LLVMCodeGenOptLevel::LLVMCodeGenLevelDefault,
        LLVMRelocMode::LLVMRelocStatic,
        LLVMCodeModel::LLVMCodeModelDefault,
    );
    (triple, tm)
}

#[cfg(feature = "llvm")]
unsafe fn emit(
    tm: *mut llvm_sys::target_machine::LLVMOpaqueTargetMachine,
    m: LLVMModuleRef,
    path: &str,
    kind: LLVMCodeGenFileType,
) {
    let p = cstr(path);
    let mut err: *mut c_char = std::ptr::null_mut();
    let rc = LLVMTargetMachineEmitToFile(tm, m, p.as_ptr(), kind, &mut err);
    if rc != 0 {
        eprintln!(
            "roscc: emit to {path} failed: {}",
            CStr::from_ptr(err).to_string_lossy()
        );
        LLVMDisposeMessage(err);
        std::process::exit(1);
    }
}

fn usage() -> ! {
    eprintln!(
        "usage: roscc demo\n       roscc ingest <in.ll> [-o out.o] [--cpu strongarm110|cortex-a72]\n       roscc link [--entry sym] [-o out,ff8] <objs...>\n       roscc link --module [-o out,ffa] <objs...>"
    );
    std::process::exit(2)
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    // ROSCC_ARGLOG=<file> appends the argument list to that file, for
    // working out what a build script actually invoked. The variable names
    // the file rather than merely switching a fixed one on: a hardcoded
    // path is one developer's machine, and this repository has two
    // platforms building from it. Failing to open it is not worth stopping
    // a compile over, so it is ignored.
    if let Ok(path) = std::env::var("ROSCC_ARGLOG") {
        use std::io::Write;
        if let Ok(mut f) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
        {
            for a in &args {
                let _ = writeln!(f, "arg[{:?}]", a);
            }
        }
    }
    match args.first().map(|s| s.as_str()) {
        Some("demo") => {
            #[cfg(feature = "llvm")]
            unsafe { cmd_demo() }
            #[cfg(not(feature = "llvm"))]
            no_llvm("demo");
        }
        Some("ingest") => {
            #[cfg(feature = "llvm")]
            unsafe { cmd_ingest(&args[1..]) }
            #[cfg(not(feature = "llvm"))]
            no_llvm("ingest");
        }
        Some("link") => cmd_link(&args[1..]),
        _ => usage(),
    }
}

#[cfg(not(feature = "llvm"))]
fn no_llvm(cmd: &str) -> ! {
    eprintln!("roscc: `{cmd}` needs a linked LLVM; rebuild with --features llvm");
    std::process::exit(2)
}

// ---------------- demo ----------------

#[cfg(feature = "llvm")]
#[allow(deprecated)]
unsafe fn build_demo_module(ctx: LLVMContextRef) -> LLVMModuleRef {
    let m = LLVMModuleCreateWithNameInContext(cstr("hello").as_ptr(), ctx);
    LLVMSetTarget(m, cstr(TRIPLE_A72).as_ptr());

    let i32t = LLVMInt32TypeInContext(ctx);
    let i8t = LLVMInt8TypeInContext(ctx);
    let i8p = LLVMPointerType(i8t, 0);
    let voidt = LLVMVoidTypeInContext(ctx);
    let b = LLVMCreateBuilderInContext(ctx);

    let mut add_params = [i32t, i32t];
    let add_ty = LLVMFunctionType(i32t, add_params.as_mut_ptr(), 2, 0);
    let add = LLVMAddFunction(m, cstr("add").as_ptr(), add_ty);
    let bb = LLVMAppendBasicBlockInContext(ctx, add, cstr("entry").as_ptr());
    LLVMPositionBuilderAtEnd(b, bb);
    let sum = LLVMBuildAdd(b, LLVMGetParam(add, 0), LLVMGetParam(add, 1), cstr("sum").as_ptr());
    LLVMBuildRet(b, sum);

    let mut w0_params = [i8p];
    let w0_ty = LLVMFunctionType(voidt, w0_params.as_mut_ptr(), 1, 0);
    let os_write0 = LLVMAddFunction(m, cstr("os_write0").as_ptr(), w0_ty);

    let hello_ty = LLVMFunctionType(voidt, std::ptr::null_mut(), 0, 0);
    let hello = LLVMAddFunction(m, cstr("hello").as_ptr(), hello_ty);
    let hbb = LLVMAppendBasicBlockInContext(ctx, hello, cstr("entry").as_ptr());
    LLVMPositionBuilderAtEnd(b, hbb);
    let msg = LLVMBuildGlobalStringPtr(
        b,
        cstr("Hello from roscc (Rust + LLVM 22) on Cortex-A72!\n").as_ptr(),
        cstr("msg").as_ptr(),
    );
    let mut call_args = [msg];
    LLVMBuildCall2(b, w0_ty, os_write0, call_args.as_mut_ptr(), 1, cstr("").as_ptr());
    LLVMBuildRetVoid(b);

    LLVMDisposeBuilder(b);
    m
}

#[cfg(feature = "llvm")]
unsafe fn cmd_demo() {
    LLVMInitializeARMTargetInfo();
    LLVMInitializeARMTarget();
    LLVMInitializeARMTargetMC();
    LLVMInitializeARMAsmParser();
    LLVMInitializeARMAsmPrinter();

    let ctx = LLVMContextCreate();
    let m = build_demo_module(ctx);
    let mut verr: *mut c_char = std::ptr::null_mut();
    LLVMVerifyModule(m, LLVMVerifierFailureAction::LLVMAbortProcessAction, &mut verr);
    LLVMDisposeMessage(verr);

    let (_triple, tm) = make_target_machine("cortex-a72");
    let ir = LLVMPrintModuleToString(m);
    println!("; ---- module IR ----\n{}", CStr::from_ptr(ir).to_string_lossy());
    LLVMDisposeMessage(ir);

    std::fs::create_dir_all("out").expect("create out/");
    emit(tm, m, "out/hello.s", LLVMCodeGenFileType::LLVMAssemblyFile);
    emit(tm, m, "out/hello.o", LLVMCodeGenFileType::LLVMObjectFile);
    LLVMDisposeTargetMachine(tm);
    LLVMDisposeModule(m);
    LLVMContextDispose(ctx);
    println!("roscc: demo -> out/hello.s, out/hello.o");
}

// ---------------- ingest ----------------

#[cfg(feature = "llvm")]
const ATTR_FN: u32 = u32::MAX; // LLVMAttributeFunctionIndex

#[cfg(feature = "llvm")]
unsafe fn cmd_ingest(args: &[String]) {
    let mut input: Option<&str> = None;
    let mut output = "a.out.o".to_string();
    let mut cpu = "cortex-a72".to_string();
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "-o" => {
                i += 1;
                output = args.get(i).cloned().unwrap_or_else(|| usage());
            }
            "--cpu" => {
                i += 1;
                cpu = args.get(i).cloned().unwrap_or_else(|| usage());
            }
            a if input.is_none() => input = Some(a),
            _ => usage(),
        }
        i += 1;
    }
    let Some(input) = input else { usage() };

    LLVMInitializeARMTargetInfo();
    LLVMInitializeARMTarget();
    LLVMInitializeARMTargetMC();
    LLVMInitializeARMAsmParser();
    LLVMInitializeARMAsmPrinter();

    let ctx = LLVMContextCreate();
    let (triple, tm) = make_target_machine(&cpu);

    // Parse the IR text (thread-local: same thread as context init).
    let mut membuf: LLVMMemoryBufferRef = std::ptr::null_mut();
    let mut merr: *mut c_char = std::ptr::null_mut();
    let path = cstr(input);
    eprintln!("[ingest] reading {input}");
    let rc = llvm_sys::core::LLVMCreateMemoryBufferWithContentsOfFile(
        path.as_ptr(),
        &mut membuf,
        &mut merr,
    );
    if rc != 0 {
        eprintln!("roscc: read {input}: {}", CStr::from_ptr(merr).to_string_lossy());
        std::process::exit(1);
    }
    eprintln!("[ingest] buffer ok, parsing IR");
    let mut m: LLVMModuleRef = std::ptr::null_mut();
    let mut perr: *mut c_char = std::ptr::null_mut();
    if LLVMParseIRInContext2(ctx, membuf, &mut m, &mut perr) != 0 {
        eprintln!("roscc: parse IR: {}", CStr::from_ptr(perr).to_string_lossy());
        eprintln!("roscc: failed to parse IR from {input}");
        std::process::exit(1);
    }

    eprintln!("[ingest] parsed; retargeting");
    // Retarget: triple + data layout from our target machine.
    LLVMSetTarget(m, triple.as_ptr());
    let dl = LLVMCreateTargetDataLayout(tm);
    llvm_sys::target::LLVMSetModuleDataLayout(m, dl);

    // Strip per-function host attributes (e.g. Mojo emits
    // target-cpu="alderlake", target-features="+adx,...").
    let mut f = LLVMGetFirstFunction(m);
    while !f.is_null() {
        for attr in ["target-cpu", "target-features", "tune-cpu"] {
            let cs = cstr(attr);
            LLVMRemoveStringAttributeAtIndex(
                f,
                ATTR_FN,
                cs.as_ptr(),
                cs.as_bytes().len() as u32,
            );
        }
        f = LLVMGetNextFunction(f);
    }

    let mut verr: *mut c_char = std::ptr::null_mut();
    LLVMVerifyModule(m, LLVMVerifierFailureAction::LLVMAbortProcessAction, &mut verr);
    LLVMDisposeMessage(verr);

    eprintln!("[ingest] attrs stripped; emitting object");
    emit(tm, m, &output, LLVMCodeGenFileType::LLVMObjectFile);
    eprintln!("[ingest] emitted");
    LLVMDisposeTargetMachine(tm);
    LLVMDisposeModule(m);
    LLVMContextDispose(ctx);
    println!("roscc: ingest {input} -> {output} ({TRIPLE_A72}, {cpu})");
}

// ---------------- link ----------------

fn cmd_link(args: &[String]) {
    let mut entry = "_start".to_string();
    let mut output: Option<String> = None;
    let mut rt_profile: Option<String> = None;
    let mut as_module = false;
    let mut objs: Vec<String> = Vec::new();
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--module" => as_module = true,
            "--entry" => {
                i += 1;
                entry = args.get(i).cloned().unwrap_or_else(|| usage());
            }
            "-o" => {
                i += 1;
                output = Some(args.get(i).cloned().unwrap_or_else(|| usage()));
            }
            "--rt" => {
                i += 1;
                let p = args.get(i).cloned().unwrap_or_else(|| usage());
                if p != "sa" && p != "a72" && p != "none" {
                    usage();
                }
                rt_profile = if p == "none" { None } else { Some(p) };
            }
            a => objs.push(a.to_string()),
        }
        i += 1;
    }

    // Attach the rostrt runtime: crt0, rostrt, hand-wimp shims, generated
    // SWI shims, aeabi division, atomics — in link order.
    if let Some(profile) = &rt_profile {
        let exe = std::env::current_exe().unwrap_or_default();
        let build_dir = exe
            .parent()
            .and_then(|d| d.parent())
            .and_then(|d| d.parent())
            .map(|d| d.join("rostrt").join("build"));
        if let Some(dir) = build_dir {
            let mut runtime = Vec::new();
            for name in [
                "crt0", "rostrt", "wimp", "swis_os", "swis_wimp", "aeabi",
                "atomics",
            ] {
                let p = dir.join(format!("{name}-{profile}.o"));
                if p.exists() {
                    runtime.push(p.to_string_lossy().into_owned());
                } else {
                    eprintln!("roscc: runtime object missing: {}", p.display());
                    std::process::exit(1);
                }
            }
            objs.splice(0..0, runtime);
        } else {
            eprintln!("roscc: cannot locate runtime directory for --rt");
            std::process::exit(1);
        }
    }

    if objs.is_empty() {
        usage();
    }
    // Inputs may be single ELF objects or ar archives (mojo build emits
    // an archive for multi-file programs).
    let mut parsed: Vec<elf::Object> = Vec::new();
    for p in &objs {
        let head = std::fs::read(p)
            .unwrap_or_else(|e| {
                eprintln!("roscc: {p}: {e}");
                std::process::exit(1);
            });
        if head.starts_with(b"!<arch>\n") || head.starts_with(b"!<thin>\n") {
            match elf::parse_archive(p) {
                Ok(mut os) => parsed.append(&mut os),
                Err(e) => {
                    eprintln!("roscc: {e}");
                    std::process::exit(1);
                }
            }
        } else {
            match elf::parse(p) {
                Ok(o) => parsed.push(o),
                Err(e) => {
                    eprintln!("roscc: {e}");
                    std::process::exit(1);
                }
            }
        }
    }
    let output = output.unwrap_or_else(|| {
        if as_module { "a.out,ffa".into() } else { "a.out,ff8".into() }
    });

    if as_module {
        match module::write_module(&parsed, &output) {
            Ok(m) => {
                println!(
                    "roscc: linked {} object(s) -> {} (module, {} bytes)",
                    parsed.len(),
                    output,
                    m.file.len()
                );
                for (name, off) in &m.entries {
                    println!("       {name:<11} +{off:#06x}");
                }
                if m.sb_size != 0 {
                    println!(
                        "       static data {} bytes ({} copied from +{:#x}, \
                         rest zeroed), claimed from the RMA at init",
                        m.sb_size, m.rw_init_size, m.rw_init_off
                    );
                }
            }
            Err(e) => {
                eprintln!("roscc: module link failed: {e}");
                std::process::exit(1);
            }
        }
        return;
    }

    match aif::write_aif(&parsed, &entry, &output) {
        Ok(entry_addr) => {
            println!(
                "roscc: linked {} object(s) -> {} (entry {:#x})",
                parsed.len(),
                output,
                entry_addr
            );
        }
        Err(e) => {
            eprintln!("roscc: link failed: {e}");
            std::process::exit(1);
        }
    }
}
