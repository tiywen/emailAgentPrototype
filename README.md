# Email Assistant（邮件摘要与优先级助手）

一个基于 Python 的邮件分析原型，支持：

- 从 Microsoft Graph 拉取邮箱邮件进行分析（Streamlit UI）
- 手动粘贴整段邮件线程进行分析
- 上传邮件文件（`.html` / `.htm` / `.eml` / `.msg`）进行分析
- 生成两种摘要（Short / Long）
- 判断是否需要回复、给出优先级判断理由，并生成可编辑回复草稿
- 保留 CLI 方式（输入 JSON）用于离线批处理/快速测试

---

## 1. 主要功能

### 摘要能力
- `Short version summary`：更简洁的概览
- `Long version summary`：更详细的上下文信息
- 摘要会输出：
  - `summary`
  - `key_points`
  - `open_questions`

### 回复优先级判断能力
- 基于结构化信号与评分规则，输出优先级判断（`HIGH/MEDIUM/UNCERTAIN/LOW`）
- 兼容前端展示字段：
  - `是否需要回复`
  - `判断原因`
  - `回复草稿`
- 默认规则：
  - `HIGH` / `MEDIUM` / `UNCERTAIN`：默认生成回复草稿
  - `LOW`：默认不生成草稿

### 多输入来源（Streamlit）
- 登录邮箱（Graph 拉取收件箱）
- 粘贴整封邮件/线程文本
- 上传邮件文件：`.html` / `.htm` / `.eml` / `.msg`

---

## 2. 项目结构

- `streamlit_app.py`：Web 前端主入口（登录、邮件输入、摘要、回复判断）
- `main.py`：CLI 入口（读取 JSON，输出摘要 JSON）
- `email_assistant/summary_pipeline.py`：统一分析流程
- `email_assistant/llm_client.py`：Prompt 构建与 OpenAI 调用
- `email_assistant/graph_mail.py`：Graph API、邮件正文提取、上传文件解析
- `email_assistant/msal_device.py`：MSAL 设备码登录与 token cache 刷新
- `email_assistant/input_loader.py`：CLI 输入 JSON 解析
- `email_assistant/preprocessor.py`：线程文本拼装与排序
- `email_assistant/models.py`：Pydantic 输入/输出模型
- `email_assistant/jwt_peek.py`：JWT 诊断字段提取（仅调试）
- `email_assistant/dotenv_load.py`：项目根 `.env` 加载
- `test/cases/`：10 个优先级评测场景（含 expected_output）
- `run_test_cases.py`：命令行批量执行测试入口
- `data/`：默认输入/输出样例

---

## 3. 安装与运行

### 环境要求
- Python 3.10+

### 安装依赖
```bash
python -m pip install -r requirements.txt
```

### 配置环境变量
1. 复制 `.env.example` 为 `.env`
2. 至少填写：
   - `OPENAI_API_KEY=...`
3. 可选：
   - `OPENAI_MODEL=gpt-4o-mini`（默认值）
   - Graph/Entra 相关变量（见下文）

### 本地运行指南（从 0 到可用）

下面这套步骤按顺序执行，产品经理或开发都可以独立完成本地部署。

1) 获取代码并进入目录

```bash
git clone <你的仓库地址>
cd emailAgentPrototype
```

2) 检查 Python 版本（建议 3.10+）

```bash
python --version
```

3) 创建并激活虚拟环境

- Windows PowerShell：
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

- macOS / Linux：
```bash
python3 -m venv .venv
source .venv/bin/activate
```

4) 安装依赖

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

5) 准备 `.env`

```bash
cp .env.example .env
```

如果你在 Windows PowerShell，没有 `cp` 命令可用，使用：

```powershell
Copy-Item .env.example .env
```

然后编辑 `.env`，最少填这 1 项即可启动手动模式：

```env
OPENAI_API_KEY=你的密钥
```

可选：

```env
OPENAI_MODEL=gpt-4o-mini
```

若要使用“登录邮箱（Graph 拉取）”模式，再补：

```env
AZURE_CLIENT_ID=...
AZURE_TENANT_ID=...
```

6) 启动 Web 应用（推荐）

```bash
streamlit run streamlit_app.py
```

看到本地地址后（通常是 `http://localhost:8501`），在浏览器打开即可。

7) 首次验证（建议按这个顺序）

