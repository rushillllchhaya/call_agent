"""
Enhanced VAD Manager — production-grade voice activity detection.
=================================================================
Inspired by AVA's EnhancedVADManager with playback gating, adaptive
thresholds, and speech confidence scoring. Replaces the inline RMS
threshold logic in streams.py with a proper state machine.

Features:
  - Playback gating: Automatically suppress during TTS output
  - Adaptive noise floor: Learns ambient noise from first 2s of audio
  - Multi-frame confidence: Require N consecutive speech frames (reduces false triggers)
  - Barge-in detection: Detect speech during playback
"""
from __future__ import annotations

import audioop
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger("vad_manager")


class VADState(Enum):
    """Current state of the VAD state machine."""
    IDLE = "idle"                    # Listening, no speech detected
    SPEECH_STARTED = "speech"        # Active speech detected
    SPEECH_ENDED = "ended"           # Speech ended (silence after speech)
    PLAYBACK_GATED = "gated"         # Suppressed during TTS playback


@dataclass
class VADResult:
    """Result of processing an audio frame through the VAD."""
    is_speech: bool = False
    is_barge_in: bool = False        # Speech detected during playback
    rms_energy: int = 0
    state: VADState = VADState.IDLE
    confidence: float = 0.0          # 0.0-1.0 confidence that this is real speech


@dataclass
class VADConfig:
    """Configuration for the VAD manager."""
    energy_threshold: int = 40           # RMS threshold for speech detection
    silence_timeout_ms: int = 700        # ms of silence to end a speech segment
    min_speech_frames: int = 3           # Consecutive speech frames to confirm speech start
    min_audio_bytes: int = 3200          # Minimum bytes for valid speech (~400ms at 8kHz)
    max_buffer_size: int = 512_000       # 500KB safety limit
    adaptive_threshold: bool = True      # Auto-learn noise floor
    noise_adaptation_rate: float = 0.05  # How fast to adapt (0.0-1.0)
    noise_floor_multiplier: float = 2.5  # Speech threshold = noise_floor * this
    pre_roll_frames: int = 10            # Number of frames to keep as pre-roll


