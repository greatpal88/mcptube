"""CLI interface — thin wrapper over McpTubeService and FastMCP server."""

import typer
from pathlib import Path

from mcptube.config import settings
from mcptube.export import default_export_dir, export_video
from mcptube.ingestion.frames import FrameExtractionError
from mcptube.ingestion.youtube import ExtractionError
from mcptube.llm import LLMClient
from mcptube.service import (
    AmbiguousVideoError,
    McpTubeService,
    VideoAlreadyExistsError,
    VideoNotFoundError,
)
from mcptube.storage.sqlite import SQLiteVideoRepository
from mcptube.wiki.engine import WikiEngine
from mcptube.wiki.models import WikiPageType
from mcptube.wiki.storage import FileWikiRepository


app = typer.Typer(
    name="mcptube",
    help="Convert any YouTube video into an AI-queryable knowledge base.",
    no_args_is_help=True,
)

wiki_app = typer.Typer(
    name="wiki",
    help="Browse and search the wiki knowledge base.",
    no_args_is_help=True,
)
app.add_typer(wiki_app, name="wiki")


def _get_service() -> McpTubeService:
    """Create a service instance with default dependencies."""
    settings.ensure_dirs()
    llm = LLMClient()
    wiki_repo = FileWikiRepository()
    wiki_engine = WikiEngine(repo=wiki_repo, llm=llm)
    return McpTubeService(
        repository=SQLiteVideoRepository(),
        wiki_engine=wiki_engine,
        llm_client=llm,
    )


def _resolve_or_exit(svc: McpTubeService, query: str):
    """Resolve a video from human-friendly input or exit with error."""
    try:
        return svc.resolve_video(query)
    except VideoNotFoundError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1)
    except AmbiguousVideoError as e:
        typer.echo(f"⚠️  {e}", err=True)
        raise typer.Exit(code=1)


# --- Library Management ---


@app.command()
def add(
    url: str = typer.Argument(..., help="YouTube video URL to ingest."),
    text_only: bool = typer.Option(False, "--text-only", help="Skip vision frame analysis."),
) -> None:
    """Ingest a YouTube video into the library and build wiki pages."""
    svc = _get_service()
    try:
        video = svc.add_video(url, text_only=text_only)
        typer.echo(f"✅ Added: {video.title}")
        typer.echo(f"   ID:       {video.video_id}")
        typer.echo(f"   Channel:  {video.channel}")
        typer.echo(f"   Duration: {video.duration:.0f}s")
        typer.echo(f"   Segments: {len(video.transcript)}")
        if video.tags:
            typer.echo(f"   Tags:     {', '.join(video.tags)}")
        tier = "text-only" if text_only else "full analysis"
        typer.echo(f"   Wiki:     ✅ (processed: {tier})")
    except VideoAlreadyExistsError as e:
        typer.echo(f"⚠️  {e}", err=True)
        raise typer.Exit(code=1)
    except ExtractionError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1)


@app.command(name="list")
def list_videos() -> None:
    """List all videos in the library."""
    svc = _get_service()
    videos = svc.list_videos()
    if not videos:
        typer.echo("Library is empty. Use 'mcptube add <url>' to add a video.")
        return
    for i, v in enumerate(videos, 1):
        tags = f" [{', '.join(v.tags)}]" if v.tags else ""
        typer.echo(f"  {i}. {v.video_id}  {v.duration:>6.0f}s  {v.channel:<20s}  {v.title}{tags}")


@app.command()
def info(query: str = typer.Argument(..., help="Video ID, index number, or search text.")) -> None:
    """Show full details for a video."""
    svc = _get_service()
    video = _resolve_or_exit(svc, query)
    typer.echo(f"Title:       {video.title}")
    typer.echo(f"Channel:     {video.channel}")
    typer.echo(f"Duration:    {video.duration:.0f}s")
    typer.echo(f"URL:         {video.url}")
    typer.echo(f"Thumbnail:   {video.thumbnail_url}")
    typer.echo(f"Tags:        {', '.join(video.tags) or '(none)'}")
    typer.echo(f"Chapters:    {len(video.chapters)}")
    typer.echo(f"Segments:    {len(video.transcript)}")
    typer.echo(f"Added:       {video.added_at}")
    if video.chapters:
        typer.echo("\nChapters:")
        for ch in video.chapters:
            typer.echo(f"  [{ch.start:>7.1f}s] {ch.title}")


@app.command()
def remove(query: str = typer.Argument(..., help="Video ID, index number, or search text.")) -> None:
    """Remove a video from the library and wiki."""
    svc = _get_service()
    video = _resolve_or_exit(svc, query)
    svc.remove_video(video.video_id)
    typer.echo(f"🗑️  Removed: {video.title} ({video.video_id})")