- 在“邮件来源”选择 `粘贴整封邮件或线程`
- 粘贴一段测试邮件
- 点击 `Short version summary`
- 再点击 `Analyze reply priority & draft`
- 看到摘要和回复判断同时出现，说明本地运行成功

8) 可选：验证文件上传解析

- 切换到 `上传邮件文件`
- 上传 `.html` / `.eml` / `.msg` 任一文件
- 点击摘要按钮，确认能正常返回结果

9) 可选：验证 Graph 登录模式

- 在侧边栏点击 `Sign in (device code)`
- 按提示完成浏览器设备码登录
- 返回页面点击 `Refresh inbox` 拉取邮件列表

10) 关闭与下次启动

- 关闭服务：在终端按 `Ctrl + C`
- 下次启动只需：
  - 进入项目目录
  - 激活虚拟环境
  - 执行 `streamlit run streamlit_app.py`

### 本地运行常见报错（快速处理）

- `OPENAI_API_KEY is not set`
  - 检查 `.env` 是否存在、是否填写了 `OPENAI_API_KEY`
  - 重启 Streamlit

- `.msg` 解析失败
  - 执行 `python -m pip install -r requirements.txt`（确保 `extract-msg` 已安装）

- PowerShell 无法执行激活脚本
  - 先执行：
  ```powershell
  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
  ```
  - 然后重新激活虚拟环境

- Graph 登录成功但拉取邮件失败（401）
  - 优先改用手动模式继续工作（不依赖 Graph）
  - 再按本文 “Microsoft Graph / Entra 配置” 和 “常见问题排查”章节处理

---

## 4. Streamlit 使用说明（推荐）

启动：
```bash
streamlit run streamlit_app.py
```

### 4.1 输入方式
在页面的“邮件来源”中选择：

1. `登录邮箱（Graph 拉取）`
   - 侧边栏设备码登录后，拉取收件箱邮件列表
   - 选中邮件后可执行摘要或回复分析

2. `粘贴整封邮件或线程`
   - 直接将完整邮件对话粘贴到文本框
   - 无需登录 Graph 即可分析

3. `上传邮件文件`
   - 支持 `.html` / `.htm` / `.eml` / `.msg`
   - 上传后自动解析正文并用于分析

### 4.2 摘要与回复分析
- 摘要仅在点击按钮后生成（不会因切换邮件自动触发）
- Short/Long 按钮颜色会与当前显示版本同步
- 已生成摘要后再做回复分析，会同时保留两块结果
- 回复草稿通过文本框展示，可直接编辑

### 4.3 处理中的交互规则
- 处理中会禁用大部分交互控件，避免并发操作冲突
- `Sign out` 按钮保留可用

---

## 5. CLI 使用说明

CLI 仍可用于基于 JSON 的摘要生成。

### 常用命令
```bash
python main.py --input data/input.json --output data/output.json --model gpt-4o-mini --style short
```

```bash
python main.py --input data/input.json --style long
```

```bash
python main.py --input data/input.json --dry-run
```

### 参数
- `--input`：输入 JSON 路径（默认 `data/input.json`）
- `--output`：输出 JSON 路径（默认 `data/output.json`）
- `--model`：模型名（默认 `gpt-4o-mini`）
- `--style`：`short` / `long`
- `--dry-run`：仅输出预处理后的线程文本，不调用模型

---

## 6. CLI 输入格式

### 单封邮件
```json
{
  "subject": "string",
  "sender": "string",
  "recipients": ["string"],
  "timestamp": "ISO-8601 string",
  "body": "string"
}
```

### 邮件线程（推荐）
```json
{
  "thread_id": "string",
  "subject": "string",
  "messages": [
    {
      "sender": "string",
      "recipients": ["string"],
      "timestamp": "ISO-8601 string",
      "body": "string"
    }
  ]
}
```

---

## 7. 输出格式

### 摘要输出
```json
{
  "summary": "string",
  "key_points": ["string"],
  "open_questions": ["string"]
}
```

### 回复判断输出（前端展示字段）
```json
{
  "是否需要回复": true,
  "判断原因": "string",
  "回复草稿": "string"
}
```

---

## 8. Microsoft Graph / Entra 配置

在 Entra App Registration 中：

- 应用类型需支持 Public Client（设备码登录）
- Graph Delegated 权限至少包含：
  - `User.Read`
  - `Mail.ReadWrite`

