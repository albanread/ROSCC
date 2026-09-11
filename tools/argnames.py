"""Name a SWI argument from the PRM's description of it.

The objective is that user code never sees the call mechanism, and a
parameter called `r2` names a register, which is the mechanism. The PRM
usually says what the argument is - "x coordinate", "number of bytes",
"station number" - so most of them can be named from the text.

Not all: a good number of entry registers are documented as bare constants
("0", "255 to 511") because they select between reason codes rather than
carry a value. Those get a positional name, which is at least not a
register.
"""
import re

# Longest and most specific first: "window handle" must win over "handle",
# and "number of" must win over "number".
PATTERNS = [
    # blocks and structures
    ("window block", "window_block"),
    ("icon block", "icon_block"),
    ("state block", "state_block"),
    ("draw block", "draw_block"),
    ("parameter block", "block"),
    ("control block", "block"),
    ("block", "block"),

    # handles
    ("task handle", "task"),
    ("window handle", "window"),
    ("icon handle", "icon"),
    ("file handle", "file"),
    ("font handle", "font"),
    ("handle", "handle"),

    # geometry
    ("x coordinate", "x"),
    ("y coordinate", "y"),
    ("width", "width"),
    ("height", "height"),
    ("bounding box", "bbox"),

    # counts and sizes
    ("number of", "count"),
    ("size of", "size"),
    ("length of", "length"),
    ("size", "size"),
    ("length", "length"),
    ("offset", "offset"),

    # networking
    ("station number", "station"),
    ("net number", "net"),
    ("port number", "port"),

    # naming and text
    ("pointer to name", "name"),
    ("file name", "filename"),
    ("filename", "filename"),
    ("string", "string"),
    ("buffer", "buffer"),
    ("message", "message"),

    # graphics and fonts
    ("sprite pointer", "sprite"),
    ("sprite name", "sprite"),
    ("sprite area", "sprite_area"),
    ("sprite", "sprite"),
    ("palette entry", "palette_entry"),
    ("palette", "palette"),
    ("colour number", "colour"),
    ("colour", "colour"),
    ("font name", "font_name"),
    ("point size", "point_size"),

    # system
    ("territory number", "territory"),
    ("territory", "territory"),
    ("reason code", "reason"),
    ("rom section", "section"),
    ("module name", "module"),
    ("variable name", "variable"),
    ("address of", "address"),
    ("address", "address"),
    ("pointer to", "ptr"),
    ("time", "time"),
    ("date", "date"),
    ("mode", "mode"),
    ("mask", "mask"),
    ("flag", "flags"),
    ("value", "value"),
]

_IDENT = re.compile(r"[^a-z0-9_]+")

# A name has to be a legal identifier in both languages it appears in: C for
# the shim, Mojo for the binding. The PRM is prose, so it happily yields
# "signed", "return" and the odd hex constant like 4c4c5546.
_LEGAL = re.compile(r"^[a-z][a-z0-9_]*$")

RESERVED = frozenset("""
auto break case char const continue default do double else enum extern float
for goto if inline int long register restrict return short signed sizeof
static struct switch typedef union unsigned void volatile while bool true
false
def fn var let alias raise try except with as import from pass and or not in
is none self owned borrowed inout mut ref out fna
""".split())


def from_text(text: str) -> str | None:
    """A name for this argument, or None if the text does not give one."""
    low = (text or "").lower()

    for needle, name in PATTERNS:
        if needle in low:
            return name if name not in RESERVED else name + "_"

    # "the foo to use" / "a foo" - take the leading noun if it is a word and
    # not a number, which is what a reason-code selector looks like.
    words = _IDENT.sub(" ", low).split()
    if not words:
        return None

    candidate = words[0]

    # Must look like an identifier: "4c4c5546" is a constant the PRM prints,
    # not a name for anything.
    if not _LEGAL.match(candidate) or len(candidate) <= 2:
        return None

    if candidate in ("the", "and", "for", "with", "set", "must", "one",
                     "bit", "bits", "all", "any", "not", "see", "use",
                     "this", "that", "from", "which", "when", "where"):
        return None

    return candidate + "_" if candidate in RESERVED else candidate
