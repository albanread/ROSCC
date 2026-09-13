//! RISC OS AIF (ARM Image Format) writer and the roscc static linker.
//!
//! Layout produced (executable AIF, per docs\DDE\CodeStds\AIF,fff):
//!   &8000  128-byte header (NOP/NOP/NOP/BL prologue, sizes, base, mode 32)
//!   &8080  read-only image  (.text, .rodata... in file order)
//!          read-write image (.data; .bss materialised as zeros)
//! Code is linked at the &8080 image base; relocations applied in place.

use crate::elf::{self, Object};
use std::collections::HashMap;

const BASE: u32 = 0x8000;
const HDR: u32 = 0x80;
const NOP: u32 = 0xE1A0_0000;
const SWI_EXIT: u32 = 0xEF00_0011; // SWI OS_Exit

// ARM relocation types we handle (ELF for ARM ABI)
pub const R_ARM_ABS32: u8 = 2;
const R_ARM_CALL: u8 = 28;
const R_ARM_JUMP24: u8 = 29;
const R_ARM_V4BX: u8 = 40;
pub const R_ARM_MOVW_ABS_NC: u8 = 43;
pub const R_ARM_MOVT_ABS: u8 = 44;
const R_ARM_REL32: u8 = 3;
pub const R_ARM_GOT_PREL: u8 = 96;

fn align4(v: u32) -> u32 {
    (v + 3) & !3
}

fn bl(from: u32, to: u32) -> u32 {
    let imm = ((to as i64 - (from as i64 + 8)) / 4) as u32;
    0xEB00_0000 | (imm & 0x00FF_FFFF)
}

pub struct LinkResult {
    pub entry: u32,
    pub ro_size: u32,
    pub rw_size: u32,
    pub file: Vec<u8>,
}

