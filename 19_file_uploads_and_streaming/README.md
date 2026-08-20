# Day 19 — File Uploads and Streaming

> **Goal:** accept files without trusting anything the client says about them,
> store them somewhere sensible, and send large responses without loading them
> into memory.
> **Time:** ~2.5 hours · **Port:** 8019 · **Builds on:** Day 18

> **Code status:** the README is the spec. Build `shelfspace/` yourself from
> sections 5 onwards before reaching for a reference implementation.

---

## 1. Why this matters

> **An upload endpoint is a stranger writing to your disk and choosing the
> filename.**

Every field in a multipart upload is attacker-controlled: the filename, the
declared content type, the size, and the bytes. The three classic outcomes are a
worker killed by a 2 GB file read into memory, a file written to
`../../etc/cron.d/`, and a `.html` "avatar" that runs JavaScript on your domain
the moment someone views it.

The download side has its own version: `FileResponse` on a 4 GB export, one
request per worker, memory exhausted.

Both are avoidable with rules that are easy to apply and easy to skip.

## 2. What you will build

```
19_file_uploads_and_streaming/
├── run.py
└── shelfspace/
    ├── files/
    │   ├── validation.py    size, real type (magic bytes), extension
    │   ├── storage.py       a Storage protocol: local disk or S3
    │   └── naming.py        safe, non-guessable, non-collide-able names
    ├── api/v1/
    │   ├── covers.py        POST /books/{id}/cover  (image upload)
    │   ├── imports.py       POST /imports/books.csv (streamed parsing)
    │   └── exports.py       GET  /exports/books.csv (streamed generation)
    └── tasks/…              (Day 18: post-processing off the request path)
```

## 3. Run it

```bash
source .venv/bin/activate
cd 19_file_uploads_and_streaming
alembic upgrade head
python run.py
```

`python-multipart` is required for form parsing and is already in
`requirements.txt` — FastAPI raises a clear error at startup without it.

## 4. Try it — learn by doing

```bash
API=http://127.0.0.1:8019/api/v1

# --- a normal upload ---
curl -sX POST $API/books/1/cover \
  -F 'file=@cover.png;type=image/png' | python -m json.tool

# --- the content type is a CLAIM, not a fact ---
cp evil.html fake.png
curl -sX POST $API/books/1/cover -F 'file=@fake.png;type=image/png' | python -m json.tool
# 422: the magic bytes say text/html

# --- path traversal in the filename ---
curl -sX POST $API/books/1/cover -F 'file=@cover.png;filename=../../../etc/passwd'
ls -la uploads/            # nothing outside the upload root, ever

# --- size limits, enforced WHILE reading, not after ---
dd if=/dev/urandom of=/tmp/big.bin bs=1m count=50 2>/dev/null
curl -s -o /dev/null -w '%{http_code}\n' -X POST $API/books/1/cover -F 'file=@/tmp/big.bin'
# 413 — and watch RSS in `top`: it does not climb by 50 MB

# --- a lying Content-Length still gets cut off at the limit ---
curl -s -o /dev/null -w '%{http_code}\n' -X POST $API/books/1/cover \
  -H 'Content-Length: 100' --data-binary @/tmp/big.bin

# --- multiple files, and files alongside form fields ---
curl -sX POST $API/books/1/gallery \
  -F 'files=@a.jpg' -F 'files=@b.jpg' -F 'caption=Two shots' | python -m json.tool

# --- streaming a large CSV import: memory stays flat ---
python -c "
print('isbn,title,price,stock')
[print(f'978-0-00-{i:06d}-0,Book {i},100.00,1') for i in range(200000)]" > /tmp/books.csv
curl -s -X POST $API/imports/books.csv --data-binary @/tmp/books.csv \
  -H 'Content-Type: text/csv' | python -m json.tool

# --- streaming an export: the first byte arrives immediately ---
curl -s -o /dev/null -w 'ttfb %{time_starttransfer}s  total %{time_total}s\n' \
  $API/exports/books.csv

# --- range requests: resumable downloads ---
curl -si -r 0-99 $API/books/1/cover | head -6         # 206 Partial Content

# --- uploaded files are served with a content type they cannot control ---
curl -sI $API/media/<the-stored-name> | grep -iE 'content-type|content-disposition|x-content-type'
```

