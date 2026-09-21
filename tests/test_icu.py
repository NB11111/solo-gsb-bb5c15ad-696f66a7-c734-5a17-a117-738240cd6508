"""解析器 / 渲染器单元测试:  python -m unittest discover -s tests -v"""
import unittest

from app.icu_parser import parse_icu
from app.renderer import render_icu


def render(src, locale="en", samples=None):
    ast, errs = parse_icu(src)
    return render_icu(ast, errs, src, locale, samples or {})


class TestParser(unittest.TestCase):
    def test_simple_arg(self):
        r = render("你好 {name}", "zh", {"name": "世界"})
        self.assertEqual(r.text, "你好 世界")

    def test_quotes(self):
        r = render("It''s a '{test}' {x}", "en", {"x": "!"})
        self.assertEqual(r.text, "Its a {test} !")

    def test_unclosed_brace(self):
        r = render("{n, plural, one {一} other {#}")
        self.assertTrue(any("未闭合" in e["message"] for e in r.errors))

    def test_extra_close_brace(self):
        r = render("a } b")
        self.assertTrue(any("多余" in e["message"] for e in r.errors))

    def test_missing_other(self):
        r = render("{g, select, x {X}}")
        self.assertTrue(any("other" in e["message"] for e in r.errors))

    def test_choice_rejected(self):
        r = render("{n, choice, 0#x}")
        self.assertTrue(any("choice" in e["message"] for e in r.errors))

    def test_duplicate_case(self):
        r = render("{g, select, x {1} x {2} other {o}}")
        self.assertTrue(any("重复" in e["message"] for e in r.errors))

    def test_bad_identifier(self):
        r = render("{1bad}")
        self.assertTrue(any("参数名" in e["message"] for e in r.errors))

    def test_hash_outside_plural(self):
        r = render("price: #")
        self.assertTrue(any("#" in e["message"] for e in r.errors))


class TestPlural(unittest.TestCase):
    TPL = ("{n, plural, offset:1 =0 {无人} =1 {仅你} "
           "one {# 人} few {# 个?} other {# 人}}")

    def test_explicit_zero(self):
        r = render(self.TPL, "en", {"n": "0"})
        self.assertEqual(r.text, "无人")

    def test_explicit_one(self):
        r = render(self.TPL, "en", {"n": "1"})
        self.assertEqual(r.text, "仅你")

    def test_offset_hash(self):
        r = render(self.TPL, "en", {"n": "5"})
        self.assertEqual(r.text, "4 人")

    def test_ru_few(self):
        r = render("{n, plural, one {#x} few {#y} many {#z} other {#o}}",
                   "ru", {"n": "3"})
        self.assertEqual(r.text, "3y")

    def test_fraction_goes_other(self):
        r = render("{n, plural, one {一} other {#}}", "en", {"n": "1.5"})
        self.assertEqual(r.text, "1.5")


class TestSelectOrdinal(unittest.TestCase):
    TPL = "{n, selectordinal, one {#st} two {#nd} few {#rd} other {#th}}"

    def test_ordinal_cases(self):
        for n, suffix in [("1", "1st"), ("2", "2nd"), ("3", "3rd"),
                          ("11", "11th"), ("21", "21st"), ("42", "42nd")]:
            r = render(self.TPL, "en", {"n": n})
            self.assertEqual(r.text, suffix, n)


class TestSelect(unittest.TestCase):
    TPL = "{g, select, female {她} male {他} other {TA}}"

    def test_match(self):
        self.assertEqual(render(self.TPL, "zh", {"g": "male"}).text, "他")

    def test_other_fallback(self):
        self.assertEqual(render(self.TPL, "zh", {"g": "??"}).text, "TA")


class TestNested(unittest.TestCase):
    TPL = ("{g, select, "
           "female {{n, plural, one {她 #} other {她们 #}}} "
           "other {其它}}")

    def test_nested_covered_paths(self):
        r = render(self.TPL, "zh", {"g": "female", "n": "5"})
        self.assertEqual(r.text, "她们 5")
        texts = {(b.covered, b.text) for b in r.branches}
        # 外层 select 容器叶子(含内部文本)、内部两个 plural 分支、other
        self.assertIn((True, "她们 5"), texts)
        self.assertIn((False, "她 5"), texts)
        self.assertIn((False, "其它"), texts)

    def test_all_branches_enumerated(self):
        r = render(self.TPL, "zh", {"g": "female", "n": "5"})
        self.assertEqual(len(r.branches), 4)
        self.assertEqual(sum(b.covered for b in r.branches), 2)


class TestHashBinding(unittest.TestCase):
    def test_nested_hash_binds_inner_plural(self):
        # # 在 plural 内必须绑定最近的 plural 变量
        tpl = ("{outer, plural, one {外#: {inner, plural, one {内#} other {内#}}} "
               "other {外#: {inner, plural, one {内#} other {内#}}}}")
        r = render(tpl, "en", {"outer": "1", "inner": "5"})
        self.assertEqual(r.text, "外1: 内5")
        r = render(tpl, "en", {"outer": "3", "inner": "2"})
        self.assertEqual(r.text, "外3: 内2")

    def test_hash_with_offset(self):
        tpl = "{n, plural, offset:2 one {#} other {#}}"
        self.assertEqual(render(tpl, "en", {"n": "3"}).text, "1")


class TestVariablesAndErrors(unittest.TestCase):
    def test_missing_variable(self):
        r = render("{a} {b, number}")
        msgs = [e["message"] for e in r.errors]
        self.assertTrue(any("a" in m for m in msgs))
        self.assertTrue(any("b" in m for m in msgs))

    def test_number_type_mismatch(self):
        r = render("{n, number}", "en", {"n": "abc"})
        self.assertTrue(any("数字" in e["message"] for e in r.errors))
        self.assertIn("⚠abc⚠", r.text)  # 仍然尽力渲染

    def test_date_format(self):
        r = render("{d, date, long}", "de", {"d": "2026-09-21"})
        self.assertIn("2026", r.text)

    def test_percent_and_currency(self):
        r = render("{p, number, percent}", "en", {"p": "0.5"})
        self.assertEqual(r.text, "50%")
        r = render("{c, number, USD}", "en", {"c": "12.3"})
        self.assertIn("12.30", r.text)

    def test_unused_sample_warning(self):
        r = render("{a}", "en", {"a": "x", "ghost": "y"})
        self.assertTrue(any("ghost" in w["message"] for w in r.warnings))

    def test_unknown_locale_falls_back(self):
        r = render("{n, plural, one {一} other {#}}", "xx-zz", {"n": "1"})
        self.assertEqual(r.resolved_locale, "en")
        self.assertTrue(any("语言环境" in e["message"] for e in r.errors))


class TestPositions(unittest.TestCase):
    def test_error_linecol_multiline(self):
        src = "line1 {a}\nline2 }\nline3 {b, number}"
        r = render(src, "en", {"a": "1"})
        err = [e for e in r.errors if "多余" in e["message"]][0]
        self.assertEqual((err["line"], err["col"]), (2, 7))


if __name__ == "__main__":
    unittest.main()
