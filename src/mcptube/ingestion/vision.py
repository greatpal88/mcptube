"""Vision model integration Ã¢â‚¬â€ describe video frames using multimodal LLM."""

import base64
import logging
from pathlib import Path


from mcptube.llm import LLMClient, LLMError
from mcptube.wiki.models import FrameDescription

logger = logging.getLogger(__name__)

from functools import lru_cache


@lru_cache(maxsize=1)
def _litellm():
    """Import litellm on first use (keeps startup fast)."""
    import litellm
    litellm.suppress_debug_info = True
    litellm.drop_params = True
    return litellm


class VisionDescriber:
    """Describes video frames using a multimodal LLM.

    Takes extracted scene-change frames and produces text descriptions
    via a vision-capable model (GPT-4o, Claude, Gemini).
    """

    _VISION_MODELS = {
        "ANTHROPIC_API_KEY": "anthropic/claude-sonnet-5",
        "OPENAI_API_KEY": "gpt-4o",
        "GOOGLE_API_KEY": "gemini/gemini-2.0-flash",
    }

    # Frames per batch request, and the reply budget for one such request.
    # The failure these guard against is a reply whose JSON array is shorter
    # than the chunk it answers: describe_batch pads the tail with
    # "(description unavailable)", which is a non-empty string and so reads
    # downstream as a real description. Fewer images per request makes the
    # model far likelier to return exactly one element per frame, and 8192
    # leaves 20 descriptions of 1-3 sentences ample room.
    _BATCH_SIZE = 20
    _BATCH_MAX_TOKENS = 8192

    _FRAME_PROMPT = """Describe this video frame concisely in 1-3 sentences.
Focus on what is visually significant:
- Slides or text on screen: transcribe key text
- Code: describe the language and what it does
- Diagrams: describe the structure and labels
- People: describe what they are doing (presenting, demoing, etc.)
- UI/demos: describe the application or tool shown

Be factual and specific. No speculation."""

    _BATCH_PROMPT = """You are analyzing frames from a YouTube video. For each frame,
provide a concise 1-3 sentence description focusing on visually significant content
(slides, code, diagrams, demos, people presenting).

Respond with a JSON array of descriptions in the same order as the frames.
Example: ["Frame shows a title slide reading 'Introduction to LLMs'", "Presenter at whiteboard drawing transformer architecture"]

Return ONLY the JSON array. No markdown, no explanation."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self._model = self._detect_vision_model()

    def describe_frames(self, frames: list[dict]) -> list[FrameDescription]:
        """Describe a list of scene-change frames using vision model.

        Args:
            frames: List of dicts with keys: "path" (Path), "timestamp" (float), "index" (int)

        Returns:
            List of FrameDescription models.

        Raises:
            LLMError: If vision model call fails.
        """
        if not self._llm.available:
            raise LLMError("Vision analysis requires an LLM. Set an API key.")

        if not frames:
            return []

        # For small batches, describe individually for better quality
        # For larger batches, use batch mode to save cost -- in chunks, so no
        # single reply has to cover more frames than it can comfortably fit.
        if len(frames) <= 5:
            return self._describe_individually(frames)

        described: list[FrameDescription] = []
        for i in range(0, len(frames), self._BATCH_SIZE):
            described.extend(
                self._describe_batch(frames[i:i + self._BATCH_SIZE]))
        return described

    def _describe_individually(self, frames: list[dict]) -> list[FrameDescription]:
        """Describe each frame with a separate vision call."""
        descriptions = []
        for frame in frames:
            try:
                desc = self._describe_single_frame(frame["path"])
                descriptions.append(FrameDescription(
                    filename=frame["path"].name,
                    timestamp=frame["timestamp"],
                    description=desc,
                ))
            except LLMError as e:
                logger.warning("Failed to describe frame %s: %s", frame["path"].name, e)
                descriptions.append(FrameDescription(
                    filename=frame["path"].name,
                    timestamp=frame["timestamp"],
                    description="(description unavailable)",
                ))
        return descriptions

    def _describe_single_frame(self, image_path: Path) -> str:
        """Describe a single frame using vision model."""
        b64 = base64.b64encode(image_path.read_bytes()).decode()
        mime = "image/jpeg"

        try:
            response = _litellm().completion(
                model=self._model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self._FRAME_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime};base64,{b64}",
                            },
                        },
                    ],
                }],
                temperature=0.2,
                max_tokens=256,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            raise LLMError(f"Vision model failed: {e}") from e

    def _describe_batch(self, frames: list[dict]) -> list[FrameDescription]:
        """Describe multiple frames in a single vision call."""
        import json

        # Build content array with all images
        content = [{"type": "text", "text": self._BATCH_PROMPT}]
        for frame in frames:
            b64 = base64.b64encode(frame["path"].read_bytes()).decode()
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{b64}",
                },
            })

        try:
            response = _litellm().completion(
                model=self._model,
                messages=[{"role": "user", "content": content}],
                temperature=0.2,
                max_tokens=self._BATCH_MAX_TOKENS,
            )
            raw = response.choices[0].message.content.strip()

            # Parse JSON array
            text = raw
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            descs = json.loads(text)

            # A reply with the wrong number of elements tells us nothing
            # about WHICH frame it skipped, so the whole chunk's alignment is
            # suspect -- keeping the first len(descs) would silently shift
            # every later description onto the wrong frame. Re-describe the
            # chunk one frame at a time instead, where each reply can only
            # belong to the image it was sent with. Padding the tail is what
            # produced the "(description unavailable)" frames this guards
            # against; a genuine per-frame failure still yields that literal,
            # but now only for the frame that actually failed.
            if len(descs) != len(frames):
                logger.warning(
                    "Vision returned %d descriptions for %d frames; "
                    "re-describing this chunk individually",
                    len(descs), len(frames))
                return self._describe_individually(frames)

            return [
                FrameDescription(
                    filename=frame["path"].name,
                    timestamp=frame["timestamp"],
                    description=descs[i],
                )
                for i, frame in enumerate(frames)
            ]

        except Exception as e:
            logger.warning("Batch vision failed, falling back to individual: %s", e)
            return self._describe_individually(frames)

    def _detect_vision_model(self) -> str:
        """Auto-detect the best available vision-capable model."""
        import os

        for key, model in self._VISION_MODELS.items():
            if os.environ.get(key):
                logger.info("Vision model: %s Ã¢â€ â€™ %s", key, model)
                return model
        return "gpt-4o"
