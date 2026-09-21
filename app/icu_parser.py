"""ICU MessageFormat (子集) 递归下降解析器。

支持:
  * 文本与单引号转义 (``'{'`` / ``''`` / ``"''"``)
  * 简单参数: var, number/date/time(带可选 style)
  * 复杂参数: plural / select / selectordinal, 可任意嵌套
  * plural 的 ``offset:N`` 与数字显式分支, 以及 ``#`` 占位符

与参考实现一致的故意差异: 不支持 ``choice`` (已废弃), 遇到即报错。
解析过程中产生的语法错误会带行列位置, 并尽量容错以便继续给出更多提示。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# AST
# ---------------------------------------------------------------------------


@dataclass
class Node:
    start: int = 0
    end: int = 0


@dataclass
class Text(Node):
    value: str = ""


@dataclass
class Hash(Node):
    """``#`` —— 仅在 plural/selectordinal 分支内合法。"""


@dataclass
class SimpleArg(Node):
    name: str = ""
    type: str = "variable"  # variable | number | date | time | spellout | duration
    style: Optional[str] = None


@dataclass
class Case(Node):
    key: str = ""          # 归一化后的键: "one" / "=3" / "female" / "other"
    raw_key: str = ""
    numeric: Optional[str] = None  # =3 -> "3"
    message: "Message" = field(default_factory=lambda: Message())
    # 该 case 键在源码中的位置 (用于点击定位)
    key_start: int = 0
    key_end: int = 0


@dataclass
class ComplexArg(Node):
    name: str = ""
    kind: str = ""         # plural | select | selectordinal
    offset: int = 0
    cases: list[Case] = field(default_factory=list)


@dataclass
class ErrorNode(Node):
    """无法解析但已跳过的片段, 渲染时原样输出包裹内容。"""

    raw: str = ""


@dataclass
class Message(Node):
    parts: list[Node] = field(default_factory=list)


@dataclass
class ParseError:
    line: int
    col: int
    offset: int
    end_offset: int
    message: str

    def to_dict(self) -> dict:
        return {
            "line": self.line,
            "col": self.col,
            "offset": self.offset,
            "end": self.end_offset,
            "message": self.message,
        }


SIMPLE_TYPES = {"number", "date", "time", "spellout", "duration"}
COMPLEX_TYPES = {"plural", "select", "selectordinal"}

# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class ICUParseError(Exception):
    """内部使用: 无法继续解析当前结构时抛出。"""


