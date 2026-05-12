import re


def inline_tables_and_drop_images(text: str, tables) -> str:
    table_map = {t.id: t.content for t in (tables or [])}

    def repl(m: re.Match) -> str:
        return table_map.get(m.group(1), m.group(0))

    text = re.sub(r"\[(tbl-\d+\.html)\]\(\1\)", repl, text)
    text = re.sub(r"(?m)^\s*!\[[^\]]*\]\([^)]+\)\s*\n?", "", text)
    return text


def to_wrapped_markdown(res) -> str:
    pages: list[str] = []
    for p in res.pages:
        body = inline_tables_and_drop_images(p.markdown, p.tables).strip()
        n = p.index + 1
        pages.append(f"[[START OF PAGE {n}]]\n\n{body}\n\n[[END OF PAGE {n}]]")
    return "\n\n".join(pages)
