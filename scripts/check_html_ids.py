"""Fail CI when the static dashboard contains duplicate element identifiers."""

from html.parser import HTMLParser
from pathlib import Path


class IdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(self, _tag: str, attrs) -> None:
        for key, value in attrs:
            if key == "id" and value:
                self.ids.append(value)


def main() -> int:
    parser = IdParser()
    parser.feed((Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8"))
    duplicates = sorted({value for value in parser.ids if parser.ids.count(value) > 1})
    if duplicates:
        raise SystemExit(f"Duplicate HTML ids: {duplicates}")
    print(f"HTML IDs unique: {len(parser.ids)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
