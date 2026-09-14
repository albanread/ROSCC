//! Minimal ELF32 little-endian ARM relocatable-object reader for the
//! roscc linker. Parses only what the linker needs: allocatable sections,
//! the symbol table, and REL relocation sections.

use std::fs;
use std::collections::HashMap;

#[derive(Debug, Clone)]
pub struct Section {
    pub name: String,
    pub sh_type: u32,
    pub flags: u32,
    /// Required alignment. Ignoring it is invisible until something in the
    /// section has an alignment attribute that matters — an OS heap base, a
    /// SIMD load — and then it is a fault a long way from here.
    pub align: u32,
    pub data: Vec<u8>,
}

#[derive(Debug, Clone)]
pub struct Symbol {
    pub name: String,
    pub value: u32,
    pub shndx: u16,
    pub bind_global: bool,
}

#[derive(Debug, Clone)]
pub struct Reloc {
    pub offset: u32,
    pub sym_idx: u32,
    pub rtype: u8,
}

pub struct Object {
    pub path: String,
    pub sections: Vec<Section>,
    /// ALL symbols, indexed exactly as in the ELF symtab (0 = null symbol).
    pub symbols: Vec<Symbol>,
    /// (kept-section index, relocations targeting it)
    pub rels: Vec<(usize, Vec<Reloc>)>,
    /// original ELF section index -> index into `sections`
    pub keep: HashMap<usize, usize>,
}

const SHT_PROGBITS: u32 = 1;
const SHT_SYMTAB: u32 = 2;
const SHT_NOBITS: u32 = 8;
const SHT_REL: u32 = 9;
const SHF_ALLOC: u32 = 0x2;

fn rd_u16(d: &[u8], o: usize) -> u16 {
    u16::from_le_bytes([d[o], d[o + 1]])
}
fn rd_u32(d: &[u8], o: usize) -> u32 {
    u32::from_le_bytes([d[o], d[o + 1], d[o + 2], d[o + 3]])
}

/// Sections the linker drops entirely (no unwinder / metadata on RISC OS).
fn skipped(name: &str) -> bool {
    name.starts_with(".ARM.")
        || name.starts_with(".note")
        || name == ".comment"
}

pub fn parse(path: &str) -> Result<Object, String> {
    let d = fs::read(path).map_err(|e| format!("{path}: {e}"))?;
    parse_bytes(&d, path)
}

/// Parse an `ar` archive of ELF objects (as emitted by mojo build):
/// regular members are parsed as objects; symbol index and long-name
/// table members are consumed for naming only.
pub fn parse_archive(path: &str) -> Result<Vec<Object>, String> {
    let d = fs::read(path).map_err(|e| format!("{path}: {e}"))?;
    let regular = &d[0..8] == b"!<arch>\n";
    let thin = &d[0..8] == b"!<thin>\n";
    if !regular && !thin {
        return Err(format!("{path}: not an ar archive"));
    }
    // Thin archive member names are file paths relative to the archive.
    let archive_dir = std::path::Path::new(path)
        .parent()
        .map(|p| p.to_path_buf())
        .unwrap_or_default();
    let mut objs = Vec::new();
    let mut off = 8;
    let mut longnames: Vec<u8> = Vec::new();
    while off + 60 <= d.len() {
        let hdr = &d[off..off + 60];
        if &hdr[58..60] != b"\x60\n" {
            return Err(format!("{path}: corrupt archive header at {off}"));
        }
        let size: usize = std::str::from_utf8(&hdr[48..58])
            .map_err(|e| e.to_string())?
            .trim()
            .parse()
            .map_err(|_| format!("{path}: bad member size"))?;
        let raw_name = std::str::from_utf8(&hdr[0..16])
            .unwrap_or("")
            .trim_end_matches([' ', '\0'])
            .to_string();
        let body = off + 60;
        let end = (body + size).min(d.len());
        let data = &d[body..end];

        let name: String = if raw_name == "//" {
            longnames = data.to_vec();
            off = end + (size & 1);
            continue;
        } else if raw_name.starts_with('/') && raw_name != "/" {
            // long-name reference: /N -> offset into longnames
            let n: usize = raw_name[1..].parse().unwrap_or(0);
            let ln = &longnames[n.min(longnames.len())..];
            let stop = ln.iter().position(|&c| c == b'\n').unwrap_or(ln.len());
            String::from_utf8_lossy(&ln[..stop])
                .trim_end_matches('/')
                .to_string()
        } else {
            raw_name.trim_end_matches('/').to_string()
        };

        if raw_name != "/" && !name.starts_with("__.SYMDEF") && !name.is_empty() {
            if thin {
                let ref_path = std::path::Path::new(&name);
                let resolved = if ref_path.is_absolute() {
                    ref_path.to_path_buf()
                } else {
                    archive_dir.join(ref_path)
                };
                let body = fs::read(&resolved)
                    .map_err(|e| format!("{}: thin member: {e}", resolved.display()))?;
                objs.push(parse_bytes(&body, &resolved.to_string_lossy())?);
            } else {
                match parse_bytes(data, &format!("{path}({name})")) {
                    Ok(o) => objs.push(o),
                    Err(e) => return Err(e),
                }
            }
        }
        off = end + (size & 1); // members are 2-byte aligned
    }
    Ok(objs)
}