Watch memory during the 50 MB upload and the 200,000-row import. Flat memory is
the entire point of both sections 6 and 9.

## 5. `UploadFile`, and what it actually gives you

```python
@router.post("/books/{book_id}/cover")
async def upload_cover(book_id: int, file: Annotated[UploadFile, File()]):
    ...
```

| | `UploadFile` | `bytes` |
|---|---|---|
| Memory | spooled: RAM up to ~1 MB, then a temp file | **the whole file in RAM** |
| Metadata | `.filename`, `.content_type`, `.size` | none |
| API | `await file.read(n)`, `.seek()`, `.close()` | — |
| Use for | everything | tiny, known-small payloads only |

> **Never annotate an upload as `bytes`.** It reads the entire body into memory
> before your code runs, so a single 2 GB request kills the worker — and no
> validation you write can prevent it, because it happens first.

Three attributes and none of them are trustworthy:

- **`.filename`** — chosen by the client. May contain `../`, null bytes, 4,000
  characters, or a name that collides with an existing file.
- **`.content_type`** — also chosen by the client. `image/png` on an HTML file is
  a one-line `curl`.
- **`.size`** — from the request, and may be absent or wrong.

## 6. Enforce the size while you read

```python
MAX_BYTES = 5 * 1024 * 1024

async def read_capped(file: UploadFile, limit: int = MAX_BYTES) -> bytes:
    chunks, total = [], 0
    while chunk := await file.read(64 * 1024):
        total += len(chunk)
        if total > limit:
            raise APIError(413, "file_too_large",
                           f"Maximum size is {limit // 1024 // 1024} MB.")
        chunks.append(chunk)
    return b"".join(chunks)
```

Checking `file.size` first is a useful fast path, not a defence — `Content-Length`
can be absent (chunked encoding) or simply wrong. The chunked read is what
actually bounds your memory.

Defend at more than one layer:

| Layer | Control |
|---|---|
| Reverse proxy | nginx `client_max_body_size 10m` — rejects before your app sees it |
| Middleware (Day 13) | `413` from `Content-Length` for a fast, cheap rejection |
| Handler | the capped read above — the one that cannot be lied to |

## 7. Validate the type by its bytes

```python
SIGNATURES = {
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff":      "image/jpeg",
    b"GIF89a":            "image/gif",
    b"%PDF-":             "application/pdf",
}

def sniff(head: bytes) -> str | None:
    return next((mime for sig, mime in SIGNATURES.items()
                 if head.startswith(sig)), None)
```

Then require **all three** to agree: the sniffed type is in your allow-list, the
extension matches it, and the declared content type is consistent. Reject
otherwise.

For images, go further and **re-encode**:

```python
img = Image.open(io.BytesIO(data))
img.verify()                       # structural check
img = Image.open(io.BytesIO(data)) # verify() exhausts the file object
img.thumbnail((1200, 1200))
img.convert("RGB").save(out_path, format="WEBP", quality=82)
```

Re-encoding strips EXIF (which routinely contains GPS coordinates of someone's
home), removes any polyglot payload hiding after the image data, normalises the
format, and caps the dimensions — a "decompression bomb" is a 200 KB PNG that
expands to 40,000 × 40,000 pixels and 6 GB of RAM. Set
`Image.MAX_IMAGE_PIXELS`.

Anything that runs — `.html`, `.svg`, `.js`, `.php` — must never be served from
your own origin. SVG is the one people miss: it is XML, it can contain
`<script>`, and browsers execute it. Either forbid SVG or sanitise it properly.

## 8. Naming and storing

```python
def safe_name(original: str, data: bytes) -> str:
    ext = ALLOWED[sniff(data[:16])]                 # extension from CONTENT
    digest = hashlib.sha256(data).hexdigest()[:16]  # dedupes identical files
    return f"{uuid4().hex}{ext}"                    # unguessable, no collisions
```

> **Never use the client's filename as a path component.** Not sanitised, not
> "just the basename" — generate your own.

If you must keep the original for display, store it as a database column and
serve it in `Content-Disposition`, never on disk. And verify containment
regardless:

