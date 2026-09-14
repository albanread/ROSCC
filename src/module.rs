//! RISC OS relocatable module writer (filetype &FFA).
//!
//! A module is not an image with a load address: RISC OS puts it wherever
//! the RMA has room and never relocates it. Everything the OS reads out of
//! it — the header at offset 0, the command table, the strings — is an
//! offset from the module's own start, and everything the module's code
//! does must work at any address. So this writer links at base 0 and then
//! *checks* that nothing in the image depends on that being the real one.
//!
//! Layout produced:
//!   +0000  `.module` section: the 13-word header, title, help, tables
//!          (the header must be word 0, so this section is placed first)
//!   +....  every other read-only section, in object order
//!   +....  the initial contents of the read-write sections, if any
//!
//! Read-write data lives in a *second* address space. Under RWPI (clang
//! `-fropi -frwpi`, Mojo `--target-features +reserve-r9`) every access to a
//! static is an offset from the static base in r9, so the writable sections
//! are laid out from zero in their own space, their initial contents are
//! carried in the image, and the module's initialisation claims RMA, copies
//! them in and points r9 at the copy. One instantiation, one static area —
//! which is what a module has always wanted and could not previously say.
//!
//! Header (PRM 1-210, plus &2C from RISC OS 3.6 and the &30 flags word):
//!   &00 start  &04 init  &08 final  &0C service  &10 title  &14 help
//!   &18 command table  &1C SWI chunk  &20 SWI handler  &24 SWI names
//!   &28 SWI decode  &2C messages  &30 flags (bit 0 = 32-bit compatible)

use crate::aif;
use crate::elf::{self, Object};
use std::collections::HashMap;

/// Section carrying the header and the OS-read tables. Its absolute-address
/// words are module offsets by definition, which is why they are allowed
/// here and nowhere else.
const HEADER_SECTION: &str = ".module";

const HDR_WORDS: usize = 13;
const HDR_NAMES: [&str; HDR_WORDS] = [
    "start", "init", "final", "service", "title", "help", "commands",
    "swi_chunk", "swi_handler", "swi_names", "swi_decode", "messages",
    "flags",
];
/// Header slots holding an offset to code (must be word-aligned, in range).
const HDR_CODE: [usize; 5] = [0, 1, 2, 3, 8];
/// Header slots holding an offset to data (string or table).
const HDR_DATA: [usize; 6] = [4, 5, 6, 9, 10, 11];
/// The one slot that is a number, not an offset.
const HDR_SWI_CHUNK: usize = 7;

/// Four words the generated header reserves for the linker: where the
/// read-write initial image is, how much of it to copy, how much static
/// space to claim in total, and the block's own offset — which is how the
/// initialisation code recovers the module's base address, having found the
/// block PC-relatively. Without this symbol a module may have no writable
/// data at all.
const PARAMS_SYMBOL: &str = "__mod_params";

const R_ARM_SBREL32: u8 = 9;

fn align4(v: u32) -> u32 {
    (v + 3) & !3
}

/// Round up to a section's own alignment. A section that asks for sixteen
/// and gets four is a bug that only shows up much later, in whatever the
/// alignment was for.
fn align_to(v: u32, a: u32) -> u32 {
    let a = a.max(4);
    (v + a - 1) & !(a - 1)
}

pub struct ModuleImage {
    pub file: Vec<u8>,
    pub header: [u32; HDR_WORDS],
    /// (name, address) for the symbols the header points at.
    pub entries: Vec<(String, u32)>,
    /// Offset in the image of the read-write initial data.
    pub rw_init_off: u32,
    /// Bytes of it to copy into the claimed static area.
    pub rw_init_size: u32,
    /// Total static area to claim: the copy above, then zeros.
    pub sb_size: u32,
}

/// Which address space a section lives in. Read-only sections are at
/// offsets from the module's start; writable ones are at offsets from the
/// static base, which is a different piece of memory entirely.
#[derive(Clone, Copy, PartialEq, Debug)]
enum Space {
    Ro,
    Sb,
}

