"""Reading one C file: what it defines at file scope, and which names it uses."""

from __future__ import annotations

from printsect.map.scanner import scan


def names(text: str, kind: str | None = None) -> list[str]:
    return [d[0] for d in scan(text).defs if kind is None or d[1] == kind]


def test_functions_with_their_line_and_static():
    found = scan("int a(void)\n{\n  return 1;\n}\n\nstatic void\nb(int x) {\n}\n")
    assert found.defs == [("a", "f", False, 1), ("b", "f", True, 7)]


def test_variables_but_not_declarations_or_types():
    text = """
int plain;
static int hidden = 3;
int table[4] = {1, 2, 3, 4};
int one, two = 2, *three;
extern int elsewhere;
void proto(int x);
typedef int myint;
typedef struct { int a; } mytype;
struct tag;
struct tag { int a; } instance;
void (*hook)(int);
"""
    found = {d[0]: d for d in scan(text).defs}
    assert sorted(found) == ["hidden", "hook", "instance", "one", "plain", "table", "three", "two"]
    assert found["hidden"][2] is True and found["plain"][2] is False


def test_what_is_inside_a_function_is_not_a_definition():
    assert names("void f(void) { int local; static int s; struct t { int x; } y; }") == ["f"]


def test_comments_and_strings_are_not_read():
    found = scan('''/* int commented(void) { */
// calls_in_comment();
void f(void) { puts("calls_in_string() {"); real_call(); }
''')
    assert [d[0] for d in found.defs] == ["f"]
    assert "real_call" in found.refs and "puts" in found.refs
    assert "calls_in_string" not in found.refs and "calls_in_comment" not in found.refs


def test_uses_leave_out_keywords_and_what_the_file_defines():
    refs = scan("static int g(void) { return 1; }\nint f(void) { return g() + other(); }").refs
    assert refs == ["other"]


def test_preprocessor_lines_are_not_uses_but_macro_bodies_are():
    text = '#include "header_name.h"\n#ifdef CONFIG_X\n#define WRAP(a) helper(a)\n#endif\n'
    refs = scan(text).refs
    assert refs == ["a", "helper"]


def test_a_name_defined_in_both_branches_is_one_definition():
    text = "#ifdef A\nint f(void) {\n#else\nint f(int x) {\n#endif\n  return 0;\n}\nint g;\n"
    assert names(text) == ["f", "g"]


def test_if_zero_is_dead_and_its_else_is_not():
    text = "#if 0\nint dead(void) { gone(); }\n#else\nint live(void) { kept(); }\n#endif\n"
    found = scan(text)
    assert [d[0] for d in found.defs] == ["live"]
    assert "kept" in found.refs and "gone" not in found.refs


def test_braces_opened_in_both_branches_of_an_if_do_not_add_up():
    text = """
int f(int a)
#ifdef A
{ if (a) {
#else
{ if (!a) {
#endif
  } }
int after(void) { return 0; }
"""
    found = scan(text)
    assert [d[0] for d in found.defs] == ["f", "after"]
    assert not found.unbalanced


def test_extern_c_block_keeps_file_scope():
    assert names('extern "C" {\nint inside(void) { return 0; }\n}\nint after;\n') == [
        "inside", "after"]


def test_old_style_definition():
    found = scan("int f(a, b)\nint a;\nstruct s *b;\n{\n  return a;\n}\nint next;\n")
    assert [(d[0], d[1]) for d in found.defs] == [("f", "f"), ("next", "v")]


def test_a_prototype_with_a_trailing_macro_then_a_type_is_no_function():
    assert names("void f(size_t) NORETURN;\nstruct s {\n  int a;\n};\n") == []


def test_attributes_and_export_macros_around_the_name():
    assert names("__attribute__((noreturn)) void die(void) { }") == ["die"]
    assert names("EXPORT(int) api(void) { return 0; }") == ["api"]
    assert names("void f(void) MACRO_AFTER(1) { }") == ["f"]
    assert names("int (*returns_pointer(int a))(int) { return 0; }") == ["returns_pointer"]


def test_a_name_made_by_a_macro_is_counted_not_invented():
    found = scan("DEFINE_THING(foo) { return 0; }\nASN1_SEQUENCE(X509) = { 1 };\n")
    assert found.defs == []
    assert found.macro_defs == 2


def test_windows_line_endings_and_line_continuation():
    found = scan("#define LONG(a) \\\r\n  first(a)\r\nint f(void) {\r\n  return 0;\r\n}\r\n")
    assert [(d[0], d[3]) for d in found.defs] == [("f", 3)]
    assert "first" in found.refs


def test_a_file_made_to_be_slow_is_not():
    import time

    for text in ("/*" * 500_000, "(" * 500_000, "int " + ",".join(["aa"] * 200_000) + ";",
                 '"\\' * 500_000, "'\\" * 500_000, "int " + "[" * 60_000 + ";",
                 "int " + "(*a[" * 15_000 + ";", "a" * 60_000 + "(;", "a " * 30_000 + "(;",
                 "[" * 500_000, "x;" + "int " * 300_000):
        started = time.perf_counter()
        scan(text)
        assert time.perf_counter() - started < 5, text[:10]


def test_an_unclosed_string_ends_with_its_line():
    found = scan("#error this isn't closed\nint after(void) { return 0; }\n")
    assert [d[0] for d in found.defs] == ["after"]
    found = scan('char *s = "open;\n;\nint after(void) { return 0; }\n')
    assert [d[0] for d in found.defs] == ["s", "after"]


def test_a_declaration_past_the_limit_is_counted_not_read():
    found = scan("int " + "x" * (70 << 10) + ";\nint after;\n")
    assert found.too_long == 1 and [d[0] for d in found.defs] == ["after"]


def test_braces_that_never_close_are_reported():
    assert scan("void f(void) {\n").unbalanced
