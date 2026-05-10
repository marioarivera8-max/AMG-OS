import numpy as np


class _Resp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {"message": {"content": "ok"}}
        self.text = str(self._body)

    def json(self):
        return self._body


class _Session:
    def __init__(self):
        self.posts = []

    def post(self, _url, json=None, timeout=None):
        self.posts.append({"json": json, "timeout": timeout})
        return _Resp()

    def get(self, _url, timeout=None):
        return _Resp(200, {"models": []})


class _RetrySession:
    def __init__(self, responses):
        self.posts = []
        self._responses = list(responses)

    def post(self, _url, json=None, timeout=None):
        self.posts.append({"json": json, "timeout": timeout})
        if self._responses:
            return self._responses.pop(0)
        return _Resp()

    def get(self, _url, timeout=None):
        return _Resp(200, {"models": []})


def test_ai_client_routes_vision_and_text_models_separately():
    from amg.scoring.ai_client import AIClient

    client = AIClient(model="vision-model-x", text_model="text-model-y")
    session = _Session()
    client._session = session

    frame = np.zeros((16, 16, 3), dtype=np.uint8)
    r1 = client.score_frame(frame, "score this")
    assert r1.success is True
    assert session.posts[-1]["json"]["model"] == "vision-model-x"

    r2 = client.generate_text("write metadata")
    assert r2.success is True
    assert session.posts[-1]["json"]["model"] == "text-model-y"


def test_ai_client_uses_env_overrides_for_both_models(monkeypatch):
    monkeypatch.setenv("AMG_VISION_MODEL_OVERRIDE", "vision-env-model")
    monkeypatch.setenv("AMG_TEXT_MODEL_OVERRIDE", "text-env-model")

    from amg.scoring.ai_client import AIClient

    client = AIClient()
    assert client.vision_model == "vision-env-model"
    assert client.text_model == "text-env-model"


def test_ai_client_text_generation_falls_back_to_secondary_model():
    from amg.scoring.ai_client import AIClient

    client = AIClient(
        model="vision-model-x",
        text_model="text-primary",
        text_model_fallback="text-fallback",
    )
    session = _RetrySession(
        responses=[
            _Resp(404, {"error": "model not found"}),
            _Resp(200, {"message": {"content": "fallback output"}}),
        ]
    )
    client._session = session

    result = client.generate_text("write metadata")

    assert result.success is True
    assert result.raw_text == "fallback output"
    assert session.posts[0]["json"]["model"] == "text-primary"
    assert session.posts[1]["json"]["model"] == "text-fallback"


def test_ai_client_structured_text_sends_schema_format():
    from amg.scoring.ai_client import AIClient

    client = AIClient(model="vision-model-x", text_model="text-model-y")
    session = _Session()
    client._session = session
    schema = {
        "type": "object",
        "properties": {"title": {"type": "string"}},
        "required": ["title"],
    }

    result = client.generate_structured_text("write metadata", schema)

    assert result.success is True
    assert session.posts[-1]["json"]["model"] == "text-model-y"
    assert session.posts[-1]["json"]["format"] == schema
