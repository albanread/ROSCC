// Copy LLVM-C.dll next to the built executable so `cargo run` works
// without PATH setup.
use std::{env, fs, path::PathBuf};

fn main() {
    let src = PathBuf::from(r"C:\Program Files\LLVM\bin\LLVM-C.dll");
    if !src.exists() {
        return;
    }
    // OUT_DIR = target/<profile>/build/roscc-<hash>/out  ->  profile dir is 3 up
    let out = PathBuf::from(env::var("OUT_DIR").unwrap());
    if let Some(profile_dir) = out.ancestors().nth(3) {
        let dst = profile_dir.join("LLVM-C.dll");
        let _ = fs::copy(&src, &dst);
    }
    println!("cargo:rerun-if-changed={}", src.display());
}
