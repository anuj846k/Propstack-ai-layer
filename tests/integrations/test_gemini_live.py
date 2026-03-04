import base64

from app.integrations.gemini_live import _normalize_response


class _Obj:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def test_normalize_response_decodes_base64_audio_fields() -> None:
    pcm = b"\x01\x02\x03\x04"
    b64 = base64.b64encode(pcm).decode("ascii")

    response = _Obj(
        data=b64,
        server_content=_Obj(
            interrupted=False,
            model_turn=_Obj(
                parts=[
                    _Obj(
                        text="hi",
                        inline_data=_Obj(data=b64),
                    )
                ]
            ),
        ),
    )

    events = _normalize_response(response)
    audio_events = [e for e in events if e.get("type") == "audio"]
    text_events = [e for e in events if e.get("type") == "text"]

    assert len(audio_events) >= 1
    assert all(isinstance(e["audio"], bytes) for e in audio_events)
    assert any(e.get("text") == "hi" for e in text_events)
