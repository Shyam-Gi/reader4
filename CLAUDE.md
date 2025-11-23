# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Reader3 is a lightweight, self-hosted EPUB reader web application. The core workflow is:
1. Process EPUB files into structured data using `reader3.py`
2. Serve the processed books via a FastAPI web server using `server.py`
3. Read books chapter-by-chapter with a clean web interface optimized for copying content to LLMs

## Development Commands

### Setup and Dependencies
The project uses [uv](https://docs.astral.sh/uv/) for dependency management. Python 3.10+ required.

```bash
# Process an EPUB file (creates a {book_name}_data directory)
uv run reader3.py <path_to_book.epub>

# Start the web server (runs at http://127.0.0.1:8123)
uv run server.py
```

### Library Management
- Books are stored as `{book_name}_data/` directories containing:
  - `book.pkl` - Pickled Book object with metadata, spine, TOC, and content
  - `images/` - Extracted images from the EPUB
- To remove a book: delete its `_data` directory
- Server auto-discovers all `*_data` directories in the root folder

## Architecture

### Core Data Model (reader3.py)

**Book Processing Pipeline:**
1. `process_epub()` - Main entry point that orchestrates EPUB parsing
2. EPUB parsing via ebooklib → extracts metadata, spine (linear reading order), TOC (navigation tree), and images
3. HTML cleaning → removes scripts, styles, forms, dangerous elements
4. Image path rewriting → converts EPUB-internal paths to local `images/{filename}` paths
5. Serialization → entire Book object pickled to `book.pkl`

**Key Data Structures:**
- `Book` - Master container with metadata, spine, toc, and image map
- `ChapterContent` - Represents a physical file in the EPUB spine (linear reading order). Contains cleaned HTML content and extracted plain text
- `TOCEntry` - Logical navigation entry (may have nested children). Maps to spine files via href matching
- `BookMetadata` - Standard DC metadata (title, authors, publisher, etc.)

**Critical Distinction:**
- **Spine** = Physical reading order (files as they appear in EPUB)
- **TOC** = Logical navigation tree (may reference multiple positions in the same file via anchors)
- Server routes use spine indices (`/read/{book_id}/{chapter_index}`) for linear navigation
- TOC entries map to spine via filename matching in JavaScript (see reader.html:124-151)

### Web Server (server.py)

**FastAPI Routes:**
- `GET /` - Library view listing all processed books
- `GET /read/{book_id}` - Redirects to first chapter (index 0)
- `GET /read/{book_id}/{chapter_index}` - Main reader interface with sidebar TOC
- `GET /read/{book_id}/images/{image_name}` - Serves extracted images

**Book Loading:**
- `load_book_cached()` uses `@lru_cache(maxsize=10)` to avoid repeated disk reads
- Books are loaded from pickle files on-demand
- Cache key is the folder name (e.g., "dracula_data")

### Frontend (templates/)

**library.html** - Grid view of available books with basic metadata

**reader.html** - Two-column layout:
- Left sidebar: Nested TOC navigation tree (rendered via Jinja2 recursive macro)
- Right panel: Current chapter content with Previous/Next navigation
- JavaScript spine map (line 127-131) enables TOC → chapter index lookup
- `findAndGo()` function (line 133-151) handles TOC link clicks by mapping filenames to spine indices

## Dependencies

From pyproject.toml:
- `ebooklib` - EPUB parsing and manipulation
- `beautifulsoup4` - HTML parsing and cleaning
- `fastapi` - Web framework
- `jinja2` - Template engine
- `uvicorn` - ASGI server

## Project Philosophy

This is a minimal, "vibe-coded" project (per README) designed to illustrate reading books with LLMs. It intentionally avoids complexity:
- No database - just pickle files and directories
- No user accounts or authentication
- No advanced features (bookmarks, annotations, etc.)
- Simple file-based library management

When making changes, preserve this simplicity and avoid adding unnecessary abstractions or features.
