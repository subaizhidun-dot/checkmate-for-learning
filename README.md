# CheckMate for Learning

A Python/Pygame board-game learning project. The current implementation supports Game 1 (G1) with a local graphical interface.

## Run locally

Developed with Python 3.13. Install the dependency and start the game from the project folder:

```sh
python -m venv .venv
```

Activate the environment on Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Or on macOS/Linux:

```sh
source .venv/bin/activate
```

Then run:

```sh
python -m pip install -r requirements.txt
python main.py
```

The game requires a graphical desktop. Keep the Python files and the `pic` directory together. The entry point is `main.py`.

## Implemented features

- G1 setup and initial time-token ownership selection.
- Turn progression and action-point management.
- Squirrel placement, animal movement, elephant pushing and bonus placement.
- Elephant/lion purchases, payment selection, sales, refunds, and transaction cancellation.
- Lion control, piece-limit handling, and time-token actions.
- Five G1 condition checks, visual feedback, time-wish continuation saves, and mate detection.
- Three manual save slots and one autosave slot, including action-phase restoration.
- Pixel-art animal/resource icons and G1 rule diagrams.

New saves are written completely before replacing the destination file. After a successful write, older JSON files belonging to the same slot are removed. Saves are created locally in `saves/` when needed.

## Source files

| File | Responsibility |
| --- | --- |
| `main.py` | Application loop, input handling, action phases, transactions, turn resolution, and save/load orchestration. |
| `basicgame.py` | Board, resource, player and interaction models, plus shared action-legality helpers. |
| `game1.py` | G1 board setup, playable area and five condition checks. |
| `gui.py` | Pygame rendering, menus, legal-target highlights, tooltips and image loading. |
| `sl_func.py` | JSON serialization, save replacement, loading and slot listing. |
| `ui_text.py` | English interface messages and tooltips. |

## Repository contents

The repository contains source code, dependency information, documentation and the 14 images currently loaded by `gui.py`. Local environments, editor settings, saved games, logs, generated previews, artwork backups, the machine-specific launcher and unused artwork are excluded through `.gitignore`.

G2/G3 and a callable agent interface are future work. The additional mole and tree artwork is currently stored only in the local workspace and is not used by G1.

## AI assistance

The original project declaration is preserved in `README.txt`. Development has used AI assistance through Codex for implementation discussions, code changes, documentation and verification. Updated pixel-art assets and rule diagrams were generated or edited with OpenAI image-generation tools. This remains a learning project and should be reviewed against the intended game rules when extended.
