"""Knowledge extractor — single LLM pass over transcript to generate wiki pages."""

import json
import logging
import re
from datetime import datetime, timezone

from mcptube.llm import LLMClient, LLMError
from mcptube.models import Video
from mcptube.wiki.models import (
    ConceptPage,
    EntityCategory,
    EntityPage,
    FrameDescription,
    TopicPage,
    VideoContribution,
    VideoPage,
    WikiPageType,
)

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """Convert text to a URL/filename-safe slug."""
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


class KnowledgeExtractor:
    """Extracts structured knowledge from a video transcript via a single LLM pass.

    Given a Video model (and optionally vision frame descriptions), produces:
    - One VideoPage (write-once summary of the video)
    - Zero or more EntityPages (people, companies, tools)
    - Zero or more TopicPages (broad themes)
    - Zero or more ConceptPages (specific ideas, techniques, opinions)

    Uses a single LLM call to extract all knowledge at once, minimising
    cost and latency.
    """

    _EXTRACTION_PROMPT = """You are a knowledge extraction system. Given a YouTube video transcript 
and metadata, extract structured knowledge for a persistent wiki.

VIDEO METADATA:
Title: {title}
Channel: {channel}
Duration: {duration:.0f}s
{frame_section}

TRANSCRIPT:
{transcript}

Extract the following and return ONLY valid JSON:

{{
    "video_summary": "2-4 sentence summary of the entire video",
    "key_timestamps": {{
        "MM:SS": "description of what happens at this moment"
    }},
    "entities": [
        {{
            "name": "Entity Name",
            "category": "person|company|tool|place|other",
            "context": "What the video says about this entity (2-3 sentences)",
            "timestamps": ["MM:SS"]
        }}
    ],
    "topics": [
        {{
            "name": "Topic Name",
            "content": "What the video says about this topic (detailed, 3-5 sentences)",
            "timestamps": ["MM:SS"],
            "tags": ["tag1", "tag2"]
        }}
    ],
    "concepts": [
        {{
            "name": "Concept Name",
            "content": "Detailed explanation of this concept as discussed in the video (3-5 sentences)",
            "timestamps": ["MM:SS"],
            "tags": ["tag1", "tag2"]
        }}
    ]
}}

Guidelines:
- Extract 3-15 entities (people, companies, tools mentioned significantly)
- Extract 2-6 topics (broad themes like "Machine Learning", "AI Safety")
- Extract 2-8 concepts (specific ideas like "Scaling Laws", "Chain of Thought Prompting")
- Topics are broad categories; concepts are specific ideas within those categories
- Timestamps should reference actual [MM:SS] values from the transcript
- Content should capture what THIS VIDEO specifically says — not general knowledge
- Be thorough but only include entities/topics/concepts with meaningful discussion
- No markdown in JSON values"""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def extract(
        self,
        video: Video,
        frame_descriptions: list[FrameDescription] | None = None,
        text_only: bool = False,
    ) -> dict:
        """Extract knowledge from a video and return wiki pages.

        Args:
            video: Full Video model with transcript.
            frame_descriptions: Optional vision model frame descriptions.
            text_only: If True, ignore frame descriptions even if provided.

        Returns:
            Dict with keys: "video_page", "entity_pages", "topic_pages", "concept_pages"
            Each value is a list of corresponding wiki page models.

        Raises:
            LLMError: If extraction fails.
        """
        if not self._llm.available:
            raise LLMError("Knowledge extraction requires an LLM. Set an API key.")

        transcript_text = self._format_transcript(video)

        # Build frame section if available
        frame_section = ""
        if not text_only and frame_descriptions:
            frame_lines = [
                f"- {f.filename} ({self._fmt_time(f.timestamp)}): {f.description}"
                for f in frame_descriptions
            ]
            frame_section = "KEY FRAMES:\n" + "\n".join(frame_lines)

        prompt = self._EXTRACTION_PROMPT.format(
            title=video.title,
            channel=video.channel,
            duration=video.duration,
            frame_section=frame_section,
            transcript=transcript_text,
        )

        raw = self._llm._complete(prompt, max_tokens=8192)
        data = self._parse_response(raw)

        return self._build_pages(video, data, frame_descriptions, text_only)

    def build_pages_without_llm(
        self,
        video: Video,
        frame_descriptions: list[FrameDescription] | None = None,
        text_only: bool = False,
    ) -> dict:
        """Same shape as extract(), with no LLM call.

        Used by MCPTUBE_SKIP_WIKI. Passing an empty `data` yields a video page
        carrying the transcript and the vision frame descriptions, with an
        empty summary/key_timestamps, and empty entity/topic/concept lists --
        so the updater writes the video page and nothing else.
        """
        return self._build_pages(video, {}, frame_descriptions, text_only)

    def _build_pages(
        self,
        video: Video,
        data: dict,
        frame_descriptions: list[FrameDescription] | None,
        text_only: bool,
    ) -> dict:
        """Convert raw LLM extraction into wiki page models."""
        now = datetime.now(timezone.utc)
        transcript_text = self._format_transcript(video)

        # --- Video Page (write-once) ---
        video_page = VideoPage(
            slug=f"video-{video.video_id}",
            title=video.title,
            video_id=video.video_id,
            channel=video.channel,
            duration=video.duration,
            processing_tier="text_only" if text_only else "full_analysis",
            summary=data.get("video_summary", ""),
            key_timestamps=data.get("key_timestamps", {}),
            #key_frames=frame_descriptions or [],
            key_frames=[] if text_only else (frame_descriptions or []),
            transcript=transcript_text,
            tags=video.tags,
            created_at=now,
            updated_at=now,
        )

        # --- Entity Pages ---
        entity_pages = []
        for entity in data.get("entities", []):
            name = entity.get("name", "").strip()
            if not name:
                continue
            slug = f"entity-{_slugify(name)}"
            category = entity.get("category", "other")
            try:
                cat = EntityCategory(category)
            except ValueError:
                cat = EntityCategory.OTHER

            page = EntityPage(
                slug=slug,
                title=name,
                category=cat,
                overview=entity.get("context", ""),
                video_references=[
                    VideoContribution(
                        video_id=video.video_id,
                        title=video.title,
                        channel=video.channel,
                        content=entity.get("context", ""),
                        timestamps=entity.get("timestamps", []),
                        added_at=now,
                    )
                ],
                tags=[cat.value],
                related_pages=[video_page.slug],
                created_at=now,
                updated_at=now,
            )
            entity_pages.append(page)

        # --- Topic Pages ---
        topic_pages = []
        for topic in data.get("topics", []):
            name = topic.get("name", "").strip()
            if not name:
                continue
            slug = f"topic-{_slugify(name)}"

            page = TopicPage(
                slug=slug,
                title=name,
                synthesis=topic.get("content", ""),
                contributions=[
                    VideoContribution(
                        video_id=video.video_id,
                        title=video.title,
                        channel=video.channel,
                        content=topic.get("content", ""),
                        timestamps=topic.get("timestamps", []),
                        added_at=now,
                    )
                ],
                tags=topic.get("tags", []),
                related_pages=[video_page.slug],
                created_at=now,
                updated_at=now,
            )
            topic_pages.append(page)

        # --- Concept Pages ---
        concept_pages = []
        for concept in data.get("concepts", []):
            name = concept.get("name", "").strip()
            if not name:
                continue
            slug = f"concept-{_slugify(name)}"

            page = ConceptPage(
                slug=slug,
                title=name,
                synthesis=concept.get("content", ""),
                contributions=[
                    VideoContribution(
                        video_id=video.video_id,
                        title=video.title,
                        channel=video.channel,
                        content=concept.get("content", ""),
                        timestamps=concept.get("timestamps", []),
                        added_at=now,
                    )
                ],
                tags=concept.get("tags", []),
                related_pages=[video_page.slug],
                created_at=now,
                updated_at=now,
            )
            concept_pages.append(page)

        return {
            "video_page": video_page,
            "entity_pages": entity_pages,
            "topic_pages": topic_pages,
            "concept_pages": concept_pages,
        }

    def _parse_response(self, raw: str) -> dict:
        """Parse the LLM JSON response."""
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(
                f"Failed to parse extraction JSON: {e}\nResponse: {raw[:300]}"
            )

    @staticmethod
    def _format_transcript(video: Video) -> str:
        """Format transcript segments with timestamps."""
        lines = []
        for seg in video.transcript:
            mins, secs = divmod(int(seg.start), 60)
            lines.append(f"[{mins:02d}:{secs:02d}] {seg.text}")
        return "\n".join(lines)

    @staticmethod
    def _fmt_time(seconds: float) -> str:
        """Format seconds as MM:SS."""
        mins, secs = divmod(int(seconds), 60)
        return f"{mins:02d}:{secs:02d}"
