"""Live mode's pipeline: voice activity detection, phrase chunking, and a whole conversation turn with fake engines."""
from __future__ import annotations

import array
import asyncio
import math

import pytest

from app import live as L

FRAME = 320


def tone(frames: int, amp: int = 9000) -> bytes:
    a = array.array("h", (int(amp * math.sin(i * 0.3)) for i in range(FRAME * frames)))
    return a.tobytes()


def quiet(frames: int) -> bytes:
    return bytes(FRAME * 2 * frames)


# ---------------------------------------------------------------- VAD
def test_rms_db_levels():
    assert L.rms_db(b"") == -96.0 and L.rms_db(bytes(640)) == -96.0 and L.rms_db(b"\x01") == -96.0
    assert -12 < L.rms_db(tone(1, 20000)) < -3 and L.rms_db(tone(1, 300)) < -35


def test_silence_makes_no_events_and_a_sentence_makes_start_and_end():
    v = L.Vad()
    assert v.feed(quiet(50)) == []
    ev = v.feed(tone(30)) + v.feed(quiet(30))
    assert [e[0] for e in ev] == ["start", "end"]
    pcm, ms = ev[1][1], ev[1][2]
    assert ms >= 590 and len(pcm) > 30 * FRAME * 2                            # includes the pre-roll and the trailing silence up to the hangover
    assert not v.speaking and v.feed(b"") == []


def test_a_cough_is_discarded_and_odd_sized_chunks_are_reassembled():
    v = L.Vad()
    data = quiet(20) + tone(6) + quiet(30)
    ev = []
    for i in range(0, len(data), 777):                                        # frames split across calls
        ev += v.feed(data[i:i + 777])
    assert [e[0] for e in ev] == ["start", "discard"]


def test_a_short_sound_never_starts_and_noise_is_learned():
    v = L.Vad()
    assert v.feed(quiet(10) + tone(3) + quiet(10)) == []                    # 60 ms of sound: below the start threshold
    hum = array.array("h", (int(120 * math.sin(i)) for i in range(FRAME * 100))).tobytes()      # about -52 dB: a quiet room
    assert v.feed(hum) == [] and v.noise > -60 and not v.speaking            # the floor rises to meet it; it never counts as speech


def test_while_the_assistant_talks_the_detector_is_stricter():
    v = L.Vad()
    v.feed(quiet(20))
    v.busy = True
    assert v.feed(tone(8)) == []                                              # 160 ms is not enough to interrupt (240 ms needed)
    ev = v.feed(tone(10))
    assert [e[0] for e in ev] == ["start"]


def test_the_length_limit_and_a_manual_finish_end_an_utterance():
    v = L.Vad(L.VadConfig(max_ms=400))
    ev = v.feed(tone(60))
    assert [e[0] for e in ev[:2]] == ["start", "end"] and len(ev[1][1]) >= 400 * 32      # cut at the limit even though the person is still talking
    v2 = L.Vad()
    v2.feed(quiet(10) + tone(20))
    assert v2.speaking
    ev2 = v2.finish()
    assert ev2[0][0] == "end" and v2.finish() == []


# ---------------------------------------------------------------- phrases
def feed_all(pieces, **kw):
    c = L.Chunker(**kw)
    out: list[str] = []
    for p in pieces:
        out += c.feed(p)
    return out + c.flush()


def test_the_first_phrase_stops_at_a_comma_or_after_six_words():
    assert feed_all(["Sure, I can help with that. Next one here."])[:2] == ["Sure, I can help with that.", "Next one here."]
    assert feed_all(["Well, hello there my friend, how are you doing today?"])[0] == "Well, hello there my friend,"
    assert feed_all(["One two three four five six seven eight nine."])[0] == "One two three four five six"
    assert feed_all(["Hi. Yes. And then a longer sentence follows here."])[0] == "Hi."


def test_later_phrases_wait_for_sentence_ends_and_skip_abbreviations_decimals_and_list_numbers():
    out = feed_all(["The moon is far away from here today, ok. It is 3.5 times smaller, said Dr. Smith. Done."])
    assert out == ["The moon is far away from", "here today, ok.", "It is 3.5 times smaller, said Dr. Smith.", "Done."]
    assert feed_all(["1. First thing to do here now. 2. Second."])[0].startswith("1. First")


def test_newlines_markdown_emoji_and_symbol_only_pieces():
    out = feed_all(["Here is **bold** text 😀\nNext line goes here.\n\n...\n"])
    assert out == ["Here is bold text", "Next line goes here."]
    assert feed_all(["", "  "]) == []


