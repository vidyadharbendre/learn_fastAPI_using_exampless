"""A hand-rolled catalogue.

Days 01–08 use this list so the lessons stay about FastAPI. Day 09 replaces the
module wholesale with SQLAlchemy — and nothing above it needs to change, which
is the point of keeping data access in one place from the start.
"""

BOOKS: list[dict] = [
    {
        "id": 1,
        "isbn": "978-0-14-303943-3",
        "title": "The Odyssey",
        "author": "Homer",
        "price": "499.00",
        "stock": 12,
    },
    {
        "id": 2,
        "isbn": "978-0-262-03384-8",
        "title": "Introduction to Algorithms",
        "author": "Cormen, Leiserson, Rivest, Stein",
        "price": "5499.00",
        "stock": 3,
    },
    {
        "id": 3,
        "isbn": "978-0-596-52068-7",
        "title": "Programming Python",
        "author": "Mark Lutz",
        "price": "3299.50",
        "stock": 0,
    },
]


def all_books() -> list[dict]:
    """Return a copy so a caller cannot mutate the catalogue by accident."""
    return [dict(book) for book in BOOKS]