```python
target = (UPLOAD_ROOT / name).resolve()
if not target.is_relative_to(UPLOAD_ROOT.resolve()):
    raise APIError(400, "invalid_path", "Invalid file path.")
```

**Where to put the bytes:**

| Storage | Fine for | Problem |
|---|---|---|
| Local disk | one server, dev | lost on redeploy; not shared between instances |
| Object storage (S3/GCS) | production | the right answer; costs a dependency |
| Database BLOB | small, transactional files | bloats backups, slows queries |

Write behind a small interface so the choice is swappable:

```python
class Storage(Protocol):
    async def save(self, key: str, data: bytes) -> str: ...
    async def open(self, key: str) -> AsyncIterator[bytes]: ...
    async def delete(self, key: str) -> None: ...
```

For production uploads at scale, skip your server entirely: issue a **presigned
S3 URL** and have the client upload directly, then confirm the key. Your API
never touches the bytes — no bandwidth, no memory, no timeout.

## 9. Streaming: reading big uploads and writing big responses

**Reading a large body without buffering it** — parse as it arrives:

```python
@router.post("/imports/books.csv")
async def import_books(request: Request):
    buffer, imported = "", 0
    async for chunk in request.stream():             # never fully in memory
        buffer += chunk.decode()
        *lines, buffer = buffer.split("\n")
        for line in lines:
            imported += process_row(line)
    return {"imported": imported}
```

**Writing a large response without building it:**

```python
@router.get("/exports/books.csv")
async def export_books(session: SessionDep):
    def rows():
        yield "isbn,title,price,stock\n"
        for book in session.execute(select(Book).execution_options(yield_per=1000)).scalars():
            yield f"{book.isbn},{book.title},{book.price},{book.stock}\n"

    return StreamingResponse(rows(), media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="books.csv"'})
```

`yield_per` is the SQLAlchemy half — without it you stream the response while
having loaded every row into memory first, which defeats the purpose.

| Response class | Use |
|---|---|
| `FileResponse` | a file on disk — handles `Range`, `ETag`, `Last-Modified` for you |
| `StreamingResponse` | generated content, or a remote/proxied stream |
| `Response(content=…)` | small, already-in-memory payloads |

Three streaming caveats:

- **You cannot change the status code after the first byte.** An error halfway
  through a `200` stream is unreportable — the client sees a truncated file.
  Validate before you start yielding.
- **`BaseHTTPMiddleware` buffers responses** (Day 13). One innocent middleware
  turns your stream back into a memory hog.
- **Do not hold a database session open for a very long stream** — it occupies a
  pool connection for the duration. For huge exports, generate to object storage
  in a background job (Day 18) and return a link.

For serving existing files, the best answer is often not to serve them at all:
let nginx (`X-Accel-Redirect`) or a CDN do it, with your app only authorising the
request.

## 10. Serving user files safely

```python
headers = {
    "Content-Type": stored.mime,                 # YOUR record, not their claim
    "Content-Disposition": f'attachment; filename="{quote(stored.original_name)}"',
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, max-age=3600",
}
```

- **Serve from a different origin** (`media.example.com`, or a bucket) when you
  can. A malicious file served from your API's origin runs with your cookies and
  your CORS trust.
- **`attachment` unless you specifically need inline display**, and never
  `inline` for anything user-supplied that a browser might render.
- **`nosniff`** stops the browser second-guessing your content type.
- **Authorize the download** — object-level, as in Day 16. An unguessable URL is
  not authorization; it is obscurity, and URLs leak through referrers, logs and
  chat apps.
- **Signed, expiring URLs** are the right pattern for private files, whether you
  sign them yourself or use S3 presigning.

## 11. Post-processing belongs in a job

Thumbnailing, virus scanning, PDF text extraction, transcoding — none of it
belongs in the request:

```python
stored = await storage.save(key, data)
enqueue("process_upload", {"key": key, "book_id": book_id})   # Day 18
return UploadAccepted(status="processing", poll_url=f"/api/v1/uploads/{key}")
```

Return `202` and a poll URL (Day 18, section 11) rather than making a user wait
eight seconds for a thumbnail. If you accept files from the public, run them
through a virus scanner (ClamAV) in that job before they are downloadable.