def test_reasoning_blocks_are_never_spoken_even_when_split_across_pieces():
    assert feed_all(["<think>hmm, let me think. ok</think>Hello there, friend. Bye."])[0] == "Hello there, friend."
    assert feed_all(["Hello there friend, <think>secret", " reasoning that never ends"]) == ["Hello there friend,"]
    assert feed_all(["<think>a</think><think>b</think>Fine."]) == ["Fine."]


def test_a_long_run_with_no_pause_is_split_at_a_space():
    c = L.Chunker(max_chars=40, first_soft=999)
    out = c.feed("word " * 30)
    assert out and all(len(x) <= 40 for x in out)


def test_junk_transcripts():
    assert L.junk_transcript("") and L.junk_transcript("...") and L.junk_transcript("[BLANK_AUDIO]") and L.junk_transcript("(music)")
    assert L.junk_transcript("Thank you.", 800) and not L.junk_transcript("Thank you.", 5000)
    assert not L.junk_transcript("What is the weather?") and not L.junk_transcript("Hello there")


def test_resampling_keeps_the_pitch_and_length_ratio():
    a = array.array("h", [0, 3000, 6000, 3000, 0, -3000] * 100)
    out = array.array("h")
    out.frombytes(L.resample_24k_to_16k(a.tobytes()))
    assert len(out) == len(a) * 2 // 3 and out[0] == 0 and max(out) <= 6000
    assert L.resample_24k_to_16k(b"") == b""


# ---------------------------------------------------------------- the session
class FStream:
    def __init__(self, chunks, gate: asyncio.Event | None = None, fail=False):
        self.chunks, self.gate, self.fail = chunks, gate, fail
        self.cancelled = self.closed = False

    def cancel(self):
        self.cancelled = True

    def close(self):
        self.closed = True

    async def __aiter__(self):
        for i, c in enumerate(self.chunks):
            if self.fail:
                raise RuntimeError("voice failed")
            if self.gate is not None and i == 1:
                await self.gate.wait()
            yield c


