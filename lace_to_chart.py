#!/usr/bin/env python3
"""
Convert a Brother KH-910 transfer lace pattern PNG to an ASCII lace chart.

Consecutive L+R transfer row pairs are merged into one chart row.

Symbol conventions:
  O  = hole/eyelet (emptied needle)
    o  = hole/eyelet on F rows
  /  = left-leaning decrease (stitch transferred rightward)
  \\  = right-leaning decrease (stitch transferred leftward)
  X  = double decrease (/ and \\ combined at same position)
  .  = plain knit stitch

Knitting direction: Row 1 = bottom of image.
    Odd rows  -> left-transfer rows  (lean \\)
    Even rows -> right-transfer rows (lean /)

Merge rules (bottom = earlier row, top = later row):
  non-. over .       -> non-. wins
  / and \\ overlap   -> X
  / over O           -> /
  \\ over O          -> \\
    o with .           -> o
  O over /, \\, or O -> ERROR
    o with anything else -> ERROR
"""

import argparse
import base64
import html
import os
import sys
from contextlib import redirect_stdout
from PIL import Image

DOUBLE_DEC = 'X'
TILED_WIDTH = 80
TILED_ROWS = 50


def extract_ayab_line(path):
    """Extract the AYAB line from the PNG Comment metadata field."""
    with Image.open(path) as img:
        text_metadata = getattr(img, "text", {}) or {}
        comment = text_metadata.get("Comment")
        if comment is None:
            comment = img.info.get("Comment")
        if comment is None:
            return None
        if isinstance(comment, bytes):
            comment = comment.decode("utf-8", errors="ignore")
        return str(comment)


def parse_ayab_blocks(ayab_line, row_count):
    """Return a mapping of knitting row number to AYAB block number."""
    if not ayab_line or not ayab_line.startswith("AYAB:"):
        return None

    symbols = ayab_line.split(":", 1)[1].strip()
    if len(symbols) < row_count:
        return None

    block_by_row = {}
    block_num = 1
    for row_num in range(1, row_count + 1):
        symbol = symbols[row_num - 1]
        block_by_row[row_num] = block_num
        if symbol in "123456789":
            block_num += 1

    return block_by_row


def parse_ayab_symbols(ayab_line, row_count):
    """Return a mapping of knitting row number to its AYAB metadata character."""
    if not ayab_line or not ayab_line.startswith("AYAB:"):
        return None

    symbols = ayab_line.split(":", 1)[1].strip()
    if len(symbols) < row_count:
        return None

    return {row_num: symbols[row_num - 1] for row_num in range(1, row_count + 1)}


def build_source_pattern(path):
    """Return the original PNG pattern as display rows in knitting order."""
    img = Image.open(path).convert('1')
    width, height = img.size
    pixels = img.load()
    if pixels is None:
        raise ValueError(f"Could not load pixel data from {path}")

    rows = []
    for knit_row in range(1, height + 1):
        y = height - knit_row
        black_symbol = '<' if knit_row % 2 == 1 else '>'
        cells = []
        for x in range(width):
            cells.append(black_symbol if pixels[x, y] == 0 else '.')
        rows.append((str(knit_row), cells, []))

    return rows, width


def build_rows(path, ayab_symbols=None):
    """Return list of (knit_row_num, direction, cells) from row 1 upward."""
    img = Image.open(path).convert('1')
    width, height = img.size
    pixels = img.load()
    if pixels is None:
        raise ValueError(f"Could not load pixel data from {path}")

    rows = []
    for knit_row in range(1, height + 1):
        y = height - knit_row
        right_row = (knit_row % 2 == 0)
        hole_symbol = 'o' if ayab_symbols and ayab_symbols.get(knit_row) == 'F' else 'O'
        cells = ['.'] * width

        for x in range(width):
            if pixels[x, y] == 0:
                if right_row:
                    cells[x] = hole_symbol
                    cells[(x + 1) % width] = '/'
                else:
                    cells[x] = hole_symbol
                    cells[(x - 1) % width] = '\\'

        rows.append((knit_row, 'R' if right_row else 'L', cells))

    return rows, width


