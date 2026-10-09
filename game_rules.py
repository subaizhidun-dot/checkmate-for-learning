"""English public instructions, selected by the current game mode.

Victory clues are supplied separately as the displayed images. This module
does not import or describe the hidden victory predicates.
"""
from copy import deepcopy


COMMON = [
    "The board has 5 by 5 playable cells. Coordinates use x to the right and y downward.",
    "Black starts with 1 AP. Follow the current action phase and its legal action list.",
    "An own movable piece normally moves one adjacent step for 1 AP. Newly placed pieces cannot actively move in that turn.",
    "Squirrels and lions move orthogonally into empty playable cells.",
    "Placing a squirrel costs 3 AP.",
    "Buying a special piece requires the current player to hold the time token. Each purchase or sale costs 1 AP.",
    "Each side may own at most one elephant, one lion, one mole and one butterfly. Only the current mode's shop is available.",
    "Purchases pay with own squirrels. Sales refund squirrels, placed one at a time. Complete the payment or refund phase, or cancel where offered.",
    "An own lion allows movement of enemy non-lion movable pieces for 2 AP.",
    "Passing the time token costs 1 AP. The fast option repeats this until AP runs out. The token holder must resolve the seven-piece limit before continuing.",
    "When a turn ends, the game checks victory and displays clue feedback. A normal next turn increases maximum AP by 1 and starts with that AP allowance.",
    "Without a time token, a side with AP remaining and no legal action can lose by mate.",
    "The displayed clue images describe the special victory puzzle. Infer their meaning from play and visible feedback.",
]

ELEPHANT = [
    "An elephant moves one orthogonal step for 1 AP and pushes a contiguous line of pieces when an empty playable cell exists beyond it.",
    "After an elephant move, place an optional free squirrel in an offered cell, or skip the bonus.",
]

MODE_RULES = {
    1: {
        "initial_setup": "Each side starts with three squirrels; each also has an outer pinecone.",
        "shop": {"elephant": {"buy": 2, "sell": 1}, "lion": {"buy": 4, "sell": 2}},
        "instructions": ELEPHANT + ["G1 victories end the game directly."],
    },
    2: {
        "initial_setup": "Each side starts with two squirrels and one rooted tree; there are no outer pinecones.",
        "shop": {"mole": {"buy": 2, "sell": 1}, "lion": {"buy": 4, "sell": 2}},
        "instructions": [
            "A tree counts as a piece and cannot be bought, sold or removed to resolve the piece limit. A rooted tree cannot move.",
            "Uproot an own tree for 1 AP, or an enemy tree for 2 AP with an own lion; no mole is needed and the tree keeps its owner. An uprooted tree moves one diagonal step into an empty playable cell for 1 AP, or 2 AP under enemy lion control.",
            "Once a tree leaves an initial tree position, a red marker remains underneath occupying pieces.",
            "A mole moves one orthogonal step into an empty playable cell for 1 AP.",
            "With an own mole anywhere on the board, plant an own uprooted tree on either red marker for 1 AP. With an own mole and an own lion, plant an enemy tree there for 2 AP; its owner stays the same.",
            "Uprooting at the left marker shifts the lower-left corner downward; uprooting at the right marker shifts the upper-right corner upward. The tile carries its occupant. Its vacated cell becomes an unusable rift; the shifted tile remains playable.",
            "Planting at the left or right marker restores its corresponding corner and occupant. This link follows the planting position rather than the tree's owner.",
            "After a G2 victory, the human user completes the time wish and destroys the time token. Model play stops for this phase. The completed wish unlocks New Game+; save the ended game before starting it.",
        ],
    },
    3: {
        "initial_setup": "Each side starts with two squirrels and one elephant.",
        "shop": {"elephant": {"buy": 2, "sell": 1}, "lion": {"buy": 4, "sell": 2},
                 "butterfly": {"buy": 4, "sell": 2}},
        "instructions": ELEPHANT + [
            "A butterfly moves to any of the eight adjacent empty playable cells for 1 AP, or 2 AP under enemy lion control.",
            "Activate an own butterfly for 1 AP to end the turn and check victory. If victory fails and AP remains, start an extra turn with the remaining AP and time token. This does not refill AP or increase maximum AP.",
            "The extra turn has a new starting position. Newly placed piece restrictions reset. No butterfly can start another extra turn within that extra turn.",
            "A newly placed butterfly cannot move or activate its ability in that turn. Enemy lion control permits butterfly movement only.",
            "After a G3 victory, the human user completes the time wish and destroys the time token. Model play stops for this phase. The completed wish unlocks New Game+; save the ended game before starting it.",
        ],
    },
}


def rules_for_game(gamemode):
    rules = deepcopy(MODE_RULES[gamemode])
    rules["gamemode"] = gamemode
    rules["instructions"] = COMMON + rules["instructions"]
    return rules
