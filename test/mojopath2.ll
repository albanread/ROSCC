; mojopath2 — a whole program in Mojo's emitted shape, entered through
; the C-library crt0: `main(argc, argv)` is called by the library's own
; _main, so malloc and friends just work.  This is the object shape a
; `from riscos import clib` Mojo app compiles to.
target triple = "armv8a-none-eabi"

@.hello = private constant [30 x i8] c"Mojo app under the C library\0A\00"
@.ok    = private constant [19 x i8] c"mojopath2: all ok\0A\00"
@.bad   = private constant [22 x i8] c"mojopath2: heap FAIL\0A\00"

declare void @OS_Write0(ptr)
declare ptr  @malloc(i32)
declare i32  @strlen(ptr)

define i32 @main(i32 %argc, ptr %argv) {
entry:
  call void @OS_Write0(ptr @.hello)
  %p = call ptr @malloc(i32 64)
  %isnull = icmp eq ptr %p, null
  br i1 %isnull, label %bad, label %fill

fill:
  %sum = call i32 @heapfill(ptr %p)
  %good = icmp eq i32 %sum, 2016
  br i1 %good, label %ok, label %bad

ok:
  call void @OS_Write0(ptr @.ok)
  ret i32 0

bad:
  call void @OS_Write0(ptr @.bad)
  ret i32 1
}

define internal i32 @heapfill(ptr %p) {
entry:
  br label %loop

loop:
  %i = phi i32 [ 0, %entry ], [ %next, %loop ]
  %acc = phi i32 [ 0, %entry ], [ %sum, %loop ]
  %trunc = trunc i32 %i to i8
  %addr = getelementptr i8, ptr %p, i32 %i
  store i8 %trunc, ptr %addr
  %byte = zext i8 %trunc to i32
  %sum = add i32 %acc, %byte
  %next = add i32 %i, 1
  %done = icmp eq i32 %next, 64
  br i1 %done, label %out, label %loop

out:
  ret i32 %sum
}