class Parser:
    def __init__(self, source: str):
        self.src = source
        self.n = len(source)
        self.pos = 0
        self.errors: list[ParseError] = []
        # 记录每个复数参数的 offset, 供 # 检查
        self._plural_stack: list[int] = []
        self._line_starts = self._build_line_starts()

    def _build_line_starts(self) -> list[int]:
        starts = [0]
        for i, ch in enumerate(self.src):
            if ch == "\n":
                starts.append(i + 1)
        return starts

    def linecol(self, offset: int) -> tuple[int, int]:
        # 二分查找所在行
        lo, hi = 0, len(self._line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._line_starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1, offset - self._line_starts[lo] + 1

    def err(self, message: str, offset: int, end: Optional[int] = None) -> None:
        end = end if end is not None else min(offset + 1, self.n)
        line, col = self.linecol(offset)
        self.errors.append(
            ParseError(line=line, col=col, offset=offset, end_offset=end, message=message)
        )

    # -- 基础工具 -----------------------------------------------------------

    def _skip_ws(self) -> None:
        while self.pos < self.n and self.src[self.pos] in " \t\r\n":
            self.pos += 1

    def _peek(self) -> str:
        return self.src[self.pos] if self.pos < self.n else ""

    def _eof(self) -> bool:
        return self.pos >= self.n

    def _read_identifier(self) -> str:
        """读取严格标识符: 字母/下划线开头, 后跟字母数字下划线点号短横线。"""
        if self._eof() or not (self.src[self.pos].isalpha() or self.src[self.pos] == "_"):
            return ""
        buf = []
        while not self._eof():
            ch = self.src[self.pos]
            if ch.isalnum() or ch in "._-":
                buf.append(ch)
                self.pos += 1
            else:
                break
        return "".join(buf)

    def _read_loose_word(self) -> str:
        """容错读取: 读到逗号或右花括号为止。"""
        start = self.pos
        depth = 0
        while not self._eof():
            ch = self.src[self.pos]
            if ch == "{":
                depth += 1
            elif ch == "}":
                if depth == 0:
                    break
                depth -= 1
            elif ch == "," and depth == 0:
                break
            self.pos += 1
        return self.src[start:self.pos].strip()

    def _skip_balanced(self) -> int:
        """从 ``{`` 之后开始, 跳到与之匹配的 ``}`` 之后, 返回该 ``}`` 的偏移。

        单引号按 ICU 规则视为转义。假设当前位置在开括号之后。
        """
        depth = 1
        while not self._eof():
            ch = self.src[self.pos]
            if ch == "'":
                self._consume_quoted()
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    close = self.pos
                    self.pos += 1
                    return close
            self.pos += 1
        raise ICUParseError("unclosed")

    def _consume_quoted(self) -> None:
        """当前位置在起始单引号, 消费到引号结束。"""
        assert self.src[self.pos] == "'"
        self.pos += 1
        doubled = False
        while not self._eof():
            ch = self.src[self.pos]
            if ch == "'":
                if self.pos + 1 < self.n and self.src[self.pos + 1] == "'":
                    self.pos += 2
                    doubled = True
                    continue
                self.pos += 1
                return
            self.pos += 1
        # 未闭合单引号: ICU 视为到文本末尾, 容错不报错 (部分实现如此)
        if doubled:
            return

    # -- 入口 ---------------------------------------------------------------

    def parse(self) -> tuple[Message, list[ParseError]]:
        start = self.pos
        msg = self._parse_message(stop_at_brace=False)
        msg.start, msg.end = start, self.pos
        return msg, self.errors

    # -- 消息体 -------------------------------------------------------------

    def _parse_message(self, stop_at_brace: bool) -> Message:
        msg = Message(start=self.pos)
        text: list[str] = []
        text_start = self.pos

        def flush_text():
            if text:
                msg.parts.append(
                    Text(start=text_start, end=self.pos, value="".join(text))
                )
                text.clear()

        while not self._eof():
            ch = self.src[self.pos]
            if stop_at_brace and ch == "}":
                break
            if ch == "{":
                flush_text()
                msg.parts.append(self._parse_argument())
                text_start = self.pos
                continue
            if ch == "#" and self._plural_stack:
                flush_text()
                msg.parts.append(Hash(start=self.pos, end=self.pos + 1))
                self.pos += 1
                text_start = self.pos
                continue
            if ch == "#":
                flush_text()
                self.err(
                    "“#” 只能出现在 plural/selectordinal 的分支内",
                    self.pos,
                )
                msg.parts.append(Text(start=self.pos, end=self.pos + 1, value="#"))
                self.pos += 1
                text_start = self.pos
                continue
            if ch == "'":
                # 转义文本
                qstart = self.pos
                raw_start = self.pos
                self.pos += 1
                buf: list[str] = []
                closed = False
                while not self._eof():
                    c = self.src[self.pos]
                    if c == "'":
                        if self.pos + 1 < self.n and self.src[self.pos + 1] == "'":
                            buf.append("'")
                            self.pos += 2
                            continue
                        self.pos += 1
                        closed = True
                        break
                    buf.append(c)
                    self.pos += 1
                if not closed:
                    self.err("单引号未闭合, 应使用 '' 转义或补充结束引号", raw_start)
                if not text:
                    text_start = qstart
                text.extend(buf)
                continue
            if ch == "}" and not stop_at_brace:
                # 多余的右括号
                flush_text()
                self.err("存在多余的右花括号 “}”", self.pos)
                msg.parts.append(Text(start=self.pos, end=self.pos + 1, value="}"))
                self.pos += 1
                text_start = self.pos
                continue
            text.append(ch)
            self.pos += 1

        flush_text()
        msg.end = self.pos
        return msg

    # -- 参数 ---------------------------------------------------------------

    def _parse_argument(self) -> Node:
        assert self.src[self.pos] == "{"
        start = self.pos
        self.pos += 1  # {
        self._skip_ws()

        name = self._read_identifier()
        if not name:
            self.err("参数名缺失或非法(应以字母或下划线开头)", self.pos)
            return self._recover_argument(start)

        self._skip_ws()
        if self._eof():
            self.err("参数未闭合: 缺少对应的 “}”", start, self.n)
            return ErrorNode(start=start, end=self.n, raw=self.src[start:])

        if self._peek() == "}":
            self.pos += 1
            return SimpleArg(start=start, end=self.pos, name=name)

        if self._peek() != ",":
            bad = self.pos
            word = self._read_loose_word()
            self.err(
                f"参数 “{name}” 语法错误: 期望 “,” 或 “}}”, 实际得到 “{word[:20]}”",
                bad,
                bad + max(len(word), 1),
            )
            return self._recover_argument(start)

        self.pos += 1  # ,
        self._skip_ws()
        type_start = self.pos
        type_word = self._read_loose_word()
        type_name = type_word.strip()
        type_end = self.pos

        if not type_name:
            self.err(f"参数 “{name}” 缺少类型(number/date/time/plural/select/selectordinal)", type_start)
            return self._recover_argument(start)

        if type_name in COMPLEX_TYPES:
            return self._parse_complex_body(start, name, type_name, type_start, type_end)

        if type_name in SIMPLE_TYPES:
            return self._parse_simple_body(start, name, type_name, type_start)

        if type_name == "choice":
            self.err(
                "不支持已废弃的 choice 参数, 请改用 plural/select",
                type_start,
                self.pos,
            )
            return self._recover_argument(start)

        self.err(
            f"未知的参数类型 “{type_name}”(支持 number/date/time/plural/select/selectordinal)",
            type_start,
            self.pos,
        )
        return self._recover_argument(start)

    def _recover_argument(self, start: int) -> Node:
        """跳过当前括号结构, 生成 ErrorNode。"""
        # pos 在 { 之后某处; 找到匹配右括号
        try:
            close = self._skip_balanced()
            return ErrorNode(start=start, end=close + 1, raw=self.src[start:close + 1])
        except ICUParseError:
            return ErrorNode(start=start, end=self.n, raw=self.src[start:])

    def _parse_simple_body(self, start: int, name: str, type_name: str, type_start: int) -> Node:
        # 可能形如 {n, number, :: percent} —— 读到匹配右括号
        depth = 0
        body_start = self.pos
        end_pos = self.n
        while not self._eof():
            ch = self.src[self.pos]
            if ch == "'":
                self._consume_quoted()
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                if depth == 0:
                    end_pos = self.pos
                    self.pos += 1
                    break
                depth -= 1
            self.pos += 1
        else:
            self.err(f"参数 “{name}” 未闭合: 缺少对应的 “}}”", start, self.n)

        body = self.src[body_start:end_pos].strip()
        style: Optional[str] = None
        if body:
            if not body.startswith(","):
                self.err(
                    f"{type_name} 参数格式应为 “{{{{{name}, {type_name}[, style]}}}}”",
                    body_start,
                    end_pos,
                )
            else:
                style = body[1:].strip()
                if not style:
                    self.err(f"参数 “{name}” 的 style 为空", body_start, end_pos)
        return SimpleArg(start=start, end=self.pos, name=name, type=type_name, style=style)

    def _parse_complex_body(self, start: int, name: str, kind: str,
                            type_start: int, type_end: int) -> Node:
        # type word 之后紧跟分隔逗号 (容错允许缺省)
        scan_pos = type_end
        if scan_pos < self.n and self.src[scan_pos] == ",":
            scan_pos += 1
        # 找到匹配右括号, 在其中解析 (便于统一报错)
        saved_pos = self.pos
        self.pos = scan_pos
        try:
            close = self._find_matching_close()
        except ICUParseError:
            # 未闭合: 剩余内容全部属于该参数, 直接到 EOF, 避免重复/误报
            self.pos = self.n
            self.err(f"{kind} 参数 “{name}” 未闭合: 缺少对应的 “}}”", start, self.n)
            return ErrorNode(start=start, end=self.n, raw=self.src[start:])

        # close 是匹配 } 的偏移; 在区间内解析
        saved_end = close  # 独占
        self.pos = scan_pos

        arg = ComplexArg(start=start, end=close + 1, name=name, kind=kind)
        self._skip_ws_in_range(saved_end)

        offset = 0
        seen_keys: set[str] = set()

        if kind in ("plural", "selectordinal"):
            # offset:N (offset 后允许空白再跟冒号)
            if self._match_word("offset", saved_end):
                saved_pos = self.pos
                self.pos += len("offset")
                self._skip_ws_in_range(saved_end)
                if self.pos < saved_end and self.src[self.pos] == ":":
                    self.pos += 1
                    self._skip_ws_in_range(saved_end)
                    num_start = self.pos
                    num = self._read_integer(saved_end)
                    if num is None:
                        self.err("offset 后必须是非负整数, 例如 offset:1", num_start, self.pos)
                    else:
                        offset = num
                else:
                    # 不是 offset:, 还原
                    self.pos = saved_pos
            self._skip_ws_in_range(saved_end)

        arg.offset = offset
        self._plural_stack.append(offset)
        try:
            while self.pos < saved_end:
                self._skip_ws_in_range(saved_end)
                if self.pos >= saved_end:
                    break
                case = self._parse_case(kind, saved_end)
                if case is None:
                    break
                if case.key in seen_keys:
                    self.err(
                        f"{kind} 参数 “{name}” 中分支 “{case.raw_key}” 重复定义",
                        case.key_start,
                        case.key_end,
                    )
                seen_keys.add(case.key)
                arg.cases.append(case)
        finally:
            self._plural_stack.pop()

        if "other" not in seen_keys:
            self.err(
                f"{kind} 参数 “{name}” 缺少必需的 “other” 兜底分支",
                start,
                min(start + 1, self.n),
            )
        self.pos = close + 1
        return arg

    def _find_matching_close(self) -> int:
        """当前位置在复杂类型关键字之后的分隔逗号处, 找到与参数起始 ``{``
        匹配的 ``}``。此时尚未消费任何花括号, 因此深度从 0 开始。"""
        depth = 0
        p = self.pos
        while p < self.n:
            ch = self.src[p]
            if ch == "'":
                # 跳过引号
                p += 1
                while p < self.n:
                    if self.src[p] == "'":
                        if p + 1 < self.n and self.src[p + 1] == "'":
                            p += 2
                            continue
                        p += 1
                        break
                    p += 1
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                if depth == 0:
                    return p
                depth -= 1
            p += 1
        raise ICUParseError("unclosed")

    def _skip_ws_in_range(self, end: int) -> None:
        while self.pos < end and self.src[self.pos] in " \t\r\n":
            self.pos += 1

    def _match_word(self, word: str, end: int) -> bool:
        if self.src.startswith(word, self.pos):
            after = self.pos + len(word)
            if after >= end or not (self.src[after].isalnum() or self.src[after] == "_"):
                return True
        return False

    def _read_integer(self, end: int) -> Optional[int]:
        p = self.pos
        digits = []
        while p < end and self.src[p].isdigit():
            digits.append(self.src[p])
            p += 1
        if not digits:
            return None
        self.pos = p
        return int("".join(digits))

    def _parse_case(self, kind: str, end: int) -> Optional[Case]:
        key_start = self.pos
        if kind == "select":
            # 键: 标识符, 或单引号包裹的任意文本
            if self.src[self.pos] == "'":
                qs = self.pos
                self.pos += 1
                buf: list[str] = []
                closed = False
                while self.pos < end:
                    c = self.src[self.pos]
                    if c == "'":
                        if self.pos + 1 < end and self.src[self.pos + 1] == "'":
                            buf.append("'")
                            self.pos += 2
                            continue
                        self.pos += 1
                        closed = True
                        break
                    buf.append(c)
                    self.pos += 1
                if not closed:
                    self.err("select 的分支键引号未闭合", qs, self.pos)
                key = "".join(buf)
                raw_key = self.src[qs:self.pos]
            else:
                key = self._read_identifier()
                if not key:
                    bad = self.pos
                    junk = self._read_loose_word()
                    self.err(
                        f"select 分支键非法(标识符或 '引号文本'), 得到 “{junk[:20]}”",
                        bad,
                        self.pos,
                    )
                    return None
                raw_key = key
        else:
            # plural: =数字 或 复数关键字
            if self.src[self.pos] == "=":
                self.pos += 1
                num_start = self.pos
                # ICU: =0, =1, =2 整数; 容忍小数
                num = self._read_number_token(end)
                if num is None:
                    self.err("“=” 后必须是数字, 例如 =0 或 =1", num_start, self.pos)
                    return None
                key = f"={num}"
                raw_key = key
            else:
                word_start = self.pos
                word = self._read_identifier()
                if not word:
                    bad = self.pos
                    junk = self._read_loose_word()
                    self.err(
                        f"plural 分支键非法(=数字/one/two/few/many/other/zero/two), 得到 “{junk[:20]}”",
                        bad,
                        self.pos,
                    )
                    return None
                raw_key = word
                key = word
                # 校验复数关键字是否属于该 locale 的合法类别放在渲染阶段; 这里只做基本校验
                if key not in ("zero", "one", "two", "few", "many", "other"):
                    self.err(
                        f"未知的复数类别 “{key}”, 合法值为 zero/one/two/few/many/other",
                        word_start,
                        self.pos,
                    )

        key_end = self.pos
        self._skip_ws_in_range(end)
        # 期望 {
        if self.pos >= end or self.src[self.pos] != "{":
            self.err(
                f"分支 “{raw_key}” 后缺少 “{{” 开始分支内容",
                key_start,
                self.pos,
            )
            return None
        self.pos += 1  # 分支起始 {
        body_start = self.pos
        # 在父参数区间 [body_start, end) 内找到与起始 { 匹配的 }
        body_end = self._scan_case_body_end(body_start, end)
        if body_end is None:
            self.err(
                f"分支 “{raw_key}” 的内容缺少结束 “}}”",
                key_start,
                min(end, self.n),
            )
            return None
        self.pos = body_start
        msg = self._parse_message_range(stop_offset=body_end)
        msg.start, msg.end = body_start, body_end
        self.pos = body_end + 1  # 消费分支结束 }

        numeric = key[1:] if key.startswith("=") else None
        return Case(
            start=key_start,
            end=self.pos,
            key=key,
            raw_key=raw_key,
            numeric=numeric,
            message=msg,
            key_start=key_start,
            key_end=key_end,
        )

    def _scan_case_body_end(self, start: int, limit: int) -> Optional[int]:
        """从分支体起始位置扫描匹配的右花括号, 不越过父参数结束 ``limit``。"""
        depth = 1
        p = start
        while p < limit:
            ch = self.src[p]
            if ch == "'":
                # 跳过引号内容
                p += 1
                while p < limit:
                    if self.src[p] == "'":
                        if p + 1 < limit and self.src[p + 1] == "'":
                            p += 2
                            continue
                        p += 1
                        break
                    p += 1
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return p
            p += 1
        return None

    def _parse_message_range(self, stop_offset: int) -> Message:
        """只在 [self.pos, stop_offset) 区间内解析消息体。"""
        saved_n = self.n
        self.n = stop_offset
        try:
            return self._parse_message(stop_at_brace=False)
        finally:
            self.n = saved_n

    def _read_number_token(self, end: int) -> Optional[str]:
        p = self.pos
        buf = []
        if p < end and self.src[p] in "+-":
            buf.append(self.src[p])
            p += 1
        seen_digit = False
        while p < end and (self.src[p].isdigit() or self.src[p] == "."):
            if self.src[p].isdigit():
                seen_digit = True
            buf.append(self.src[p])
            p += 1
        if not seen_digit:
            return None
        token = "".join(buf)
        # 多个小数点非法
        if token.count(".") > 1:
            return None
        self.pos = p
        return token


# 便捷入口
def parse_icu(source: str) -> tuple[Message, list[ParseError]]:
    return Parser(source).parse()
