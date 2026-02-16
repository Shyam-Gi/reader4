"""
Parses EPUB/PDF files into a structured object for the local reader web interface.
"""

import os
import pickle
import shutil
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from datetime import datetime
from urllib.parse import unquote
from html import escape
import re

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup, Comment
from pypdf import PdfReader

# --- Data structures ---

@dataclass
class ChapterContent:
    """
    Represents a physical file in the EPUB (Spine Item).
    A single file might contain multiple logical chapters (TOC entries).
    """
    id: str           # Internal ID (e.g., 'item_1')
    href: str         # Filename (e.g., 'part01.html')
    title: str        # Best guess title from file
    content: str      # Cleaned HTML with rewritten image paths
    text: str         # Plain text for search/LLM context
    order: int        # Linear reading order


@dataclass
class TOCEntry:
    """Represents a logical entry in the navigation sidebar."""
    title: str
    href: str         # original href (e.g., 'part01.html#chapter1')
    file_href: str    # just the filename (e.g., 'part01.html')
    anchor: str       # just the anchor (e.g., 'chapter1'), empty if none
    children: List['TOCEntry'] = field(default_factory=list)


@dataclass
class BookMetadata:
    """Metadata"""
    title: str
    language: str
    authors: List[str] = field(default_factory=list)
    description: Optional[str] = None
    publisher: Optional[str] = None
    date: Optional[str] = None
    identifiers: List[str] = field(default_factory=list)
    subjects: List[str] = field(default_factory=list)


@dataclass
class Book:
    """The Master Object to be pickled."""
    metadata: BookMetadata
    spine: List[ChapterContent]  # The actual content (linear files)
    toc: List[TOCEntry]          # The navigation tree
    images: Dict[str, str]       # Map: original_path -> local_path

    # Meta info
    source_file: str
    processed_at: str
    version: str = "3.0"


# --- Utilities ---

def clean_html_content(soup: BeautifulSoup) -> BeautifulSoup:

    # Remove dangerous/useless tags
    for tag in soup(['script', 'style', 'iframe', 'video', 'nav', 'form', 'button']):
        tag.decompose()

    # Remove HTML comments
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    # Remove input tags
    for tag in soup.find_all('input'):
        tag.decompose()

    return soup


def extract_plain_text(soup: BeautifulSoup) -> str:
    """Extract clean text for LLM/Search usage."""
    text = soup.get_text(separator=' ')
    # Collapse whitespace
    return ' '.join(text.split())


