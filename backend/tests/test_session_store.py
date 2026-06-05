from backend.app.agent.conversation import ConversationContext
from backend.app.api.session_store import SessionStore


def test_session_store_returns_latest_turn_and_caps_turns():
    now = 1000.0
    store = SessionStore(max_turns=2, clock=lambda: now)

    store.append("s1", _context("q1"))
    store.append("s1", _context("q2"))
    store.append("s1", _context("q3"))

    assert store.get("s1").question == "q3"
    assert [turn.question for turn in store._sessions["s1"].turns] == ["q2", "q3"]


def test_session_store_evicts_expired_sessions():
    now = 1000.0

    def clock():
        return now

    store = SessionStore(ttl_seconds=10, clock=clock)
    store.append("s1", _context("q1"))
    now = 1011.0

    assert store.get("s1") is None


def test_session_store_enforces_lru_session_cap():
    now = 1000.0
    store = SessionStore(max_sessions=2, clock=lambda: now)

    store.append("s1", _context("q1"))
    store.append("s2", _context("q2"))
    store.get("s1")
    store.append("s3", _context("q3"))

    assert list(store._sessions) == ["s1", "s3"]


def _context(question: str) -> ConversationContext:
    return ConversationContext(
        question=question,
        normalized_sql="SELECT 1",
        datasource_name="duckdb_ecommerce",
    )
