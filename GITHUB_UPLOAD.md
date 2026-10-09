# GitHub 上传清单与交接

整理日期：2026-10-09。路径均相对于项目根目录，上传时保留目录结构。

## 本次交接范围

将当前项目工作区的源码、测试、文档、依赖声明和游戏资源同步到现有 GitHub 仓库。本文件只是交接清单；本次没有执行暂存、提交或推送。

当前工作区包含多轮开发修改，应一起核对并提交。尤其要加入下面列出的新增文件，仅提交已经被 Git 跟踪的修改会漏掉运行模块及测试。

## 新增文件（整理时尚未跟踪）

- `GITHUB_UPLOAD.md`
- `agent_context.py`
- `chat_stream.py`
- `note_review.py`
- `probe_image.py`
- `tests/test_chat_stream.py`
- `tests/test_model_workflows.py`
- `tests/test_review_policy.py`
- `tests/test_sidebar_navigation.py`
- `tests/test_transaction_legality.py`

## 已跟踪文件的修改

- `README.md`
- `agent_play.py`
- `agent_prompts.py`
- `agenttools.py`
- `basicgame.py`
- `game2.py`
- `game3.py`
- `game_rules.py`
- `gameengine.py`
- `gui.py`
- `llm_usage.py`
- `local_settings.py`
- `main.py`
- `settings_widgets.py`
- `sl_func.py`
- `tests/test_agent_play.py`
- `tests/test_agent_tools.py`
- `tests/test_game2.py`
- `tests/test_game3.py`
- `tests/test_gui_agent_settings.py`
- `tests/test_gui_g2.py`
- `tests/test_gui_human_controls.py`
- `tests/test_gui_new_game_plus.py`
- `tests/test_local_settings.py`
- `tests/test_new_game_plus.py`
- `ui_text.py`

## 完整上传文件列表

### 根目录源码（22 个）

- `agent_context.py`
- `agent_play.py`
- `agent_prompts.py`
- `agenthelper.py`
- `agenttools.py`
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
- `ui_text.py`

### 图片资源（18 个）

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

### 测试（21 个）

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
- `tests/test_transaction_legality.py`

### 文档与配置（4 个）

- `.gitignore`
- `GITHUB_UPLOAD.md`
- `README.md`
- `requirements.txt`

## 本地内容边界

按 `.gitignore` 排除 Python 环境和缓存、存档、日志、生成预览、本地编辑器设置、`checkmate.exe`、未使用图片和原始图片备份。不要用强制添加把这些内容带入仓库。

个人 Set 配置位于 `%LOCALAPPDATA%/CheckMate/settings.json`，不属于上传内容。本机中文提示词保存在该文件；程序内置默认提示词仍为英文。该本地文件还包含个人 API 配置及受保护的凭据，不要复制到仓库。根目录 `settings.json` 和 `.env` 同样属于本地文件。

## 已完成的验证

最近一次代码验证：`python -B -m unittest discover -s tests -q`，267 项测试全部通过；随后仅整理了本清单及 README 的分发说明。测试使用模拟请求和本机 HTTP 服务，未调用远程模型。

上传模型开始工作时，先重新查看 `git status --short` 和差异，确认清单整理后是否又有修改。暂存后核对 `git diff --cached --name-only`，确保新增模块、测试和文档齐全，且没有本地配置或凭据。目标仓库、分支及提交方式按用户另行提供的上传要求执行。
