from __future__ import annotations

from typing import Any


# Startup menu.
STARTUP_DIALOG = "Choose Start > Start G1 or Load a saved game."

# Top menu.
START_MENU_DIALOG = "Start menu."
SAVE_MENU_DIALOG = "Save menu."
LOAD_MENU_DIALOG = "Load menu."
G1_STARTED_DIALOG = "Game 1 started. Choose the time token owner."

# Clicks that cannot be handled in the current state.
NO_BUTTON_DIALOG = "No button at this position."
START_OR_LOAD_DIALOG = "Start or load a game first."
ACTION_UNAVAILABLE_DIALOG = "This action is not available now."
NO_LEGAL_ACTION_DIALOG = "No legal action from that cell."
ILLEGAL_TARGET_DIALOG = "That target is not legal."
ILLEGAL_SQUIRREL_TARGET_DIALOG = "That board cell is not legal for squirrel."
SELECTED_PIECE_MISSING_DIALOG = "Selected piece no longer exists."

# Time-token owner selection.
CHOOSE_TIME_TOKEN_DIALOG = "Click a player panel to assign the time token."

# Right-click cancellation.
SELECTION_CLEARED_DIALOG = "Selection cleared."

# Time-token actions.
ASSIGN_TOKEN_FIRST_DIALOG = "Assign the time token before moving it."
NOT_ENOUGH_AP_TOKEN_DIALOG = "Not enough AP to move the time token."
CURRENT_PANEL_ONLY_DIALOG = "Only the current player's panel can move the time token."
TOKEN_MOVED_ONCE_DIALOG = "Time token moved once."
TOKEN_FAST_MOVED_DIALOG = "Time token moved until AP ran out."
PIECE_LIMIT_DIALOG = "Remove pieces until the time token holder has 7 pieces."
PIECE_LIMIT_TARGET_DIALOG = "Choose a piece owned by the time token holder."
PIECE_LIMIT_RESOLVED_DIALOG = "Piece limit resolved."

# Resource buttons.
PLACE_SQUIRREL_DIALOG = "Choose where to place squirrel."
NOT_ENOUGH_AP_SQUIRREL_DIALOG = "Not enough AP to place squirrel."
SQUIRREL_PLACED_DIALOG = "Squirrel placed."
CHOOSE_BUY_COST_DIALOG = "Choose squirrels to pay the cost."
BUY_TARGET_DIALOG = "Choose an empty cell for the new special piece."
RESOURCE_ILLEGAL_DIALOG = "That reserve action is not legal now."

# Piece movement and elephant follow-up.
ELEPHANT_BONUS_DIALOG = "Elephant moved. Place one free squirrel, or right-click to skip."
ELEPHANT_BONUS_PLACED_DIALOG = "Free squirrel placed."
ELEPHANT_BONUS_SKIPPED_DIALOG = "Elephant bonus skipped."

# Turn and victory states.
CHECKING_WIN_DIALOG = "Checking victory conditions..."
GAME_OVER_DIALOG = "The game is over."
TIME_WISH_DIALOG = "Only the time token holder may spend the token by clicking their player panel."
TIME_WISH_RESOLVED_DIALOG = "The time token is destroyed. Please load or start another game."
CONDITION_PANEL_DIALOG = "Victory conditions are shown here."

# Save and load.
NO_SAVE_FILE_DIALOG = "No save file found."
NO_GAME_TO_SAVE_DIALOG = "No game to save."
LOADED_CHOOSE_TOKEN_DIALOG = "Game loaded. Choose the time token owner."
LOAD_FAILED_PREFIX = "Load failed"

# Victory-condition tooltips.
CONDITION_GENERIC_TOOLTIP = "One of the victory conditions. I will not tell you exactly what it means."
CONDITION_GROUP_TOOLTIP = "Satisfying all victory conditions is one way to win."
CONDITION_TRUE_TOOLTIP = "At the end of the previous turn, this condition was satisfied."
CONDITION_FALSE_TOOLTIP = "At the end of the previous turn, this condition was not satisfied."


def saved(filename: str) -> str:
    # Save succeeded.
    return f"Game saved to {filename}."


def loaded(filename: str) -> str:
    # Load succeeded.
    return f"Game loaded from {filename}."


def load_failed(error: Exception | str) -> str:
    # Load failed.
    return f"{LOAD_FAILED_PREFIX}: {error}"


def selected_piece(kind: str) -> str:
    # A piece has been selected.
    return f"Selected {kind}. Choose a target."


def turn_started(player: str) -> str:
    # A new turn has started.
    return f"{player.capitalize()}'s turn."


def token_assigned(owner: str, current_player: str) -> str:
    # The initial time-token owner has been selected.
    return f"Time token assigned to {owner}. {current_player.capitalize()} to move."


def action_complete(message: str, ap: int) -> str:
    # An action is complete, but AP remains.
    return f"{message} Action complete. {ap} AP remaining."


def moved_piece(kind: str) -> str:
    # A non-elephant piece has moved.
    return f"{kind.capitalize()} moved."


def special_bought(kind: str) -> str:
    # A special piece has been bought.
    return f"{kind.capitalize()} bought."


def special_sold(kind: str) -> str:
    # A special piece has been sold.
    return f"{kind.capitalize()} sold."


