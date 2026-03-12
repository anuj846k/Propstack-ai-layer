import json


class TranscriptCollector:
    """Collects conversation transcript in JSON format for call logging."""

    def __init__(self):
        self.parts: list[dict] = []
        self._finalized_texts: set[str] = set()

    def _is_duplicate(self, text: str) -> bool:
        return text in self._finalized_texts

    def _mark_finalized(self, text: str):
        self._finalized_texts.add(text)

    def add_user_speech(self, text: str, is_final: bool = True):
        if text and text.strip() and is_final:
            if self._is_duplicate(text.strip()):
                return
            self._mark_finalized(text.strip())
            self.parts.append(
                {"speaker": "user", "text": text.strip(), "is_final": is_final}
            )

    def add_ai_speech(self, text: str, is_final: bool = True):
        if text and text.strip() and is_final:
            if self._is_duplicate(text.strip()):
                return
            self._mark_finalized(text.strip())
            self.parts.append(
                {"speaker": "sara", "text": text.strip(), "is_final": is_final}
            )

    def add_interruption(self):
        self.parts.append(
            {"speaker": "system", "text": "[User interrupted]", "is_final": True}
        )

    def add_error(self, error: str):
        self.parts.append(
            {"speaker": "system", "text": f"[Error: {error}]", "is_final": True}
        )

    def get_transcript_json(self) -> str:
        return json.dumps(self.parts, ensure_ascii=False, indent=2)

    def get_transcript_text(self) -> str:
        lines = []
        for part in self.parts:
            speaker = part.get("speaker", "unknown")
            text = part.get("text", "")
            if speaker == "user":
                lines.append(f"User: {text}")
            elif speaker == "sara":
                lines.append(f"Sara: {text}")
            else:
                lines.append(text)
        return "\n".join(lines)

    def get_transcript(self) -> str:
        return self.get_transcript_text()
