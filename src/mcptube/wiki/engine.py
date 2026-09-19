"""Wiki engine — orchestrates knowledge extraction, updates, and retrieval."""

import logging

from mcptube.llm import LLMClient, LLMError
from mcptube.models import Video
from mcptube.wiki.extractor import KnowledgeExtractor
from mcptube.wiki.models import FrameDescription, WikiPageBase, WikiPageType
from mcptube.wiki.repository import WikiRepository
from mcptube.wiki.updater import WikiUpdater

logger = logging.getLogger(__name__)

def _skip_wiki() -> bool:
    """True when MCPTUBE_SKIP_WIKI asks for video-page-only ingest."""
    import os

    return os.environ.get("MCPTUBE_SKIP_WIKI", "").strip().lower() in {
        "1", "true", "yes", "on",
    }



class WikiEngine:
    """High-level orchestrator for the wiki knowledge base.

    This is the single entry point that McpTubeService calls.
    It coordinates extraction, updates, retrieval, and TOC generation.
    """

    def __init__(self, repo: WikiRepository, llm: LLMClient) -> None:
        self._repo = repo
        self._llm = llm
        self._extractor = KnowledgeExtractor(llm)
        self._updater = WikiUpdater(repo, llm)

    # --- Ingestion ---

    def ingest_video(
        self,
        video: Video,
        frame_descriptions: list[FrameDescription] | None = None,
        text_only: bool = False,
    ) -> dict:
        """Extract knowledge from a video and merge into the wiki.

        Args:
            video: Full Video model with transcript.
            frame_descriptions: Optional vision frame descriptions.
            text_only: If True, skip frame descriptions.

        Returns:
            Stats dict: {"created": int, "updated": int, "skipped": int}

        Raises:
            LLMError: If extraction or update fails.
        """
        logger.info("Ingesting video into wiki: %s — %s", video.video_id, video.title)

        # Step 1: Extract knowledge (single LLM pass)
        #
        # MCPTUBE_SKIP_WIKI=1 skips the knowledge-extraction pass and, with it,
        # every entity/topic/concept page -- so the updater has nothing to
        # synthesise and makes no LLM calls either. The video page is still
        # built and saved, because its key_frames list is assembled
        # mechanically from the vision output; only `summary` and
        # `key_timestamps` come from the skipped LLM pass and are left empty.
        # Transcript, tags, frame extraction and vision descriptions are
        # unaffected, and the markdown export reads key_frames only.
        if _skip_wiki():
            logger.info(
                "MCPTUBE_SKIP_WIKI set: saving video page only (transcript + "
                "%d frame descriptions kept); skipping knowledge extraction "
                "and entity/topic/concept synthesis",
                len(frame_descriptions or []),
            )
            extracted = self._extractor.build_pages_without_llm(
                video, frame_descriptions, text_only
            )
        else:
            extracted = self._extractor.extract(
                video, frame_descriptions, text_only
            )

        # Step 2: Merge into existing wiki
        stats = self._updater.update_wiki(extracted)

        logger.info(
            "Wiki ingest complete for %s: %d created, %d updated, %d skipped",
            video.video_id, stats["created"], stats["updated"], stats["skipped"],
        )
        return stats

    # --- Retrieval ---

    def search(self, query: str, limit: int = 10) -> list[WikiPageBase]:
        """FTS5 search across wiki pages.

        Args:
            query: Search query string.
            limit: Maximum results.

        Returns:
            List of matching wiki pages.
        """
        return self._repo.search(query, limit=limit)

    def ask(self, question: str) -> str:
        """Agentic Q&A — hybrid retrieval over wiki.

        Step 1: FTS5 narrows to candidate pages
        Step 2: Agent reads candidates + TOC, reasons, answers

        Args:
            question: User's question.

        Returns:
            Answer string.

        Raises:
            LLMError: If LLM call fails.
        """
        if not self._llm.available:
            raise LLMError("Asking questions requires an LLM. Set an API key.")

        # Step 1: FTS5 search for candidates
        candidates = self._repo.search(question, limit=5)

        # Step 2: Get TOC for broader context
        toc = self._repo.get_toc()

        # Step 3: Build agent prompt
        candidate_texts = []
        for page in candidates:
            candidate_texts.append(self._format_page_for_context(page))

        candidates_section = "\n\n---\n\n".join(candidate_texts) if candidate_texts else "(No direct matches found)"

        prompt = f"""You are a knowledgeable assistant with access to a curated video wiki.
Use the wiki content below to answer the user's question thoroughly and accurately.

## Wiki Table of Contents
{toc}

## Most Relevant Pages
{candidates_section}

## Question
{question}

Guidelines:
- Answer based ONLY on the wiki content provided
- Cite video sources when referencing specific information
- If the wiki doesn't contain enough information, say so
- If multiple videos offer different perspectives, present all of them
- Be thorough but concise"""

        return self._llm._complete(prompt, max_tokens=4096)

    # --- Page access ---

    def get_page(self, slug: str) -> WikiPageBase | None:
        """Get a specific wiki page by slug."""
        return self._repo.get_page(slug)

    def list_pages(
        self,
        page_type: WikiPageType | None = None,
        tag: str | None = None,
    ) -> list[WikiPageBase]:
        """List wiki pages with optional filtering."""
        return self._repo.list_pages(page_type=page_type, tag=tag)

    def get_toc(self) -> str:
        """Get the wiki table of contents."""
        return self._repo.get_toc()

    def get_page_history(self, slug: str) -> list[WikiPageBase]:
        """Get version history for a wiki page."""
        return self._repo.get_page_history(slug)

    # --- Cleanup ---

    def remove_video(self, video_id: str) -> int:
        """Remove a video's wiki page and clean references from other pages.

        Note: Does NOT remove entity/topic/concept pages — they may
        have contributions from other videos. Only removes the video
        page itself and the specific video's contributions.

        Args:
            video_id: YouTube video ID.

        Returns:
            Number of pages modified.
        """
        modified = 0
        video_slug = f"video-{video_id}"

        # Delete the video page
        if self._repo.exists(video_slug):
            self._repo.delete_page(video_slug)
            modified += 1

        # Clean references from entity pages
        for page in self._repo.list_pages(page_type=WikiPageType.ENTITY):
            from mcptube.wiki.models import EntityPage
            if not isinstance(page, EntityPage):
                continue
            original_count = len(page.video_references)
            page.video_references = [
                ref for ref in page.video_references if ref.video_id != video_id
            ]
            if video_slug in page.related_pages:
                page.related_pages.remove(video_slug)
            if len(page.video_references) < original_count:
                if not page.video_references:
                    self._repo.delete_page(page.slug)
                else:
                    self._repo.save_page(page)
                modified += 1

        # Clean contributions from topic/concept pages
        for page_type in (WikiPageType.TOPIC, WikiPageType.CONCEPT):
            for page in self._repo.list_pages(page_type=page_type):
                if not hasattr(page, "contributions"):
                    continue
                original_count = len(page.contributions)
                page.contributions = [
                    c for c in page.contributions if c.video_id != video_id
                ]
                if video_slug in page.related_pages:
                    page.related_pages.remove(video_slug)
                if len(page.contributions) < original_count:
                    if not page.contributions:
                        self._repo.delete_page(page.slug)
                    else:
                        self._repo.save_page(page)
                    modified += 1

        logger.info("Removed video %s from wiki: %d pages modified", video_id, modified)
        return modified

    # --- Internal helpers ---

    @staticmethod
    def _format_page_for_context(page: WikiPageBase) -> str:
        """Format a wiki page for inclusion in an LLM prompt."""
        from mcptube.wiki.models import (
            ConceptPage,
            EntityPage,
            TopicPage,
            VideoPage,
        )

        lines = [f"# {page.title} ({page.page_type.value})"]
        lines.append(f"Slug: {page.slug}")
        if page.tags:
            lines.append(f"Tags: {', '.join(page.tags)}")

        if isinstance(page, VideoPage):
            lines.append(f"Video: {page.video_id} by {page.channel}")
            lines.append(f"Summary: {page.summary}")
            if page.key_timestamps:
                lines.append("Key moments:")
                for ts, desc in page.key_timestamps.items():
                    lines.append(f"  [{ts}] {desc}")

        elif isinstance(page, EntityPage):
            lines.append(f"Category: {page.category.value}")
            lines.append(f"Overview: {page.overview}")
            for ref in page.video_references:
                lines.append(f"From {ref.title}: {ref.content}")

        elif isinstance(page, (TopicPage, ConceptPage)):
            lines.append(f"Synthesis: {page.synthesis}")
            for contrib in page.contributions:
                lines.append(f"From {contrib.title}: {contrib.content}")

        return "\n".join(lines)