const SHT_NOBITS: u32 = 8;

/// Link objects into a flat module image based at zero.
pub fn build(objs: &[Object]) -> Result<ModuleImage, String> {
    let mut addr: HashMap<(usize, usize), (Space, u32)> = HashMap::new();

    // The header section goes first: RISC OS reads word 0 of the module.
    let mut header_owner = None;
    let mut ro_cursor: u32 = 0;
    for (oi, o) in objs.iter().enumerate() {
        for (si, s) in o.sections.iter().enumerate() {
            if s.name == HEADER_SECTION {
                if header_owner.is_some() {
                    return Err(format!(
                        "more than one {HEADER_SECTION} section \
                         (a module has exactly one header)"
                    ));
                }
                if s.data.len() < HDR_WORDS * 4 {
                    return Err(format!(
                        "{}: {HEADER_SECTION} is {} bytes, a module header \
                         is {} words",
                        o.path,
                        s.data.len(),
                        HDR_WORDS
                    ));
                }
                addr.insert((oi, si), (Space::Ro, 0));
                ro_cursor = s.data.len() as u32;
                header_owner = Some((oi, si));
            }
        }
    }
    let header_owner = header_owner.ok_or_else(|| {
        format!(
            "no {HEADER_SECTION} section: a module needs a header at offset 0 \
             (generate one with tools/gen_module.py)"
        )
    })?;

    // Read-only sections, in object order, after the header.
    for (oi, o) in objs.iter().enumerate() {
        for (si, s) in o.sections.iter().enumerate() {
            if (oi, si) == header_owner || s.data.is_empty() || elf::is_writable(s)
            {
                continue;
            }
            ro_cursor = align_to(ro_cursor, s.align);
            addr.insert((oi, si), (Space::Ro, ro_cursor));
            ro_cursor += s.data.len() as u32;
        }
    }
    let ro_end = align4(ro_cursor);

    // Writable sections, in the static-data space from zero. Sections with
    // initial contents come first and zero-filled ones last, so the image
    // need only carry the first part and initialisation can zero the rest.
    let mut sb_cursor: u32 = 0;
    let mut rw_data_end: u32 = 0;
    for zero_filled in [false, true] {
        for (oi, o) in objs.iter().enumerate() {
            for (si, s) in o.sections.iter().enumerate() {
                if !elf::is_writable(s) || s.data.is_empty() {
                    continue;
                }
                if (s.sh_type == SHT_NOBITS) != zero_filled {
                    continue;
                }
                sb_cursor = align_to(sb_cursor, s.align);
                addr.insert((oi, si), (Space::Sb, sb_cursor));
                sb_cursor += s.data.len() as u32;
            }
        }
        if !zero_filled {
            rw_data_end = align_to(sb_cursor, 16);
            sb_cursor = rw_data_end;
        }
    }
    let sb_size = align4(sb_cursor);

    let rw_init_off = align_to(ro_end, 16);
    let image_len = rw_init_off + rw_data_end;

    // Global symbols, each tagged with the space it lives in.
    let mut globals: HashMap<String, (Space, u32)> = HashMap::new();
    for (oi, o) in objs.iter().enumerate() {
        for sym in &o.symbols {
            if sym.shndx == 0 || !sym.bind_global {
                continue;
            }
            if let Some(&si) = o.keep.get(&(sym.shndx as usize)) {
                if let Some(&(space, a)) = addr.get(&(oi, si)) {
                    globals.insert(sym.name.clone(), (space, a + sym.value));
                }
            }
        }
    }

    // Where a section's bytes sit in the file, whichever space it is in:
    // writable ones are patched where their initial copy is carried.
    let file_off = |space: Space, a: u32| -> u32 {
        match space {
            Space::Ro => a,
            Space::Sb => rw_init_off + a,
        }
    };

    let mut image = vec![0u8; image_len as usize];
    for (oi, o) in objs.iter().enumerate() {
        for (si, s) in o.sections.iter().enumerate() {
            if let Some(&(space, a)) = addr.get(&(oi, si)) {
                if space == Space::Sb && s.sh_type == SHT_NOBITS {
                    continue; // zeroed at initialisation, not carried
                }
                let off = file_off(space, a) as usize;
                image[off..off + s.data.len()].copy_from_slice(&s.data);
            }
        }
    }

    // Relocate. Each kind is only meaningful between particular spaces, and
    // saying which is most of the value here: a mistake produces a module
    // that loads and then quietly reads the wrong memory.
    let mut abs_sites: Vec<String> = Vec::new();
    for (oi, o) in objs.iter().enumerate() {
        for (target, rels) in &o.rels {
            let Some(&(tspace, sec_addr)) = addr.get(&(oi, *target)) else {
                continue;
            };
            let in_header = (oi, *target) == header_owner;
            for r in rels {
                let sym = o
                    .symbols
                    .get(r.sym_idx as usize)
                    .ok_or_else(|| format!("{}: bad sym index", o.path))?;
                let (sspace, s_val) = if sym.shndx == 0 {
                    *globals
                        .get(&sym.name)
                        .ok_or_else(|| format!("undefined: {}", sym.name))?
                } else {
                    match globals.get(&sym.name) {
                        Some(&a) => a,
                        None => {
                            let si = o
                                .keep
                                .get(&(sym.shndx as usize))
                                .copied()
                                .ok_or_else(|| {
                                    format!(
                                        "{}: symbol {} in a dropped section",
                                        o.path, sym.name
                                    )
                                })?;
                            let (space, a) = addr[&(oi, si)];
                            (space, a + sym.value)
                        }
                    }
                };
                let sym_name: &str = if sym.name.is_empty() {
                    "<local>"
                } else {
                    &sym.name
                };

                // Absolute references are module offsets only inside the
                // header section; anywhere else they are a load-address
                // dependency the OS will never fix up.
                let absolute = matches!(
                    r.rtype,
                    aif::R_ARM_ABS32
                        | aif::R_ARM_MOVW_ABS_NC
                        | aif::R_ARM_MOVT_ABS
                );
                if absolute && !in_header {
                    abs_sites.push(format!(
                        "    {} {}+{:#x} -> {} (reloc type {})",
                        o.path,
                        o.sections[*target].name,
                        r.offset,
                        sym_name,
                        r.rtype
                    ));
                    continue;
                }
                if r.rtype == aif::R_ARM_GOT_PREL {
                    return Err(format!(
                        "{}: {} needs a GOT slot, which holds an absolute \
                         address a module cannot have. Rebuild that \
                         translation unit without GOT-indirect access.",
                        o.path, sym.name
                    ));
                }

                let off = (file_off(tspace, sec_addr) + r.offset) as usize;
                if off + 4 > image.len() {
                    return Err(format!(
                        "{}: relocation outside section ({:#x})",
                        o.path, r.offset
                    ));
                }

                if r.rtype == R_ARM_SBREL32 {
                    if sspace != Space::Sb {
                        return Err(format!(
                            "{}: static-base-relative reference to {}, which \
                             is not in writable data",
                            o.path, sym_name
                        ));
                    }
                    // The static base is zero in its own space, so the fixup
                    // is that offset plus the addend already in place.
                    let w =
                        u32::from_le_bytes(image[off..off + 4].try_into().unwrap());
                    let v = s_val.wrapping_add(w);
                    image[off..off + 4].copy_from_slice(&v.to_le_bytes());
                    continue;
                }
                if sspace == Space::Sb {
                    return Err(format!(
                        "{}: {} is in writable data but is reached by \
                         relocation type {}, which cannot express a \
                         static-base offset. Build it -fropi -frwpi.",
                        o.path, sym_name, r.rtype
                    ));
                }
                if tspace == Space::Sb {
                    return Err(format!(
                        "{}: writable data holds a relocated value ({} via \
                         type {}). A static initialiser pointing at code or \
                         read-only data cannot be expressed in a module: \
                         assign it at run time instead.",
                        o.path, sym_name, r.rtype
                    ));
                }

                let p = sec_addr.wrapping_add(r.offset);
                let w = u32::from_le_bytes(image[off..off + 4].try_into().unwrap());
                let w = aif::apply(r.rtype, w, s_val, p)
                    .map_err(|e| format!("{}: {}", o.path, e))?;
                image[off..off + 4].copy_from_slice(&w.to_le_bytes());
            }
        }
    }
    if !abs_sites.is_empty() {
        return Err(format!(
            "{} absolute relocation(s) outside {HEADER_SECTION}: this code \
             would only run at the address it was linked for, and a module \
             runs wherever the RMA has room.\n{}",
            abs_sites.len(),
            abs_sites.join("\n")
        ));
    }

    // Hand the generated initialisation code the three numbers it cannot
    // know for itself: where the static data's initial contents are, how
    // much of them to copy, and how much to claim in total.
    match globals.get(PARAMS_SYMBOL) {
        Some(&(Space::Ro, at)) => {
            let at = at as usize;
            if at + 16 > image.len() {
                return Err(format!("{PARAMS_SYMBOL} is outside the image"));
            }
            let words = [rw_init_off, rw_data_end, sb_size, at as u32];
            for (i, v) in words.iter().enumerate() {
                image[at + i * 4..at + i * 4 + 4].copy_from_slice(&v.to_le_bytes());
            }
        }
        Some(_) => return Err(format!("{PARAMS_SYMBOL} must be in read-only data")),
        None if sb_size == 0 => {}
        None => {
            return Err(format!(
                "this module has {sb_size} bytes of writable data but no \
                 {PARAMS_SYMBOL}: regenerate the header with \
                 gen_module.py --rwpi, so initialisation claims a static area \
                 and points the static base at it"
            ))
        }
    }

    // Read the header back and check it the way RISC OS will.
    let mut header = [0u32; HDR_WORDS];
    for (i, h) in header.iter_mut().enumerate() {
        *h = u32::from_le_bytes(image[i * 4..i * 4 + 4].try_into().unwrap());
    }
    check_header(&header, ro_end)?;

    let mut entries: Vec<(String, u32)> = Vec::new();
    for (i, name) in HDR_NAMES.iter().enumerate() {
        if i != HDR_SWI_CHUNK && header[i] != 0 {
            entries.push((name.to_string(), header[i]));
        }
    }

    Ok(ModuleImage {
        file: image,
        header,
        entries,
        rw_init_off,
        rw_init_size: rw_data_end,
        sb_size,
    })
}

