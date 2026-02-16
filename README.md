# reader 4

## Screenshots

![Library](assets/screenshots/library.png)
![Reader](assets/screenshots/reader.png)

A lightweight, self-hosted EPUB/PDF reader that lets you read through books one section at a time. This makes it very easy to copy paste the contents of a section to an LLM, to read along. Basically - get EPUB books (e.g. [Project Gutenberg](https://www.gutenberg.org/) has many) or PDFs, open them up in this reader, copy paste text around to your favorite LLM, and read together and along.

This project was 90% vibe coded just to illustrate how one can very easily [read books together with LLMs](https://x.com/karpathy/status/1990577951671509438). I'm not going to support it in any way, it's provided here as is for other people's inspiration and I don't intend to improve it. Code is ephemeral now and libraries are over, ask your LLM to change it in whatever way you like.

## Usage

The project uses [uv](https://docs.astral.sh/uv/). Process an EPUB:

```bash
uv run reader4.py "your-book.epub"
```

Or process a PDF:

```bash
uv run reader4.py "your-book.pdf"
```

This creates the directory `<book_name>_data`, which registers the book to your local library. We can then run the server:

```bash
uv run server.py
```

And visit [localhost:8123](http://localhost:8123/) to see your current Library. You can easily add more books, or delete them from your library by deleting the folder. It's not supposed to be complicated or complex.

### PDF segmentation notes

- If the PDF has bookmarks/outlines, `reader4.py` segments by those.
- It prefers chapter-like outline entries (instead of only high-level parts).
- If no useful outline exists, it falls back to page-by-page segmentation.

## License

MIT
