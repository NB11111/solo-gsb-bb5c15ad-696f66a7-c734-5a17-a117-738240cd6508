"""icu.py 的单元测试。"""
import pytest

from icu import (
    Parser, RenderCtx, analyze, plural_category, render_message,
    representative_for_category,
)


def parse(text):
    p = Parser(text)
    return p.parse(), p


def render(text, variables, locale="en"):
    parts, _ = parse(text)
    ctx = RenderCtx(variables, locale)
    return render_message(parts, ctx), ctx


# ---------------- 基础解析 ----------------

def test_plain_text():
    out, _ = render("你好，世界", {})
    assert out == "你好，世界"


def test_simple_arg():
    out, _ = render("Hello {name}!", {"name": "Ada"})
    assert out == "Hello Ada!"


def test_missing_arg_placeholder():
    out, ctx = render("Hello {name}!", {})
    assert out == "Hello {name}!"
    assert "name" in ctx.missing


def test_apostrophe_escapes():
    out, _ = render("a''b '{x}' c", {})
    assert out == "a'b {x} c"


def test_hash_outside_plural_is_literal():
    out, _ = render("issue #42", {})
    assert out == "issue #42"


# ---------------- plural ----------------

def test_plural_categories_en():
    tpl = "{n, plural, one{# file} other{# files}}"
    assert render(tpl, {"n": 1})[0] == "1 file"
    assert render(tpl, {"n": 2})[0] == "2 files"
    assert render(tpl, {"n": 0})[0] == "0 files"


def test_plural_explicit_value():
    tpl = "{n, plural, =0{none} one{one} other{many}}"
    assert render(tpl, {"n": 0})[0] == "none"
    assert render(tpl, {"n": 1})[0] == "one"
    assert render(tpl, {"n": 5})[0] == "many"


def test_plural_offset():
    tpl = "{n, plural, offset:1 =0{nobody} one{you} other{you and # others}}"
    assert render(tpl, {"n": 0})[0] == "nobody"
    assert render(tpl, {"n": 1})[0] == "you"
    assert render(tpl, {"n": 4})[0] == "you and 3 others"


def test_plural_locale_ru():
    tpl = "{n, plural, one{A} few{B} many{C} other{D}}"
    assert render(tpl, {"n": 1}, "ru")[0] == "A"
    assert render(tpl, {"n": 3}, "ru")[0] == "B"
    assert render(tpl, {"n": 5}, "ru")[0] == "C"
    assert render(tpl, {"n": 1.5}, "ru")[0] == "D"


def test_plural_locale_zh_only_other():
    tpl = "{n, plural, one{A} other{B}}"
    assert render(tpl, {"n": 1}, "zh-Hans")[0] == "B"


def test_selectordinal_en():
    tpl = "{pos, selectordinal, one{#st} two{#nd} few{#rd} other{#th}}"
    assert render(tpl, {"pos": 1})[0] == "1st"
    assert render(tpl, {"pos": 2})[0] == "2nd"
    assert render(tpl, {"pos": 3})[0] == "3rd"
    assert render(tpl, {"pos": 4})[0] == "4th"
    assert render(tpl, {"pos": 11})[0] == "11th"
    assert render(tpl, {"pos": 22})[0] == "22nd"


# ---------------- select ----------------

def test_select_basic():
    tpl = "{g, select, male{He} female{She} other{They}}"
    assert render(tpl, {"g": "male"})[0] == "He"
    assert render(tpl, {"g": "female"})[0] == "She"
    assert render(tpl, {"g": "x"})[0] == "They"


def test_nested_select_in_plural():
    inner1 = "{n, plural, one{his {n} file} other{his {n} files}}"
    inner2 = "{n, plural, one{their file} other{their files}}"
    tpl = "{g, select, male{" + inner1 + "} other{" + inner2 + "}}"
    assert render(tpl, {"g": "male", "n": 1})[0] == "his 1 file"
    assert render(tpl, {"g": "male", "n": 3})[0] == "his 3 files"
    assert render(tpl, {"g": "x", "n": 1})[0] == "their file"


# ---------------- 语法错误 ----------------

def test_error_unclosed_brace():
    _, p = parse("hello {name")
    assert any("未闭合" in e["message"] for e in p.errors)


def test_error_stray_close_brace():
    _, p = parse("hello } world")
    assert any("多余的右花括号" in e["message"] for e in p.errors)


def test_error_unknown_type():
    _, p = parse("{n, pluralx, one{a} other{b}}")
    assert any("未知的格式类型" in e["message"] for e in p.errors)


def test_error_missing_other():
    _, p = parse("{n, plural, one{a}}")
    assert any("other" in e["message"] for e in p.errors)


def test_error_duplicate_selector():
    _, p = parse("{n, plural, one{a} one{b} other{c}}")
    assert any("重复的分支选择器" in e["message"] for e in p.errors)


def test_error_bad_plural_category():
    _, p = parse("{n, plural, single{a} other{b}}")
    assert any("无效的复数类别" in e["message"] for e in p.errors)


def test_error_positions_have_line_col():
    _, p = parse("line1\n{n, plural, one{a}}")
    err = [e for e in p.errors if "other" in e["message"]]
    assert err and err[0]["line"] == 2


def test_tokens_emitted():
    _, p = parse("{n, plural, one{#} other{#}}")
    classes = {t["cls"] for t in p.tokens}
    assert {"brace", "var", "keyword", "selector", "hash"} <= classes


# ---------------- 类型错误 ----------------

def test_type_error_plural_non_numeric():
    r = analyze("{n, plural, one{a} other{b}}", "en", {"n": "abc"})
    assert any("需要数字" in e["message"] for e in r["errors"])
    assert r["stats"]["coverage_available"] is False