def text_to_html(text: str) -> str:
    """Convert plain text into simple paragraph HTML."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "<p></p>"
    return "".join(f"<p>{escape(line)}</p>" for line in lines)


def normalize_pdf_text(text: str) -> str:
    """
    Normalize common PDF extraction artifacts while preserving paragraph boundaries.
    """
    if not text:
        return ""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")

    # Merge words split by line-break hyphenation: "serv-\nices" -> "services"
    normalized = re.sub(r"([A-Za-z])[-\u2010\u2011\u00AD]\s*\n\s*([A-Za-z])", r"\1\2", normalized)

    # Join single newlines inside paragraphs; keep blank-line paragraph breaks.
    lines = normalized.split("\n")
    rebuilt: List[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            rebuilt.append("")
            continue
        if not rebuilt or rebuilt[-1] == "":
            rebuilt.append(stripped)
            continue

        prev = rebuilt[-1]
        # Start a new line for list-like/bullet-like content.
        if re.match(r"^([-*]|\d+[.)])\s", stripped):
            rebuilt.append(stripped)
            continue
        # Continue paragraph when previous line does not clearly terminate.
        if not re.search(r"[.!?:;)]$", prev):
            rebuilt[-1] = f"{prev} {stripped}"
        else:
            rebuilt.append(stripped)

    # Compress repeated blank lines.
    compact: List[str] = []
    for line in rebuilt:
        if line == "" and compact and compact[-1] == "":
            continue
        compact.append(line)

    return "\n".join(compact).strip()


def parse_toc_recursive(toc_list, depth=0) -> List[TOCEntry]:
    """
    Recursively parses the TOC structure from ebooklib.
    """
    result = []

    for item in toc_list:
        # ebooklib TOC items are either `Link` objects or tuples (Section, [Children])
        if isinstance(item, tuple):
            section, children = item
            entry = TOCEntry(
                title=section.title,
                href=section.href,
                file_href=section.href.split('#')[0],
                anchor=section.href.split('#')[1] if '#' in section.href else "",
                children=parse_toc_recursive(children, depth + 1)
            )
            result.append(entry)
        elif isinstance(item, epub.Link):
            entry = TOCEntry(
                title=item.title,
                href=item.href,
                file_href=item.href.split('#')[0],
                anchor=item.href.split('#')[1] if '#' in item.href else ""
            )
            result.append(entry)
        # Note: ebooklib sometimes returns direct Section objects without children
        elif isinstance(item, epub.Section):
             entry = TOCEntry(
                title=item.title,
                href=item.href,
                file_href=item.href.split('#')[0],
                anchor=item.href.split('#')[1] if '#' in item.href else ""
            )
             result.append(entry)

    return result


def get_fallback_toc(book_obj) -> List[TOCEntry]:
    """
    If TOC is missing, build a flat one from the Spine.
    """
    toc = []
    for item in book_obj.get_items():
        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            name = item.get_name()
            # Try to guess a title from the content or ID
            title = item.get_name().replace('.html', '').replace('.xhtml', '').replace('_', ' ').title()
            toc.append(TOCEntry(title=title, href=name, file_href=name, anchor=""))
    return toc


def extract_metadata_robust(book_obj) -> BookMetadata:
    """
    Extracts metadata handling both single and list values.
    """
    def get_list(key):
        data = book_obj.get_metadata('DC', key)
        return [x[0] for x in data] if data else []

    def get_one(key):
        data = book_obj.get_metadata('DC', key)
        return data[0][0] if data else None

    return BookMetadata(
        title=get_one('title') or "Untitled",
        language=get_one('language') or "en",
        authors=get_list('creator'),
        description=get_one('description'),
        publisher=get_one('publisher'),
        date=get_one('date'),
        identifiers=get_list('identifier'),
        subjects=get_list('subject')
    )


# --- Main Conversion Logic ---

def process_epub(epub_path: str, output_dir: str) -> Book:
    if not epub_path.lower().endswith(".epub"):
        raise ValueError(
            f"Unsupported file type: '{epub_path}'. This script currently supports only .epub files."
        )

    # 1. Load Book
    print(f"Loading {epub_path}...")
    book = epub.read_epub(epub_path)

    # 2. Extract Metadata
    metadata = extract_metadata_robust(book)

    # 3. Prepare Output Directories
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    images_dir = os.path.join(output_dir, 'images')
    os.makedirs(images_dir, exist_ok=True)

    # 4. Extract Images & Build Map
    print("Extracting images...")
    image_map = {} # Key: internal_path, Value: local_relative_path

    for item in book.get_items():
        if item.get_type() == ebooklib.ITEM_IMAGE:
            # Normalize filename
            original_fname = os.path.basename(item.get_name())
            # Sanitize filename for OS
            safe_fname = "".join([c for c in original_fname if c.isalpha() or c.isdigit() or c in '._-']).strip()

            # Save to disk
            local_path = os.path.join(images_dir, safe_fname)
            with open(local_path, 'wb') as f:
                f.write(item.get_content())

            # Map keys: We try both the full internal path and just the basename
            # to be robust against messy HTML src attributes
            rel_path = f"images/{safe_fname}"
            image_map[item.get_name()] = rel_path
            image_map[original_fname] = rel_path

    # 5. Process TOC
    print("Parsing Table of Contents...")
    toc_structure = parse_toc_recursive(book.toc)
    if not toc_structure:
        print("Warning: Empty TOC, building fallback from Spine...")
        toc_structure = get_fallback_toc(book)

    # 6. Process Content (Spine-based to preserve HTML validity)
    print("Processing chapters...")
    spine_chapters = []

    # We iterate over the spine (linear reading order)
    for i, spine_item in enumerate(book.spine):
        item_id, linear = spine_item
        item = book.get_item_with_id(item_id)

        if not item:
            continue

        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            # Raw content
            raw_content = item.get_content().decode('utf-8', errors='ignore')
            soup = BeautifulSoup(raw_content, 'html.parser')

            # A. Fix Images
            for img in soup.find_all('img'):
                src = img.get('src', '')
                if not src: continue

                # Decode URL (part01/image%201.jpg -> part01/image 1.jpg)
                src_decoded = unquote(src)
                filename = os.path.basename(src_decoded)

                # Try to find in map
                if src_decoded in image_map:
                    img['src'] = image_map[src_decoded]
                elif filename in image_map:
                    img['src'] = image_map[filename]

            # B. Clean HTML
            soup = clean_html_content(soup)

            # C. Extract Body Content only
            body = soup.find('body')
            if body:
                # Extract inner HTML of body
                final_html = "".join([str(x) for x in body.contents])
            else:
                final_html = str(soup)

            # D. Create Object
            chapter = ChapterContent(
                id=item_id,
                href=item.get_name(), # Important: This links TOC to Content
                title=f"Section {i+1}", # Fallback, real titles come from TOC
                content=final_html,
                text=extract_plain_text(soup),
                order=i
            )
            spine_chapters.append(chapter)

    # 7. Final Assembly
    final_book = Book(
        metadata=metadata,
        spine=spine_chapters,
        toc=toc_structure,
        images=image_map,
        source_file=os.path.basename(epub_path),
        processed_at=datetime.now().isoformat()
    )

    return final_book


def get_pdf_outline_entries(reader: PdfReader) -> List[Dict[str, Any]]:
    """
    Return outline entries as [{"title": str, "page": int}] sorted by page.
    Selects a bookmark depth that is likely to represent chapter-level sections.
    """
    chapter_like_re = re.compile(
        r"\b(chapter|chap\.|prologue|epilogue|appendix)\b", re.IGNORECASE
    )
    part_like_re = re.compile(r"\b(part|book|section)\b", re.IGNORECASE)

    def as_entry(item, level: int) -> Optional[Dict[str, Any]]:
        title = getattr(item, "title", None)
        if not title:
            return None
        try:
            page_number = reader.get_destination_page_number(item)
        except Exception:
            return None
        if page_number is None or page_number < 0:
            return None
        return {"title": str(title).strip(), "page": int(page_number), "level": level}

    def dedupe_by_page(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        dedup = {}
        for e in entries:
            key = (e["title"], e["page"])
            dedup[key] = e
        sorted_entries = sorted(dedup.values(), key=lambda x: x["page"])

        unique_by_page: List[Dict[str, Any]] = []
        seen_pages = set()
        for entry in sorted_entries:
            page = entry["page"]
            if page in seen_pages:
                continue
            seen_pages.add(page)
            unique_by_page.append(entry)
        return unique_by_page

    entries_by_level: Dict[int, List[Dict[str, Any]]] = {}

    def walk(items, level: int = 0):
        for item in items:
            if isinstance(item, list):
                walk(item, level + 1)
                continue
            entry = as_entry(item, level)
            if not entry:
                continue
            entries_by_level.setdefault(level, []).append(entry)

    try:
        outline = reader.outline
    except Exception:
        outline = []

    if outline:
        walk(outline, 0)
    if not entries_by_level:
        return []

    normalized_by_level: Dict[int, List[Dict[str, Any]]] = {
        level: dedupe_by_page(entries)
        for level, entries in entries_by_level.items()
    }

    # If top-level is mostly "Part/Book/Section", prefer one level deeper.
    top_entries = normalized_by_level.get(0, [])
    if top_entries:
        part_like_count = sum(1 for e in top_entries if part_like_re.search(e["title"]))
        if part_like_count >= max(2, len(top_entries) // 2):
            if 1 in normalized_by_level and normalized_by_level[1]:
                return [
                    {"title": e["title"], "page": e["page"]}
                    for e in normalized_by_level[1]
                ]

    # Prefer the level with strongest chapter-like signal.
    best_level = None
    best_score = None
    for level, entries in normalized_by_level.items():
        if len(entries) < 3:
            continue
        chapter_like_count = sum(
            1 for e in entries if chapter_like_re.search(e["title"])
        )
        ratio = chapter_like_count / len(entries) if entries else 0.0
        score = (chapter_like_count, ratio, len(entries))
        if best_score is None or score > best_score:
            best_level = level
            best_score = score

    if best_level is not None and best_score and best_score[0] >= 3:
        return [
            {"title": e["title"], "page": e["page"]}
            for e in normalized_by_level[best_level]
        ]

    # Fallback to top-level, then shallowest available level.
    fallback_level = 0 if 0 in normalized_by_level else min(normalized_by_level.keys())
    return [
        {"title": e["title"], "page": e["page"]}
        for e in normalized_by_level[fallback_level]
    ]


def process_pdf(pdf_path: str, output_dir: str) -> Book:
    if not pdf_path.lower().endswith(".pdf"):
        raise ValueError(
            f"Unsupported file type: '{pdf_path}'. This function expects a .pdf file."
        )

    print(f"Loading {pdf_path}...")
    reader = PdfReader(pdf_path)

    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    doc_meta = reader.metadata or {}
    title = doc_meta.get("/Title") or os.path.splitext(os.path.basename(pdf_path))[0]
    author = doc_meta.get("/Author")
    subject = doc_meta.get("/Subject")
    producer = doc_meta.get("/Producer")
    creation_date = doc_meta.get("/CreationDate")

    metadata = BookMetadata(
        title=title,
        language="en",
        authors=[author] if author else [],
        description=subject if subject else None,
        publisher=producer if producer else None,
        date=creation_date if creation_date else None,
    )

    page_texts: List[str] = []
    print("Extracting PDF text...")
    for page in reader.pages:
        page_texts.append(normalize_pdf_text(page.extract_text() or ""))

    outline_entries = get_pdf_outline_entries(reader)
    spine_chapters: List[ChapterContent] = []
    toc_structure: List[TOCEntry] = []

    if outline_entries:
        print(f"Found {len(outline_entries)} outline entries. Building chapter ranges...")
        starts = [e["page"] for e in outline_entries]
        starts.append(len(page_texts))

        for i, entry in enumerate(outline_entries):
            start = starts[i]
            end = starts[i + 1]
            if start >= len(page_texts):
                continue
            end = max(start + 1, min(end, len(page_texts)))
            segment_text = "\n\n".join(page_texts[start:end]).strip()

            href = f"chapter-{i + 1}"
            chapter_title = entry["title"] or f"Chapter {i + 1}"
            spine_chapters.append(
                ChapterContent(
                    id=href,
                    href=href,
                    title=chapter_title,
                    content=text_to_html(segment_text),
                    text=" ".join(segment_text.split()),
                    order=i,
                )
            )
            toc_structure.append(
                TOCEntry(
                    title=chapter_title,
                    href=href,
                    file_href=href,
                    anchor="",
                )
            )
    else:
        print("No PDF outline found. Falling back to page-based segmentation...")
        for i, page_text in enumerate(page_texts):
            href = f"page-{i + 1}"
            page_title = f"Page {i + 1}"
            spine_chapters.append(
                ChapterContent(
                    id=href,
                    href=href,
                    title=page_title,
                    content=text_to_html(page_text),
                    text=" ".join(page_text.split()),
                    order=i,
                )
            )
            toc_structure.append(
                TOCEntry(
                    title=page_title,
                    href=href,
                    file_href=href,
                    anchor="",
                )
            )

    final_book = Book(
        metadata=metadata,
        spine=spine_chapters,
        toc=toc_structure,
        images={},
        source_file=os.path.basename(pdf_path),
        processed_at=datetime.now().isoformat()
    )
    return final_book


def process_book(input_path: str, output_dir: str) -> Book:
    ext = os.path.splitext(input_path)[1].lower()
    if ext == ".epub":
        return process_epub(input_path, output_dir)
    if ext == ".pdf":
        return process_pdf(input_path, output_dir)
    raise ValueError(
        f"Unsupported file type: '{input_path}'. Supported extensions: .epub, .pdf."
    )


def save_to_pickle(book: Book, output_dir: str):
    p_path = os.path.join(output_dir, 'book.pkl')
    with open(p_path, 'wb') as f:
        pickle.dump(book, f)
    print(f"Saved structured data to {p_path}")


# --- CLI ---

if __name__ == "__main__":

    import sys
    if len(sys.argv) < 2:
        print("Usage: python reader4.py <file.epub|file.pdf>")
        sys.exit(1)

    input_file = sys.argv[1]
    assert os.path.exists(input_file), "File not found."
    if not input_file.lower().endswith((".epub", ".pdf")):
        print(f"Error: '{input_file}' is not a supported file type.")
        print("This tool currently supports .epub and .pdf input files.")
        sys.exit(1)
    out_dir = os.path.splitext(input_file)[0] + "_data"

    book_obj = process_book(input_file, out_dir)
    save_to_pickle(book_obj, out_dir)
    print("\n--- Summary ---")
    print(f"Title: {book_obj.metadata.title}")
    print(f"Authors: {', '.join(book_obj.metadata.authors)}")
    print(f"Physical Files (Spine): {len(book_obj.spine)}")
    print(f"TOC Root Items: {len(book_obj.toc)}")
    print(f"Images extracted: {len(book_obj.images)}")
