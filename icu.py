"""ICU MessageFormat 解析、渲染与分支分析。

支持嵌套的 plural / select / selectordinal，提供：
- 带行列信息的语法错误（括号、类型、变量）
- 按语言环境（CLDR 复数规则）渲染
- 枚举所有可达分支路径并给出触发条件
- 标记样例变量未覆盖的分支
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 复数规则：优先使用 Babel（CLDR 数据），不可用时退回内置的常见语言规则
# ---------------------------------------------------------------------------

try:
    from babel import Locale as _BabelLocale
    from babel.numbers import format_decimal as _babel_decimal
    from babel.numbers import format_percent as _babel_percent
    HAS_BABEL = True
except Exception:  # pragma: no cover
    HAS_BABEL = False

CARDINAL_CATEGORIES = ("zero", "one", "two", "few", "many", "other")


def _lang(locale: str) -> str:
    return (locale or "en").replace("_", "-").split("-")[0].lower()


def _is_int(n: float) -> bool:
    return float(n) == int(n)


# 内置兜底 cardinal 规则（仅在未安装 Babel 时使用，覆盖常见语言）
def _fb_other(n):
    return "other"


def _fb_one_if_1(n):
    return "one" if n == 1 else "other"


def _fb_fr(n):
    return "one" if n in (0, 1) else "other"


def _fb_ru(n):
    if not _is_int(n):
        return "other"
    i = int(abs(n))
    if i % 10 == 1 and i % 100 != 11:
        return "one"
    if 2 <= i % 10 <= 4 and not 12 <= i % 100 <= 14:
        return "few"
    if i % 10 == 0 or 5 <= i % 10 <= 9 or 11 <= i % 100 <= 14:
        return "many"
    return "other"


def _fb_pl(n):
    if n == 1:
        return "one"
    if not _is_int(n):
        return "other"
    i = int(abs(n))
    if 2 <= i % 10 <= 4 and not 12 <= i % 100 <= 14:
        return "few"
    if i % 10 in (0, 1) or 5 <= i % 10 <= 9 or 12 <= i % 100 <= 14:
        return "many"
    return "other"


def _fb_cs(n):
    if n == 1:
        return "one"
    if _is_int(n) and 2 <= int(n) <= 4:
        return "few"
    if not _is_int(n):
        return "many"
    return "other"


def _fb_ar(n):
    if not _is_int(n):
        return "other"
    i = int(abs(n))
    if i == 0:
        return "zero"
    if i == 1:
        return "one"
    if i == 2:
        return "two"
    if 3 <= i % 100 <= 10:
        return "few"
    if 11 <= i % 100 <= 99:
        return "many"
    return "other"


def _fb_ro(n):
    if n == 1:
        return "one"
    if not _is_int(n):
        return "few"
    i = int(abs(n))
    if i == 0 or 1 <= i % 100 <= 19:
        return "few"
    return "other"


def _fb_lt(n):
    if not _is_int(n):
        return "many"
    i = int(abs(n))
    if i % 10 == 1 and not 11 <= i % 100 <= 19:
        return "one"
    if 2 <= i % 10 <= 9 and not 11 <= i % 100 <= 19:
        return "few"
    return "other"


def _fb_ga(n):
    if n == 1:
        return "one"
    if n == 2:
        return "two"
    if 3 <= n <= 6:
        return "few"
    if 7 <= n <= 10:
        return "many"
    return "other"


def _fb_he(n):
    if n == 1:
        return "one"
    if n == 2:
        return "two"
    if _is_int(n) and n != 0 and int(n) % 10 == 0:
        return "many"
    return "other"


_FALLBACK_CARDINAL = {
    "en": _fb_one_if_1, "de": _fb_one_if_1, "es": _fb_one_if_1,
    "it": _fb_one_if_1, "nl": _fb_one_if_1, "sv": _fb_one_if_1,
    "da": _fb_one_if_1, "fi": _fb_one_if_1, "el": _fb_one_if_1,
    "pt": _fb_one_if_1, "tr": _fb_other, "ja": _fb_other, "ko": _fb_other,
    "zh": _fb_other, "vi": _fb_other, "th": _fb_other, "id": _fb_other,
    "ms": _fb_other, "fr": _fb_fr, "ru": _fb_ru, "uk": _fb_ru,
    "be": _fb_ru, "hr": _fb_ru, "sr": _fb_ru, "bs": _fb_ru,
    "pl": _fb_pl, "cs": _fb_cs, "sk": _fb_cs, "ar": _fb_ar,
    "ro": _fb_ro, "lt": _fb_lt, "ga": _fb_ga, "he": _fb_he,
}


def plural_category(locale: str, n: float, ordinal: bool = False) -> str:
    """返回数字 n 在指定语言环境下的 CLDR 复数类别。"""
    if ordinal:
        return _ordinal_category(locale, n)
    if HAS_BABEL:
        try:
            loc = _BabelLocale.parse((locale or "en").replace("-", "_"))
            return str(loc.plural_form(n))
        except Exception:
            pass
    fn = _FALLBACK_CARDINAL.get(_lang(locale), _fb_other)
    return fn(n)


def _ordinal_category(locale: str, n: float) -> str:
    """常见语言的 CLDR ordinal 规则；其余语言回退为 other。"""
    lang = _lang(locale)
    if not _is_int(n):
        return "other"
    i = int(abs(n))
    if lang == "en":
        if i % 10 == 1 and i % 100 != 11:
            return "one"
        if i % 10 == 2 and i % 100 != 12:
            return "two"
        if i % 10 == 3 and i % 100 != 13:
            return "few"
        return "other"
    if lang == "fr":
        return "one" if i == 1 else "other"
    if lang == "it":
        return "many" if i in (8, 11, 80, 800) else "other"
    if lang == "ca":
        if i in (1, 3):
            return "one"
        if i == 2:
            return "two"
        if i == 4:
            return "few"
        return "other"
    if lang == "uk":
        return "few" if i % 10 == 3 and i % 100 != 13 else "other"
    if lang == "mk":
        if i % 10 == 1 and i % 100 != 11:
            return "one"
        if i % 10 == 2 and i % 100 != 12:
            return "two"
        if i % 10 in (7, 8) and i % 100 not in (17, 18):
            return "many"
        return "other"
    return "other"


def representative_for_category(locale: str, category: str, ordinal: bool,
                                exclude: set[float]):
    """找一个能触发指定复数类别的示例数字（避开 exclude 中的显式值）。"""
    candidates = list(range(0, 211)) + [i + 0.5 for i in range(0, 11)]
    for c in candidates:
        if c in exclude:
            continue
        if plural_category(locale, c, ordinal) == category:
            return c
    return None


def format_number(value, locale: str, style: str | None = None) -> str:
    if HAS_BABEL:
        try:
            if style == "percent":
                return _babel_percent(value, locale=locale)
            return _babel_decimal(value, locale=locale)
        except Exception:
            pass
    return str(value)


# ---------------------------------------------------------------------------
# AST
# ---------------------------------------------------------------------------

@dataclass
class Text:
    text: str


@dataclass
class Arg:
    name: str
    pos: int


@dataclass
class FmtArg:
    name: str
    fmt: str
    style: str | None
    pos: int


@dataclass
class Hash:
    pos: int


@dataclass
class Branch:
    selector: str
    message: list
    pos: int


@dataclass
class Plural:
    name: str
    kind: str  # 'plural' | 'selectordinal'
    offset: float
    branches: list[Branch]
    pos: int
    node_id: str


@dataclass
class Select:
    name: str
    branches: list[Branch]
    pos: int
    node_id: str


SIMPLE_FORMATS = ("number", "date", "time", "spellout", "ordinal", "duration")

# ---------------------------------------------------------------------------
# 解析器
# ---------------------------------------------------------------------------


class Parser:
    def __init__(self, text: str):
        self.s = text
        self.n = len(text)
        self.i = 0
        self.errors: list[dict] = []
        self.tokens: list[dict] = []
        self.plural_depth = 0
        self.node_counter = 0

    # -- 工具 --
    def error(self, pos: int, message: str, length: int = 1):
        pos = max(0, min(pos, self.n))
        line = self.s.count("\n", 0, pos) + 1
        last_nl = self.s.rfind("\n", 0, pos)
        col = pos - last_nl
        self.errors.append({
            "message": message, "pos": pos, "length": max(1, length),
            "line": line, "col": col, "severity": "error",
        })

    def token(self, start: int, end: int, cls: str):
        if end > start:
            self.tokens.append({"start": start, "end": end, "cls": cls})

    def skip_ws(self):
        while self.i < self.n and self.s[self.i] in " \t\r\n":
            self.i += 1

    def recover(self):
        """出错后跳到下一个可同步的位置（匹配的右括号之后）。"""
        depth = 0
        while self.i < self.n:
            c = self.s[self.i]
            if c == "{":
                depth += 1
            elif c == "}":
                if depth == 0:
                    self.i += 1
                    return
                depth -= 1
            self.i += 1

    # -- 消息 --
    def parse_message(self, top: bool):
        parts: list = []
        buf: list[str] = []

        def flush():
            if buf:
                parts.append(Text("".join(buf)))
                buf.clear()

        while self.i < self.n:
            c = self.s[self.i]
            if c == "'":
                if self.i + 1 < self.n and self.s[self.i + 1] == "'":
                    buf.append("'")
                    self.i += 2
                elif self.i + 1 < self.n and self.s[self.i + 1] in "{}#|":
                    j = self.s.find("'", self.i + 2)
                    if j == -1:
                        buf.append(self.s[self.i + 1:])
                        self.i = self.n
                    else:
                        buf.append(self.s[self.i + 1:j])
                        self.i = j + 1
                else:
                    buf.append("'")
                    self.i += 1
            elif c == "{":
                flush()
                arg = self.parse_argument()
                if arg is not None:
                    parts.append(arg)
            elif c == "}":
                if top:
                    self.error(self.i, "多余的右花括号 '}'")
                    self.i += 1
                else:
                    break
            elif c == "#":
                if self.plural_depth > 0:
                    flush()
                    parts.append(Hash(self.i))
                    self.token(self.i, self.i + 1, "hash")
                    self.i += 1
                else:
                    buf.append("#")
                    self.i += 1
            else:
                buf.append(c)
                self.i += 1
        flush()
        return parts

    def parse(self):
        return self.parse_message(top=True)

    # -- 参数 --
    def parse_argument(self):
        start = self.i
        self.token(start, start + 1, "brace")
        self.i += 1
        self.skip_ws()
        name_start = self.i
        while self.i < self.n and (self.s[self.i].isalnum() or self.s[self.i] in "_.-"):
            self.i += 1
        name = self.s[name_start:self.i]
        if not name:
            self.error(self.i, "缺少变量名")
        else:
            self.token(name_start, self.i, "var")
        self.skip_ws()

        if self.i >= self.n:
            self.error(start, "未闭合的 '{'：缺少右括号 '}'")
            return Arg(name or "?", start)
        if self.s[self.i] == "}":
            self.token(self.i, self.i + 1, "brace")
            self.i += 1
            return Arg(name, start)
        if self.s[self.i] != ",":
            self.error(self.i, "此处应为 ',' 或 '}'")
            self.recover()
            return Arg(name, start)

        self.i += 1
        self.skip_ws()
        tstart = self.i
        while self.i < self.n and self.s[self.i].isalpha():
            self.i += 1
        typ = self.s[tstart:self.i]
        if typ:
            self.token(tstart, self.i, "keyword")
        self.skip_ws()

        if typ in ("plural", "selectordinal", "select"):
            return self.parse_branch_arg(name, typ, start)
        if typ in SIMPLE_FORMATS:
            style = None
            if self.i < self.n and self.s[self.i] == ",":
                self.i += 1
                self.skip_ws()
                sstart = self.i
                while self.i < self.n and self.s[self.i] != "}":
                    self.i += 1
                style = self.s[sstart:self.i].strip() or None
            if self.i < self.n and self.s[self.i] == "}":
                self.token(self.i, self.i + 1, "brace")
                self.i += 1
            else:
                self.error(start, "未闭合的 '{'：缺少右括号 '}'")
            return FmtArg(name, typ, style, start)
        if not typ:
            self.error(self.i, "缺少格式类型（如 plural、select、selectordinal、number）")
        else:
            self.error(tstart, f"未知的格式类型 '{typ}'", max(1, len(typ)))
        self.recover()
        return Arg(name, start)

    def parse_branch_arg(self, name: str, typ: str, start: int):
        self.node_counter += 1
        node_id = f"n{self.node_counter}"
        is_plural = typ in ("plural", "selectordinal")
        offset = 0.0
        branches: list[Branch] = []
        seen: set[str] = set()

        # 类型关键字后的第一个逗号
        self.skip_ws()
        if self.i < self.n and self.s[self.i] == ",":
            self.i += 1

        # 分支之间以空白分隔（也容忍多余的逗号），直到右括号
        while True:
            self.skip_ws()
            if self.i < self.n and self.s[self.i] == ",":
                self.i += 1
                self.skip_ws()
            if self.i >= self.n:
                self.error(start, "未闭合的 '{'：分支定义不完整")
                break
            if self.s[self.i] == "}":
                self.token(self.i, self.i + 1, "brace")
                self.i += 1
                if not branches:
                    self.error(start, f"'{typ}' 至少需要一个分支")
                break

            # offset:N（仅 plural / selectordinal）
            if is_plural and self.s.startswith("offset", self.i) and \
                    (self.i + 6 >= self.n or not self.s[self.i + 6].isalnum()):
                ostart = self.i
                self.i += 6
                self.token(ostart, self.i, "keyword")
                self.skip_ws()
                if self.i < self.n and self.s[self.i] == ":":
                    self.i += 1
                    self.skip_ws()
                    nstart = self.i
                    while self.i < self.n and (self.s[self.i].isdigit() or self.s[self.i] in ".-+"):
                        self.i += 1
                    numtxt = self.s[nstart:self.i]
                    try:
                        offset = float(numtxt)
                        self.token(nstart, self.i, "number")
                    except ValueError:
                        self.error(nstart, f"offset 值无效：'{numtxt}'", max(1, len(numtxt)))
                else:
                    self.error(self.i, "offset 后应为 ':'")
                continue

            # 选择器
            sel_start = self.i
            if self.i < self.n and self.s[self.i] == "=":
                self.i += 1
                nstart = self.i
                while self.i < self.n and (self.s[self.i].isdigit() or self.s[self.i] in ".-+"):
                    self.i += 1
                numtxt = self.s[nstart:self.i]
                selector = "=" + numtxt
                self.token(sel_start, self.i, "selector")
                try:
                    float(numtxt)
                except ValueError:
                    self.error(sel_start, f"显式值选择器应为数字：'={numtxt}'",
                               max(1, self.i - sel_start))
            else:
                while self.i < self.n and (self.s[self.i].isalnum() or self.s[self.i] == "_"):
                    self.i += 1
                selector = self.s[sel_start:self.i]
                if not selector:
                    self.error(self.i, "缺少分支选择器（如 one、other、=0）")
                    self.recover()
                    break
                self.token(sel_start, self.i, "selector")
                if is_plural and selector not in CARDINAL_CATEGORIES:
                    self.error(
                        sel_start,
                        f"无效的复数类别 '{selector}'"
                        "（应为 zero/one/two/few/many/other 或 =N）",
                        len(selector),
                    )
                if not is_plural and selector.startswith("="):
                    self.error(sel_start, "select 不支持 '=N' 选择器", len(selector))

            if selector in seen:
                self.error(sel_start, f"重复的分支选择器 '{selector}'", len(selector))
            seen.add(selector)
            self.skip_ws()

            if self.i >= self.n or self.s[self.i] != "{":
                self.error(self.i, f"分支 '{selector}' 后应为 '{{'")
                self.recover()
                break
            self.token(self.i, self.i + 1, "brace")
            self.i += 1
            if is_plural:
                self.plural_depth += 1
            sub = self.parse_message(top=False)
            if is_plural:
                self.plural_depth -= 1
            if self.i < self.n and self.s[self.i] == "}":
                self.token(self.i, self.i + 1, "brace")
                self.i += 1
            else:
                self.error(start, "未闭合的 '{'：分支消息缺少右括号 '}'")
                break
            branches.append(Branch(selector, sub, sel_start))

        if branches and "other" not in seen:
            self.error(start, f"'{typ}' 必须包含 'other' 分支")

        if is_plural:
            return Plural(name, typ, offset, branches, start, node_id)
        return Select(name, branches, start, node_id)


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

_MISSING = object()


def _to_number(value):
    """把样例值转成数字；失败返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _fmt_num(v: float) -> str:
    if float(v) == int(v) and abs(v) < 1e15:
        return str(int(v))
    return str(v)


