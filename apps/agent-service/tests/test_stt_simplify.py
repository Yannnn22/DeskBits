"""语音转写简体化：繁体中文统一转为简体（zhconv）。"""

from pathlib import Path

from desktopmate_agent.config import Settings
from desktopmate_agent.speech.stt import WhisperSTT, simplify_chinese_text


class FakeSegment:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeInfo:
    def __init__(self, language: str, duration: float) -> None:
        self.language = language
        self.duration = duration


class FakeModel:
    def __init__(self, segments: list[FakeSegment], info: FakeInfo) -> None:
        self._segments = segments
        self._info = info

    def transcribe(self, path, *, language, beam_size, vad_filter):  # noqa: ARG002
        return iter(self._segments), self._info


def make_stt(tmp_path: Path, model: FakeModel) -> WhisperSTT:
    settings = Settings(
        trace_dir=tmp_path / "traces",
        settings_dir=tmp_path / "settings",
        whisper_model="tiny",
        whisper_model_dir=tmp_path / "whisper",
        whisper_lang="auto",
        openai_api_key=None,
        deepseek_api_key=None,
    )
    stt = WhisperSTT(settings=settings)
    stt._model = model  # 注入假模型，避免真实加载
    return stt


def test_simplify_chinese_text_converts_traditional_to_simplified():
    assert simplify_chinese_text("語音輸入是繁體中文") == "语音输入是繁体中文"
    assert simplify_chinese_text("今天天氣怎麼樣？請告訴我。") == "今天天气怎么样？请告诉我。"


def test_simplify_chinese_text_keeps_non_han_untouched():
    assert simplify_chinese_text("Hello 世界 123 !@#") == "Hello 世界 123 !@#"
    assert simplify_chinese_text("") == ""


def test_whisper_stt_applies_simplification_for_zh(tmp_path):
    model = FakeModel(
        [FakeSegment("語音輸入"), FakeSegment("是繁體")],
        FakeInfo(language="zh", duration=1.5),
    )
    result = make_stt(tmp_path, model).transcribe(b"fake-wav")
    assert result.text == "语音输入是繁体"
    assert result.language == "zh"


def test_whisper_stt_keeps_non_zh_untouched(tmp_path):
    model = FakeModel(
        [FakeSegment("Hello, world")],
        FakeInfo(language="en", duration=1.0),
    )
    result = make_stt(tmp_path, model).transcribe(b"fake-wav")
    assert result.text == "Hello, world"