# --- Search ---


@app.command()
def search(
    query: str = typer.Argument(..., help="Natural language search query."),
    limit: int = typer.Option(10, "--limit", "-n", help="Maximum results."),
) -> None:
    """Search the wiki knowledge base."""
    svc = _get_service()
    results = svc.wiki_search(query, limit=limit)
    if not results:
        typer.echo("No results found.")
        return
    for i, page in enumerate(results, 1):
        tags = f" [{', '.join(page.tags)}]" if page.tags else ""
        typer.echo(f"  {i}. [{page.page_type.value}] {page.title} ({page.slug}){tags}")


@app.command()
def ask(
    question: str = typer.Argument(..., help="Question to ask about your video library."),
) -> None:
    """Ask a question — answered via agentic wiki retrieval."""
    svc = _get_service()
    try:
        typer.echo("🤔 Thinking...")
        answer = svc.wiki_ask(question)
        typer.echo(f"\n{answer}")
    except RuntimeError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1)


# --- Frames ---


@app.command()
def frame(
    query: str = typer.Argument(..., help="Video ID, index number, or search text."),
    timestamp: float = typer.Argument(..., help="Timestamp in seconds to extract frame at."),
) -> None:
    """Extract a frame from a video at a specific timestamp."""
    svc = _get_service()
    video = _resolve_or_exit(svc, query)
    try:
        path = svc.get_frame(video.video_id, timestamp)
        typer.echo(f"🖼️  Frame extracted: {path}")
    except FrameExtractionError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def frame_query(
    query: str = typer.Argument(..., help="Video ID, index number, or search text."),
    search_query: str = typer.Argument(..., help="Natural language description of the moment to capture."),
) -> None:
    """Extract a frame by searching the transcript for the best matching moment."""
    svc = _get_service()
    video = _resolve_or_exit(svc, query)
    try:
        result = svc.get_frame_by_query(video.video_id, search_query)
        mins, secs = divmod(int(result["start"]), 60)
        typer.echo(f"🖼️  Frame extracted: {result['path']}")
        typer.echo(f"   Timestamp: [{mins:02d}:{secs:02d}]")
        typer.echo(f"   Matched:   {result['text']}")
    except (FrameExtractionError, RuntimeError) as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1)


# --- Export ---


@app.command()
def export(
    query: str = typer.Argument(
        None, help="Video ID, index number, or search text. Omit with --all."
    ),
    export_all: bool = typer.Option(
        False, "--all", help="Export every video in the library."
    ),
    out: str = typer.Option(
        None,
        "--out",
        "-o",
        help="Output directory. Defaults to $MCPTUBE_EXPORT_DIR.",
    ),
    no_fetch_published: bool = typer.Option(
        False,
        "--no-fetch-published",
        help="Skip the yt-dlp lookup that fills the 'published' frontmatter field.",
    ),
    no_assets: bool = typer.Option(
        False,
        "--no-assets",
        help="Do not copy frame images into <export dir>/../assets/<videoID>/.",
    ),
):
    """Export video(s) as self-contained source-material markdown.

    Writes the transcript (organised under chapters) and the raw per-frame
    vision descriptions — source material, not compiled wiki pages. Reads the
    existing stores only, so re-exporting never re-ingests. Re-running
    overwrites the same file rather than creating a duplicate.
    """
    if not export_all and not query:
        typer.echo("❌ Give a video, or use --all.", err=True)
        raise typer.Exit(code=1)

    out_dir = Path(out) if out else default_export_dir()

    if export_all:
        repo = SQLiteVideoRepository()
        stubs = repo.list_all()
        if not stubs:
            typer.echo("Library is empty. Nothing to export.")
            raise typer.Exit()
        # list_all() omits transcripts, so reload each video in full.
        videos = [v for v in (repo.get(s.video_id) for s in stubs) if v]
    else:
        svc = _get_service()
        videos = [_resolve_or_exit(svc, query)]

    written = []
    for video in videos:
        try:
            written.append((video, export_video(
                video, out_dir, fetch_published_date=not no_fetch_published,
                copy_assets=not no_assets)))
        except OSError as e:
            typer.echo(f"❌ {video.video_id}: {e}", err=True)

    for video, path in written:
        typer.echo(f"✅ {video.video_id}  →  {path}")
    if len(written) > 1:
        typer.echo(f"\n{len(written)} files written to {out_dir}")
    if not written:
        raise typer.Exit(code=1)


# --- Analysis & Reports ---