def is_plain(cells):
    return all(c == '.' for c in cells)


def parse_label_range(label):
    """Return (start, end) numeric range for a chart row label."""
    if '-' in label:
        start, end = label.split('-', 1)
        return int(start), int(end)
    value = int(label)
    return value, value


def merge_cells(bottom_cells, top_cells, bottom_label=None, top_label=None):
    """Merge two rows of cells using the same symbol-overlap rules."""
    result = []
    errors = []
    for i, (b, t) in enumerate(zip(bottom_cells, top_cells)):
        if b == '.' and t == '.':
            result.append('.')
        elif b == '.':
            result.append(t)
        elif t == '.':
            result.append(b)
        elif (b == '/' and t == '\\') or (b == '\\' and t == '/'):
            result.append(DOUBLE_DEC)
        elif t in ('/', '\\') and b == 'O':
            result.append(t)
        elif t == 'o':
            if bottom_label and top_label:
                errors.append(f"stitch {i+1}: o (row {top_label}) over '{b}' (row {bottom_label})")
            else:
                errors.append(f"stitch {i+1}: o over '{b}'")
            result.append('!')
        elif t == 'O':
            if bottom_label and top_label:
                errors.append(f"stitch {i+1}: O (row {top_label}) over '{b}' (row {bottom_label})")
            else:
                errors.append(f"stitch {i+1}: O over '{b}'")
            result.append('!')
        elif b == 'o':
            if bottom_label and top_label:
                errors.append(f"stitch {i+1}: '{t}' (row {top_label}) over o (row {bottom_label})")
            else:
                errors.append(f"stitch {i+1}: '{t}' over o")
            result.append('!')
        else:
            if bottom_label and top_label:
                errors.append(
                    f"stitch {i+1}: unhandled combination '{b}' (row {bottom_label}) + '{t}' (row {top_label})"
                )
            else:
                errors.append(f"stitch {i+1}: unhandled combination '{b}' + '{t}'")
            result.append('?')
    return result, errors


def merge_rows_by_ayab_block(rows, ayab_blocks, ayab_symbols=None):
    """Merge rows within each AYAB-defined block, bottom row first."""
    if not ayab_blocks:
        return build_unmerged_chart(rows)

    merged_blocks = []
    block_order = []
    rows_by_block = {}

    for row_num, _direction, cells in rows:
        block_num = ayab_blocks.get(row_num)
        if block_num not in rows_by_block:
            rows_by_block[block_num] = []
            block_order.append(block_num)
        rows_by_block[block_num].append((row_num, cells))

    for block_num in block_order:
        block_rows = rows_by_block[block_num]
        start_row = block_rows[0][0]
        end_row = block_rows[-1][0]
        merged_cells = list(block_rows[0][1])
        merged_errors = []

        for row_num, cells in block_rows[1:]:
            merged_cells, new_errors = merge_cells(
                merged_cells,
                cells,
                bottom_label=f"{start_row}-{row_num - 1}" if row_num - 1 > start_row else str(start_row),
                top_label=str(row_num),
            )
            merged_errors.extend(new_errors)

        merged_blocks.append((f"{start_row}-{end_row}", merged_cells, merged_errors))

        block_symbol = ""
        if ayab_symbols:
            block_symbol = ayab_symbols.get(end_row, "")
        if block_symbol.isdigit() and int(block_symbol) > 2:
            extra_rows = int(block_symbol) - 2
            plain_cells = ['.'] * len(merged_cells)
            for _ in range(extra_rows):
                merged_blocks.append(("", list(plain_cells), []))

    return merged_blocks


def build_unmerged_chart(rows):
    """Return the converted chart before row-pair merging or blank-row removal."""
    return [(str(num), cells, []) for num, _direction, cells in rows]