def _str_value(value) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return ""
    if isinstance(value, float) and value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return str(value)


class RenderCtx:
    def __init__(self, variables: dict, locale: str):
        self.variables = variables
        self.locale = locale
        self.number_value = 0.0          # 当前 plural 的 # 值（已减 offset）
        self.taken: set[tuple[str, str]] = set()   # (node_id, selector)
        self.conditions: list[str] = []
        self.errors: list[dict] = []
        self.missing: set[str] = set()
        self.incomplete = False          # 分支变量缺失/类型错误 → 覆盖情况不可判定

    def child(self, number_value=None):
        c = RenderCtx(self.variables, self.locale)
        c.number_value = self.number_value if number_value is None else number_value
        c.taken = self.taken
        c.conditions = self.conditions
        c.errors = self.errors
        c.missing = self.missing
        return c

    def error_at(self, pos: int, message: str):
        self.errors.append({
            "message": message, "pos": pos, "length": 1,
            "line": None, "col": None, "severity": "error",
        })


def route_plural(node: Plural, num: float, locale: str):
    """根据数值选择 plural 分支，返回 (branch, category_or_None)。"""
    for b in node.branches:
        if b.selector.startswith("="):
            try:
                if float(b.selector[1:]) == num:
                    return b, None
            except ValueError:
                continue
    cat = plural_category(locale, num, node.kind == "selectordinal")
    for b in node.branches:
        if b.selector == cat:
            return b, cat
    for b in node.branches:
        if b.selector == "other":
            return b, cat
    return node.branches[-1] if node.branches else None, cat