fn build(objs: &[Object], entry_sym: &str) -> Result<LinkResult, String> {
    let mut addr: HashMap<(usize, usize), u32> = HashMap::new();
    let mut globals: HashMap<String, u32> = HashMap::new();

    // Layout: read-only sections first, then read-write, from &8080.
    let mut cursor = BASE + HDR;
    for pass in 0..2 {
        for (oi, o) in objs.iter().enumerate() {
            for (si, s) in o.sections.iter().enumerate() {
                if (pass == 0) == elf::is_writable(s) {
                    continue;
                }
                cursor = align4(cursor);
                addr.insert((oi, si), cursor);
                cursor += s.data.len() as u32;
            }
        }
    }
    // ro_end = first RW address (or end of image if no RW sections).
    let mut ro_end = cursor;
    'find: for (oi, o) in objs.iter().enumerate() {
        for (si, s) in o.sections.iter().enumerate() {
            if elf::is_writable(s) {
                ro_end = addr[&(oi, si)];
                break 'find;
            }
        }
    }

    // Global symbols.
    for (oi, o) in objs.iter().enumerate() {
        for sym in &o.symbols {
            if sym.shndx == 0 || !sym.bind_global {
                continue;
            }
            if let Some(&si) = o.keep.get(&(sym.shndx as usize)) {
                let a = addr[&(oi, si)] + sym.value;
                globals.insert(sym.name.clone(), a);
            }
        }
    }
    // The end of everything linked — the SharedCLibrary handshake wants the
    // top of the client's statics, and programs need a defensible "heap
    // starts here" without parsing the AIF header themselves.
    globals.insert("__image_end".to_string(), cursor);
    let entry = *globals
        .get(entry_sym)
        .ok_or_else(|| format!("entry symbol '{entry_sym}' not found"))?;

    // Synthesise a GOT for R_ARM_GOT_PREL references (Mojo accesses its
    // closures through a GOT even in static output).
    let resolve_sym = |o: &Object, idx: usize| -> Result<u32, String> {
        let sym = o
            .symbols
            .get(idx)
            .ok_or_else(|| format!("{}: bad sym index", o.path))?;
        if sym.shndx == 0 {
            return globals
                .get(&sym.name)
                .copied()
                .ok_or_else(|| format!("undefined: {}", sym.name));
        }
        if let Some(&a) = globals.get(&sym.name) {
            return Ok(a);
        }
        Err(format!("{}: symbol {} not resolvable", o.path, sym.name))
    };
    let mut got_names: Vec<String> = Vec::new();
    for o in objs {
        for (_, rels) in &o.rels {
            for r in rels {
                if r.rtype == R_ARM_GOT_PREL {
                    let name = o.symbols[r.sym_idx as usize].name.clone();
                    if !got_names.contains(&name) {
                        got_names.push(name);
                    }
                }
            }
        }
    }
    // cursor has to move to got_base, not merely past it: the slots are
    // placed from the aligned address while cursor was advanced from the
    // unaligned one, so the image buffer came out up to 3 bytes short and
    // the copy panicked. It only ever aligned by luck - every .bss so far
    // had happened to be a multiple of 4.
    let got_base = align4(cursor);
    cursor = got_base;
    let mut got_slots: HashMap<String, u32> = HashMap::new();
    let mut got_vals: HashMap<String, u32> = HashMap::new();
    for (i, name) in got_names.iter().enumerate() {
        // Resolve via globals, else as a local in its defining section.
        let mut val = globals.get(name).copied();
        if val.is_none() {
            'def: for (oi, o) in objs.iter().enumerate() {
                for sym in &o.symbols {
                    if &sym.name == name && sym.shndx != 0 {
                        if let Some(&si) = o.keep.get(&(sym.shndx as usize)) {
                            val = Some(addr[&(oi, si)] + sym.value);
                            break 'def;
                        }
                    }
                }
            }
        }
        let v = val.ok_or_else(|| format!("GOT symbol {name} unresolved"))?;
        got_vals.insert(name.clone(), v);
        got_slots.insert(name.clone(), got_base + (i as u32) * 4);
        cursor += 4;
    }

    // _end: the first address past the image, defined here because only the
    // linker knows it. A runtime wanting a heap should claim it from the
    // application slot - RISC OS hands a program everything from here up to
    // the limit OS_GetEnv returns in R1 - rather than reserving one inside
    // the image. Reserving it is what made a hello world 304 KB, of which
    // 299,312 bytes were rostrt's heap_area and global_arena written out as
    // zeros because .bss is materialised rather than declared.
    globals.insert("_end".to_string(), align4(cursor));

    // Build the image: copy section data, then apply relocations.
    let img_base = BASE + HDR;
    let mut image = vec![0u8; (cursor - img_base) as usize];
    // Fill GOT slots
    for (name, slot) in &got_slots {
        if let Some(v) = got_vals.get(name) {
            let off = (*slot - img_base) as usize;
            image[off..off + 4].copy_from_slice(&v.to_le_bytes());
        }
    }
    for (oi, o) in objs.iter().enumerate() {
        for (si, s) in o.sections.iter().enumerate() {
            let a = (addr[&(oi, si)] - img_base) as usize;
            image[a..a + s.data.len()].copy_from_slice(&s.data);
        }
    }

    for (oi, o) in objs.iter().enumerate() {
        for (target, rels) in &o.rels {
            let sec_addr = addr[&(oi, *target)];
            let base_off = (sec_addr - img_base) as usize;
            for r in rels {
                let sym = o
                    .symbols
                    .get(r.sym_idx as usize)
                    .ok_or_else(|| format!("{}: bad sym index", o.path))?;
                let s_val = if sym.shndx == 0 {
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
                                        "{}: symbol {} in dropped section",
                                        o.path, sym.name
                                    )
                                })?;
                            addr[&(oi, si)] + sym.value
                        }
                    }
                };
                let p = sec_addr.wrapping_add(r.offset);
                if r.rtype == R_ARM_GOT_PREL {
                    let slot = *got_slots
                        .get(&sym.name)
                        .ok_or_else(|| format!("no GOT slot for {}", sym.name))?;
                    let off = base_off + r.offset as usize;
                    let w = u32::from_le_bytes(
                        image[off..off + 4].try_into().unwrap(),
                    );
                    let val = (slot as i64 + w as i32 as i64 - p as i64) as u32;
                    image[off..off + 4].copy_from_slice(&val.to_le_bytes());
                    continue;
                }
                let off = base_off + r.offset as usize;
                if off + 4 > image.len() {
                    return Err(format!(
                        "{}: relocation outside section ({:#x})",
                        o.path, r.offset
                    ));
                }
                let mut w =
                    u32::from_le_bytes(image[off..off + 4].try_into().unwrap());
                w = apply(r.rtype, w, s_val, p)
                    .map_err(|e| format!("{}: {}", o.path, e))?;
                image[off..off + 4].copy_from_slice(&w.to_le_bytes());
            }
        }
    }

    // Executable AIF header.
    let ro_incl_hdr = HDR + (ro_end - img_base);
    let rw_size = cursor.saturating_sub(ro_end);
    let mut file = Vec::with_capacity(HDR as usize + image.len());
    let mut hdr = [0u32; 32];
    hdr[0] = NOP; // no decompression
    hdr[1] = NOP; // not self-relocating
    hdr[2] = NOP; // no zero-init code (bss materialised)
    hdr[3] = bl(BASE + 0x0c, entry); // BL ImageEntryPoint
    hdr[4] = SWI_EXIT; // last-ditch exit instruction
    hdr[5] = ro_incl_hdr; // read-only size incl. header
    hdr[6] = rw_size; // read-write size
    hdr[7] = 0; // debug size
    hdr[8] = 0; // zero-init size (materialised in file)
    hdr[9] = 0; // debug type
    hdr[10] = BASE; // image base
    hdr[11] = 0; // workspace
    hdr[12] = 32 | 0x100; // 32-bit mode + separate data base
    hdr[13] = BASE + ro_incl_hdr; // data base
    hdr[16] = NOP; // debug init
    for w in hdr {
        file.extend_from_slice(&w.to_le_bytes());
    }
    file.extend_from_slice(&image);

    Ok(LinkResult { entry, ro_size: ro_end - img_base, rw_size, file })
}

