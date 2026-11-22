# EconLab

通过 Codex 阅读经济学论文，将文献中的模型整理为可检索的方法记录和可执行工具，再用经过验证的工具回答研究问题。

项目采用 **FastAPI + SQLite + 原生 JavaScript**，运行在本机浏览器中。AI 使用本机已登录的 Codex 账户，模型计算由 Python 或 MATLAB 执行。

## 功能

| 页面 | 功能 |
| --- | --- |
| 文献库 | 导入与删除 PDF，管理附件、知识卡和原文证据；论文追问、人工修改与审核；查看复现任务和执行日志 |
| 方法库 | 保存模型的问题、假设、机制、方程、求解步骤、数据要求、适用范围及来源文献 |
| 工具库 | 管理方法封装、输入输出、依赖环境与验证记录，执行复现验证和计算 |
| 问题求解 | 根据研究问题比较方法，检查数据与适用条件，调用已验证工具，返回数值、解释、依据和运行记录 |

三库通过来源与映射关系关联：论文提供方法依据，方法关联工具，工具关联输入、参考结果和验证记录。各库支持版本化修改与审核；删除论文时同步处理关联关系，共享的方法和工具保留并重新核对。

## 快速开始

### 环境要求

- Windows，Python 3.11 或以上版本。
- 已安装 Codex CLI，并有可用的 Codex / ChatGPT 登录账户及额度。
- MATLAB 为可选依赖，仅运行 MATLAB 工具时需要，以及对应工具要求的工具箱。

Codex CLI 可通过 npm 安装，使用此方式需先安装 Node.js：

```powershell
npm install -g @openai/codex
```

### 启动

```powershell
git clone https://github.com/yaobishun/econlab.git
cd econlab
.\econlab\start.cmd
```

启动脚本检查 Python 依赖，缺失时创建虚拟环境并安装。首次安装需要联网。启动后访问 **http://127.0.0.1:8765**。

在右上角设置中登录 Codex / ChatGPT，刷新连接并测试。无需配置 AI API Key。

### 使用

1. 在文献库导入 PDF，保留完整分析与自动准备复现工具选项。
2. 在论文页查看知识卡、关联方法、材料来源和复现进度；可补充附件、网址或指定实验。
3. Agent 准备代码与输入后，核对并审核工具，点击“审核后继续复现”。
4. 验证通过后，在问题求解页面输入问题与数据条件，确认方案并执行。

任务支持停止与继续，工作区和会话会保留。材料缺失、账户额度不足或运行失败时，论文页会显示原因。

### 停止

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\econlab\stop.ps1
```

## 工作原理

### 从论文到工具

```text
导入 PDF → 逐页视觉阅读 → 生成知识卡和方法记录
→ 检索并下载公开材料 → 固定实验、参考结果和验收指标
→ 检查数据、编写并执行适配程序、生成工具封装
→ 人工审核 → 配置环境、实际运行 → 独立数值验收 → 更新三库
```

PDF 全部页面转为图像，分批交给 Codex 阅读，不使用 OCR。原 PDF 文本层保留用于原文展示和引文匹配。

应用通过 Codex `app-server` 接口调用已登录账户。复现 Agent 在每篇论文的独立工作区内检查文件、编写脚本、执行数据适配并根据错误修正；应用提供公开材料搜索下载、原页查看、实验固定和成果提交工具。代码可来自作者公开材料，也可依据论文实现。

Agent 提交结构化成果，由后端检查来源、版本与三库映射后保存。正式工具经过审核后，由运行器实际计算并比较参考结果；AI 不能自行设置“验证通过”。修正任务会收到对应版本的运行日志和实际输出，改动后的代码需重新审核。

### 验证与求解

复现使用真实输入和独立参考结果。对未舍入输出逐项验收：非零参考值的相对误差必须**严格小于 10%**，零参考值使用预先指定的绝对容差。参考文件不进入计算工作区，验证记录绑定代码、方法版本和材料哈希，相关变更后需重新验证。

```text
研究问题与数据条件 → 检索方法库 → 比较假设、数据和适用范围
→ 补充缺失信息、确认方案 → 调用已验证工具 → 返回结果与依据
```

求解仅覆盖已验证工具的适用范围。缺少必要数据、对应工具或验证记录时，系统返回缺口；通过某个实验的验收不代表整篇论文的全部结果均已复现。结果解释同时参考实际输出、工具实现与原文引文索引。

### 会话与数据

每篇论文分别维护问答会话和执行会话，共享最新三库状态与研究摘要。重启后恢复会话和工作区；原生会话无法恢复时，使用本地历史与摘要重建上下文。

数据默认保存在 `econlab/.local/`，包括数据库、论文附件、会话、工作区和运行结果。备份时停止服务并复制该目录。`.local/` 和 `.venv/` 已加入 `.gitignore`，仓库不附带本地论文库与测试数据。

网站为本地单机服务。AI 阅读与分析需要联网，相关论文页面和任务上下文会发送至 Codex 在线服务。Agent 使用限定工作区的写入沙箱；正式 Python / MATLAB 工具以本机账户权限运行，执行前需审核代码与依赖。

## 代码结构

```text
econlab/
├── app.py                          # FastAPI 接口与本地访问控制
├── catalog.py / store.py            # 三库映射、版本、审核与 SQLite 存储
├── engine.py                       # 论文分析、问答、任务和问题求解编排
├── codex_bridge.py / provider_bridge.py  # Codex app-server 接入
├── pdf_visual.py                   # PDF 逐页图像输入
├── materials.py                    # 公开材料搜索、下载、解包与来源记录
├── execution_agent.py              # Agent 工作区、宿主工具与成果校验
├── reproduction.py                 # 复现准备、审核与验证流程
├── runner.py / environments.py     # Python / MATLAB 执行与依赖环境
├── static/                         # 网页界面
├── tests/                          # 自动化测试
└── tools/                          # 隔离验证、浏览器检查与源码打包脚本
```

## 开发与测试

在仓库根目录执行：

```powershell
python -m venv econlab\.venv
.\econlab\.venv\Scripts\python.exe -m pip install -r econlab\requirements.txt
.\econlab\.venv\Scripts\python.exe -m unittest discover -s econlab\tests -v
```

[agent_capability_probe.py](econlab/tools/agent_capability_probe.py) 可测试实际 Codex 文件与执行能力；[agent_workflow_validation.py](econlab/tools/agent_workflow_validation.py) 可建立隔离库验证复现流程。这些在线检查需要登录账户并消耗额度，生成材料保存在 `econlab/.local/validation/`。

## 许可证

项目尚未提供自有代码的 `LICENSE`。第三方组件与研究材料说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
