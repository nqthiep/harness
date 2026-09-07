"""`examples/audio_tools.py` — ears, layer by layer.

Everything here runs with no microphone, no API key and no network. The two properties
worth pinning hardest are the ones that were measured rather than assumed: a missing
PortAudio surfaces as `OSError` and not `ImportError`, and `mode=SMART` cannot be
combined with diarization (the SDK's own field description says so).
"""
import asyncio
import importlib.util
import unittest

from audio_tools import (CHUNK_BYTES, CHUNK_MS, MIME_TYPE, SAMPLE_RATE,
                         FakeTranscriber, GeminiTranscriber, Microphone, Utterance,
                         UtteranceBuffer)

_HAS_GENAI = importlib.util.find_spec("google.genai") is not None


class UtteranceThat(unittest.TestCase):
    def test_counts_words_without_exposing_them_to_a_priority(self):
        self.assertEqual(Utterance("hôm nay trời đẹp").words, 4)
        self.assertEqual(Utterance("").words, 0)

    def test_is_ok_unless_it_carries_an_error(self):
        self.assertTrue(Utterance("xin chào").ok)
        self.assertFalse(Utterance(error="mất kết nối").ok)

    def test_is_frozen_so_a_background_task_can_publish_by_rebinding(self):
        with self.assertRaises(Exception):
            Utterance("a").text = "b"          # type: ignore[misc]


class TheUtteranceBufferThat(unittest.TestCase):
    """State, not events: the latest interim, and the last thing actually said."""

    def test_an_interim_replaces_the_previous_interim(self):
        buf = UtteranceBuffer()
        buf.publish(Utterance("hôm", False))
        buf.publish(Utterance("hôm nay", False))
        self.assertEqual(buf.interim.text, "hôm nay")
        self.assertIsNone(buf.last_final)

    def test_a_final_clears_the_interim_it_completes(self):
        buf = UtteranceBuffer()
        buf.publish(Utterance("hôm nay", False))
        buf.publish(Utterance("Hôm nay trời đẹp.", True))
        self.assertIsNone(buf.interim, "a finished sentence leaves nothing in progress")
        self.assertEqual(buf.last_final.text, "Hôm nay trời đẹp.")

    def test_a_new_interim_after_a_final_does_not_erase_the_final(self):
        buf = UtteranceBuffer()
        buf.publish(Utterance("Câu một.", True))
        buf.publish(Utterance("câu", False))
        self.assertEqual(buf.last_final.text, "Câu một.")
        self.assertEqual(buf.interim.text, "câu")


class TheFakeTranscriberThat(unittest.TestCase):
    def test_yields_one_scripted_batch_per_poll_then_nothing(self):
        fake = FakeTranscriber(script=[(Utterance("a"),), (Utterance("b"), Utterance("c"))])
        got = asyncio.run(self._drain(fake, 3))
        self.assertEqual([[u.text for u in b] for b in got], [["a"], ["b", "c"], []])

    def test_records_what_it_was_fed_so_a_test_can_see_the_feed_ran(self):
        fake = FakeTranscriber()
        asyncio.run(fake.push(b"\x00" * CHUNK_BYTES))
        self.assertEqual(fake.pushed, [b"\x00" * CHUNK_BYTES])

    @staticmethod
    async def _drain(fake, times):
        return [await fake.poll() for _ in range(times)]