pub fn apply(rtype: u8, word: u32, s: u32, p: u32) -> Result<u32, String> {
    Ok(match rtype {
        // S + A, addend in place (REL form)
        R_ARM_ABS32 => s.wrapping_add(word),
        R_ARM_CALL | R_ARM_JUMP24 => {
            let off = (s as i64 - 8 - p as i64) / 4;
            if !(-0x80_0000..0x80_0000).contains(&off) {
                return Err(format!("branch out of range: {s:#x} from {p:#x}"));
            }
            (word & 0xFF00_0000) | ((off as u32) & 0x00FF_FFFF)
        }
        R_ARM_MOVW_ABS_NC => {
            let imm = s & 0xFFFF;
            (word & 0xFFF0_F000) | ((imm & 0xF000) << 4) | (imm & 0x0FFF)
        }
        R_ARM_MOVT_ABS => {
            let imm = (s >> 16) & 0xFFFF;
            (word & 0xFFF0_F000) | ((imm & 0xF000) << 4) | (imm & 0x0FFF)
        }
        R_ARM_V4BX => word,
        // S + A - P (A = signed in-place addend)
        R_ARM_REL32 => (s as i64 + word as i32 as i64 - p as i64) as u32,
        // GOT + A - P
        other => {
            return Err(format!(
                "unsupported relocation type {other} — extend the linker"
            ))
        }
    })
}

/// Link objects and write the executable-AIF file plus an ELF sidecar
/// (for llvm-objdump verification). Returns the entry address.
pub fn write_aif(
    objs: &[Object],
    entry_sym: &str,
    out_path: &str,
) -> Result<u32, String> {
    let r = build(objs, entry_sym)?;
    std::fs::write(out_path, &r.file)
        .map_err(|e| format!("write {out_path}: {e}"))?;
    let sidecar = format!("{}.elf", out_path.trim_end_matches(",ff8"));
    write_sidecar_elf(&r.file, r.entry, &sidecar)?;
    Ok(r.entry)
}

/// Minimal ELF32-ARM executable wrapping the AIF image so llvm-objdump
/// can disassemble it.
fn write_sidecar_elf(
    image: &[u8],
    _entry: u32,
    out_path: &str,
) -> Result<(), String> {
    let mut out: Vec<u8> = Vec::new();
    let ehsize = 52usize;
    let phnum = 1usize;
    let shnum = 2usize; // null + one PROGBITS covering the image
    let phoff = ehsize;
    let shoff = phoff + phnum * 32;
    let data_off = shoff + shnum * 40;

    let mut e = vec![0u8; data_off];
    e[0..4].copy_from_slice(b"\x7fELF");
    e[4] = 1;
    e[5] = 1;
    e[6] = 1;
    e[16..18].copy_from_slice(&2u16.to_le_bytes()); // ET_EXEC
    e[18..20].copy_from_slice(&40u16.to_le_bytes()); // EM_ARM
    e[24..28].copy_from_slice(&BASE.to_le_bytes()); // entry
    e[28..32].copy_from_slice(&(phoff as u32).to_le_bytes());
    e[32..36].copy_from_slice(&(shoff as u32).to_le_bytes());
    e[36..40].copy_from_slice(&0x0500u32.to_le_bytes()); // e_flags: EABI5
    e[40..42].copy_from_slice(&52u16.to_le_bytes()); // ehsize
    e[42..44].copy_from_slice(&32u16.to_le_bytes()); // phentsize
    e[44..46].copy_from_slice(&(phnum as u16).to_le_bytes());
    e[46..48].copy_from_slice(&40u16.to_le_bytes()); // shentsize
    e[48..50].copy_from_slice(&(shnum as u16).to_le_bytes());
    e[50..52].copy_from_slice(&0u16.to_le_bytes()); // shstrndx

    let ph = &mut e[phoff..phoff + 32];
    ph[0..4].copy_from_slice(&1u32.to_le_bytes()); // PT_LOAD
    ph[4..8].copy_from_slice(&(data_off as u32).to_le_bytes());
    ph[8..12].copy_from_slice(&BASE.to_le_bytes());
    ph[12..16].copy_from_slice(&BASE.to_le_bytes());
    ph[16..20].copy_from_slice(&(image.len() as u32).to_le_bytes());
    ph[20..24].copy_from_slice(&(image.len() as u32).to_le_bytes());
    ph[24..28].copy_from_slice(&7u32.to_le_bytes()); // RWX
    ph[28..32].copy_from_slice(&4u32.to_le_bytes());

    let sh = &mut e[shoff + 40..shoff + 80]; // second section header
    sh[4..8].copy_from_slice(&1u32.to_le_bytes()); // PROGBITS
    sh[8..12].copy_from_slice(&0x6u32.to_le_bytes()); // ALLOC|EXEC
    sh[12..16].copy_from_slice(&BASE.to_le_bytes()); // addr
    sh[16..20].copy_from_slice(&(data_off as u32).to_le_bytes());
    sh[20..24].copy_from_slice(&(image.len() as u32).to_le_bytes());

    out.extend_from_slice(&e);
    out.extend_from_slice(image);
    std::fs::write(out_path, &out).map_err(|e| format!("write {out_path}: {e}"))?;
    Ok(())
}
