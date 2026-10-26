"""Existing mapping/expansion functions, copied verbatim; see core-provenance.json."""
import re

ARTICLE = re.compile(r"^(第[零〇一二三四五六七八九十百千万两0-9]+条)[\s\u3000]")


ITEM = re.compile(r"^(（[零〇一二三四五六七八九十百千万两0-9]+）)")


def source_mapping(source_bytes: bytes, cloud_segments: list[dict]):
    """Map complete blank-line-delimited article blocks to frozen segment IDs."""
    text = source_bytes.decode("utf-8-sig").replace("\r\n", "\n")
    if "\r" in text:
        raise ValueError("unsupported source newline")
    lines = [line for line in text.split("\n") if line]
    if any(line != line.strip() for line in lines):
        raise ValueError("nonempty source line would require whitespace rewriting")
    segments = sorted(cloud_segments, key=lambda row: row["position"])
    if [row["position"] for row in segments] != list(range(1, len(lines) + 1)):
        raise ValueError("cloud source positions not complete and contiguous")
    if [row["content"] for row in segments] != lines:
        raise ValueError("cloud segment text differs from ordered full-source lines")
    if len({row["id"] for row in segments}) != len(segments):
        raise ValueError("duplicate frozen segment ID")
    mapping = {}
    groups = {}
    cursor = 0
    for block in re.split(r"\n[ \t]*\n+", text.strip("\n")):
        block_lines = block.split("\n")
        heading = ARTICLE.match(block_lines[0])
        article_group = f"article-at-{cursor + 1}" if heading else None
        paragraph = 0
        for offset, line in enumerate(block_lines):
            row = segments[cursor]
            if row["content"] != line:
                raise ValueError("blank-line block alignment mismatch")
            item = ITEM.match(line) if heading else None
            if heading:
                if offset and ARTICLE.match(line):
                    raise ValueError("multiple article headings in one source block")
                if item is None:
                    paragraph += 1
            group = article_group or f"standalone-{row['position']}"
            entry = {
                "id": row["id"],
                "position": row["position"],
                "content": line,
                "group": group,
                "kind": "article" if heading else "standalone_metadata",
                "article_label_explicit": heading.group(1) if heading else None,
                "paragraph_ordinal_inferred": paragraph if heading else None,
                "paragraph_ordinal_printed": False,
                "item_marker_explicit": item.group(1) if item else None,
            }
            mapping[row["id"]] = entry
            groups.setdefault(group, []).append(row["id"])
            cursor += 1
    if cursor != len(segments):
        raise ValueError("source segments not fully mapped")
    return mapping, groups


def expand_parent_context(retrieved_segment_ids, mapping, groups):
    """Expand each hit to its entire article; keep first group occurrence order."""
    output_ids = []
    emitted_articles = set()
    for segment_id in retrieved_segment_ids:
        entry = mapping[segment_id]
        if entry["kind"] == "standalone_metadata":
            output_ids.append(segment_id)
            continue
        group = entry["group"]
        if group not in emitted_articles:
            output_ids.extend(groups[group])
            emitted_articles.add(group)
    return output_ids, "\n".join(mapping[sid]["content"] for sid in output_ids)