class EnhancedVADManager:
    """
    Production-grade VAD with playback gating and adaptive thresholds.

    Usage:
        vad = EnhancedVADManager(VADConfig())

        # During TTS playback:
        vad.start_playback_gate(duration_seconds=3.5)

        # For each incoming audio frame:
        result = vad.process_frame(audio_bytes)
        if result.is_speech:
            audio_buffer.extend(audio_bytes)
        elif result.state == VADState.SPEECH_ENDED:
            process_speech_buffer(audio_buffer)
    """

    def __init__(self, config: Optional[VADConfig] = None):
        self.config = config or VADConfig()

        # State
        self._state = VADState.IDLE
        self._consecutive_speech_frames = 0
        self._consecutive_silence_frames = 0
        self._last_speech_time: float = 0.0
        self._speech_start_time: float = 0.0

        # Playback gating
        self._gate_until: float = 0.0         # Suppress VAD until this timestamp
        self._is_playing: bool = False

        # Adaptive noise floor
        self._noise_samples: deque[int] = deque(maxlen=50)  # RMS samples for noise estimation
        self._noise_floor: int = 0
        self._noise_calibrated: bool = False
        self._calibration_start: float = 0.0
        self._calibration_duration: float = 2.0  # Calibrate for first 2 seconds

        # Audio pre-roll buffer (keep last N frames for context)
        self._pre_roll: deque[bytes] = deque(maxlen=self.config.pre_roll_frames)

        # Metrics
        self._total_frames: int = 0
        self._speech_frames: int = 0

    @property
    def is_gated(self) -> bool:
        """Check if VAD is currently suppressed (playback gating)."""
        return time.monotonic() < self._gate_until

    @property
    def effective_threshold(self) -> int:
        """Current effective speech threshold (adaptive or fixed)."""
        if self.config.adaptive_threshold and self._noise_calibrated and self._noise_floor > 0:
            adaptive = int(self._noise_floor * self.config.noise_floor_multiplier)
            return max(adaptive, self.config.energy_threshold)
        return self.config.energy_threshold

    @property
    def noise_floor(self) -> int:
        return self._noise_floor

    def start_playback_gate(self, duration_seconds: float, buffer_seconds: float = 0.5):
        """
        Suppress VAD for the duration of TTS playback + buffer.
        This prevents greeting echo and TTS feedback from triggering the VAD.
        """
        self._gate_until = time.monotonic() + duration_seconds + buffer_seconds
        self._is_playing = True
        self._consecutive_speech_frames = 0
        self._state = VADState.PLAYBACK_GATED
        logger.info(
            f"[VAD] Playback gate ON for {duration_seconds + buffer_seconds:.1f}s "
            f"(audio={duration_seconds:.1f}s + buffer={buffer_seconds:.1f}s)"
        )

    def stop_playback_gate(self):
        """Manually stop playback gating (e.g., on barge-in)."""
        self._gate_until = 0.0
        self._is_playing = False
        if self._state == VADState.PLAYBACK_GATED:
            self._state = VADState.IDLE
        logger.info("[VAD] Playback gate OFF")

    def get_pre_roll(self) -> bytes:
        """Get accumulated pre-roll audio context."""
        return b"".join(self._pre_roll)

    def reset(self):
        """Reset state for a new speech segment."""
        self._consecutive_speech_frames = 0
        self._consecutive_silence_frames = 0
        self._state = VADState.IDLE

    def process_frame(self, audio_bytes: bytes) -> VADResult:
        """
        Process a single audio frame through the VAD state machine.

        Args:
            audio_bytes: Raw mu-law or PCM audio frame

        Returns:
            VADResult with speech detection status
        """
        self._total_frames += 1
        now = time.monotonic()

        # Calculate RMS energy (mu-law → PCM16 → RMS)
        try:
            pcm_data = audioop.ulaw2lin(audio_bytes, 2)
            rms = audioop.rms(pcm_data, 2)
        except Exception:
            rms = 0

        result = VADResult(rms_energy=rms)

        # ── Playback gating ──────────────────────────────────
        if self.is_gated:
            # Still in gate window — but check for loud barge-in
            barge_threshold = self.effective_threshold * 3  # Need MUCH louder to barge in
            if rms > barge_threshold:
                result.is_barge_in = True
                result.state = VADState.SPEECH_STARTED
                logger.info(f"[VAD] BARGE-IN detected! RMS={rms} > {barge_threshold}")
                self.stop_playback_gate()
                return result

            result.state = VADState.PLAYBACK_GATED
            self._pre_roll.append(audio_bytes)
            return result
        elif self._is_playing:
            # Gate window expired
            self._is_playing = False
            self._state = VADState.IDLE

        # ── Adaptive noise floor calibration ─────────────────
        if self.config.adaptive_threshold:
            if not self._noise_calibrated:
                if self._calibration_start == 0.0:
                    self._calibration_start = now

                self._noise_samples.append(rms)

                if now - self._calibration_start >= self._calibration_duration:
                    if self._noise_samples:
                        self._noise_floor = int(sum(self._noise_samples) / len(self._noise_samples))
                        self._noise_calibrated = True
                        logger.info(
                            f"[VAD] Noise floor calibrated: {self._noise_floor} "
                            f"(threshold={self.effective_threshold})"
                        )
            else:
                # Slow continuous adaptation during silence
                if rms < self.effective_threshold:
                    self._noise_floor = int(
                        self._noise_floor * (1 - self.config.noise_adaptation_rate)
                        + rms * self.config.noise_adaptation_rate
                    )

        # ── Speech detection ─────────────────────────────────
        is_above_threshold = rms > self.effective_threshold

        if is_above_threshold:
            self._consecutive_speech_frames += 1
            self._consecutive_silence_frames = 0
            self._last_speech_time = now

            if self._consecutive_speech_frames >= self.config.min_speech_frames:
                if self._state != VADState.SPEECH_STARTED:
                    self._state = VADState.SPEECH_STARTED
                    self._speech_start_time = now
                    logger.debug(f"[VAD] Speech START (RMS={rms}, threshold={self.effective_threshold})")

                result.is_speech = True
                result.state = VADState.SPEECH_STARTED
                result.confidence = min(1.0, self._consecutive_speech_frames / 10.0)
                self._speech_frames += 1
        else:
            self._consecutive_silence_frames += 1

            if self._state == VADState.SPEECH_STARTED:
                # Check if silence has lasted long enough to end speech
                silence_duration_ms = self._consecutive_silence_frames * 20  # ~20ms per frame at 8kHz
                if silence_duration_ms >= self.config.silence_timeout_ms:
                    self._state = VADState.SPEECH_ENDED
                    speech_duration = now - self._speech_start_time
                    result.state = VADState.SPEECH_ENDED
                    logger.debug(f"[VAD] Speech END after {speech_duration:.1f}s of speech")
                    self._consecutive_speech_frames = 0
                else:
                    # Still in speech (just a brief pause)
                    result.is_speech = True
                    result.state = VADState.SPEECH_STARTED
            else:
                self._consecutive_speech_frames = 0
                result.state = VADState.IDLE

        # Always maintain pre-roll
        self._pre_roll.append(audio_bytes)
        return result