`.env` 常用字段：

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- 可选 `AZURE_AUTHORITY`（如 `.../common`）
- 可选 `GRAPH_API_ROOT`（国别云场景）
- 可选 `AZURE_MSAL_DISABLE_INSTANCE_DISCOVERY=true`（部分代理环境）

---

## 9. 常见问题排查

### 9.1 `AADSTS700016` / directory `''`
- 检查 `AZURE_CLIENT_ID` / `AZURE_TENANT_ID` 是否填写正确且不是同一个 GUID
- 确认 `.env` 已加载（本项目默认会从项目根加载并覆盖同名空值）
- 必要时设置 `AZURE_AUTHORITY`

### 9.2 Graph `401 Unauthorized`
- 检查 `GRAPH_API_ROOT` 与租户云环境是否匹配
- 检查 token 的 `aud` / `iss` 是否指向 Graph 与正确云
- 对个人 Outlook（MSA）建议使用 `AZURE_AUTHORITY=https://login.microsoftonline.com/common`

### 9.3 `.msg` 无法解析
- 确保依赖已安装：`extract-msg`
- 重新执行：
  ```bash
  python -m pip install -r requirements.txt
  ```

---

## 10. 说明

- 本项目为原型，重点在流程可用与交互验证。
- 结果质量受模型能力、邮件原文质量、上下文完整性影响。
- JWT 仅做无签名解析用于调试显示，不用于安全验证。  

---

## 11. 测试说明（当前评估框架）

当前测试集位于 `test/cases/`，共 10 个 dry-run case（`tc01`~`tc10`），每个 case 使用统一结构：

- `input`：线程化邮件输入（含 user_context / thread / messages）
- `expected_output.triage`：信号、分数区间、优先级与原因锚点规则
- `expected_output.summary`：摘要覆盖/禁幻觉/开放问题/长度约束
- `expected_output.reply`：是否应生成、语气、必须包含/禁止包含要点

### 11.1 评估逻辑

`run_test_cases.py` 对每个 case 生成三类能力输出并分别评估：

- `triage`（规则评估，deterministic）
  - 核验 `needs_response`
  - 核验 `allowed_priority` / `disallowed_priority`
  - 核验 `expected_signal_assertions`
  - 核验 `expected_score_band`
  - 核验 `reason_must_include_any`
- `summary`（rubric 评估，LLM-as-a-judge）
  - 从 `coverage` / `faithfulness` / `usefulness` 三维判定
  - 输出 `PASS / PARTIAL / FAIL`
  - 记录 `missing_items`、`hallucinations`、`notes`
- `reply`（rubric 评估，LLM-as-a-judge）
  - 同样按 `coverage` / `faithfulness` / `usefulness` 判定
  - 输出 `PASS / PARTIAL / FAIL`

`overall_result` 基于三块结果综合给出（`PASS / PARTIAL / FAIL`）。

### 11.2 运行方式

运行全部：

```bash
python run_test_cases.py --all
```

运行单个：

```bash
python run_test_cases.py --case tc01
```

运行多个：

```bash
python run_test_cases.py --case tc01 --case tc06 --case tc10
```

指定模型和输出目录：

```bash
python run_test_cases.py --all --model gpt-4o-mini --save-dir test/results
```

可选参数（常用）：

- `--cases-dir`：指定 case 目录（默认 `test/cases`）
- `--current-user-identity`：覆盖测试时的用户身份描述

### 11.3 结果文件（已精简）

`test/results/` 当前仅保留必要产物：

- `report.json`：总览统计 + 每个 case 的完整评估结果
- `tcXX.actual.json`：模型原始输出（实际 output）
- `tcXX.result.json`：单 case 评估结果（含 expected / actual 对照）

### 11.4 当前最新一轮结果快照

基于最近一次 `--all` 运行（见 `test/results/report.json`）：

- 总 case：`10`
- Overall：`PASS 2` / `PARTIAL 8` / `FAIL 0`（`overall_pass_rate = 0.2`）
- Triage：`PASS 9` / `PARTIAL 1` / `FAIL 0`
- Summary：`PASS 2` / `PARTIAL 8` / `FAIL 0`
- Reply：`PASS 10` / `PARTIAL 0` / `FAIL 0`

说明：当前主要瓶颈在 `summary` 的 rubric 命中（常见为关键点遗漏或开放问题不贴合）；`reply` 已稳定通过，`triage` 仅有少量边界样本为 `PARTIAL`。