def mate_loss(loser: str, winner: str) -> str:
    # The current player has no legal actions in a game with no time-token holder.
    return f"{loser.capitalize()} has no legal actions. {winner.capitalize()} wins by mate."


def time_wish_prompt(winner: str, token_owner: str | None) -> str:
    # Check victory prompt before the time-token holder spends the token.
    owner = token_owner.capitalize() if token_owner in {"black", "white"} else "Time token holder"
    return (
        f"{winner.capitalize()} wins, but is this truly the end? "
        f"{owner}, spend the time token and make your wish."
    )


def time_wish_turn(token_owner: str | None) -> str:
    # The special 1 AP turn after a check victory.
    owner = token_owner.capitalize() if token_owner in {"black", "white"} else "Time token holder"
    return f"{owner} has 1 AP. Click your player panel to spend the time token."


def time_wish_saved(filename: str) -> str:
    # The post-wish continuation save starts at G1 with no time-token holder.
    return f"The time token is destroyed. New G1 start save created: {filename}. Please load or start another game."


def choose_cost(kind: str, selected: int, required: int) -> str:
    # Cost selection while buying a special piece.
    return f"Choose squirrels to buy {kind}. Cost selected: {selected}/{required}."


def choose_refund(remaining: int) -> str:
    # Refund placement after selling a special piece.
    return f"Choose refund cells. {remaining} squirrel(s) remaining."


def condition_tooltip(result: bool | None) -> str:
    # Tooltip for a victory-condition icon.
    lines = [CONDITION_GENERIC_TOOLTIP, CONDITION_GROUP_TOOLTIP]
    if result is True:
        lines.append(CONDITION_TRUE_TOOLTIP)
    elif result is False:
        lines.append(CONDITION_FALSE_TOOLTIP)
    return tooltip_lines(lines)


def player_panel_tooltip(player: str, has_token: bool, is_current: bool) -> str:
    # Tooltip for a player panel.
    lines = [f"{player.capitalize()} player panel"]
    if has_token:
        lines.append("Holds the time token.")
        if is_current:
            lines.append("Can buy special pieces from the shop.")
        lines.append("The token holder can have at most 7 pieces.")
    return tooltip_lines(lines)


def piece_tooltip(piece: Any, owner_label: str, can_lion_control: bool, is_new: bool) -> str:
    # Tooltip for a board resource or piece.
    kind = getattr(piece, "kind", "")
    lines: list[str] = []
    if kind == "pine":
        lines.extend([
            f"{owner_label} pinecone",
            "Cannot move.",
            "It seems this is not the only board where pinecones matter.",
        ])
    elif kind == "squirrel":
        lines.extend([
            f"{owner_label} squirrel",
            "Spend 1 AP: move to an adjacent empty cell.",
        ])
        if can_lion_control:
            lines.append("Can be intimidated by a lion and moved for 2 AP.")
        if is_new:
            lines.append("Placed this turn. Cannot actively move this turn.")
    elif kind == "elephant":
        lines.extend([
            f"{owner_label} elephant",
            "Spend 1 AP: move one step and push pieces in that line.",
            "After moving, you may place one free squirrel at its old cell.",
        ])
        if can_lion_control:
            lines.append("Can be intimidated by a lion and moved for 2 AP.")
        if is_new:
            lines.append("Placed this turn. Cannot actively move this turn.")
    elif kind == "lion":
        lines.extend([
            f"{owner_label} lion",
            "Spend 1 AP: move to an adjacent empty cell.",
            "Spend 1 extra AP: intimidate an enemy non-lion piece to move.",
        ])
        if is_new:
            lines.append("Placed this turn. Cannot actively move this turn.")
    return tooltip_lines(lines)


def resource_button_tooltip(resource_id: str, context: dict[str, Any]) -> str:
    # Tooltip for a bottom resource button.
    lines: list[str] = []
    if resource_id == "squirrel":
        lines.append("Spend 3 AP: choose an empty cell and place a squirrel.")
    elif resource_id == "elephant":
        if context.get("selected_own_elephant"):
            lines.append("Spend 1 AP: sell this elephant and place 1 refunded squirrel.")
        else:
            lines.append("Spend 1 AP and 2 squirrels: choose an empty cell and place an elephant.")
            if not context.get("current_player_has_token"):
                lines.append("Requires the time token.")
    elif resource_id == "lion":
        if context.get("selected_own_lion"):
            lines.append("Spend 1 AP: sell this lion and place 2 refunded squirrels.")
        else:
            lines.append("Spend 1 AP and 4 squirrels: choose an empty cell and place a lion.")
            if not context.get("current_player_has_token"):
                lines.append("Requires the time token.")
    elif resource_id == "time_token":
        ap = context.get("current_ap", 0)
        lines.append("Fast-transfer the time token and spend all AP to end the turn.")
        if context.get("any_player_over_piece_limit"):
            lines.append("Unavailable while a player has more than 7 pieces.")
        if ap > 0:
            if ap % 2 == 1:
                lines.append("With odd AP, the token ends with the opponent.")
            else:
                lines.append("With even AP, the token stays with its current holder.")
    return tooltip_lines(lines)


def tooltip_lines(lines: list[str]) -> str:
    return "".join(f"- {line}\n" for line in lines if line)
