from __future__ import annotations

import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field

from backend.app.agent.conversation import ConversationContext


@dataclass
class SessionState:
    turns: deque[ConversationContext] = field(default_factory=deque)
    last_updated: float = field(default_factory=time.time)


class SessionStore:
    def __init__(
        self,
        *,
        ttl_seconds: int = 1800,
        max_turns: int = 5,
        max_sessions: int = 500,
        clock=time.time,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_turns = max_turns
        self.max_sessions = max_sessions
        self._clock = clock
        self._sessions: OrderedDict[str, SessionState] = OrderedDict()

    def get(self, session_id: str) -> ConversationContext | None:
        self.evict_expired()
        state = self._sessions.get(session_id)
        if state is None or not state.turns:
            return None
        state.last_updated = self._clock()
        self._sessions.move_to_end(session_id)
        return state.turns[-1]

    def append(self, session_id: str, context: ConversationContext) -> None:
        self.evict_expired()
        state = self._sessions.get(session_id)
        if state is None:
            state = SessionState(turns=deque(maxlen=self.max_turns))
            self._sessions[session_id] = state
        state.turns.append(context)
        state.last_updated = self._clock()
        self._sessions.move_to_end(session_id)
        self._enforce_session_cap()

    def evict_expired(self) -> None:
        now = self._clock()
        expired = [
            session_id
            for session_id, state in self._sessions.items()
            if now - state.last_updated > self.ttl_seconds
        ]
        for session_id in expired:
            self._sessions.pop(session_id, None)

    def _enforce_session_cap(self) -> None:
        while len(self._sessions) > self.max_sessions:
            self._sessions.popitem(last=False)