def tile_chart(chart, width, target_width, target_rows):
    """Repeat the merged chart horizontally and vertically to the target size."""
    tiled = []
    for row_num in range(1, target_rows + 1):
        _, cells, errors = chart[(row_num - 1) % len(chart)]
        repeated = (cells * ((target_width + width - 1) // width))[:target_width]
        tiled.append((str(row_num), repeated, list(errors)))
    return tiled


def build_holes_only_chart(chart):
    """Render a chart using only O and . characters."""
    holes_only = []
    for label, cells, errors in chart:
        simplified = ['o' if cell == 'o' else 'O' if cell == 'O' else '.' for cell in cells]
        holes_only.append((label, simplified, list(errors)))
    return holes_only


def replace_dots_with_spaces(chart):
    """Render dots as spaces for display-oriented chart sections."""
    spaced = []
    for label, cells, errors in chart:
        converted = [' ' if cell == '.' else cell for cell in cells]
        spaced.append((label, converted, list(errors)))
    return spaced


def convert_to_alternative_lace_chart(chart):
    """Render a chart using the alternate lace notation requested by the user."""
    converted_chart = []
    transfer_symbols = {'/', '\\', DOUBLE_DEC}

    for label, cells, errors in chart:
        converted = []
        for index, cell in enumerate(cells):
            if cell in ('.', ' '):
                converted.append('"')
            elif cell in ('O', 'o'):
                converted.append('#')
            elif cell == '/':
                right_neighbor = cells[index + 1] if index + 1 < len(cells) else None
                converted.append('(' if right_neighbor in transfer_symbols else ')')
            elif cell == '\\':
                left_neighbor = cells[index - 1] if index - 1 >= 0 else None
                converted.append("'" if left_neighbor in transfer_symbols else '*')
            elif cell == DOUBLE_DEC:
                converted.append('/')
            else:
                converted.append(cell)
        converted_chart.append((label, converted, list(errors)))

    return converted_chart


def print_chart(
    chart,
    width,
    legend="Legend: O=hole  o=F-hole  /=left-lean  \\=right-lean  X=double-dec  .=knit",
    row_blocks=None,
    row_symbols=None,
    merged_row_symbol_mode=None,
    label_separator=':',
    include_legend=True,
):
    print(
        format_chart(
            chart,
            width,
            legend=legend,
            row_blocks=row_blocks,
            row_symbols=row_symbols,
            merged_row_symbol_mode=merged_row_symbol_mode,
            label_separator=label_separator,
            include_legend=include_legend,
        ),
        end="",
    )


def format_chart(
    chart,
    width,
    legend="Legend: O=hole  o=F-hole  /=left-lean  \\=right-lean  X=double-dec  .=knit",
    row_blocks=None,
    row_symbols=None,
    merged_row_symbol_mode=None,
    label_separator=':',
    include_legend=True,
):
    col_w = max(len(e[0]) for e in chart)
    tens  = ''.join(str((i + 1) // 10) if (i + 1) % 10 == 0 else ' ' for i in range(width))
    units = ''.join(str((i + 1) % 10) for i in range(width))
    pad   = ' ' * (col_w + 2)
    reversed_chart = list(reversed(chart))
    lines = []

    lines.append(f"{pad}{tens}")
    lines.append(f"{pad}{units}")
    lines.append("")
    for index, (label, cells, errors) in enumerate(reversed_chart):
        suffix = ""
        if row_symbols and label:
            start_row, end_row = parse_label_range(label)
            if start_row == end_row:
                symbol = row_symbols.get(start_row, "")
                if symbol and symbol != '0':
                    suffix = f" {symbol}"
            elif merged_row_symbol_mode == "end-row":
                symbol = row_symbols.get(end_row, "")
                if symbol:
                    suffix = f" {symbol}"
        lines.append(f"{label:>{col_w}}{label_separator} {''.join(cells)}{suffix}")
        for e in errors:
            lines.append(f"  ERROR: {e}")
        if row_blocks and index + 1 < len(reversed_chart):
            current_start, current_end = parse_label_range(label)
            next_start, next_end = parse_label_range(reversed_chart[index + 1][0])
            if current_start == current_end and next_start == next_end:
                if row_blocks.get(current_start) != row_blocks.get(next_start):
                    lines.append("")
    lines.append("")
    lines.append(f"{pad}{units}")
    lines.append(f"{pad}{tens}")
    if include_legend:
        lines.append("")
        lines.append(legend)

    return "\n".join(lines) + "\n"


def print_source_pattern(pattern, width, row_symbols=None):
    col_w = max(len(e[0]) for e in pattern)
    tens  = ''.join(str((i + 1) // 10) if (i + 1) % 10 == 0 else ' ' for i in range(width))
    units = ''.join(str((i + 1) % 10) for i in range(width))
    pad   = ' ' * (col_w + 2)

    print(f"{pad}{tens}")
    print(f"{pad}{units}")
    print()
    for label, cells, _ in reversed(pattern):
        suffix = ""
        if row_symbols:
            symbol = row_symbols.get(int(label), "")
            if symbol and symbol != '0':
                suffix = f" {symbol}"
        print(f"{label:>{col_w}}: {''.join(cells)}{suffix}")
    print()
    print(f"{pad}{units}")
    print(f"{pad}{tens}")
    print()
    print("Legend: .=white pixel  <=black pixel on odd knitting rows  >=black pixel on even knitting rows")


def emit_processing_errors(path, chart):
    """Print unique processing errors for a pattern to stderr."""
    seen_errors = set()
    for _label, _cells, errors in chart:
        for error in errors:
            if error in seen_errors:
                continue
            seen_errors.add(error)
            print(f"{path}: {error}", file=sys.stderr)


def write_alternative_chart_html(path, chart, width, font_path):
    """Write the alternative chart representation to a minimal HTML file."""
    chart_text = format_chart(chart, width, label_separator=' ', include_legend=False)
    font_root, _font_ext = os.path.splitext(font_path)
    woff2_path = font_root + ".woff2"
    with open(woff2_path, "rb") as font_file:
        font_data = base64.b64encode(font_file.read()).decode("ascii")
    html_text = """<!DOCTYPE html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\">
<title>Alternative Lace Chart</title>
<style>
@font-face {
    font-family: 'RoosChartEmbedded';
    src: url(data:font/woff2;base64,""" + font_data + """) format('woff2');
}
body {
    margin: 0;
    background: white;
}
pre {
    margin: 0;
    padding: 16px;
    white-space: pre;
    font-family: 'RoosChartEmbedded', monospace;
    font-size: 16px;
    line-height: 1;
}
</style>
</head>
<body>
<pre>""" + html.escape(chart_text, quote=False) + """</pre>
</body>
</html>
"""
    with open(path, "w", encoding="utf-8") as html_file:
        html_file.write(html_text)


def parse_args():
    parser = argparse.ArgumentParser(description="Convert a lace PNG into text chart renderings.")
    parser.add_argument("path", nargs="?", default="StitchWorld/109.png", help="Input PNG path")
    parser.add_argument("-o", "--output", help="Write output to a text file instead of stdout")
    parser.add_argument("--html-output", help="Write the alternative tiled chart to an HTML file")
    return parser.parse_args()


def build_report_data(path):
    ayab_line = extract_ayab_line(path)
    source_pattern, source_width = build_source_pattern(path)
    ayab_symbols = parse_ayab_symbols(ayab_line, len(source_pattern))
    rows, width = build_rows(path, ayab_symbols)
    ayab_blocks = parse_ayab_blocks(ayab_line, len(rows))
    unmerged_chart = build_unmerged_chart(rows)
    block_merged_chart = merge_rows_by_ayab_block(rows, ayab_blocks, ayab_symbols)
    chart = block_merged_chart
    tiled_chart = tile_chart(chart, width, TILED_WIDTH, TILED_ROWS)
    alternate_tiled_chart = convert_to_alternative_lace_chart(tiled_chart)
    tiled_holes_only_chart = build_holes_only_chart(tiled_chart)

    return {
        "ayab_line": ayab_line,
        "source_pattern": source_pattern,
        "source_width": source_width,
        "ayab_symbols": ayab_symbols,
        "rows": rows,
        "width": width,
        "ayab_blocks": ayab_blocks,
        "unmerged_chart": unmerged_chart,
        "chart": chart,
        "tiled_chart": tiled_chart,
        "alternate_tiled_chart": alternate_tiled_chart,
        "tiled_holes_only_chart": tiled_holes_only_chart,
    }


def render_report(path, report_data=None):
    report_data = report_data or build_report_data(path)
    ayab_line = report_data["ayab_line"]
    source_pattern = report_data["source_pattern"]
    source_width = report_data["source_width"]
    ayab_symbols = report_data["ayab_symbols"]
    rows = report_data["rows"]
    width = report_data["width"]
    ayab_blocks = report_data["ayab_blocks"]
    unmerged_chart = report_data["unmerged_chart"]
    chart = report_data["chart"]
    tiled_chart = report_data["tiled_chart"]
    alternate_tiled_chart = report_data["alternate_tiled_chart"]
    tiled_holes_only_chart = report_data["tiled_holes_only_chart"]

    emit_processing_errors(path, chart)

    print(f"Original pattern: {path}  ({source_width} sts x {len(source_pattern)} rows)")
    print(f"AYAB metadata: {ayab_line or 'not found'}\n")
    print_source_pattern(source_pattern, source_width, row_symbols=ayab_symbols)
    print()

    print(f"Unmerged chart: {path}  ({width} sts x {len(unmerged_chart)} rows)\n")
    print_chart(unmerged_chart, width, row_blocks=ayab_blocks, row_symbols=ayab_symbols)
    print()

    print(f"Final chart: {path}  ({width} sts x {len(rows)} rows -> {len(chart)} chart rows)\n")
    print_chart(chart, width, row_symbols=ayab_symbols, merged_row_symbol_mode="end-row")

    print()
    print(f"Tiled chart: {TILED_WIDTH} sts x {TILED_ROWS} rows\n")
    print_chart(
        replace_dots_with_spaces(tiled_chart),
        TILED_WIDTH,
        legend="Legend: space=plain knit  O=hole  o=F-hole  /=left-lean  \\=right-lean  X=double-dec"
    )

    print()
    print(f"Alternative tiled chart: {TILED_WIDTH} sts x {TILED_ROWS} rows\n")
    print_chart(
        alternate_tiled_chart,
        TILED_WIDTH,
        legend="Legend: \"=plain stitch  #=hole  )=right-wise transfer  (=right-wise transfer before transfer  *=left-wise transfer  '=left-wise transfer after transfer  /=double decrease",
        label_separator=' '
    )

    print()
    print(f"Tiled holes-only chart: {TILED_WIDTH} sts x {TILED_ROWS} rows\n")
    print_chart(
        replace_dots_with_spaces(tiled_holes_only_chart),
        TILED_WIDTH,
        legend="Legend: space=all non-hole positions  O=hole  o=F-hole"
    )


def main():
    args = parse_args()
    try:
        report_data = build_report_data(args.path)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as output_file:
                with redirect_stdout(output_file):
                    render_report(args.path, report_data=report_data)
        else:
            render_report(args.path, report_data=report_data)

        if args.html_output:
            write_alternative_chart_html(
                args.html_output,
                report_data["alternate_tiled_chart"],
                TILED_WIDTH,
                "/Users/jonathanperret/src/ayab-patterns/StitchWorld/ROOSMLN1.TTF",
            )
    except Exception as exc:
        print(f"{args.path}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