def route_select(node: Select, key: str):
    for b in node.branches:
        if b.selector == key:
            return b
    for b in node.branches:
        if b.selector == "other":
            return b
    return node.branches[-1] if node.branches else None


def render_message(parts: list, ctx: RenderCtx) -> str:
    out: list[str] = []
    for node in parts:
        if isinstance(node, Text):
            out.append(node.text)
        elif isinstance(node, Hash):
            out.append(format_number(ctx.number_value, ctx.locale))
        elif isinstance(node, Arg):
            v = ctx.variables.get(node.name, _MISSING)
            if v is _MISSING:
                ctx.missing.add(node.name)
                out.append("{" + node.name + "}")
            else:
                out.append(_str_value(v))
        elif isinstance(node, FmtArg):
            v = ctx.variables.get(node.name, _MISSING)
            if v is _MISSING:
                ctx.missing.add(node.name)
                out.append("{" + node.name + "}")
            elif node.fmt == "number":
                num = _to_number(v)
                if num is None:
                    ctx.error_at(node.pos, f"变量 '{node.name}' 需要数字，当前为 {_str_value(v)!r}")
                    out.append("{" + node.name + "}")
                else:
                    style = node.style if node.style in ("percent",) else None
                    out.append(format_number(num, ctx.locale, style))
            else:  # date/time/spellout/ordinal/duration：预览时原样输出
                out.append(_str_value(v))
        elif isinstance(node, Plural):
            v = ctx.variables.get(node.name, _MISSING)
            if v is _MISSING:
                ctx.missing.add(node.name)
                ctx.incomplete = True
                branch = next((b for b in node.branches if b.selector == "other"),
                              node.branches[-1] if node.branches else None)
                if branch:
                    sub = ctx.child(number_value=0.0)
                    out.append(render_message(branch.message, sub))
                continue
            num = _to_number(v)
            if num is None:
                ctx.error_at(node.pos, f"变量 '{node.name}' 用于 {node.kind}，需要数字，"
                                       f"当前为 {_str_value(v)!r}")
                ctx.incomplete = True
                branch = next((b for b in node.branches if b.selector == "other"),
                              node.branches[-1] if node.branches else None)
                if branch:
                    sub = ctx.child(number_value=0.0)
                    out.append(render_message(branch.message, sub))
                continue
            branch, cat = route_plural(node, num, ctx.locale)
            if branch is None:
                continue
            ctx.taken.add((node.node_id, branch.selector))
            if branch.selector.startswith("="):
                ctx.conditions.append(f"{node.name} = {_fmt_num(num)}（精确匹配 {branch.selector}）")
            else:
                ctx.conditions.append(
                    f"{node.name} = {_fmt_num(num)} → 复数类别 \"{branch.selector}\"")
            sub = ctx.child(number_value=num - node.offset)
            out.append(render_message(branch.message, sub))
        elif isinstance(node, Select):
            v = ctx.variables.get(node.name, _MISSING)
            if v is _MISSING:
                ctx.missing.add(node.name)
                ctx.incomplete = True
                branch = next((b for b in node.branches if b.selector == "other"),
                              node.branches[-1] if node.branches else None)
                if branch:
                    out.append(render_message(branch.message, ctx.child()))
                continue
            key = _str_value(v)
            branch = route_select(node, key)
            if branch is None:
                continue
            ctx.taken.add((node.node_id, branch.selector))
            ctx.conditions.append(f"{node.name} = \"{key}\"")
            out.append(render_message(branch.message, ctx.child()))
    return "".join(out)


