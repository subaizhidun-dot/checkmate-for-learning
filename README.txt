Project name:
CheckMate

AI use declaration:
I confirm that I used GPT-5.5, an artificial intelligence language model developed by OpenAI, through Codex during this project.
I used the model to discuss possible program implementation methods, data storage methods, project structure, framework setup, translation of documentation comments and visible user-interface text from my native Chinese into English, rapid implementation of complex functions, and fast code-level testing.
I have tested and modified the AI-assisted content and take full responsibility for the final submitted work.

Python version:
This project is currently developed and tested with Python 3.13.9.

Required external library:
This project uses pygame 2.6.1 to create and display the graphical user interface.

Install pygame with:
pip install pygame

Main script:
Run main.py. This is the entry point of the project.

How to run:
Open a terminal in the project folder and run:

python main.py

On this development machine, checkmate.exe is an optional local Windows launcher for main.py. It is excluded from the GitHub repository. Repository users should run python main.py; no executable launcher is required.

Runtime notes:
This project is a Pygame GUI application. It opens a game window when it runs.
Because it requires a graphical display, it should be run in a local Python environment rather than Ed's normal online code runner.

Required files and folders:
Please keep all Python source files and the pic folder together. See README.md for repository setup instructions and requirements.txt for the dependency version.

The pic folder contains the board, pieces, player panels, time-token images, and victory-condition images used by the GUI.

The saves folder is used for saving and loading games. If it is missing, the program can create it when needed.

Python file overview:

main.py:
Starts the program, owns the main game loop, handles menu actions, save/load actions, player input, action phases, turn transitions, check/mate resolution, and the special time-wish continuation save.

gui.py:
Draws the Pygame window, including the board, pieces, buttons, player panels, message area, tooltips, legal-action hints, mate failure marks, and victory-condition feedback.

basicgame.py:
Defines the core Resource, Playerstate, Chessboard, and Interaction classes. It also contains shared legal-operation helpers for board targets, resource buttons, player panels, elephant movement, piece counts, and player-accessible cells.

game1.py:
Creates the Game 1 board and checks the Game 1 victory-condition list.

sl_func.py:
Handles saving, loading, save-file listing, autosaves, active save slots, and serialization of board, player, interaction, and phase state.

ui_text.py:
Stores visible dialog and tooltip text used by the Pygame interface. Visible runtime text is English to avoid font-rendering issues in pygame.

Current implemented game features:
The current version supports starting Game 1, choosing the initial time-token owner, moving pieces, buying and selling elephant/lion pieces, placing squirrels, elephant pushing and bonus placement, time-token movement, piece-limit handling, save/load slots, autosaves, visual victory-condition checking, check resolution, time-wish save creation, and mate detection when no player holds the time token.

External library declaration:
This project uses pygame.
This project does not use audio or video processing.
This project does not use web, networking, or server features.