fn check_header(h: &[u32; HDR_WORDS], len: u32) -> Result<(), String> {
    if h[4] == 0 {
        return Err(
            "module header has no title string (offset &10): RISC OS cannot \
             name, list or kill the module"
                .into(),
        );
    }
    for &i in HDR_CODE.iter() {
        if h[i] != 0 && h[i] % 4 != 0 {
            return Err(format!(
                "header offset &{:02X} ({}) is {:#x}: code entries must be \
                 word aligned",
                i * 4,
                HDR_NAMES[i],
                h[i]
            ));
        }
    }
    for i in HDR_CODE.iter().chain(HDR_DATA.iter()).copied() {
        if h[i] != 0 && h[i] >= len {
            return Err(format!(
                "header offset &{:02X} ({}) is {:#x}, past the end of a \
                 {:#x}-byte module — RISC OS rejects out-of-range offsets",
                i * 4,
                HDR_NAMES[i],
                h[i],
                len
            ));
        }
    }
    // &30 flags: bit 0 set means 32-bit compatible. RISC OS 5 refuses to
    // initialise a module without it.
    if h[12] == 0 {
        return Err(
            "header has no flags word (offset &30): RISC OS 5 will refuse the \
             module as not 32-bit compatible"
                .into(),
        );
    }
    if h[12] >= len {
        return Err(format!(
            "flags-word offset {:#x} is outside the module",
            h[12]
        ));
    }
    Ok(())
}