class FEngine:
    def __init__(self, text="what is the moon", gate=None, fail_tts=False, fail_stt=False):
        self.text, self.gate, self.fail_tts, self.fail_stt = text, gate, fail_tts, fail_stt
        self.warmed = 0
        self.spoken: list[tuple] = []
        self.streams: list[FStream] = []
        self.stt_calls = 0

    async def warm(self):
        self.warmed += 1
        return {"device": "cpu"}

    async def stt(self, pcm):
        self.stt_calls += 1
        if self.fail_stt:
            raise RuntimeError("recogniser down")
        return {"text": self.text, "ms": 60, "audio_ms": len(pcm) // 32}

    async def tts(self, text, voice, speed):
        self.spoken.append((text, voice, speed))
        s = FStream([b"\x01\x00" * 240, b"\x02\x00" * 240], self.gate, self.fail_tts)
        self.streams.append(s)
        return s


class FLlm:
    local = True

    def __init__(self, pieces=("The moon is a rock, ", "that circles Earth. ", "It is quiet there."), error=None, gate=None):
        self.pieces, self.error, self.gate = pieces, error, gate
        self.warmed = 0
        self.calls: list[list[dict]] = []

    async def warm(self):
        self.warmed += 1

    async def stream(self, messages, max_tokens=300):
        self.calls.append(messages)
        if self.error:
            raise RuntimeError(self.error)
        for i, p in enumerate(self.pieces):
            if self.gate is not None and i == 1:
                await self.gate.wait()
            yield p


def make_session(engine=None, llm=None, cfg=None):
    sent: list = []
    audio: list[bytes] = []

    async def send_json(m):
        sent.append(m)

    async def send_audio(b):
        audio.append(b)
    s = L.LiveSession(engine or FEngine(), llm or FLlm(), send_json, send_audio, cfg)
    return s, sent, audio


async def say_something(s):
    await s.audio(quiet(10) + tone(30) + quiet(25))
    await s.wait()


def types(sent):
    return [m["type"] for m in sent]


def states(sent):
    return [m["state"] for m in sent if m["type"] == "state"]


@pytest.mark.asyncio
async def test_start_warms_both_engines_and_announces_readiness():
    eng, llm = FEngine(), FLlm()
    s, sent, _ = make_session(eng, llm)
    msg = await s.start()
    assert eng.warmed == 1 and llm.warmed == 1 and s.state == "listening"
    assert states(sent) == ["loading", "listening"] and msg["type"] == "ready" and msg["local_model"] is True and msg["engine"] == {"device": "cpu"}


@pytest.mark.asyncio
async def test_audio_before_the_session_is_ready_is_ignored():
    s, sent, _ = make_session()
    await s.audio(tone(30) + quiet(30))
    assert sent == [] and s.task is None


@pytest.mark.asyncio
async def test_a_spoken_question_gets_a_spoken_answer_with_timings():
    eng, llm = FEngine(), FLlm()
    s, sent, audio = make_session(eng, llm)
    await s.start()
    sent.clear()
    await say_something(s)
    assert states(sent) == ["hearing", "thinking", "speaking", "listening"]
    t = types(sent)
    assert t.index("transcript") < t.index("assistant") < t.index("metrics")
    assert [m for m in sent if m["type"] == "transcript"][0]["text"] == "what is the moon"
    final = [m for m in sent if m.get("final")][0]
    assert final["text"] == "The moon is a rock, that circles Earth. It is quiet there."
    assert [x[0] for x in eng.spoken] == ["The moon is a rock,", "that circles Earth.", "It is quiet there."]
    assert eng.spoken[0][1:] == ("af_heart", 1.0) and len(audio) == 6
    m = [m for m in sent if m["type"] == "metrics"][0]
    assert m["turn"] == 1 and m["endpoint_ms"] == 350 and m["stt_ms"] == 60 and m["user_perceived_ms"] == m["ttfa_ms"] + 350
    for k in ("llm_first_token_ms", "first_chunk_ms", "tts_first_ms", "total_ms", "stt_wall_ms"):
        assert isinstance(m[k], int)
    assert llm.calls[0][0]["role"] == "system" and "by voice" in llm.calls[0][0]["content"] and llm.calls[0][-1] == {"role": "user", "content": "what is the moon"}
    assert s.history == [{"role": "user", "content": "what is the moon"}, {"role": "assistant", "content": "The moon is a rock, that circles Earth. It is quiet there."}]
    assert len(s.metrics) == 1 and s.metrics[0]["turn"] == 1


@pytest.mark.asyncio
async def test_the_agents_instructions_lead_the_system_prompt_and_history_is_capped():
    llm = FLlm(pieces=("Okay.",))
    s, sent, _ = make_session(llm=llm, cfg=L.LiveConfig(instruction="You are Pirate Pete.", max_history=4))
    await s.start()
    for _ in range(4):
        await say_something(s)
    assert llm.calls[0][0]["content"].startswith("You are Pirate Pete.\n\n")
    assert len(s.history) == 4 and len(llm.calls[-1]) == 1 + 4 + 1          # system prompt + the last four messages + the new question


@pytest.mark.asyncio
async def test_noise_that_the_recogniser_hears_as_words_is_not_answered():
    eng, llm = FEngine(text="Thank you."), FLlm()
    s, sent, _ = make_session(eng, llm)
    await s.start()
    await s.audio(quiet(10) + tone(15) + quiet(25))                           # 300 ms: a short sound that Whisper turns into "Thank you."
    await s.wait()
    assert llm.calls == [] and s.state == "listening" and "transcript" not in types(sent)


@pytest.mark.asyncio
async def test_a_typed_message_skips_the_recogniser():
    eng, llm = FEngine(), FLlm(pieces=("Sure thing.",))
    s, sent, _ = make_session(eng, llm)
    await s.start()
    await s.say("hello")
    await s.wait()
    assert eng.stt_calls == 0 and [m for m in sent if m["type"] == "metrics"][0]["endpoint_ms"] == 0 and eng.spoken[0][0] == "Sure thing."
    await s.say("again")                                                       # a second typed message while idle
    await s.wait()
    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_push_to_talk_commit_ends_the_utterance_now():
    s, sent, _ = make_session()
    await s.start()
    await s.audio(quiet(10) + tone(20))
    assert s.state == "hearing"
    await s.commit()
    await s.wait()
    assert "metrics" in types(sent)
    await s.commit()                                                           # nothing to commit: harmless


@pytest.mark.asyncio
async def test_talking_over_the_assistant_stops_it_and_keeps_only_what_was_said():
    gate = asyncio.Event()
    eng, llm = FEngine(gate=gate), FLlm()
    s, sent, audio = make_session(eng, llm)
    await s.start()
    await s.audio(quiet(10) + tone(30) + quiet(25))                           # the question: the answer starts, then parks at the gate mid-phrase
    for _ in range(100):
        if s.state == "speaking":
            break
        await asyncio.sleep(0.01)
    assert s.state == "speaking" and s.vad.busy
    sent.clear()
    await s.audio(tone(15))                                                    # the person talks over it
    assert s.state == "hearing"
    assert "clear" in types(sent) and states(sent) == ["listening", "hearing"]
    assert eng.streams[0].closed and s.task.done()
    assert s.history[-2:] == [{"role": "user", "content": "what is the moon"}, {"role": "assistant", "content": "The moon is a rock,"}]
    assert s.metrics[-1]["interrupted"] is True
    gate.set()


@pytest.mark.asyncio
async def test_without_barge_in_the_assistant_is_not_interrupted():
    gate = asyncio.Event()
    s, sent, _ = make_session(FEngine(gate=gate), FLlm(), L.LiveConfig(barge_in=False))
    await s.start()
    await s.audio(quiet(10) + tone(30) + quiet(25))
    for _ in range(100):
        if s.state == "speaking":
            break
        await asyncio.sleep(0.01)
    sent.clear()
    await s.audio(tone(30) + quiet(25))                                        # ignored: the assistant is busy
    assert sent == [] and not s.vad.busy
    gate.set()
    await s.wait()


@pytest.mark.asyncio
async def test_interrupt_clears_even_with_nothing_running_and_close_cancels_a_running_turn():
    gate = asyncio.Event()
    s, sent, _ = make_session(FEngine(gate=gate), FLlm())
    await s.start()
    await s.interrupt()
    assert "clear" in types(sent)
    await s.say("go on")
    await asyncio.sleep(0.05)
    await s.close()
    assert s.closed and s.task.done()
    await s.close()                                                            # closing twice is fine
    await s.wait()


@pytest.mark.asyncio
async def test_failures_are_shown_and_the_conversation_goes_on():
    s, sent, _ = make_session(llm=FLlm(error="model exploded"))
    await s.start()
    await say_something(s)
    assert [m for m in sent if m["type"] == "error"][0]["message"] == "model exploded" and s.state == "listening"
    s2, sent2, _ = make_session(engine=FEngine(fail_stt=True))
    await s2.start()
    await say_something(s2)
    assert "recogniser down" in [m for m in sent2 if m["type"] == "error"][0]["message"]
    s3, sent3, _ = make_session(engine=FEngine(fail_tts=True))
    await s3.start()
    await say_something(s3)
    assert "voice failed" in [m for m in sent3 if m["type"] == "error"][0]["message"] and s3.state == "listening"
    class Silent(FLlm):
        async def stream(self, messages, max_tokens=300):
            raise RuntimeError()
            yield ""
    s4, sent4, _ = make_session(llm=Silent())
    await s4.start()
    await s4.say("x")
    await s4.wait()
    assert [m for m in sent4 if m["type"] == "error"][0]["message"] == "RuntimeError"


@pytest.mark.asyncio
async def test_remember_prefers_what_was_spoken_then_the_full_reply_then_nothing():
    s, _, _ = make_session(cfg=L.LiveConfig(max_history=3))
    s._remember("q1", ["said"], "full reply")
    s._remember("q2", [], "full reply")
    s._remember("q3", [], "  ")
    assert s.history == [{"role": "user", "content": "q2"}, {"role": "assistant", "content": "full reply"}, {"role": "user", "content": "q3"}]
    s._remember("q4", ["spoken part"], "ignored full")
    assert s.history[-1] == {"role": "assistant", "content": "spoken part"}


@pytest.mark.asyncio
async def test_an_utterance_that_ends_while_the_assistant_is_idle_after_a_discard_returns_to_listening():
    s, sent, _ = make_session()
    await s.start()
    await s.audio(quiet(10) + tone(6) + quiet(25))                             # start, then discard
    assert states(sent)[-2:] == ["hearing", "listening"]


@pytest.mark.asyncio
async def test_a_typed_message_while_the_assistant_is_busy_interrupts_it():
    gate = asyncio.Event()
    s, sent, _ = make_session(FEngine(gate=gate), FLlm())
    await s.start()
    await s.say("first question")
    for _ in range(100):
        if s.state == "speaking":
            break
        await asyncio.sleep(0.01)
    sent.clear()
    gate.set()
    await s.say("never mind, second question")
    await s.wait()
    assert "clear" in types(sent) and [m for m in sent if m["type"] == "transcript"][-1]["text"] == "never mind, second question"
