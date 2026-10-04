"""Automatic checkpoints for desktop and scripted play."""
from dataclasses import dataclass, field


@dataclass
class AutosavePolicy:
    agent_play_mode: bool = False
    llm_colors: frozenset[str] = field(default_factory=lambda: frozenset({"white"}))
    _saved: tuple | None = None
    _session: object | None = None
    _llm_turn_pending: bool = False

    def configure(self, enabled: bool, llm_colors=("white",)):
        colors = frozenset(llm_colors)
        if not colors or not colors <= {"black", "white"}:
            raise ValueError("LLM sides must be black or white")
        self.agent_play_mode = bool(enabled)
        self.llm_colors = colors
        self._llm_turn_pending = False

    def should_save(self, session):
        if session is not self._session:
            self._session = session
            self._llm_turn_pending = False
        if session.game is None:
            return False
        player = session.game.current_player
        if self.agent_play_mode and player in self.llm_colors:
            self._llm_turn_pending = True
        if session.flow.phase == "checking_win" or session.is_over():
            return False
        if self.agent_play_mode and session.flow.phase != "choose_token":
            if player in self.llm_colors:
                return False
            # Over-limit decisions and wishes are not the start of a human turn.
            if self._llm_turn_pending and session.flow.phase != "playing":
                return False
        return self._saved != (session, session.revision, session.flow.phase, session.llm_usage.revision)

    def mark_saved(self, session):
        self._saved = (session, session.revision, session.flow.phase, session.llm_usage.revision)
        self._llm_turn_pending = False
