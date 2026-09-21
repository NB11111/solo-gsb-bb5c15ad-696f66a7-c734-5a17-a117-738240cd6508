"""根据变量样例与 locale 渲染 ICU AST, 并枚举所有可达分支。"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from babel.core import Locale, UnknownLocaleError
from babel.dates import format_date, format_time
from babel.numbers import (
    format_compact_decimal,
    format_currency,
    format_decimal,
    format_percent,
)

from .icu_parser import (
    Case,
    ComplexArg,
    ErrorNode,
    Hash,
    Message,
    Node,
    ParseError,
    SimpleArg,
    Text,
)

PLURAL_KEYWORDS = ("zero", "one", "two", "few", "many", "other")


@dataclass
class Condition:
    """一条分支条件, 如 count 是复数类别 one (=1)。"""

    var: str
    kind: str            # plural | selectordinal | select
    matcher: str         # category | exact
    value: str           # one / female / =3
    label: str           # 展示文本
    key_start: int
    key_end: int
    arg_uid: tuple = ()
    case_uid: tuple = ()

    def signature(self) -> tuple:
        # 带节点 id, 避免同名复数参数或重复键造成串分支
        return (self.arg_uid, self.case_uid)


@dataclass
class Branch:
    id: str
    depth: int
    conditions: list[Condition]
    text: str
    covered: bool
    key_start: int
    key_end: int
    arg_start: int
    arg_end: int


@dataclass
class VariableInfo:
    name: str
    type: str            # number | date | time | datetime | string
    used_in: str         # "plural" / "select" / ...
    sample: str
    line: int = 0
    col: int = 0
    offset: int = 0


@dataclass
class RenderResult:
    text: str = ""
    branches: list[Branch] = field(default_factory=list)
    variables: list[VariableInfo] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    locale: str = ""
    resolved_locale: str = ""
    active_path: list[tuple] = field(default_factory=list)


class Renderer:
    def __init__(self, root: Message, parse_errors: list[ParseError],
                 locale: str, samples: dict[str, str], source: str = ""):
        self.root = root
        self.source = source
        self.locale_input = (locale or "en").strip()
        self.samples = {k: v for k, v in samples.items()}
        self.errors: list[dict] = []
        self.warnings: list[dict] = []
        self.variables: dict[str, VariableInfo] = {}
        self.branches: list[Branch] = []
        self._branch_seq = 0
        self._plural_offset_stack: list[tuple[str, int]] = []
        # 枚举时已选定的外层分支, 供嵌套分支预览继承
        self._preview_context: dict[int, str] = {}
        self._arg_by_id: dict[int, ComplexArg] = {}
        self._index_args(root)

        # locale 解析
        try:
            self.locale = Locale.parse(self.locale_input.replace("-", "_"))
            self.resolved_locale = str(self.locale)
        except (UnknownLocaleError, ValueError):
            self.locale = Locale.parse("en")
            self.resolved_locale = "en"
            self.errors.append({
                "line": 1, "col": 1, "offset": 0, "end": 0,
                "message": f"无法识别的语言环境 “{self.locale_input}”, 已回退为 en",
            })

        self.plural_fn = self.locale._data["plural_form"]
        self.ordinal_fn = self.locale._data.get("ordinal_form") or self.plural_fn

        for e in parse_errors:
            self.errors.append(e.to_dict())

    # -- 对外入口 -----------------------------------------------------------

    def render(self) -> RenderResult:
        # 1. 收集变量信息
        self._collect_variables(self.root)

        # 2. 选当前样例对应的路径
        active_path: list[tuple] = []
        values: dict[str, Any] = {}
        ok = self._resolve_active_path(self.root, values, active_path)

        # 3. 枚举全部叶子分支
        self._enumerate(self.root, [])

        # 4. 标记覆盖
        active_sigs = {(p[4], p[5]) for p in active_path}
        for b in self.branches:
            b.covered = all(
                (c.arg_uid[0], c.case_uid[0]) in active_sigs
                for c in b.conditions
            )

        # 4.5 检查 locale 下不可达的复数类别分支
        self._check_unreachable_categories(self.root)

        # 5. 按当前样例渲染 (即使路径上有变量错误也尽力输出, 问题变量显示占位)
        text = self._render_message(self.root, values)

        # 6. 未使用的样例变量
        used = {v.name for v in self.variables.values()}
        for name, value in self.samples.items():
            if name not in used and value.strip() != "":
                self.warnings.append({
                    "line": 0, "col": 0, "offset": 0, "end": 0,
                    "message": f"变量 “{name}” 提供了样例, 但模板中没有使用",
                })

        return RenderResult(
            text=text,
            branches=self.branches,
            variables=list(self.variables.values()),
            errors=self.errors,
            warnings=self.warnings,
            locale=self.locale_input,
            resolved_locale=self.resolved_locale,
            active_path=active_path,
        )

    # -- 变量收集 -----------------------------------------------------------

    def _index_args(self, node: Node) -> None:
        if isinstance(node, ComplexArg):
            self._arg_by_id[id(node)] = node
            for c in node.cases:
                self._index_args(c.message)
        elif isinstance(node, Message):
            for p in node.parts:
                self._index_args(p)

    def _check_unreachable_categories(self, node: Node) -> None:
        """标记当前 locale 的复数规则下永不命中的类别分支。"""
        if isinstance(node, ComplexArg):
            if node.kind in ("plural", "selectordinal"):
                fn = self.ordinal_fn if node.kind == "selectordinal" else self.plural_fn
                reachable = self._reachable_categories(fn)
                for case in node.cases:
                    if case.numeric is None and case.key != "other" \
                            and case.key not in reachable:
                        line, col = self._linecol(case.key_start)
                        self.warnings.append({
                            "line": line, "col": col,
                            "offset": case.key_start,
                            "end": case.key_end,
                            "message": (
                                f"{node.kind} 分支 “{case.key}” 在 locale "
                                f"{self.resolved_locale} 下永远不会命中"
                                f"(该语言只有 {sorted(reachable)} 类别)"),
                        })
            for c in node.cases:
                self._check_unreachable_categories(c.message)
        elif isinstance(node, Message):
            for p in node.parts:
                self._check_unreachable_categories(p)

    @staticmethod
    def _reachable_categories(fn) -> set[str]:
        from decimal import Decimal
        cats = set()
        for n in range(0, 301):
            cats.add(fn(n))
        # 再尝试常见小数值 (覆盖 v != 0 时才出现 other 等规则)
        for i in range(1, 200):
            cats.add(fn(Decimal(i) / Decimal(10)))
        return cats

    def _linecol(self, offset: int) -> tuple[int, int]:
        line, col = 1, 1
        for i, ch in enumerate(self.source):
            if i >= offset:
                break
            if ch == "\n":
                line += 1
                col = 1
            else:
                col += 1
        return line, col

    def _collect_variables(self, node: Node) -> None:
        if isinstance(node, SimpleArg):
            self._register_variable(node)
        elif isinstance(node, ComplexArg):
            info = self.variables.get(node.name)
            vtype = "number" if node.kind in ("plural", "selectordinal") else "string"
            used_in = node.kind
            if info is None:
                self.variables[node.name] = VariableInfo(
                    name=node.name, type=vtype, used_in=used_in,
                    sample=self.samples.get(node.name, ""),
                    offset=node.start,
                )
            else:
                # 后写类型更具体的
                if info.type == "string" and vtype == "number":
                    info.type = "number"
            for c in node.cases:
                self._collect_variables(c.message)
        elif isinstance(node, Message):
            for p in node.parts:
                self._collect_variables(p)

    def _register_variable(self, node: SimpleArg) -> None:
        tmap = {"number": "number", "date": "date", "time": "time",
                "variable": "string", "spellout": "number", "duration": "number"}
        vtype = tmap.get(node.type, "string")
        info = self.variables.get(node.name)
        if info is None:
            self.variables[node.name] = VariableInfo(
                name=node.name, type=vtype, used_in=node.type,
                sample=self.samples.get(node.name, ""),
                offset=node.start,
            )
        elif info.type == "string" and vtype != "string":
            info.type = vtype

    # -- 当前样例路径 -------------------------------------------------------

    def _resolve_active_path(self, node: Node, values: dict[str, Any],
                             path: list[tuple]) -> bool:
        if isinstance(node, Message):
            ok = True
            for p in node.parts:
                if not self._resolve_active_path(p, values, path):
                    ok = False
            return ok
        if isinstance(node, SimpleArg):
            self._prepare_value(node.name, node)
            return True
        if isinstance(node, ComplexArg):
            if node.kind == "select":
                val = self._prepare_value(node.name)
                chosen = self._match_select(node, str(val) if val is not None else "")
            else:
                num = self._prepare_number(node.name)
                if num is None:
                    return False
                chosen = self._match_plural(node, num)
            if chosen is None:
                # 理论上 other 兜底, 容错
                self._error_at(node.start, node.end,
                               f"{node.kind} 参数 “{node.name}” 没有可匹配的分支")
                return False
            cond = self._condition_for(node, chosen, num=(
                num if node.kind != "select" else None))
            path.append((node.name, node.kind, cond.matcher, cond.value,
                         id(node), id(chosen)))
            values.setdefault("__chosen__", {})[id(node)] = chosen.key
            return self._resolve_active_path(chosen.message, values, path)
        return True

    def _condition_for(self, arg: ComplexArg, case: Case,
                       num: Optional[Decimal]) -> Condition:
        common = dict(key_start=case.key_start, key_end=case.key_end,
                      arg_uid=(id(arg),), case_uid=(id(case),))
        if arg.kind == "select":
            return Condition(
                var=arg.name, kind="select", matcher="exact",
                value=case.key, label=f"{arg.name} = {case.key}",
                **common,
            )
        if case.numeric is not None:
            return Condition(
                var=arg.name, kind=arg.kind, matcher="exact",
                value="=" + case.numeric,
                label=f"{arg.name} = {case.numeric}",
                **common,
            )
        cat = case.key
        if num is not None:
            label = f"{arg.name} → {cat} (例: {self._representative(cat, arg.kind)})"
        else:
            label = f"{arg.name} → {cat}"
        return Condition(
            var=arg.name, kind=arg.kind, matcher="category",
            value=cat, label=label, **common,
        )

    # -- 分支枚举 -----------------------------------------------------------

    def _enumerate(self, node: Node, conditions: list[Condition]) -> None:
        if isinstance(node, Message):
            for p in node.parts:
                self._enumerate(p, list(conditions))
            return
        if isinstance(node, SimpleArg):
            return
        if isinstance(node, ComplexArg):
            for case in node.cases:
                cond = self._condition_for(node, case, None)
                self._branch_seq += 1
                bid = f"b{self._branch_seq}"
                self._preview_context[id(node)] = case
                preview = self._render_message_preview(case.message)
                self.branches.append(Branch(
                    id=bid,
                    depth=len(conditions),
                    conditions=conditions + [cond],
                    text=preview,
                    covered=False,
                    key_start=case.key_start,
                    key_end=case.key_end,
                    arg_start=node.start,
                    arg_end=node.end,
                ))
                self._enumerate(case.message, conditions + [cond])
            self._preview_context.pop(id(node), None)

    def _render_message_preview(self, msg: Message) -> str:
        parts: list[str] = []
        for p in msg.parts:
            parts.append(self._preview_part(p))
        return "".join(parts).strip()

    def _preview_part(self, node: Node) -> str:
        if isinstance(node, Text):
            return node.value
        if isinstance(node, Hash):
            # 绑定到最近的复数参数 (ICU 语义), 输出 变量值 - offset
            for arg_id, case in reversed(list(self._preview_context.items())):
                arg = self._arg_by_id.get(arg_id)
                if arg is not None and arg.kind in ("plural", "selectordinal"):
                    num = self._to_decimal(self.samples.get(arg.name, ""))
                    if num is None:
                        return "#"
                    return format_decimal(num - arg.offset, locale=self.locale)
            return "#"
        if isinstance(node, ErrorNode):
            return node.raw
        if isinstance(node, SimpleArg):
            sample = self.samples.get(node.name, "")
            return sample if sample else "{" + node.name + "}"
        if isinstance(node, ComplexArg):
            # 优先用枚举上下文; 否则按样例匹配; 再退回 other
            case = self._preview_context.get(id(node))
            if case is None and node.kind == "select":
                case = self._match_select(node, self.samples.get(node.name, ""))
            elif case is None:
                num = self._to_decimal(self.samples.get(node.name, ""))
                if num is not None:
                    case = self._match_plural(node, num)
            if case is None:
                case = self._find_case(node, "other") or (
                    node.cases[0] if node.cases else None)
            if case is None:
                return ""
            prev = self._preview_context.get(id(node))
            self._preview_context[id(node)] = case
            try:
                return self._render_message_preview(case.message)
            finally:
                if prev is None:
                    self._preview_context.pop(id(node), None)
                else:
                    self._preview_context[id(node)] = prev
        return ""

    # -- 最终渲染 -----------------------------------------------------------

    def _render_message(self, msg: Message, values: dict[str, Any]) -> str:
        out: list[str] = []
        for p in msg.parts:
            out.append(self._render_part(p, values))
        return "".join(out)

    def _render_part(self, node: Node, values: dict[str, Any]) -> str:
        if isinstance(node, Text):
            return node.value
        if isinstance(node, ErrorNode):
            return node.raw
        if isinstance(node, Hash):
            if self._plural_offset_stack:
                varname, offset = self._plural_offset_stack[-1]
            else:
                varname, offset = "", 0
            raw = self.samples.get(varname, "0") if varname else "0"
            num = self._to_decimal(raw) or Decimal(0)
            return format_decimal(num - offset, locale=self.locale)
        if isinstance(node, SimpleArg):
            return self._format_simple(node)
        if isinstance(node, ComplexArg):
            chosen_key = values.get("__chosen__", {}).get(id(node))
            case = next((c for c in node.cases if c.key == chosen_key), None)
            if case is None:
                # 兜底
                case = self._find_case(node, "other") or (
                    node.cases[0] if node.cases else None)
            if case is None:
                return ""
            if node.kind in ("plural", "selectordinal"):
                self._plural_offset_stack.append((node.name, node.offset))
                try:
                    return self._render_message(case.message, values)
                finally:
                    self._plural_offset_stack.pop()
            return self._render_message(case.message, values)
        return ""

    # -- 格式化简单参数 -----------------------------------------------------

    def _format_simple(self, node: SimpleArg) -> str:
        raw = self.samples.get(node.name, "")
        if raw == "":
            self._error_at(node.start, node.end,
                           f"缺少变量 “{node.name}” 的样例值")
            return f"⚠{node.name}⚠"

        if node.type == "variable":
            return raw
        if node.type in ("number", "spellout", "duration"):
            num = self._to_decimal(raw)
            if num is None:
                self._error_at(node.start, node.end,
                               f"变量 “{node.name}” 需要数字, 得到 “{raw}”")
                return f"⚠{raw}⚠"
            try:
                return self._format_number(num, node)
            except Exception as exc:  # 样式本身非法
                self._error_at(node.start, node.end,
                               f"number 样式 “{node.style}” 无效: {exc}")
                return str(num)
        if node.type == "date":
            dt = self._parse_date(raw)
            if dt is None:
                self._error_at(node.start, node.end,
                               f"变量 “{node.name}” 需要日期 (YYYY-MM-DD), 得到 “{raw}”")
                return f"⚠{raw}⚠"
            try:
                return format_date(dt, format=node.style or "medium",
                                   locale=self.locale)
            except Exception:
                return format_date(dt, locale=self.locale)
        if node.type == "time":
            t = self._parse_time(raw)
            if t is None:
                self._error_at(node.start, node.end,
                               f"变量 “{node.name}” 需要时间 (HH:MM), 得到 “{raw}”")
                return f"⚠{raw}⚠"
            try:
                return format_time(t, format=node.style or "medium",
                                   locale=self.locale)
            except Exception:
                return format_time(t, locale=self.locale)
        return raw

    def _format_number(self, num: Decimal, node: SimpleArg) -> str:
        style = (node.style or "").strip()
        if node.type in ("spellout", "duration"):
            return format_decimal(num, locale=self.locale)
        if not style:
            return format_decimal(num, locale=self.locale)

        named = {
            "integer": ("decimal", {"format": "0"}),
            "percent": ("percent", {}),
            "currency": ("currency", {}),
        }
        if style == "integer":
            return format_decimal(num, format="0", locale=self.locale)
        if style == "percent":
            return format_percent(num, locale=self.locale)
        if style == "scientific":
            return format_decimal(num, format="0.00E0", locale=self.locale)
        if style in ("short", "long"):
            try:
                return format_compact_decimal(
                    num, format_type=style, locale=self.locale,
                    fraction_digits=0 if num == num.to_integral_value() else 2)
            except Exception:
                return format_decimal(num, locale=self.locale)
        if len(style) == 3 and style.isalpha():
            # 视为货币代码
            try:
                return format_currency(num, style.upper(), locale=self.locale)
            except Exception as exc:
                raise ValueError(str(exc))
        # 其余视为数字模式 (如 0.00 / #,##0.00)
        if any(ch in style for ch in "0#.,%;"):
            return format_decimal(num, format=style, locale=self.locale)
        raise ValueError(f"未知 number 样式 “{style}”")

    # -- 值准备与类型转换 ---------------------------------------------------

    def _prepare_value(self, name: str, node: Optional[Node] = None) -> Optional[str]:
        if name not in self.samples or self.samples[name] == "":
            start = node.start if node else 0
            end = node.end if node else 0
            self._error_at(start, end, f"缺少变量 “{name}” 的样例值")
            return None
        return self.samples[name]

    def _prepare_number(self, name: str, node: Optional[ComplexArg] = None) -> Optional[Decimal]:
        raw = self.samples.get(name, "")
        num = self._to_decimal(raw)
        if num is None:
            if node is not None:
                self._error_at(node.start, node.end,
                               f"{node.kind} 变量 “{name}” 需要数字, 当前样例为 “{raw or '（空）'}”")
            elif raw == "":
                self._error_at(0, 0, f"缺少变量 “{name}” 的样例值")
            else:
                self._error_at(0, 0, f"变量 “{name}” 需要数字, 得到 “{raw}”")
        return num

    @staticmethod
    def _to_decimal(raw: Any) -> Optional[Decimal]:
        if raw is None:
            return None
        s = str(raw).strip().replace(",", "")
        if s == "":
            return None
        try:
            return Decimal(s)
        except InvalidOperation:
            return None

    @staticmethod
    def _parse_date(raw: str):
        from datetime import date, datetime
        s = raw.strip()
        # YYYY-M-D 单位数补零容错
        pieces = s.split("-")
        if len(pieces) == 3 and all(p.isdigit() for p in pieces):
            try:
                y, m, d = (int(p) for p in pieces)
                return date(y, m, d)
            except ValueError:
                return None
        for parser in (
            lambda: date.fromisoformat(s),
            lambda: datetime.fromisoformat(s),
        ):
            try:
                return parser()
            except ValueError:
                continue
        return None

    @staticmethod
    def _parse_time(raw: str):
        from datetime import datetime, time
        s = raw.strip()
        # 接受 H[:MM[:SS]] 单位数补零, 以及 ISO 格式
        parts = s.split(":")
        if 1 <= len(parts) <= 3 and all(p.strip().isdigit() for p in parts):
            try:
                nums = [int(p) for p in parts]
                nums += [0] * (3 - len(nums))
                return time(nums[0], nums[1], nums[2])
            except ValueError:
                return None
        try:
            return time.fromisoformat(s)
        except ValueError:
            pass
        try:
            return datetime.fromisoformat("2000-01-01T" + s).time()
        except ValueError:
            return None

    # -- 匹配 ---------------------------------------------------------------

    def _find_case(self, arg: ComplexArg, key: str) -> Optional[Case]:
        return next((c for c in arg.cases if c.key == key), None)

    def _match_select(self, arg: ComplexArg, value: str) -> Optional[Case]:
        for c in arg.cases:
            if c.key == value:
                return c
        return self._find_case(arg, "other")

    def _match_plural(self, arg: ComplexArg, value: Decimal) -> Optional[Case]:
        adjusted = value - arg.offset
        # 1. 精确数字分支
        int_val = int(value) if value == value.to_integral_value() else None
        if int_val is not None:
            exact = self._find_case(arg, "=" + str(int_val))
            if exact is not None:
                return exact
        # 2. 复数类别 (基于 offset 后的值, 与 ICU 一致)
        fn = self.ordinal_fn if arg.kind == "selectordinal" else self.plural_fn
        category = fn(adjusted)
        # 3. 该类别分支
        hit = self._find_case(arg, category)
        if hit is not None:
            return hit
        # 4. other 兜底
        return self._find_case(arg, "other")

    def _representative(self, category: str, kind: str) -> str:
        """找一个落到指定复数类别的非负小整数作为说明。"""
        fn = self.ordinal_fn if kind == "selectordinal" else self.plural_fn
        for n in range(0, 201):
            if fn(n) == category:
                return str(n)
        return category

    # -- 错误 ---------------------------------------------------------------

    def _error_at(self, start: int, end: int, message: str) -> None:
        line, col = self._linecol(start)
        self.errors.append({
            "line": line, "col": col, "offset": start, "end": max(end, start + 1),
            "message": message,
        })


def render_icu(root: Message, parse_errors: list[ParseError], source: str,
               locale: str, samples: dict[str, str]) -> RenderResult:
    r = Renderer(root, parse_errors, locale, samples, source=source)
    return r.render()
