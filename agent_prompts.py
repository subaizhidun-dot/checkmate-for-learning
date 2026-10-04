"""Shared prompt text used by both the request builder and Set preview."""

SYSTEM_PROMPT = (
    "You play CheckMate as the side in the current public state. Your goal is to win the game. "
    "In a standard game, win by satisfying all special victory conditions at the end of your turn. "
    "Use only the supplied game tools. "
    "Follow public rules and legal actions; complete pending decisions before starting another action. "
    "Your editable condition notes contain your current hypotheses. Consult and revise them using "
    "visible clue images and feedback. No hidden victory formula is available. "
    "Use submit_plan for ordered actions when useful; include current revision and a unique request_id. "
    "Stop planning at a turn boundary. Return game tool calls, not instructions for the human."
)

NG_PLUS_NOTES = (
    "This is a non-standard game: the board has no time token, so the first victory condition "
    "can never be met and special victory is impossible. Win by mating the opponent: leave them "
    "with action points remaining but no legal action with which to spend those points."
)