class TheMicrophoneThat(unittest.TestCase):
    """`Camera`'s contract, for audio: a sentence, never an exception."""

    def test_reports_a_missing_portaudio_as_a_sentence(self):
        """Measured on a container with no PortAudio: `import sounddevice` raises
        `OSError`, NOT `ImportError`. Catching only the latter — the obvious thing to
        write — turns this into a crash inside `Driver._pump_forever`, which has no
        per-sensor try.
        """
        pcm, err = Microphone().read()
        if err is None:                       # a machine that really has a microphone
            self.skipTest("this machine has a working audio device")
        self.assertIsNone(pcm)
        self.assertIn("không mở được micro", err)

    def test_a_borrowed_stream_is_used_and_never_closed(self):
        """Owning it means closing it — `Camera`'s rule, so a caller who passed their own
        device still has it afterwards."""
        class Stream:
            def __init__(self): self.closed = False
            def read(self, frames): return b"\x01" * CHUNK_BYTES, False
            def stop(self): self.closed = True
            def close(self): self.closed = True

        stream = Stream()
        mic = Microphone(stream=stream)
        pcm, err = mic.read()
        self.assertIsNone(err)
        self.assertEqual(len(pcm), CHUNK_BYTES)
        mic.close()
        self.assertFalse(stream.closed, "closed a device it did not open")

    def test_turns_a_read_failure_into_a_sentence_too(self):
        class Angry:
            def read(self, frames): raise RuntimeError("thiết bị rút ra rồi")
            def stop(self): ...
            def close(self): ...

        pcm, err = Microphone(stream=Angry()).read()
        self.assertIsNone(pcm)
        self.assertIn("đọc micro lỗi", err)
        self.assertIn("thiết bị rút ra rồi", err)

    def test_the_chunk_size_is_the_wire_format_the_service_documents(self):
        self.assertEqual(SAMPLE_RATE, 16_000)
        self.assertEqual(MIME_TYPE, "audio/pcm;rate=16000")
        self.assertEqual(CHUNK_BYTES, SAMPLE_RATE * CHUNK_MS // 1000 * 2)


class TheGeminiTranscriberThat(unittest.TestCase):
    """Unrun against the real service (see the module docstring). What CAN be pinned
    without a key is pinned here."""

    def test_refuses_smart_mode_together_with_diarization(self):
        """`google/genai/types.py`, field `mode`: "Timestamps and diarization are
        incompatible with mode `SMART`." Refused at construction rather than left for
        the server to reject halfway through a conversation."""
        with self.assertRaises(ValueError) as caught:
            GeminiTranscriber(smart=True, diarization=True)
        self.assertIn("SMART", str(caught.exception))

    def test_allows_either_one_on_its_own(self):
        GeminiTranscriber(smart=True)
        GeminiTranscriber(diarization=True)

    def test_turns_the_servers_two_transcription_fields_into_utterances(self):
        """The drain is the whole adapter. Interim and final arrive on DIFFERENT fields
        of the same `server_content`, and which one it is decides `final` — that is the
        state/event distinction, handed over by the wire protocol."""
        got = asyncio.run(self._drain([
            _content(interim="hôm"),
            _content(interim="hôm nay"),
            _content(final="Hôm nay trời đẹp.", language="vi-VN", speaker="spk_1"),
        ]))
        self.assertEqual([(u.text, u.final) for u in got],
                         [("hôm", False), ("hôm nay", False),
                          ("Hôm nay trời đẹp.", True)])
        self.assertEqual(got[-1].language, "vi-VN")
        self.assertEqual(got[-1].speaker, "spk_1")

    def test_a_dropped_socket_becomes_an_utterance_carrying_an_error(self):
        """Not a raise: the sensor above must be able to say "tôi không nghe được"
        rather than take the whole pump down with it. A ten-minute session cap makes
        this normal operation, not an edge case."""
        got = asyncio.run(self._drain([_content(interim="a")], then=IOError("socket")))
        self.assertEqual(got[0].text, "a")
        self.assertFalse(got[1].ok)
        self.assertIn("mất kết nối phiên nghe", got[1].error)

    def test_poll_empties_what_it_returns(self):
        got = asyncio.run(self._drain_twice([_content(final="xong")]))
        self.assertEqual([u.text for u in got[0]], ["xong"])
        self.assertEqual(got[1], ())

    @unittest.skipUnless(_HAS_GENAI, "google-genai not installed")
    def test_builds_a_config_the_sdk_accepts(self):
        from google.genai import types
        cfg = GeminiTranscriber(language_codes=["vi-VN"],
                                custom_vocabulary=["Thiep", "Nghia"])._config()
        self.assertEqual(cfg.response_modalities, [types.Modality.TEXT])
        audio = cfg.input_audio_transcription
        self.assertEqual(audio.language_codes, ["vi-VN"])
        self.assertEqual(audio.custom_vocabulary, ["Thiep", "Nghia"])
        self.assertEqual(audio.mode, types.AudioTranscriptionConfigMode.VERBATIM)

    @unittest.skipUnless(_HAS_GENAI, "google-genai not installed")
    def test_an_empty_language_list_means_automatic_detection(self):
        """The SDK's own wording: "If omitted or empty, defaults to automatic language
        detection." That is the default here, so a bilingual room needs no configuration."""
        self.assertEqual(GeminiTranscriber()._config()
                         .input_audio_transcription.language_codes, [])

    # -- helpers ---------------------------------------------------------------------

    @staticmethod
    async def _drain(contents, then=None):
        t = GeminiTranscriber()
        t._session = _Session(contents, then)
        await t._drain_forever()
        return await t.poll()

    @staticmethod
    async def _drain_twice(contents):
        t = GeminiTranscriber()
        t._session = _Session(contents, None)
        await t._drain_forever()
        return [await t.poll(), await t.poll()]


class _Transcription:
    def __init__(self, text, language=None, speaker=None):
        self.text, self.language_code, self.speaker_label = text, language, speaker


class _ServerContent:
    def __init__(self, interim=None, final=None, language=None, speaker=None):
        self.interim_input_transcription = (
            _Transcription(interim, language, speaker) if interim else None)
        self.input_transcription = (
            _Transcription(final, language, speaker) if final else None)


def _content(**kw):
    class Response:
        server_content = _ServerContent(**kw)
    return Response()


class _Session:
    """Stands in for `client.aio.live.connect(...)`'s session — the drain only iterates
    `receive()` and reads attributes, so no SDK is needed to exercise it."""

    def __init__(self, contents, then):
        self.contents, self.then = contents, then

    async def receive(self):
        for c in self.contents:
            yield c
        if self.then is not None:
            raise self.then


if __name__ == "__main__":
    unittest.main()