# ---------------------------------------------------------------------------
# 分支枚举
# ---------------------------------------------------------------------------

def walk_branch_nodes(parts: list):
    for node in parts:
        if isinstance(node, (Plural, Select)):
            yield node
            for b in node.branches:
                yield from walk_branch_nodes(b.message)


def collect_used_vars(parts: list, acc: set):
    for node in parts:
        if isinstance(node, (Arg, FmtArg)):
            acc.add(node.name)
        elif isinstance(node, (Plural, Select)):
            acc.add(node.name)
            for b in node.branches:
                collect_used_vars(b.message, acc)


def _explicit_values(node) -> set[float]:
    vals = set()
    for b in node.branches:
        if b.selector.startswith("="):
            try:
                vals.add(float(b.selector[1:]))
            except ValueError:
                pass
    return vals


def branch_condition(node, selector: str, locale: str, rep_override=None):
    """返回 (条件描述, 代表值, 是否可达)。

    rep_override 非空时，以该值作为分支代表值（用于样例值命中的情况）。
    """
    if isinstance(node, Select):
        return f'{node.name} = "{selector}"', selector, True
    # plural / selectordinal
    if selector.startswith("="):
        try:
            num = float(selector[1:])
        except ValueError:
            return f"{node.name} = ?", None, False
        return f"{node.name} = {_fmt_num(num)}（精确匹配）", num, True
    ordinal = node.kind == "selectordinal"
    kind_label = "序数" if ordinal else "复数"
    if rep_override is not None:
        rep = rep_override
        return (f"{node.name} 的{kind_label}类别为 \"{selector}\""
                f"（样例值：{node.name} = {_fmt_num(rep)}）"), rep, True
    rep = representative_for_category(locale, selector, ordinal, _explicit_values(node))
    if rep is None:
        return (f"{node.name} 的{kind_label}类别为 \"{selector}\""
                f"（当前语言环境下无可用取值，不可达）"), None, False
    return (f"{node.name} 的{kind_label}类别为 \"{selector}\""
            f"（示例：{node.name} = {_fmt_num(rep)}）"), rep, True


