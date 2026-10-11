def test_conversations_fold_prefixes(tmp_path):
    from core.models import AgentAnswer, Trace
    from core.observability.replay import ReplayCache

    cache = ReplayCache(tmp_path / "r.json")
    answer = AgentAnswer(
        resolved_question="q", answered=True, answer_markdown="a", confidence="high"
    )

    def trace(msg):
        return Trace(trace_id="t", session_id="s", timestamp="", user_message=msg, model="m")

    cache.put("k1", answer, trace("A"), messages=["A"])
    cache.put("k2", answer, trace("B"), messages=["A", "B"])
    cache.put("k3", answer, trace("C"), messages=["C"])
    assert cache.conversations() == [["A", "B"], ["C"]]
    assert ReplayCache(tmp_path / "r.json").conversations() == [["A", "B"], ["C"]]
