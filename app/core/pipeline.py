"""
Voice Pipeline — per-turn STT → LLM → TTS orchestration with timeouts.
========================================================================
Inspired by AVA's PipelineOrchestrator, simplified for BSB's single-provider
setup (Sarvam STT/TTS + Groq LLM). Provides:
  - Per-step timeouts (STT 10s, LLM 20s, TTS 12s)
  - Streaming TTS (sentence-by-sentence synthesis)
  - Fallback responses on any step failure
  - Per-turn latency metrics for debugging
  - Barge-in support via cancellation tokens
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import AsyncGenerator, Optional, Callable, Any

logger = logging.getLogger("pipeline")


@dataclass
class TurnMetrics:
    """Latency breakdown for a single conversation turn."""
    stt_ms: float = 0.0
    llm_ms: float = 0.0
    tts_ms: float = 0.0
    total_ms: float = 0.0
    stt_error: Optional[str] = None
    llm_error: Optional[str] = None
    tts_error: Optional[str] = None
    used_fallback: bool = False

    @property
    def summary(self) -> str:
        parts = [f"STT={self.stt_ms:.0f}ms", f"LLM={self.llm_ms:.0f}ms", f"TTS={self.tts_ms:.0f}ms"]
        if self.used_fallback:
            parts.append("FALLBACK")
        return " | ".join(parts) + f" | Total={self.total_ms:.0f}ms"


@dataclass
class PipelineConfig:
    """Configurable timeouts and limits for the voice pipeline."""
    stt_timeout: float = 10.0     # Max seconds for STT transcription
    llm_timeout: float = 20.0     # Max seconds for LLM response
    tts_timeout: float = 12.0     # Max seconds for TTS synthesis
    max_response_chars: int = 500 # Truncate LLM responses longer than this
    fallback_message: str = "Ji, thoda sa repeat karein please?"
    network_slow_message: str = "Ji, network thoda slow hai. Aap please dobara boliye?"


class VoicePipeline:
    """
    Orchestrates a single STT → LLM → TTS turn with per-step timeouts.

    Usage:
        pipeline = VoicePipeline(config)
        metrics = await pipeline.process_turn(
            audio_bytes=raw_mulaw,
            stt_func=sarvam.transcribe_audio,
            llm_func=orchestrator.process_message_async,
            tts_func=sarvam.text_to_speech,
            call_id=call_id,
            phone_number=phone,
        )
    """

    def __init__(self, config: Optional[PipelineConfig] = None):
        self.config = config or PipelineConfig()
        self._cancelled = False
        self._metrics_history: list[TurnMetrics] = []

    def cancel(self):
        """Signal barge-in — cancel current pipeline execution."""
        self._cancelled = True

    def reset(self):
        """Reset cancellation flag for next turn."""
        self._cancelled = False

    @property
    def metrics_history(self) -> list[TurnMetrics]:
        return self._metrics_history

    async def run_stt(
        self,
        audio_bytes: bytes,
        stt_func: Callable,
        language_code: str = "hi-IN",
    ) -> tuple[str, str, float]:
        """
        Run STT with timeout. Returns (transcript, detected_lang, latency_ms).
        """
        t0 = time.monotonic()
        try:
            transcript, detected_lang = await asyncio.wait_for(
                stt_func(audio_bytes, language_code),
                timeout=self.config.stt_timeout,
            )
            latency = (time.monotonic() - t0) * 1000
            logger.info(f"[Pipeline:STT] '{transcript[:60]}' lang={detected_lang} ({latency:.0f}ms)")
            return transcript, detected_lang, latency
        except asyncio.TimeoutError:
            latency = (time.monotonic() - t0) * 1000
            logger.error(f"[Pipeline:STT] TIMEOUT after {latency:.0f}ms")
            return "", language_code, latency
        except Exception as e:
            latency = (time.monotonic() - t0) * 1000
            logger.error(f"[Pipeline:STT] Error after {latency:.0f}ms: {e}")
            return "", language_code, latency

    async def run_llm(
        self,
        llm_func: Callable,
        call_id: str,
        user_text: str,
        phone_number: str = "",
        language_code: str = "hi-IN",
    ) -> tuple[str, bool, float]:
        """
        Run LLM with timeout. Returns (response_text, should_end, latency_ms).
        """
        t0 = time.monotonic()
        try:
            ai_text, should_end = await asyncio.wait_for(
                llm_func(call_id, user_text, phone_number, language_code),
                timeout=self.config.llm_timeout,
            )
            latency = (time.monotonic() - t0) * 1000
            # Truncate excessively long responses
            if len(ai_text) > self.config.max_response_chars:
                ai_text = ai_text[:self.config.max_response_chars] + "..."
                logger.warning(f"[Pipeline:LLM] Truncated response to {self.config.max_response_chars} chars")
            logger.info(f"[Pipeline:LLM] '{ai_text[:80]}' end={should_end} ({latency:.0f}ms)")
            return ai_text, should_end, latency
        except asyncio.TimeoutError:
            latency = (time.monotonic() - t0) * 1000
            logger.error(f"[Pipeline:LLM] TIMEOUT after {latency:.0f}ms")
            return self.config.network_slow_message, False, latency
        except Exception as e:
            latency = (time.monotonic() - t0) * 1000
            logger.error(f"[Pipeline:LLM] Error after {latency:.0f}ms: {e}")
            return self.config.fallback_message, False, latency

    async def run_tts(
        self,
        tts_func: Callable,
        text: str,
        language_code: str = "hi-IN",
    ) -> tuple[bytes, float]:
        """
        Run TTS with timeout. Returns (audio_bytes, latency_ms).
        """
        t0 = time.monotonic()
        try:
            audio = await asyncio.wait_for(
                tts_func(text, language_code),
                timeout=self.config.tts_timeout,
            )
            latency = (time.monotonic() - t0) * 1000
            logger.info(f"[Pipeline:TTS] {len(audio)} bytes ({latency:.0f}ms)")
            return audio, latency
        except asyncio.TimeoutError:
            latency = (time.monotonic() - t0) * 1000
            logger.error(f"[Pipeline:TTS] TIMEOUT after {latency:.0f}ms")
            return b"", latency
        except Exception as e:
            latency = (time.monotonic() - t0) * 1000
            logger.error(f"[Pipeline:TTS] Error after {latency:.0f}ms: {e}")
            return b"", latency

    async def process_turn(
        self,
        audio_bytes: bytes,
        stt_func: Callable,
        llm_func: Callable,
        tts_func: Callable,
        call_id: str,
        phone_number: str = "",
        language_code: str = "hi-IN",
    ) -> tuple[bytes, str, bool, TurnMetrics]:
        """
        Full pipeline: audio_in → STT → LLM → TTS → audio_out.

        Returns:
            (tts_audio_bytes, ai_text, should_end, metrics)
        """
        self.reset()
        metrics = TurnMetrics()
        t_start = time.monotonic()

        # ── Step 1: STT ──────────────────────────────────────────
        transcript, detected_lang, stt_ms = await self.run_stt(
            audio_bytes, stt_func, language_code,
        )
        metrics.stt_ms = stt_ms

        if self._cancelled:
            logger.info("[Pipeline] Cancelled after STT (barge-in)")
            metrics.total_ms = (time.monotonic() - t_start) * 1000
            return b"", "", False, metrics

        if not transcript.strip():
            logger.info("[Pipeline] Empty transcript — skipping LLM+TTS")
            metrics.total_ms = (time.monotonic() - t_start) * 1000
            return b"", "", False, metrics

        # ── Step 2: LLM ──────────────────────────────────────────
        ai_text, should_end, llm_ms = await self.run_llm(
            llm_func, call_id, transcript, phone_number, detected_lang,
        )
        metrics.llm_ms = llm_ms

        if self._cancelled:
            logger.info("[Pipeline] Cancelled after LLM (barge-in)")
            metrics.total_ms = (time.monotonic() - t_start) * 1000
            return b"", ai_text, should_end, metrics

        if not ai_text.strip():
            ai_text = self.config.fallback_message
            metrics.used_fallback = True

        # ── Step 3: TTS ──────────────────────────────────────────
        tts_audio, tts_ms = await self.run_tts(
            tts_func, ai_text, detected_lang,
        )
        metrics.tts_ms = tts_ms

        if not tts_audio:
            metrics.used_fallback = True

        metrics.total_ms = (time.monotonic() - t_start) * 1000
        self._metrics_history.append(metrics)

        logger.info(f"[Pipeline] Turn complete: {metrics.summary}")
        return tts_audio, ai_text, should_end, metrics

    async def stream_turn(
        self,
        audio_bytes: bytes,
        stt_func: Callable,
        llm_stream_func: Callable,
        tts_func: Callable,
        call_id: str,
        phone_number: str = "",
        language_code: str = "hi-IN",
        is_interrupted: bool = False,
    ) -> AsyncGenerator[tuple[bytes, str, bool], None]:
        """
        Streaming pipeline: yields TTS audio sentence-by-sentence.

        llm_stream_func should be an AsyncGenerator yielding (sentence, is_final) pairs.
        This allows TTS to start synthesizing the first sentence while LLM is still generating.
        """
        self.reset()
        t_start = time.monotonic()

        # ── Step 1: STT ──────────────────────────────────────────
        transcript, detected_lang, stt_ms = await self.run_stt(
            audio_bytes, stt_func, language_code,
        )

        if self._cancelled or not transcript.strip():
            return

        # ── Step 2+3: Stream LLM → TTS sentence by sentence ─────
        should_end = False
        async for sentence, is_final in llm_stream_func(
            call_id, transcript, phone_number, detected_lang, is_interrupted,
        ):
            if self._cancelled:
                logger.info("[Pipeline:Stream] Cancelled mid-stream (barge-in)")
                return

            if not sentence.strip():
                if is_final:
                    should_end = True
                continue

            # Synthesize each sentence immediately
            tts_audio, tts_ms = await self.run_tts(
                tts_func, sentence, detected_lang,
            )

            if tts_audio and not self._cancelled:
                yield tts_audio, sentence, is_final

            if is_final:
                should_end = True

        elapsed = (time.monotonic() - t_start) * 1000
        logger.info(f"[Pipeline:Stream] Complete in {elapsed:.0f}ms")