def _routes_to(node, value, selector: str, locale: str) -> bool:
    """判断给定值在该节点是否会路由到 selector 分支。"""
    if isinstance(node, Plural):
        num = _to_number(value)
        if num is None:
            return False
        branch, _ = route_plural(node, num, locale)
    else:
        branch = route_select(node, _str_value(value))
    return branch is not None and branch.selector == selector


def iter_paths(parts: list, base_vars: dict, locale: str, limit: int,
               prefer: dict | None = None):
    """枚举所有可达分支路径。

    每条路径为 (conditions:list[str], variables:dict)。
    同一变量在多个分支节点间共享取值，保证路径自洽。
    prefer 中的样例值若能触发目标分支，则优先用作该分支的代表值
    （这样被样例覆盖的路径会用真实样例值渲染）。
    """
    prefer = prefer or {}
    results = []

    def rec(frag: list, assigned: dict, conds: list):
        if len(results) >= limit:
            return
        idx = -1
        node = None
        for k, item in enumerate(frag):
            if isinstance(item, (Plural, Select)):
                idx = k
                node = item
                break
        if node is None:
            results.append((conds, assigned))
            return
        rest = frag[idx + 1:]
        name = node.name
        if name in assigned:
            # 变量已在该路径上赋值 → 按该值路由到唯一分支
            v = assigned[name]
            if isinstance(node, Plural):
                num = _to_number(v)
                if num is None:
                    branch = next((b for b in node.branches if b.selector == "other"),
                                  node.branches[-1] if node.branches else None)
                else:
                    branch, _ = route_plural(node, num, locale)
            else:
                branch = route_select(node, _str_value(v))
            if branch is not None:
                rec(branch.message + rest, assigned, conds)
            return
        for b in node.branches:
            use_prefer = name in prefer and \
                _routes_to(node, prefer[name], b.selector, locale)
            cond, rep, reachable = branch_condition(
                node, b.selector, locale,
                prefer[name] if use_prefer else None)
            if not reachable:
                continue
            new_assigned = dict(assigned)
            new_assigned[name] = rep
            rec(b.message + rest, new_assigned, conds + [cond])

    rec(parts, dict(base_vars), [])
    return results


