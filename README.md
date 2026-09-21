# ICU 文案分支预览器

一个基于 **Python + Flask** 的单页 Web 工具：粘贴 [ICU MessageFormat](https://unicode-org.github.io/icu/userguide/format_parse/messages/) 模板、填写变量样例，自动解析嵌套的 `plural` / `select` / `selectordinal` 语法，按所选语言环境（locale）渲染文案，并：

- 🌳 列出**所有可达分支**及其触发条件（支持任意深度嵌套，条件以「且」串联）
- ✅ 用当前变量样例渲染最终结果，并标记每个分支**已覆盖 / 未覆盖**
- ⚠️ 对括号不匹配、参数类型错误、变量缺失 / 类型不符等问题给出**带行列号的行内提示**，点击即可跳转到模板对应位置
- 🔢 复数规则与数字 / 日期 / 时间格式化由 [Babel](https://babel.pocoo.org/)（CLDR 数据）驱动，支持 `zh / en / ru / ar / fr / de / ja …` 等数百个 locale
- 📋 一键**复制渲染结果**、**导出 JSON**（含所有分支、覆盖情况与错误清单）
- 🐳 提供 Dockerfile 与 docker-compose，一键启动

## 支持的 ICU 语法

| 语法 | 示例 |
| --- | --- |
| 简单变量 | `{name}` |
| 数字 / 日期 / 时间 | `{n, number}`、`{n, number, percent}`、`{d, date, long}`、`{t, time, short}`、`{p, number, USD}`、`{n, number, 0.00}` |
| select | `{gender, select, female {她} male {他} other {TA}}` |
| plural（含显式数字分支与 offset） | `{count, plural, offset:1 =0 {无人} =1 {仅你} one {# 人} other {# 人}}` |
| selectordinal（序数 1st/2nd/3rd…） | `{n, selectordinal, one {#st} two {#nd} few {#rd} other {#th}}` |
| 嵌套 | 分支消息体内可继续包含任意上述结构 |
| 转义 | `''` 输出单引号；`'{'`、`'}'`、`'#'` 输出字面符号 |

> 注：已废弃的 `choice` 参数会被明确报错，请改用 `plural`。

## 快速开始（Docker，推荐）

```bash
# 构建并后台启动
docker compose up -d --build
```

然后浏览器访问：

> **http://localhost:8000**

停止：

```bash
docker compose down
```

如 8000 端口被占用，修改 `docker-compose.yml` 中端口映射，例如 `"9000:8000"`。

也可以直接使用 Docker：

```bash
docker build -t icu-branch-preview .
docker run --rm -p 8000:8000 icu-branch-preview
```

## 本地直接运行（不用 Docker）

需要 Python 3.10+：

```bash
python -m venv .venv
source .venv/bin/activate   # Windows 请用: .venv\Scripts\activate
pip install -r requirements.txt
python wsgi.py
```

访问 **http://localhost:8000** 。

开发模式（Flask 热重载）：

```bash
python -m flask --app app run --debug -p 8000
```

## 使用说明

1. **模板**：在左侧编辑框粘贴 ICU 模板（支持 Tab 缩进、行号显示）。
2. **Locale**：填写 BCP-47 风格的语言标记，如 `zh`、`en-US`、`ru`、`ar`、`pt_BR`。
   - 复数类别（one/few/many/…）与数字、日期格式均随 locale 变化。
   - 无法识别的 locale 会回退到 `en` 并给出提示。
3. **变量样例**：模板中出现的变量会自动出现在变量表中（类型自动推断，可补填样例值）；也可以手动添加变量。
4. 右侧即时显示：
   - **当前样例渲染结果**（可复制 / 导出）；
   - **行内错误与提示**（点击跳转到出错位置）；
   - **全部可达分支**：每条显示条件链与分支文本，绿色为当前样例命中，灰色为未覆盖；点击分支卡片同样可定位回模板。

### 导出的 JSON 结构

```json
{
  "template": "…",
  "locale": "zh",
  "locale_resolved": "zh",
  "variables": {"count": "3"},
  "rendered": "…",
  "branches": [
    {"conditions": "count → one (例: 1)", "rendered": "…", "covered": true}
  ],
  "coverage": {"total": 4, "covered": 1},
  "errors": []
}
```

## HTTP 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/` | 页面 |
| `POST` | `/api/preview` | 入参 `{template, locale, variables}`，返回渲染结果、分支、错误等完整结构 |
| `POST` | `/api/export` | 同样入参，以附件形式返回精简 JSON 文件 |
| `GET` | `/healthz` | 健康检查 |

## 项目结构

```
.
├── app/
│   ├── __init__.py        # Flask 路由与响应组装
│   ├── icu_parser.py      # ICU MessageFormat 递归下降解析器(带行列位置/容错)
│   ├── renderer.py        # 分支枚举、样例渲染、复数匹配、数字/日期格式化
│   ├── templates/index.html
│   └── static/{style.css,app.js}
├── wsgi.py                # waitress 生产入口
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```

## 复数匹配规则说明

`plural` / `selectordinal` 的分支选择遵循 ICU 行为：

1. 优先匹配显式数字分支（`=0`、`=1`…，需变量值为整数）；
2. 否则取 `变量值 - offset` 后，查 CLDR 的基数（cardinal）/ 序数（ordinal）复数类别；
3. 模板中有对应类别分支则使用之；
4. 最终回退到必需的 `other` 分支（缺失会报错）。

分支中的 `#` 会按当前 locale 的数字格式输出「变量值减去 offset」。