## 12. Best practices introduced today

| Practice | Reason |
|---|---|
| `UploadFile`, never `bytes` | `bytes` loads the whole file into RAM first |
| Enforce size **while reading**, in chunks | `Content-Length` can be absent or a lie |
| Limit at the proxy *and* the app | defence in depth, and cheaper rejection |
| Validate type by magic bytes | the declared content type is a claim |
| Require extension, sniff and declared type to agree | each catches a different trick |
| Re-encode images | strips EXIF, kills polyglots, caps dimensions |
| Cap image pixel dimensions | decompression bombs are 200 KB on the wire |
| Never serve executable types from your origin | including SVG |
| Generate your own filenames (UUID + sniffed extension) | the client's filename is an attack surface |
| Verify the resolved path stays inside the upload root | belt and braces against traversal |
| Object storage in production; a `Storage` interface | local disk does not survive a deploy |
| Presigned direct uploads at scale | your API never touches the bytes |
| Stream large requests and responses | flat memory regardless of size |
| `yield_per` when streaming from the database | otherwise you buffer every row |
| Validate before the first byte of a stream | you cannot change the status afterwards |
| Serve user files from a separate origin | contains anything malicious |
| `Content-Disposition: attachment` + `nosniff` | stops browser rendering and sniffing |
| Authorize every download; use signed expiring URLs | unguessable is not private |
| Post-process in a background job, return `202` | uploads should not block on thumbnails |
| Virus-scan public uploads before serving | you are a distribution channel otherwise |

## 13. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| Worker killed by one upload | annotated as `bytes` | `UploadFile` + chunked read |
| Memory climbs with file size | read fully before validating | cap while reading |
| 50 MB accepted despite a limit | trusted `file.size` | enforce during the read |
| An HTML file stored as `image/png` | trusted `content_type` | sniff the bytes |
| A `.svg` avatar ran JavaScript | SVG treated as an image | forbid or sanitise |
| File written outside the upload directory | used `filename` as a path | generate names; verify containment |
| Uploads overwrite each other | client filenames collide | UUID names |
| GPS coordinates leaked from photos | EXIF preserved | re-encode |
| 6 GB RAM from a 200 KB PNG | decompression bomb | `MAX_IMAGE_PIXELS`, cap dimensions |
| Files vanish after deploy | local disk on ephemeral storage | object storage |
| Private files reachable by URL | obscurity as authorization | authorize; sign and expire |
| Stored XSS on your own domain | user file served from your origin | separate origin, `attachment`, `nosniff` |
| Truncated CSV with a 200 status | error mid-stream | validate before streaming |
| Streaming response fully buffered | `BaseHTTPMiddleware` | pure ASGI middleware |
| Export exhausts the connection pool | long stream holding a session | export to storage in a job |
| Upload endpoint times out | thumbnailing inline | background job + `202` |
| `python-multipart` error at startup | dependency missing | it is in `requirements.txt` |

## 14. Exercises

1. Implement `POST /books/{id}/cover` with the chunked capped read, then upload a
   50 MB file and watch memory while it is rejected.
2. Rename an HTML file to `.png`, declare `image/png`, and confirm magic-byte
   sniffing rejects it.
3. Attempt a traversal filename and confirm nothing is written outside the upload
   root. Then log the attempt — it is an attack signal.
4. Re-encode uploads to WEBP with a maximum dimension, and verify with `exiftool`
   that GPS data is gone.
5. Stream-import a 200,000-row CSV and plot memory against a version that does
   `await request.body()` first.
6. Stream-export the catalogue with `yield_per`, and compare time-to-first-byte
   against building the CSV in memory.
7. Add a `BaseHTTPMiddleware` that touches the response and demonstrate that it
   breaks streaming. Then convert it to pure ASGI.
8. Add authorization plus a signed, expiring download URL, and confirm the URL
   stops working after its expiry.

## 15. What's next

**[Day 20 — WebSockets and Realtime →](../20_websockets_and_realtime/)**
Polling a job status endpoint every second works and is wasteful. Tomorrow:
WebSockets for two-way communication, Server-Sent Events for one-way updates, a
connection manager that survives multiple workers, and knowing which of the three
a problem actually needs.