def count_paths(parts: list, base_vars: dict, locale: str, cap: int = 10000) -> tuple[int, bool]:
    """返回 (路径数, 是否被 cap 截断)。"""
    res = iter_paths(parts, base_vars, locale, cap)
    return len(res), len(res) >= cap


# ---------------------------------------------------------------------------
# 顶层分析
# ---------------------------------------------------------------------------

MAX_TEMPLATE_LEN = 20000
MAX_PATHS = 300


def analyze(template: str, locale: str, variables: dict) -> dict:
    locale = (locale or "en").strip() or "en"
    variables = variables or {}

    if len(template) > MAX_TEMPLATE_LEN:
        return {
            "ok": False,
            "errors": [{"message": f"模板过长（>{MAX_TEMPLATE_LEN} 字符）",
                        "pos": 0, "length": 1, "line": 1, "col": 1,
                        "severity": "error"}],
            "warnings": [], "infos": [], "tokens": [],
        }

    parser = Parser(template)
    parts = parser.parse()
    errors = list(parser.errors)
    warnings: list[dict] = []
    infos: list[dict] = []

    # 变量检查
    used: set[str] = set()
    collect_used_vars(parts, used)
    provided = set(variables.keys())
    for v in sorted(used - provided):
        warnings.append({"message": f"变量 '{v}' 在模板中使用，但未提供样例值",
                         "severity": "warning"})
    for v in sorted(provided - used):
        infos.append({"message": f"样例变量 '{v}' 未在模板中使用",
                      "severity": "info"})

    # 主渲染（使用用户样例）
    ctx = RenderCtx(variables, locale)
    main_output = render_message(parts, ctx)
    errors.extend(ctx.errors)

    # 分支节点汇总
    nodes = []
    for node in walk_branch_nodes(parts):
        branches_info = []
        for b in node.branches:
            cond, _rep, reachable = branch_condition(node, b.selector, locale)
            covered = (node.node_id, b.selector) in ctx.taken
            branches_info.append({
                "selector": b.selector,
                "condition": cond,
                "covered": covered and not ctx.incomplete,
                "reachable": reachable,
            })
            if not reachable:
                warnings.append({
                    "message": f"变量 '{node.name}' 的分支 '{b.selector}' 在语言环境 "
                               f"'{locale}' 下不可达（被 =N 覆盖或该类别不存在）",
                    "severity": "warning",
                })
        nodes.append({
            "id": node.node_id,
            "kind": node.kind if isinstance(node, Plural) else "select",
            "variable": node.name,
            "pos": node.pos,
            "branches": branches_info,
        })

    # 枚举所有可达路径
    total, capped = count_paths(parts, {}, locale, cap=10000)
    truncated = total > MAX_PATHS
    paths = []
    for conds, assigned in iter_paths(parts, {}, locale, MAX_PATHS, prefer=variables):
        render_vars = dict(variables)
        render_vars.update(assigned)
        pctx = RenderCtx(render_vars, locale)
        output = render_message(parts, pctx)
        covered = (not ctx.incomplete) and pctx.taken == ctx.taken
        paths.append({
            "id": len(paths) + 1,
            "conditions": conds,
            "output": output,
            "covered": covered,
        })

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "infos": infos,
        "tokens": parser.tokens,
        "locale": locale,
        "variables_used": sorted(used),
        "main": {
            "output": main_output,
            "conditions": ctx.conditions,
            "incomplete": ctx.incomplete,
        },
        "nodes": nodes,
        "paths": paths,
        "stats": {
            "total_paths": total,
            "shown_paths": len(paths),
            "covered_paths": sum(1 for p in paths if p["covered"]),
            "truncated": truncated or capped,
            "max_paths": MAX_PATHS,
            "coverage_available": not ctx.incomplete,
        },
    }