/// Write the module image, plus an ELF sidecar so the debugger can be given
/// symbols at whatever address the RMA ends up putting the module.
pub fn write_module(objs: &[Object], out_path: &str) -> Result<ModuleImage, String> {
    let m = build(objs)?;
    std::fs::write(out_path, &m.file)
        .map_err(|e| format!("write {out_path}: {e}"))?;
    let sidecar = format!("{}.elf", out_path.trim_end_matches(",ffa"));
    write_sidecar_elf(objs, &m, &sidecar)?;
    Ok(m)
}

/// ELF32-ARM executable at base 0 wrapping the module image, carrying the
/// linked symbols. `sym.load {elf, base}` in the emulator's debugger biases
/// these by the module's live address.
fn write_sidecar_elf(
    objs: &[Object],
    m: &ModuleImage,
    out_path: &str,
) -> Result<(), String> {
    // Collect linked global symbols for the sidecar symtab.
    let mut syms: Vec<(String, u32)> = Vec::new();
    {
        let mut addr: HashMap<(usize, usize), u32> = HashMap::new();
        let mut cursor: u32 = 0;
        let mut header_owner = None;
        for (oi, o) in objs.iter().enumerate() {
            for (si, s) in o.sections.iter().enumerate() {
                if s.name == HEADER_SECTION {
                    addr.insert((oi, si), 0);
                    cursor = s.data.len() as u32;
                    header_owner = Some((oi, si));
                }
            }
        }
        // Read-only sections only: a symbol in writable data has no address
        // in this space at all — it is an offset from a static base that does
        // not exist until the module initialises.
        for (oi, o) in objs.iter().enumerate() {
            for (si, s) in o.sections.iter().enumerate() {
                if Some((oi, si)) == header_owner
                    || s.data.is_empty()
                    || elf::is_writable(s)
                {
                    continue;
                }
                cursor = align4(cursor);
                addr.insert((oi, si), cursor);
                cursor += s.data.len() as u32;
            }
        }
        for (oi, o) in objs.iter().enumerate() {
            for sym in &o.symbols {
                if sym.shndx == 0 || sym.name.is_empty() {
                    continue;
                }
                if sym.name.starts_with('$') {
                    continue; // ARM mapping symbols
                }
                if let Some(&si) = o.keep.get(&(sym.shndx as usize)) {
                    if let Some(&a) = addr.get(&(oi, si)) {
                        syms.push((sym.name.clone(), a + sym.value));
                    }
                }
            }
        }
    }
    syms.sort_by_key(|(_, a)| *a);
    syms.dedup_by(|a, b| a.0 == b.0);

    let mut strtab: Vec<u8> = vec![0];
    let mut symtab: Vec<u8> = vec![0u8; 16]; // null symbol
    for (name, a) in &syms {
        let noff = strtab.len() as u32;
        strtab.extend_from_slice(name.as_bytes());
        strtab.push(0);
        let mut e = [0u8; 16];
        e[0..4].copy_from_slice(&noff.to_le_bytes());
        e[4..8].copy_from_slice(&a.to_le_bytes());
        e[12] = (1 << 4) | 2; // GLOBAL | STT_FUNC
        e[14..16].copy_from_slice(&1u16.to_le_bytes()); // section 1
        symtab.extend_from_slice(&e);
    }

    let shstr: Vec<u8> = {
        let mut v = vec![0u8];
        for n in [".text", ".symtab", ".strtab", ".shstrtab"] {
            v.extend_from_slice(n.as_bytes());
            v.push(0);
        }
        v
    };
    let name_off = |n: &str| -> u32 {
        let needle = {
            let mut v = n.as_bytes().to_vec();
            v.push(0);
            v
        };
        shstr
            .windows(needle.len())
            .position(|w| w == needle.as_slice())
            .unwrap_or(0) as u32
    };

    let ehsize = 52usize;
    let phnum = 1usize;
    let shnum = 5usize; // null, .text, .symtab, .strtab, .shstrtab
    let phoff = ehsize;
    let shoff = phoff + phnum * 32;
    let mut off = shoff + shnum * 40;
    let text_off = off;
    off += m.file.len();
    let symtab_off = off;
    off += symtab.len();
    let strtab_off = off;
    off += strtab.len();
    let shstr_off = off;

    let mut e = vec![0u8; text_off];
    e[0..4].copy_from_slice(b"\x7fELF");
    e[4] = 1;
    e[5] = 1;
    e[6] = 1;
    e[16..18].copy_from_slice(&2u16.to_le_bytes()); // ET_EXEC
    e[18..20].copy_from_slice(&40u16.to_le_bytes()); // EM_ARM
    e[24..28].copy_from_slice(&0u32.to_le_bytes()); // entry
    e[28..32].copy_from_slice(&(phoff as u32).to_le_bytes());
    e[32..36].copy_from_slice(&(shoff as u32).to_le_bytes());
    e[36..40].copy_from_slice(&0x0500u32.to_le_bytes()); // EABI5
    e[40..42].copy_from_slice(&52u16.to_le_bytes());
    e[42..44].copy_from_slice(&32u16.to_le_bytes());
    e[44..46].copy_from_slice(&(phnum as u16).to_le_bytes());
    e[46..48].copy_from_slice(&40u16.to_le_bytes());
    e[48..50].copy_from_slice(&(shnum as u16).to_le_bytes());
    e[50..52].copy_from_slice(&4u16.to_le_bytes()); // shstrndx

    {
        let ph = &mut e[phoff..phoff + 32];
        ph[0..4].copy_from_slice(&1u32.to_le_bytes()); // PT_LOAD
        ph[4..8].copy_from_slice(&(text_off as u32).to_le_bytes());
        ph[16..20].copy_from_slice(&(m.file.len() as u32).to_le_bytes());
        ph[20..24].copy_from_slice(&(m.file.len() as u32).to_le_bytes());
        ph[24..28].copy_from_slice(&5u32.to_le_bytes()); // R+X
        ph[28..32].copy_from_slice(&4u32.to_le_bytes());
    }
    let mut sh = |i: usize, name: &str, ty: u32, flags: u32, a: u32,
                  o: usize, sz: usize, link: u32, info: u32, entsz: u32| {
        let b = shoff + i * 40;
        let s = &mut e[b..b + 40];
        s[0..4].copy_from_slice(&name_off(name).to_le_bytes());
        s[4..8].copy_from_slice(&ty.to_le_bytes());
        s[8..12].copy_from_slice(&flags.to_le_bytes());
        s[12..16].copy_from_slice(&a.to_le_bytes());
        s[16..20].copy_from_slice(&(o as u32).to_le_bytes());
        s[20..24].copy_from_slice(&(sz as u32).to_le_bytes());
        s[24..28].copy_from_slice(&link.to_le_bytes());
        s[28..32].copy_from_slice(&info.to_le_bytes());
        s[32..36].copy_from_slice(&4u32.to_le_bytes());
        s[36..40].copy_from_slice(&entsz.to_le_bytes());
    };
    sh(1, ".text", 1, 0x6, 0, text_off, m.file.len(), 0, 0, 0);
    sh(2, ".symtab", 2, 0, 0, symtab_off, symtab.len(), 3, 1, 16);
    sh(3, ".strtab", 3, 0, 0, strtab_off, strtab.len(), 0, 0, 0);
    sh(4, ".shstrtab", 3, 0, 0, shstr_off, shstr.len(), 0, 0, 0);

    let mut out = e;
    out.extend_from_slice(&m.file);
    out.extend_from_slice(&symtab);
    out.extend_from_slice(&strtab);
    out.extend_from_slice(&shstr);
    std::fs::write(out_path, &out).map_err(|e| format!("write {out_path}: {e}"))?;
    Ok(())
}
