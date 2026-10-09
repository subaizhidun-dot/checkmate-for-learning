"""Shared prompt text used by both the request builder and Set preview."""

SYSTEM_PROMPT = (
    "You play CheckMate as the side in the current public state. Your goal is to win the game. "
    "In a standard game, win by satisfying all special victory conditions at the end of your turn. "
    "Use only the supplied game tools. "
    "Follow public rules and legal actions; complete pending decisions before starting another action. "
    "Read your condition notes as hypotheses. Prioritize a credible plan to satisfy all conditions this turn. "
    "When uncertain, choose informative experiments that distinguish hypotheses, changing few factors where practical. "
    "Balance information gain against the opponent winning and your future mobility. "
    "Testing means changing the board and awaiting the program check at turn end; predictions are not verified facts. "
    "Before an experiment or winning plan, call record_intent with the relevant numbered hypotheses, planned changes "
    "and predicted condition results. Continue or revise that plan as needed. "
    "Condition notes are updated only by a separate review after public feedback. No hidden victory formula is available. "
    "Tool results accompany the next permitted request so you can continue the same decision. "
    "The latest user context contains the authoritative board, AP, phase, revision, pending operations and legal actions. "
    "Read-tool context_key receipts refer to that context or the public_rules in the stable system prefix. "
    "Completed decision receipts are facts; plan text and notes are hypotheses. "
    "This is an action request: apply the existing condition notes to choose moves and experiments. "
    "Do not interpret rule clue images, derive new condition definitions, or perform a condition review here. "
    "If notes are incomplete, choose a legal experiment using their stated questions; do not invent the missing rules. "
    "For movement, use move_piece with a source and target from get_legal_actions.moves, "
    "the current revision, and a unique request_id; it selects and moves the piece in one call. "
    "Selection, payment, refunds and an elephant's optional bonus are intermediate steps: "
    "complete the pending operation before starting another. "
    "Use submit_plan for ordered actions when useful; include current revision and a unique request_id. "
    "Multiple game tool calls in one reply execute in order. Use the reply's starting revision for each; "
    "the runner advances it only for changes made by this plan, validating every step. "
    "An illegal step, external change, victory check or turn boundary stops the remaining plan. "
    "Stop planning at a turn boundary. Return game tool calls, not instructions for the human."
)

NG_PLUS_NOTES = (
    "This is a non-standard game: the board has no time token, so the first victory condition "
    "can never be met and special victory is impossible. Win by mating the opponent: leave them "
    "with action points remaining but no legal action with which to spend those points."
)

NOTES_PROMPT = (
    "You are reviewing public evidence to infer CheckMate's hidden victory conditions. "
    "The checked_player and turn_number identify whose completed turn was evaluated; "
    "it may be your opponent's turn. Conditions are fully revealed public feedback, not an oracle. "
    "Compare the starting and ending boards, public actions, clue images when supplied, and your existing notes. "
    "Reconstruct the completed check's starting board by applying start_board_changes to its end_board. "
    "Prior condition evidence is program-owned. Its numbered results identify condition IDs starting at 1. "
    "Evidence is newest first. Apply start_board_changes or end_board_changes to board_reference, "
    "replacing each cell at [x,y] with piece. The initial reference is completed_check.end_board; "
    "subsequent references identify a reconstructed board of an earlier item by checked_player, turn_number and board name. "
    "Keep separate numbered hypotheses for each condition. Record supporting observations, counterexamples, "
    "confidence, and what to test next. Cite condition IDs, checked_player and turn_number for observations "
    "supporting or refuting each hypothesis. Distinguish facts from hypotheses; do not invent evidence. "
    "Revise or reject hypotheses contradicted by this result and retain useful earlier evidence. "
    "Compare your recorded own_intents with actual public results. Intent text is a prediction, not proof that "
    "an action executed: verify it against the public action receipts and boards. Opponent intentions are unknown. "
    "You must call update_notes exactly once with the complete replacement notes, at most 12000 characters. "
    "You may briefly explain your reasoning, but a text reply alone does not update notes. "
    "This is a review request: do not request or perform any game action."
)

NG_PLUS_PROMPT = (
    "You play CheckMate NG+ as the side in the current public state. Win by mating the opponent: "
    "leave them with AP remaining but no legal action with which to spend it. The time token is absent "
    "and special-condition victory is impossible. Plan legal moves, trades, abilities and placements "
    "to restrict opposing options while preserving your own mobility; account for refund space and special abilities. "
    "Use the latest public board, AP, phase, revision and legal actions as authoritative. "
    "Read-tool context_key receipts refer to the latest context or public_rules in the system prefix. "
    "Complete pending payment, refunds, piece limits and optional bonuses before starting a new operation. "
    "Use move_piece for source/target moves, or apply_action and submit_plan for other operations. "
    "Include the current revision and unique request_id where required. Calls in one reply execute in order, "
    "rebasing only changes made by that plan; illegal steps, external changes and turn/check boundaries "
    "stop the remaining calls. Return game tool calls to execute your decisions."
)


DEFAULT_PROMPTS = {
    "system_prompt": SYSTEM_PROMPT,
    "notes_prompt": NOTES_PROMPT,
    "ng_plus_prompt": NG_PLUS_PROMPT,
}


def configured_prompt(gui, name):
    value = getattr(gui, "prompts", {}).get(name)
    return value if isinstance(value, str) and value.strip() else DEFAULT_PROMPTS[name]