@app.command()
def classify(
    query: str = typer.Argument(..., help="Video ID, index number, or search text."),
) -> None:
    """Classify or re-classify a video using LLM."""
    svc = _get_service()
    video = _resolve_or_exit(svc, query)
    try:
        tags = svc.classify_video(video.video_id)
        typer.echo(f"🏷️  Tags for: {video.title}")
        typer.echo(f"   {', '.join(tags)}")
    except RuntimeError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def report(
    query: str = typer.Argument(..., help="Video ID, index number, or search text."),
    focus: str | None = typer.Option(None, "--focus", "-f", help="Focus query to guide the report."),
    fmt: str = typer.Option("markdown", "--format", help="Output format: markdown or html."),
    output: str | None = typer.Option(None, "--output", "-o", help="Save report to file."),
) -> None:
    """Generate an illustrated report for a single video."""
    svc = _get_service()
    video = _resolve_or_exit(svc, query)
    try:
        typer.echo(f"📝 Generating report for: {video.title}...")
        rpt, rendered = svc.generate_report(video.video_id, query=focus, fmt=fmt)
        if output:
            Path(output).write_text(rendered, encoding="utf-8")
            typer.echo(f"✅ Report saved: {output}")
        else:
            typer.echo(rendered)
    except (RuntimeError, Exception) as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def report_query(
    query: str = typer.Argument(..., help="Topic or question for the cross-video report."),
    tags: list[str] | None = typer.Option(None, "--tag", "-t", help="Filter by tag (repeatable)."),
    fmt: str = typer.Option("markdown", "--format", help="Output format: markdown or html."),
    output: str | None = typer.Option(None, "--output", "-o", help="Save report to file."),
) -> None:
    """Generate an illustrated report across matching library videos."""
    svc = _get_service()
    try:
        typer.echo(f"📝 Generating cross-video report for: {query}...")
        rpt, rendered = svc.generate_report_from_query(query, tags=tags, fmt=fmt)
        if output:
            Path(output).write_text(rendered, encoding="utf-8")
            typer.echo(f"✅ Report saved: {output}")
        else:
            typer.echo(rendered)
    except (RuntimeError, Exception) as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def discover(
    topic: str = typer.Argument(..., help="Topic to search YouTube for."),
) -> None:
    """Discover YouTube videos on a topic — filtered and clustered."""
    svc = _get_service()
    try:
        typer.echo(f"🔍 Searching YouTube for: {topic}...")
        result = svc.discover_videos(topic)
        if not result.clusters:
            typer.echo("No relevant videos found.")
            return
        typer.echo(f"Found {result.total_found} results, clustered:\n")
        for cluster_name, videos in result.clusters.items():
            typer.echo(f"  📁 {cluster_name}")
            for v in videos:
                mins, secs = divmod(int(v.duration), 60)
                typer.echo(f"     • {v.title} ({v.channel}, {mins}:{secs:02d})")
                typer.echo(f"       {v.url}")
            typer.echo("")
    except RuntimeError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def synthesize_cmd(
    topic: str = typer.Argument(..., help="Focus topic for cross-video synthesis."),
    videos: list[str] = typer.Option(..., "--video", "-v", help="Video IDs to synthesize (repeatable)."),
    fmt: str = typer.Option("markdown", "--format", help="Output format: markdown or html."),
    output: str | None = typer.Option(None, "--output", "-o", help="Save report to file."),
) -> None:
    """Cross-reference themes across multiple library videos."""
    svc = _get_service()
    try:
        typer.echo(f"🔗 Synthesizing {len(videos)} videos on: {topic}...")
        rpt, rendered = svc.synthesize(videos, topic, fmt=fmt)
        if output:
            Path(output).write_text(rendered, encoding="utf-8")
            typer.echo(f"✅ Synthesis saved: {output}")
        else:
            typer.echo(rendered)
    except (VideoNotFoundError, RuntimeError) as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1)


# --- Wiki subcommands ---


@wiki_app.command(name="list")
def wiki_list(
    page_type: str | None = typer.Option(None, "--type", "-t", help="Filter by type: video, entity, topic, concept."),
    tag: str | None = typer.Option(None, "--tag", help="Filter by tag."),
) -> None:
    """Browse all wiki pages."""
    svc = _get_service()
    pt = WikiPageType(page_type) if page_type else None
    pages = svc.wiki_list(page_type=pt, tag=tag)
    if not pages:
        typer.echo("Wiki is empty. Add videos to build the knowledge base.")
        return
    for i, page in enumerate(pages, 1):
        tags = f" [{', '.join(page.tags)}]" if page.tags else ""
        typer.echo(f"  {i}. [{page.page_type.value:<8s}] {page.title} ({page.slug}){tags}")


