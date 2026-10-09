from __future__ import annotations

from typing import Any


# Startup menu.
STARTUP_DIALOG = "Choose Start > Start G1 / Start G2 / Start G3 or Load a saved game."

# Top menu.
START_MENU_DIALOG = "Start menu."
SAVE_MENU_DIALOG = "Save menu."
LOAD_MENU_DIALOG = "Load menu."
G1_STARTED_DIALOG = "Game 1 started. Choose the time token owner."
G2_STARTED_DIALOG = "Game 2 started. Choose the time token owner."
G2_CONTINUATION_DIALOG = "Game 2 continues without the time token. Black starts."

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
NEW_GAME_PLUS_HINT = "Save this game, then start New Game+."
TIME_WISH_RESOLVED_DIALOG = NEW_GAME_PLUS_HINT
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


def selected_piece(kind: str, can_move=True, can_sell=False, ability=None) -> str:
    options = []
    if can_move:
        options.append("choose a highlighted destination to move")
    if can_sell:
        options.append(f"click the {kind.capitalize()} shop button to sell")
    if ability:
        options.append(f"click this piece again to {ability}")
    return f"Selected {kind}: " + "; or ".join(options) + ". Right-click to cancel."


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
        f"{winner.capitalize()} wins. Human control: click the {owner} player panel "
        "to make the time wish and destroy the time token. This unlocks New Game+."
    )


def time_wish_turn(token_owner: str | None) -> str:
    # The special 1 AP turn after a check victory.
    owner = token_owner.capitalize() if token_owner in {"black", "white"} else "Time token holder"
    return f"{owner} has 1 AP. Click your player panel to spend the time token."



def choose_cost(kind: str, selected: int, required: int) -> str:
    # Cost selection while buying a special piece.
    return f"Choose squirrels to buy {kind}. Cost selected: {selected}/{required}."


def choose_refund(remaining: int) -> str:
    # Refund placement after selling a special piece.
    return f"Choose empty cells for {remaining} refunded squirrel(s). The sold piece's cell is available. Right-click to cancel the sale."


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
    elif kind == "butterfly":
        lines.extend([
            f"{owner_label} butterfly",
            "Spend 1 AP: move to any of the eight adjacent empty cells.",
            "Select, then click again: spend 1 AP to end this turn and check victory.",
            "Start an extra turn with the remaining AP and a new starting formation.",
            "Cannot start another extra turn during that extra turn.",
        ])
        if can_lion_control:
            lines.append("Can be intimidated by a lion and moved for 2 AP.")
        if is_new:
            lines.append("Placed this turn. Cannot move or start an extra turn this turn.")
    elif kind == "lion":
        lines.extend([
            f"{owner_label} lion",
            "Spend 1 AP: move to an adjacent empty cell.",
            "Spend 1 extra AP: intimidate an enemy non-lion piece to move.",
        ])
        if is_new:
            lines.append("Placed this turn. Cannot actively move this turn.")
    elif kind == "mole":
        lines.extend([
            f"{owner_label} mole",
            "Spend 1 AP: move to an orthogonally adjacent empty cell.",
            "Spend 1 AP: plant an own tree on a red marker, from any mole position.",
            "With an own lion too: plant an enemy tree on a red marker for 2 AP.",
        ])
        if can_lion_control:
            lines.append("Can be intimidated by a lion and moved for 2 AP.")
        if is_new:
            lines.append("Placed this turn. Cannot actively move this turn.")
    elif kind == "tree":
        if piece.rooted:
            lines.extend([
                f"{owner_label} rooted tree",
                "Cannot move or be removed. Counts as one piece.",
                "Its owner can select, then click again to uproot for 1 AP. No mole needed.",
                "With an own lion, select an enemy tree and click again to uproot for 2 AP.",
            ])
        else:
            lines.extend([
                f"{owner_label} uprooted tree",
                "Cannot be bought, sold or removed. Counts as one piece.",
                "Spend 1 AP: move to a diagonally adjacent empty cell.",
                "On a red marker with an own mole: select, then click again to plant for 1 AP.",
                "Plant an enemy tree for 2 AP with both an own mole and an own lion.",
                "Left marker shifts the lower-left corner; right marker shifts the upper-right.",
            ])
            if can_lion_control:
                lines.append("Can be intimidated by a lion and moved for 2 AP.")
    return tooltip_lines(lines)


def resource_button_tooltip(resource_id: str, context: dict[str, Any]) -> str:
    # Tooltip for a bottom resource button.
    lines: list[str] = []
    if resource_id == "squirrel":
        lines.append("Spend 3 AP: choose an empty cell and place a squirrel.")
    elif resource_id in {"elephant", "mole"}:
        if context.get(f"selected_own_{resource_id}"):
            lines.append(f"Spend 1 AP: sell this {resource_id} and place 1 refunded squirrel.")
        else:
            lines.append(f"Spend 1 AP and 2 squirrels: choose an empty cell and place a {resource_id}.")
            if not context.get("current_player_has_token"):
                lines.append("Requires the time token.")
    elif resource_id in {"lion", "butterfly"}:
        if context.get(f"selected_own_{resource_id}"):
            lines.append(f"Spend 1 AP: sell this {resource_id} and place 2 refunded squirrels.")
        else:
            lines.append(f"Spend 1 AP and 4 squirrels: choose an empty cell and place a {resource_id}.")
            if not context.get("current_player_has_token"):
                lines.append("Requires the time token.")
    elif resource_id == "time_token":
        ap = context.get("current_ap", 0)
        lines.append("Fast-transfer the time token and spend all AP to end the turn.")
        if context.get("any_player_over_piece_limit"):
            lines.append("Unavailable while a player has more than 7 pieces.")
        if ap > 0:
            holder = context.get("time_token_owner")
            if holder in {"black", "white"}:
                destination = ("white" if holder == "black" else "black") if ap % 2 else holder
                lines.append(f"After spending {ap} AP, {destination.capitalize()} will hold the token.")
            else:
                lines.append("Unavailable: this game has no time token.")
    return tooltip_lines(lines)


def tooltip_lines(lines: list[str]) -> str:
    return "".join(f"- {line}\n" for line in lines if line)
