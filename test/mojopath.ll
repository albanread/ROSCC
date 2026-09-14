; mojopath.ll — what Mojo's external_call lowers to, by hand: the shape
; `mojo build --emit object` produces for a wrapper like
;   def strlen(s) -> UInt32: return external_call["strlen", UInt32](s)
; Compiled with plain clang here because the Mojo compiler builds on the
; Windows rig; the pipeline either way is: object -> roscc link -> run.
target triple = "armv8a-none-eabi"

@.mojostr = private constant [24 x i8] c"Hello from Mojo's path!\00"

declare i32  @strlen(ptr)
declare i32  @strcmp(ptr, ptr)
declare ptr  @malloc(i32)

define i32 @mojo_strlen(ptr %s) {
entry:
  %r = call i32 @strlen(ptr %s)
  ret i32 %r
}

define i32 @mojo_strcmp(ptr %a, ptr %b) {
entry:
  %r = call i32 @strcmp(ptr %a, ptr %b)
  ret i32 %r
}

define ptr @mojo_malloc(i32 %n) {
entry:
  %r = call ptr @malloc(i32 %n)
  ret ptr %r
}

define ptr @mojo_str() {
entry:
  ret ptr @.mojostr
}