@wiki_app.command(name="show")
def wiki_show(
    slug: str = typer.Argument(..., help="Wiki page slug to display."),
) -> None:
    """Read a specific wiki page."""
    svc = _get_service()
    page = svc.wiki_show(slug)
    if page is None:
        typer.echo(f"❌ Wiki page not found: {slug}", err=True)
        raise typer.Exit(code=1)

    from mcptube.wiki.models import ConceptPage, EntityPage, TopicPage, VideoPage

    typer.echo(f"📄 {page.title}")
    typer.echo(f"   Type: {page.page_type.value}")
    typer.echo(f"   Slug: {page.slug}")
    if page.tags:
        typer.echo(f"   Tags: {', '.join(page.tags)}")
    if page.related_pages:
        typer.echo(f"   Related: {', '.join(page.related_pages)}")
    typer.echo(f"   Updated: {page.updated_at}")
    typer.echo("")

    if isinstance(page, VideoPage):
        typer.echo(f"Video ID: {page.video_id}")
        typer.echo(f"Channel:  {page.channel}")
        typer.echo(f"Duration: {page.duration:.0f}s")
        typer.echo(f"Tier:     {page.processing_tier}")
        typer.echo(f"\nSummary:\n{page.summary}")
        if page.key_timestamps:
            typer.echo("\nKey Timestamps:")
            for ts, desc in page.key_timestamps.items():
                typer.echo(f"  [{ts}] {desc}")
        if page.key_frames:
            typer.echo("\nKey Frames:")
            for f in page.key_frames:
                typer.echo(f"  {f.filename} ({f.timestamp:.1f}s): {f.description}")

    elif isinstance(page, EntityPage):
        typer.echo(f"Category: {page.category.value}")
        typer.echo(f"\nOverview:\n{page.overview}")
        if page.video_references:
            typer.echo("\nVideo References:")
            for ref in page.video_references:
                typer.echo(f"\n  From: {ref.title} ({ref.video_id})")
                typer.echo(f"  {ref.content}")

    elif isinstance(page, (TopicPage, ConceptPage)):
        typer.echo(f"\nSynthesis:\n{page.synthesis}")
        if page.contributions:
            typer.echo("\nPer-Video Contributions:")
            for c in page.contributions:
                typer.echo(f"\n  From: {c.title} ({c.video_id})")
                typer.echo(f"  {c.content}")


@wiki_app.command(name="search")
def wiki_search(
    query: str = typer.Argument(..., help="Search query."),
    limit: int = typer.Option(10, "--limit", "-n", help="Maximum results."),
) -> None:
    """Search wiki pages via full-text search."""
    svc = _get_service()
    results = svc.wiki_search(query, limit=limit)
    if not results:
        typer.echo("No results found.")
        return
    for i, page in enumerate(results, 1):
        tags = f" [{', '.join(page.tags)}]" if page.tags else ""
        typer.echo(f"  {i}. [{page.page_type.value:<8s}] {page.title} ({page.slug}){tags}")


@wiki_app.command(name="toc")
def wiki_toc() -> None:
    """Display the wiki table of contents."""
    svc = _get_service()
    typer.echo(svc.wiki_toc())


@wiki_app.command(name="history")
def wiki_history(
    slug: str = typer.Argument(..., help="Wiki page slug."),
) -> None:
    """Show version history for a wiki page."""
    svc = _get_service()
    versions = svc.wiki_history(slug)
    if not versions:
        typer.echo(f"No history found for: {slug}")
        return
    typer.echo(f"📜 Version history for: {slug}")
    for i, v in enumerate(versions, 1):
        typer.echo(f"  {i}. {v.updated_at}")


@wiki_app.command(name="export")
def wiki_export(
    fmt: str = typer.Option("markdown", "--format", help="Export format: markdown, html, or pdf."),
    output: str = typer.Option("wiki_export", "--output", "-o", help="Output file or directory."),
    slug: str | None = typer.Option(None, "--page", "-p", help="Export a single page by slug."),
) -> None:
    """Export wiki pages."""
    svc = _get_service()

    if slug:
        page = svc.wiki_show(slug)
        if page is None:
            typer.echo(f"❌ Wiki page not found: {slug}", err=True)
            raise typer.Exit(code=1)
        pages = [page]
    else:
        pages = svc.wiki_list()

    if not pages:
        typer.echo("Wiki is empty — nothing to export.")
        return

    if fmt == "markdown":
        _export_markdown(pages, output)
    elif fmt == "html":
        _export_html(pages, output)
    else:
        typer.echo(f"❌ Unsupported format: {fmt}. Use markdown or html.", err=True)
        raise typer.Exit(code=1)


