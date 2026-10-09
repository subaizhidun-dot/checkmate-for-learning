# CheckMate GitHub 上传清单

整理日期：2026-10-09。路径均相对于项目根目录，上传时保留目录结构。

## 上传范围

清单涵盖 CheckMate 运行源码、测试、文档、依赖声明和游戏资源。上传前核对当前工作区差异，并按完整文件列表检查各目录。

## 工作区新增文件

- `app_paths.py`
- `thinking_selection.py`
- `tests/test_thinking_interaction.py`
- `pic/app_icon.png`
- `docs/screenshots/g1.png`
- `docs/screenshots/g2.png`
- `docs/screenshots/g3.png`

## 工作区已跟踪文件修改

- `.gitignore`
- `GITHUB_UPLOAD.md`
- `README.md`
- `agent_play.py`
- `agent_prompts.py`
- `agenthelper.py`
- `agenttools.py`
- `gameengine.py`
- `gui.py`
- `main.py`
- `note_review.py`
- `sl_func.py`
- `tests/test_agent_play.py`
- `tests/test_agent_tools.py`
- `tests/test_gui_agent_settings.py`
- `tests/test_gui_human_controls.py`
- `tests/test_model_workflows.py`
- `tests/test_review_policy.py`
- `tests/test_transaction_legality.py`

## 完整上传文件列表

### 根目录源码（24 个）

- `agent_context.py`
- `agent_play.py`
- `agent_prompts.py`
- `agenthelper.py`
- `agenttools.py`
- `app_paths.py`
- `basicgame.py`
- `chat_stream.py`
- `game1.py`
- `game2.py`
- `game3.py`
- `game_rules.py`
- `gameengine.py`
- `gui.py`
- `llm_usage.py`
- `local_settings.py`
- `main.py`
- `note_review.py`
- `probe_image.py`
- `save_policy.py`
- `settings_widgets.py`
- `sl_func.py`
- `thinking_selection.py`
- `ui_text.py`

### 图片资源（19 个）

- `pic/app_icon.png`
- `pic/black_chess.png`
- `pic/board.png`
- `pic/butterfly.png`
- `pic/elephant.png`
- `pic/g1_0.png`
- `pic/g1_1.png`
- `pic/g1_2.png`
- `pic/g1_3.png`
- `pic/g1_4.png`
- `pic/lion.png`
- `pic/mole.png`
- `pic/pine.png`
- `pic/squ.png`
- `pic/time_token_dark.png`
- `pic/time_token_light.png`
- `pic/tree_rooted.png`
- `pic/tree_uprooted.png`
- `pic/white_chess.png`

### 测试（22 个）

- `tests/test_agent_autosave.py`
- `tests/test_agent_play.py`
- `tests/test_agent_tools.py`
- `tests/test_chat_stream.py`
- `tests/test_game2.py`
- `tests/test_game3.py`
- `tests/test_gui_agent_settings.py`
- `tests/test_gui_g2.py`
- `tests/test_gui_g3.py`
- `tests/test_gui_human_controls.py`
- `tests/test_gui_new_game_plus.py`
- `tests/test_llm_usage.py`
- `tests/test_local_settings.py`
- `tests/test_model_workflows.py`
- `tests/test_new_game_plus.py`
- `tests/test_review_policy.py`
- `tests/test_save_policy.py`
- `tests/test_settings_widgets.py`
- `tests/test_sidebar_navigation.py`
- `tests/test_special_inventory.py`
- `tests/test_thinking_interaction.py`
- `tests/test_transaction_legality.py`

### 文档与配置（4 个）

- `.gitignore`
- `GITHUB_UPLOAD.md`
- `README.md`
- `requirements.txt`

## 本地内容边界

按 `.gitignore` 排除 Python 环境和缓存、存档、日志、生成预览、本地编辑器设置、`checkmate.exe`、未使用图片和原始图片备份。

个人 Set 配置位于 `%LOCALAPPDATA%/CheckMate/settings.json`，包含自定义提示词、API 配置及受保护的凭据，保留在本机。根目录 `settings.json` 和 `.env` 同属本地配置。

