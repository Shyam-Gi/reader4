import os
import pickle
import re
import shutil
from functools import lru_cache
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from reader4 import Book, BookMetadata, ChapterContent, TOCEntry

app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/assets", StaticFiles(directory="assets"), name="assets")

# Where are the book folders located?
BOOKS_DIR = "."
FAVICON_CANDIDATES = [
    os.path.join("assets", "icons", "favicon.png"),
    os.path.join("assets", "icons", "reader4.jpg"),
]
IGNORED_SCAN_DIRS = {".git", ".venv", "__pycache__", "assets", "templates"}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Serve a site icon for browser tabs."""
    for icon_name in FAVICON_CANDIDATES:
        icon_path = os.path.join(BOOKS_DIR, icon_name)
        if os.path.exists(icon_path):
            return FileResponse(icon_path)
    raise HTTPException(status_code=404, detail="Favicon not found")


def decode_book_id(book_id: str) -> str:
    # Route-safe encoding: nested path separators are represented as "__"
    return book_id.replace("__", os.sep)


def encode_book_id(rel_path: str) -> str:
    return rel_path.replace(os.sep, "__")


def safe_book_dir(book_id: str) -> Optional[str]:
    rel = os.path.normpath(decode_book_id(book_id)).strip()
    if not rel or rel == ".":
        return None
    if os.path.isabs(rel) or rel.startswith("..") or f"{os.sep}..{os.sep}" in rel:
        return None

    root_abs = os.path.abspath(BOOKS_DIR)
    full_path = os.path.abspath(os.path.join(root_abs, rel))
    if os.path.commonpath([root_abs, full_path]) != root_abs:
        return None
    return full_path


@lru_cache(maxsize=10)
def load_book_cached(folder_name: str) -> Optional[Book]:
    """
    Loads the book from the pickle file.
    Cached so we don't re-read the disk on every click.
    """
    book_dir = safe_book_dir(folder_name)
    if not book_dir:
        return None
    file_path = os.path.join(book_dir, "book.pkl")
    if not os.path.exists(file_path):
        return None

    try:
        with open(file_path, "rb") as f:
            book = pickle.load(f)
        return book
    except Exception as e:
        print(f"Error loading book {folder_name}: {e}")
        return None


def category_from_rel_path(rel_path: str) -> str:
    parts = rel_path.split(os.sep)
    if len(parts) <= 1:
        return "", "Uncategorized"
    key = parts[0].strip()
    label = key.replace("_", " ").replace("-", " ").strip()
    return key, (label.title() if label else "Uncategorized")


def normalize_category_key(category: str) -> str:
    raw = (category or "").strip()
    if not raw:
        return ""
    normalized = raw.lower().replace(" ", "-")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", normalized):
        raise HTTPException(status_code=400, detail="Invalid category name")
    return normalized

@app.get("/", response_class=HTMLResponse)
async def library_view(request: Request):
    """Lists all available processed books."""
    books = []

    # Scan directory tree for folders ending in '_data' that contain book.pkl
    if os.path.exists(BOOKS_DIR):
        for root, dirs, files in os.walk(BOOKS_DIR, topdown=True):
            dirs[:] = [d for d in dirs if d not in IGNORED_SCAN_DIRS and not d.startswith(".")]

            if os.path.basename(root).endswith("_data") and "book.pkl" in files:
                rel_folder = os.path.relpath(root, BOOKS_DIR)
                book = load_book_cached(encode_book_id(rel_folder))
                if book:
                    author = ", ".join(book.metadata.authors)
                    books.append(
                        {
                            "id": encode_book_id(rel_folder),
                            "title": book.metadata.title,
                            "author": author,
                            "chapters": len(book.spine),
                            "category_key": category_from_rel_path(rel_folder)[0],
                            "category": category_from_rel_path(rel_folder)[1],
                        }
                    )
                # No need to descend further inside a _data directory.
                dirs[:] = []

    books = sorted(books, key=lambda x: x["title"].lower())
    grouped_books = {}
    for book in books:
        grouped_books.setdefault(book["category"], []).append(book)

    category_options = [
        {"key": "technical", "label": "Technical"},
        {"key": "self-help", "label": "Self Help"},
        {"key": "", "label": "Uncategorized"},
    ]
    known_keys = {opt["key"] for opt in category_options}
    for book in books:
        key = book["category_key"]
        if key and key not in known_keys:
            label = key.replace("_", " ").replace("-", " ").title()
            category_options.append({"key": key, "label": label})
            known_keys.add(key)

    return templates.TemplateResponse(
        "library.html",
        {
            "request": request,
            "books": books,
            "grouped_books": grouped_books,
            "category_options": category_options,
        },
    )


@app.get("/library/move")
async def move_book_to_category(book_id: str, target: str):
    src_dir = safe_book_dir(book_id)
    if not src_dir or not os.path.isdir(src_dir):
        raise HTTPException(status_code=404, detail="Book not found")
    if not os.path.basename(src_dir).endswith("_data"):
        raise HTTPException(status_code=400, detail="Invalid book folder")

    category_key = normalize_category_key(target)
    if category_key:
        dest_root = os.path.join(BOOKS_DIR, category_key)
        os.makedirs(dest_root, exist_ok=True)
    else:
        dest_root = BOOKS_DIR

    base_name = os.path.basename(src_dir)
    dest_dir = os.path.abspath(os.path.join(dest_root, base_name))
    if os.path.abspath(src_dir) != dest_dir:
        if os.path.exists(dest_dir):
            raise HTTPException(
                status_code=409,
                detail=f"Destination already has '{base_name}'",
            )
        shutil.move(src_dir, dest_dir)
        load_book_cached.cache_clear()

    return RedirectResponse(url="/", status_code=303)

@app.get("/read/{book_id}", response_class=HTMLResponse)
async def redirect_to_first_chapter(book_id: str):
    """Helper to just go to chapter 0."""
    return await read_chapter(book_id=book_id, chapter_index=0)

@app.get("/read/{book_id}/{chapter_index}", response_class=HTMLResponse)
async def read_chapter(request: Request, book_id: str, chapter_index: int):
    """The main reader interface."""
    book = load_book_cached(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    if chapter_index < 0 or chapter_index >= len(book.spine):
        raise HTTPException(status_code=404, detail="Chapter not found")

    current_chapter = book.spine[chapter_index]

    # Calculate Prev/Next links
    prev_idx = chapter_index - 1 if chapter_index > 0 else None
    next_idx = chapter_index + 1 if chapter_index < len(book.spine) - 1 else None

    return templates.TemplateResponse("reader.html", {
        "request": request,
        "book": book,
        "current_chapter": current_chapter,
        "chapter_index": chapter_index,
        "book_id": book_id,
        "prev_idx": prev_idx,
        "next_idx": next_idx
    })

@app.get("/read/{book_id}/images/{image_name}")
async def serve_image(book_id: str, image_name: str):
    """
    Serves images specifically for a book.
    The HTML contains <img src="images/pic.jpg">.
    The browser resolves this to /read/{book_id}/images/pic.jpg.
    """
    book_dir = safe_book_dir(book_id)
    if not book_dir:
        raise HTTPException(status_code=404, detail="Book not found")
    safe_image_name = os.path.basename(image_name)
    img_path = os.path.join(book_dir, "images", safe_image_name)

    if not os.path.exists(img_path):
        raise HTTPException(status_code=404, detail="Image not found")

    return FileResponse(img_path)

if __name__ == "__main__":
    import uvicorn
    print("Starting server at http://127.0.0.1:8123")
    uvicorn.run(app, host="127.0.0.1", port=8123)