def _export_markdown(pages, output_dir: str) -> None:
    """Export wiki pages as markdown files."""
    from mcptube.wiki.models import ConceptPage, EntityPage, TopicPage, VideoPage

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    for page in pages:
        lines = [f"# {page.title}", ""]
        lines.append(f"**Type:** {page.page_type.value}")
        if page.tags:
            lines.append(f"**Tags:** {', '.join(page.tags)}")
        if page.related_pages:
            lines.append(f"**Related:** {', '.join(page.related_pages)}")
        lines.append("")

        if isinstance(page, VideoPage):
            lines.append(f"**Video:** {page.video_id} by {page.channel}")
            lines.append(f"**Duration:** {page.duration:.0f}s")
            lines.append(f"\n## Summary\n{page.summary}")
            if page.key_timestamps:
                lines.append("\n## Key Timestamps")
                for ts, desc in page.key_timestamps.items():
                    lines.append(f"- [{ts}] {desc}")

        elif isinstance(page, EntityPage):
            lines.append(f"**Category:** {page.category.value}")
            lines.append(f"\n## Overview\n{page.overview}")
            if page.video_references:
                lines.append("\n## Video References")
                for ref in page.video_references:
                    lines.append(f"\n### From: {ref.title} ({ref.video_id})")
                    lines.append(ref.content)

        elif isinstance(page, (TopicPage, ConceptPage)):
            lines.append(f"\n## Synthesis\n{page.synthesis}")
            if page.contributions:
                lines.append("\n## Per-Video Contributions")
                for c in page.contributions:
                    lines.append(f"\n### From: {c.title} ({c.video_id})")
                    lines.append(c.content)

        filepath = out / f"{page.slug}.md"
        filepath.write_text("\n".join(lines), encoding="utf-8")

    typer.echo(f"✅ Exported {len(pages)} pages to: {out}/")


def _export_html(pages, output_file: str) -> None:
    """Export wiki pages as a single HTML file."""
    from mcptube.wiki.models import ConceptPage, EntityPage, TopicPage, VideoPage

    sections = []
    for page in pages:
        content = f"<h2>{page.title} <small>({page.page_type.value})</small></h2>"
        if page.tags:
            content += f"<p><strong>Tags:</strong> {', '.join(page.tags)}</p>"

        if isinstance(page, VideoPage):
            content += f"<p><strong>Video:</strong> {page.video_id} by {page.channel}</p>"
            content += f"<p>{page.summary}</p>"

        elif isinstance(page, EntityPage):
            content += f"<p><strong>Category:</strong> {page.category.value}</p>"
            content += f"<p>{page.overview}</p>"

        elif isinstance(page, (TopicPage, ConceptPage)):
            content += f"<p>{page.synthesis}</p>"
            for c in page.contributions:
                content += f"<h3>From: {c.title}</h3><p>{c.content}</p>"

        sections.append(f"<section>{content}</section>")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>mcptube Wiki Export</title>
<style>
    body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; line-height: 1.6; }}
    h1 {{ border-bottom: 2px solid #333; padding-bottom: 0.5rem; }}
    h2 {{ color: #2c5282; margin-top: 2rem; }}
    small {{ color: #888; font-weight: normal; }}
    section {{ border-bottom: 1px solid #eee; padding-bottom: 1rem; }}
</style>
</head>
<body>
<h1>mcptube Wiki</h1>
{"".join(sections)}
</body>
</html>"""

    out = Path(output_file)
    if not out.suffix:
        out = out.with_suffix(".html")
    out.write_text(html, encoding="utf-8")
    typer.echo(f"✅ Exported {len(pages)} pages to: {out}")


# --- Server ---


@app.command()
def serve(
    stdio: bool = typer.Option(False, "--stdio", help="Use stdio transport instead of HTTP."),
    host: str = typer.Option(settings.host, "--host", help="Host to bind to."),
    port: int = typer.Option(settings.port, "--port", help="Port to bind to."),
    reload: bool = typer.Option(False, "--reload", help="Enable hot-reload for development."),
) -> None:
    """Start the mcptube MCP server."""
    from mcptube.server import mcp

    if stdio:
        typer.echo("Starting mcptube MCP server (stdio)...", err=True)
        mcp.run(transport="stdio")
    else:
        typer.echo(f"Starting mcptube MCP server on http://{host}:{port}/mcp")
        mcp.run(transport="streamable-http", host=host, port=port)
