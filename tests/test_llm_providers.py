"""Provider contract: error classification, wire parsing, and the mock's honesty."""

import json

import pytest

from munshi.llm import build_provider
from munshi.llm.base import (
    LLMMalformed,
    LLMProvider,
    LLMRateLimited,
    LLMTimeout,
    LLMUnavailable,
    ToolSpec,
)
from munshi.llm.groq_provider import GroqProvider, _classify, salvage_json
from munshi.llm.mock_provider import MockProvider


class _Fn:
    def __init__(self, name, arguments):
        self.name, self.arguments = name, arguments


class _TC:
    def __init__(self, id, name, arguments):
        self.id, self.function = id, _Fn(name, arguments)


class _Msg:
    def __init__(self, content=None, tool_calls=None):
        self.content, self.tool_calls = content, tool_calls


class _Choice:
    def __init__(self, msg, finish="stop"):
        self.message, self.finish_reason = msg, finish


class _Resp:
    def __init__(self, msg, finish="stop"):
        self.choices = [_Choice(msg, finish)]
        self.usage = type("U", (), {"prompt_tokens": 11, "completion_tokens": 22})()


class _Client:
    """Stands in for groq.Groq; records what would have gone over the wire."""

    def __init__(self, resp=None, raises=None):
        self._resp, self._raises = resp, raises
        self.sent = None
        self.chat = type("C", (), {"completions": self})()

    def create(self, **kw):
        self.sent = kw
        if self._raises:
            raise self._raises
        return self._resp


def provider(resp=None, raises=None):
    c = _Client(resp, raises)
    return GroqProvider(client=c), c


# ---------------------------------------------------------------------------
def test_providers_satisfy_the_protocol():
    assert isinstance(MockProvider(), LLMProvider)
    p, _ = provider(_Resp(_Msg("hi")))
    assert isinstance(p, LLMProvider)


def test_tools_are_sent_in_the_openai_function_shape():
    p, c = provider(_Resp(_Msg("ok")))
    spec = ToolSpec("t", "does a thing", {"type": "object", "properties": {},
                                          "required": [], "additionalProperties": False})
    p.chat([{"role": "user", "content": "x"}], [spec])
    assert c.sent["tools"][0]["type"] == "function"
    assert c.sent["tools"][0]["function"]["name"] == "t"
    assert c.sent["model"] == p.model
    assert c.sent["max_completion_tokens"] > 0


def test_tool_calls_are_parsed_with_their_ids_intact():
    """A regenerated id silently breaks the tool-result round trip."""
    msg = _Msg(None, [_TC("call_abc", "check_policy", '{"action_type":"retry_payment"}')])
    p, _ = provider(_Resp(msg, "tool_calls"))
    turn = p.chat([], [])
    assert turn.wants_tools
    assert turn.tool_calls[0].id == "call_abc"
    assert turn.tool_calls[0].arguments == {"action_type": "retry_payment"}
    echoed = turn.raw_message["tool_calls"][0]
    assert echoed["id"] == "call_abc"
    assert json.loads(echoed["function"]["arguments"]) == {"action_type": "retry_payment"}


def test_unparseable_tool_arguments_are_surfaced_not_guessed():
    msg = _Msg(None, [_TC("c1", "check_policy", "{not json")])
    p, _ = provider(_Resp(msg, "tool_calls"))
    with pytest.raises(LLMMalformed, match="unparseable arguments"):
        p.chat([], [])


def test_non_object_tool_arguments_are_rejected():
    msg = _Msg(None, [_TC("c1", "check_policy", "[1,2,3]")])
    p, _ = provider(_Resp(msg, "tool_calls"))
    with pytest.raises(LLMMalformed, match="not an object"):
        p.chat([], [])


def test_a_response_with_no_choices_is_malformed():
    empty = type("R", (), {"choices": []})()
    p, _ = provider(empty)
    with pytest.raises(LLMMalformed, match="no choices"):
        p.chat([], [])


@pytest.mark.parametrize("exc,expected", [
    (type("RateLimitError", (Exception,), {})("slow down"), LLMRateLimited),
    (type("APITimeoutError", (Exception,), {})("deadline"), LLMTimeout),
    (type("AuthenticationError", (Exception,), {})("bad key"), LLMUnavailable),
    (type("BadRequestError", (Exception,), {})("schema"), LLMMalformed),
    (type("APIConnectionError", (Exception,), {})("refused"), LLMTimeout),
    (RuntimeError("something else entirely"), LLMUnavailable),
])
def test_provider_exceptions_are_classified(exc, expected):
    assert isinstance(_classify(exc), expected)


def test_http_status_codes_are_classified():
    e = RuntimeError("boom")
    e.status_code = 429
    assert isinstance(_classify(e), LLMRateLimited)
    e2 = RuntimeError("boom")
    e2.status_code = 503
    assert isinstance(_classify(e2), LLMUnavailable)


def test_a_raw_provider_exception_never_escapes():
    p, _ = provider(raises=RuntimeError("kaboom"))
    with pytest.raises(LLMUnavailable):
        p.chat([], [])


def test_groq_refuses_to_construct_without_a_key(monkeypatch):
    from munshi.config import settings

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    settings.cache_clear()
    try:
        with pytest.raises(LLMUnavailable, match="GROQ_API_KEY"):
            GroqProvider()
    finally:
        settings.cache_clear()


# ---------------------------------------------------------------------------
def test_salvage_recovers_a_json_object_from_prose():
    assert salvage_json('Sure! {"action_type":"retry_payment"} Hope this helps.') == {
        "action_type": "retry_payment"}


@pytest.mark.parametrize("text", [None, "", "no json here at all", "{broken", "[1,2]"])
def test_salvage_never_invents_anything(text):
    assert salvage_json(text) is None


# ---------------------------------------------------------------------------
def test_the_factory_picks_mock_without_a_key(monkeypatch):
    from munshi.config import settings

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    settings.cache_clear()
    try:
        assert build_provider().name == "mock"
    finally:
        settings.cache_clear()


def test_the_mock_never_claims_to_be_a_model():
    m = MockProvider()
    assert m.name == "mock" and "mock" in m.model


def test_the_mock_can_be_scripted_for_adversarial_cases():
    from munshi.llm.base import LLMTurn

    m = MockProvider(script=[lambda msgs: LLMTurn(text="nope")])
    assert m.chat([]).text == "nope"
    assert m.chat([]).text == "no further scripted turns"