def test_type_error_number_format():
    r = analyze("{price, number}", "en", {"price": "abc"})
    assert any("需要数字" in e["message"] for e in r["errors"])


# ---------------- analyze：分支枚举与覆盖 ----------------

SAMPLE = ("{gender, select, male{He} female{She} other{They}} has "
          "{count, plural, =0{no new messages} one{# new message} other{# new messages}}.")


def test_analyze_enumerates_all_paths():
    r = analyze(SAMPLE, "en", {"gender": "female", "count": 3})
    assert r["ok"]
    # 3 个 select 分支 × 3 个 plural 分支 = 9 条路径
    assert r["stats"]["total_paths"] == 9
    assert len(r["paths"]) == 9
    # 样例 gender=female, count=3 → 恰好覆盖 1 条
    assert r["stats"]["covered_paths"] == 1
    covered = [p for p in r["paths"] if p["covered"]]
    assert covered[0]["output"] == "She has 3 new messages."


def test_analyze_main_render():
    r = analyze(SAMPLE, "en", {"gender": "male", "count": 0})
    assert r["main"]["output"] == "He has no new messages."
    assert any("gender" in c for c in r["main"]["conditions"])
    assert any("count" in c for c in r["main"]["conditions"])


def test_analyze_uncovered_branches_marked():
    r = analyze(SAMPLE, "en", {"gender": "female", "count": 3})
    nodes = {n["variable"]: n for n in r["nodes"]}
    gender = {b["selector"]: b for b in nodes["gender"]["branches"]}
    count = {b["selector"]: b for b in nodes["count"]["branches"]}
    assert gender["female"]["covered"] is True
    assert gender["male"]["covered"] is False
    assert count["other"]["covered"] is True
    assert count["=0"]["covered"] is False


def test_analyze_unreachable_branch_zh():
    tpl = "{n, plural, one{X} other{Y}}"
    r = analyze(tpl, "zh-Hans", {"n": 1})
    branch = r["nodes"][0]["branches"][0]
    assert branch["selector"] == "one"
    assert branch["reachable"] is False
    assert any("不可达" in w["message"] for w in r["warnings"])
    # zh 下只有 other 一条路径
    assert r["stats"]["total_paths"] == 1


def test_analyze_shadowed_keyword_branch():
    # en 下 one 仅 n=1，被 =1 抢占 → one 不可达
    tpl = "{n, plural, =1{exact} one{cat} other{other}}"
    r = analyze(tpl, "en", {"n": 1})
    branches = {b["selector"]: b for b in r["nodes"][0]["branches"]}
    assert branches["one"]["reachable"] is False
    assert r["stats"]["total_paths"] == 2


def test_analyze_missing_and_unused_vars():
    r = analyze("Hi {name}, {n, plural, one{a} other{b}}", "en", {"n": 1, "extra": 1})
    assert any("name" in w["message"] for w in r["warnings"])
    assert any("extra" in i["message"] for i in r["infos"])


def test_analyze_shared_variable_consistency():
    # 同一变量出现在两个 plural 中：路径必须自洽
    tpl = "{n, plural, one{a} other{b}}-{n, plural, one{c} other{d}}"
    r = analyze(tpl, "en", {"n": 1})
    outputs = sorted(p["output"] for p in r["paths"])
    assert outputs == ["a-c", "b-d"]
    assert r["stats"]["total_paths"] == 2


def test_analyze_path_conditions_present():
    r = analyze(SAMPLE, "en", {"gender": "female", "count": 1})
    for p in r["paths"]:
        assert len(p["conditions"]) == 2
    conds = {c for p in r["paths"] for c in p["conditions"]}
    assert any('gender = "male"' in c for c in conds)
    assert any("count = 0" in c for c in conds)
    assert any("one" in c for c in conds)


def test_analyze_nested_path_count():
    tpl = ("{a, select, x{{b, plural, one{1} other{2}}} "
           "y{{b, plural, one{3} other{4}}} other{z}}")
    r = analyze(tpl, "en", {"a": "x", "b": 1})
    # x→2, y→2, other→1 = 5
    assert r["stats"]["total_paths"] == 5


def test_truncation_cap():
    # 8 个 select × 3 分支 = 6561 条 → 截断
    tpl = " ".join(
        "{v%d, select, a{x} b{y} other{z}}" % i for i in range(8))
    r = analyze(tpl, "en", {})
    assert r["stats"]["total_paths"] == 6561
    assert r["stats"]["truncated"] is True
    assert len(r["paths"]) == r["stats"]["max_paths"]


# ---------------- 复数规则 ----------------

@pytest.mark.parametrize("locale,n,cat", [
    ("en", 1, "one"), ("en", 0, "other"), ("en", 1.5, "other"),
    ("fr", 0, "one"), ("fr", 2, "other"),
    ("ar", 0, "zero"), ("ar", 2, "two"), ("ar", 3, "few"), ("ar", 11, "many"),
    ("pl", 1, "one"), ("pl", 2, "few"), ("pl", 5, "many"),
    ("ja", 1, "other"),
])
def test_plural_category(locale, n, cat):
    assert plural_category(locale, n) == cat


def test_ordinal_category():
    assert plural_category("en", 1, ordinal=True) == "one"
    assert plural_category("en", 2, ordinal=True) == "two"
    assert plural_category("en", 3, ordinal=True) == "few"
    assert plural_category("en", 11, ordinal=True) == "other"


def test_representative_excludes_explicit():
    # en 的 one 只有 1；排除 1 后无代表值
    assert representative_for_category("en", "one", False, {1.0}) is None
    assert representative_for_category("en", "one", False, set()) == 1
