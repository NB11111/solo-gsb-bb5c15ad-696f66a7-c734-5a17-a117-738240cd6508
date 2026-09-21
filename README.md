# ICU 文案分支预览器（ICU MessageFormat Branch Previewer）

一个 Flask 网页工具：粘贴 ICU MessageFormat 模板、填写变量样例，即可
解析嵌套的 `plural` / `select` / `selectordinal` 语法，按语言环境（locale）
渲染所有可达分支并列出每个分支的触发条件。

## 功能

- **全分支预览**：枚举模板的所有可达分支路径（含嵌套），逐条渲染并标注触发条件
  （如 `count = 0（精确匹配）`、`count 的复数类别为 "one"（示例：count = 1）`）。
- **CLDR 复数规则**：按语言环境计算复数类别（cardinal 基于 Babel/CLDR 数据，
  ordinal 内置常见语言规则），同一变量在多个分支间保证路径自洽。
- **行内错误提示**：括号不配对、未知格式类型、缺少 `other` 分支、重复选择器、
  非法复数类别、变量类型错误（如 plural 变量给了字符串）等，均在编辑器中
  以红色波浪线标出，并在下方列出带行列号的说明，点击可定位。
- **未覆盖分支标记**：对照当前变量样例，标记每个分支 / 每条路径为
  「已覆盖 / 未覆盖 / 不可达」（不可达 = 该复数类别在当前语言环境下不存在，
  或被 `=N` 显式分支抢占）。
- **变量管理**：从模板一键提取变量；数值 / 布尔自动识别。
- **复制与导出**：复制单条或全部渲染结果；一键导出包含模板、变量与完整
  分析结果的 JSON 文件。

## 快速开始

### 方式一：Docker Compose（推荐）

```bash
docker compose up --build
```

### 方式二：Docker

```bash
docker build -t icu-branch-previewer .
docker run --rm -p 5000:5000 icu-branch-previewer
```

### 方式三：本地运行

```bash
pip install -r requirements.txt
python app.py            # 或：gunicorn --bind 0.0.0.0:5000 app:app
```

### 访问

启动后在浏览器打开：**http://localhost:5000**

端口可通过环境变量 `PORT` 修改（Compose 中需同步调整 `ports` 映射）。

## 使用说明

1. 在左侧粘贴 ICU MessageFormat 模板，例如：

   ```
   {gender, select, male{He} female{She} other{They}} has {count, plural, =0{no new messages} one{# new message} other{# new messages}}.
   ```

2. 选择语言环境（如 `en`、`zh-Hans`、`ru`、`ar`），并填写变量样例
   （或点「从模板提取变量」）。
3. 右侧实时展示：
   - **样例渲染结果**：当前变量取值下的输出及其命中的分支条件；
   - **分支节点**：每个 plural/select 的全部分支、触发条件与覆盖状态；
   - **可达分支路径**：所有分支组合的渲染结果，未覆盖的以橙色标记。
4. 点击「复制」复制结果，点击「导出 JSON」下载完整分析。

## HTTP API

`POST /api/preview`

```json
{
  "template": "{count, plural, one{# item} other{# items}}",
  "locale": "en",
  "variables": {"count": 3}
}
```

响应包含 `errors / warnings / infos`（带行列号）、`main`（样例渲染）、
`nodes`（分支结构与覆盖状态）、`paths`（全部可达路径）、`stats`
（路径总数、覆盖数、是否截断）以及编辑器高亮用的 `tokens`。

## 支持的语法

| 语法 | 示例 |
| --- | --- |
| 简单变量 | `{name}` |
| 数字格式化 | `{n, number}`、`{n, number, percent}` |
| 复数 | `{n, plural, =0{none} one{# item} other{# items}}` |
| 序数 | `{pos, selectordinal, one{#st} two{#nd} few{#rd} other{#th}}` |
| 选择 | `{g, select, male{He} female{She} other{They}}` |
| 偏移 | `{n, plural, offset:1 one{you} other{you and # others}}` |
| 转义 | `''` 输出单引号；`'{`、`'}`、`'#` 输出字面符号 |
| 嵌套 | 以上语法可任意嵌套 |

说明：`date` / `time` / `spellout` / `duration` 类型在预览中原样输出变量值；
路径枚举上限为 300 条（超出会标记 `truncated`）。

## 项目结构

```
├── app.py               # Flask 入口（/ 与 /api/preview）
├── icu.py               # ICU 解析器、渲染器、分支枚举与覆盖分析
├── templates/index.html # 单页界面
├── static/app.js        # 前端逻辑（高亮、实时分析、复制、导出）
├── static/style.css
├── tests/test_icu.py    # pytest 单元测试
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```

## 测试

```bash
pip install pytest
python -m pytest tests/ -q
```