## README 展示截图

- `docs/screenshots/g1.png`
- `docs/screenshots/g2.png`
- `docs/screenshots/g3.png`

以上三张截图与 README 一起上传。

## 当前功能

### 行动决策和执行回执

Step 的一次 Next 授权完整行动决策。查询、record_intent、选择、材料支付、退款和大象奖励会自动反馈结果并续问；完成耗 AP 的操作及其待完成阶段、同回复顺序计划或到达回合/检查边界后停止，等待下一次 Next。行动决策与条件复盘严格串行。Pause、Stop、模式切换、外部棋盘变更和换局清理授权。查询、意图和选择沿用同一无 AP 消耗计时；两种模式每次决策最多 16 次请求。

程序生成的界面文案使用英文，模型内容和自定义文本保留原文。界面分别展示回复已接收、工具待执行和实际执行结果，并列出 AP、revision、停止原因及失败参数。submit_plan 的条目使用游戏动作类型和参数，例如 place_squirrel/at、select_piece/at 和 move/from/to。失败回执包含拒绝步骤及当时合法动作证据，并保留已执行的步骤。

行动请求应用条件笔记选择合法动作；条件复盘读取公开反馈与规则线索并更新条件笔记。NG+ 使用独立的将死目标和行动流程。

### 提示词和 Thinking

三类提示词分别提供并排的 Copy / Restore 按钮。Copy 复制编辑框当前内容，Restore 保存对应的内置英文默认；后续对应请求读取保存后的文本。恢复操作保留其他设置、游戏状态和在途请求。

Thinking 支持中文多行和当前展示回合内的跨消息选择复制、选择高亮、边缘自动滚动、流式选区与阅读位置保持、窗口缩放和回合/新局清理。输出区域只读，快捷键按当前输入焦点处理。黑白气泡、滚动条及回合切换继续使用现有样式。

### 时间印记和七子限制

G1/G2/G3 的单次时间印记转移提供与实际执行一致的合法提示。接收方超过七子时，转移后进入该持有者的销毁选择。持有者完成大象免费松鼠、普通松鼠放置或出售退款后超过七子，也会立即进入同一限制阶段。AP 用尽时先完成限制销毁，再开始胜利检查；销毁本身不额外消耗 AP。移除新放置的棋子时同步清理其本回合标记。限制对象为时间印记持有者，NG+ 沿用无时间印记规则。

## 验证和复现

回归覆盖完整 Step 决策、购买/退款/奖励、顺序多动作、行动与复盘串行、授权清理、非法计划、无 AP 超时/请求上限、英文程序文案、提示词恢复以及 Thinking 交互。七子限制回归覆盖三种模式的时间印记转移、免费奖励、AP 为零、多次销毁、存档重载、普通放置、退款完成与新棋子标记清理。

GUI 检查使用真实 Pygame 渲染器，包括窗口缩放、180 像素侧栏、黑白气泡、文本选择及人类绿色/LLM 蓝色合法高亮。测试请求使用模拟回复或本机 HTTP 服务。剪贴板快捷键通过模拟系统接口验证；外部程序粘贴和真实提供商行为需在实际使用环境验证。

完整离线回归 `python -B -m unittest discover -s tests -q`：293 项通过，包括 11 项人类操作 GUI 测试和 8 项 Thinking/提示词交互测试。已检查时间印记转移与限制销毁的实际 Pygame 渲染；`git diff --check` 通过。

PowerShell 复现：

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q
$env:CHECKMATE_GUI_CAPTURE_DIR = Join-Path (Get-Location) 'output/interaction-qa'
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_thinking_interaction.py -q
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_gui_human_controls.py -q
```

GUI 截图生成在 `output/` 下的本地验证目录。上传前重新检查 `git status --short` 和实际差异；暂存后核对 `git diff --cached --name-only`，确保文件清单完整且个人配置留在本机。目标仓库、分支和提交方式按用户的上传要求执行。