pub fn parse_bytes(d: &[u8], path: &str) -> Result<Object, String> {
    if d.len() < 52 || &d[0..4] != b"\x7fELF" {
        return Err(format!("{path}: not an ELF file"));
    }
    if d[4] != 1 || d[5] != 1 {
        return Err(format!("{path}: not ELF32 little-endian"));
    }
    if rd_u16(d, 16) != 1 {
        return Err(format!("{path}: not relocatable (e_type)"));
    }
    if rd_u16(d, 18) != 40 {
        return Err(format!("{path}: not ARM (e_machine)"));
    }

    let shoff = rd_u32(d, 0x20) as usize;
    let shentsize = rd_u16(d, 0x2e) as usize;
    let shnum = rd_u16(d, 0x30) as usize;
    let shstrndx = rd_u16(d, 0x32) as usize;

    let mut shs: Vec<(u32, u32, u32, u32, u32, u32, u32, u32)> = Vec::new();
    for i in 0..shnum {
        let b = shoff + i * shentsize;
        shs.push((
            rd_u32(d, b),      // name off
            rd_u32(d, b + 4),  // type
            rd_u32(d, b + 8),  // flags
            rd_u32(d, b + 16), // offset
            rd_u32(d, b + 20), // size
            rd_u32(d, b + 24), // link
            rd_u32(d, b + 28), // info
            rd_u32(d, b + 32), // addralign
        ));
    }

    let shstr_off = shs[shstrndx].3 as usize;
    let shstr_len = shs[shstrndx].4 as usize;
    let shstr = &d[shstr_off..shstr_off + shstr_len];
    let name_of = |off: u32| -> String {
        let start = off as usize;
        if start >= shstr.len() {
            return String::new();
        }
        let nul = shstr[start..].iter().position(|&c| c == 0).unwrap_or(0);
        String::from_utf8_lossy(&shstr[start..start + nul]).into_owned()
    };

    let mut obj = Object {
        path: path.to_string(),
        sections: Vec::new(),
        symbols: Vec::new(),
        rels: Vec::new(),
        keep: HashMap::new(),
    };

    // Symbols: keep every entry (index-exact), including the null symbol.
    for so in &shs {
        if so.1 != SHT_SYMTAB {
            continue;
        }
        let strtab = &shs[so.5 as usize];
        let strt = &d[strtab.3 as usize..(strtab.3 + strtab.4) as usize];
        for i in 0..(so.4 as usize / 16) {
            let b = so.3 as usize + i * 16;
            let st_name = rd_u32(d, b);
            let st_value = rd_u32(d, b + 4);
            let st_info = d[b + 12];
            let st_shndx = rd_u16(d, b + 14);
            let s = &strt[st_name as usize..];
            let nul = s.iter().position(|&c| c == 0).unwrap_or(0);
            obj.symbols.push(Symbol {
                name: String::from_utf8_lossy(&s[..nul]).into_owned(),
                value: st_value,
                shndx: st_shndx,
                bind_global: st_info >> 4 == 1,
            });
        }
    }

    // Keep allocatable PROGBITS/NOBITS sections.
    for (i, so) in shs.iter().enumerate() {
        let (name_off, ty, flags, off, size, _link, _info, align) = *so;
        if flags & SHF_ALLOC == 0 || (ty != SHT_PROGBITS && ty != SHT_NOBITS) {
            continue;
        }
        let name = name_of(name_off);
        if skipped(&name) || size == 0 {
            continue;
        }
        let data = if ty == SHT_NOBITS {
            vec![0u8; size as usize]
        } else {
            d[off as usize..(off + size) as usize].to_vec()
        };
        obj.keep.insert(i, obj.sections.len());
        obj.sections.push(Section {
            name,
            sh_type: ty,
            flags,
            align: align.max(4),
            data,
        });
    }

    // Relocations against kept sections.
    for so in &shs {
        if so.1 != SHT_REL {
            continue;
        }
        if let Some(&target) = obj.keep.get(&(so.6 as usize)) {
            let mut rs = Vec::with_capacity(so.4 as usize / 8);
            for k in 0..(so.4 as usize / 8) {
                let b = so.3 as usize + k * 8;
                let r_info = rd_u32(d, b + 4);
                rs.push(Reloc {
                    offset: rd_u32(d, b),
                    sym_idx: r_info >> 8,
                    rtype: (r_info & 0xff) as u8,
                });
            }
            obj.rels.push((target, rs));
        }
    }
    Ok(obj)
}

pub fn is_writable(s: &Section) -> bool {
    s.flags & 0x1 != 0 // SHF_WRITE
}
