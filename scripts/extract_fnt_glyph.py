#!/usr/bin/env python3
"""
extract_fnt_glyphs.py

Recursively scans a parent directory for subdirectories containing
HELEN (Hanover Displays) .fnt font files. For each .fnt file found:
  - Extracts all bitmap characters into individual BMP files named
    by their Windows-1252 code (e.g. 0033.BMP for '!'), written in
    the same directory as the .fnt file.
  - Generates a config.json in that same directory with the font name
    and spacing parameters.

.fnt file format (text, CRLF line endings):
    Line 1: header "space height max_width ? inter_char_space"
              - space            : width (px) of the space character
              - height           : character height in pixels
              - max_width        : max character width in pixels
              - (4th value)      : unknown / unused
              - inter_char_space : spacing (px) between consecutive characters
    Line 2: list of per-character widths (px), ordered by Windows-1252
            codes starting at 32 (space) through 255.
    Following lines: one line per character (same order as widths),
            containing max_width space-separated bytes. Each byte
            encodes a vertical column of `height` pixels: bit 0 (LSB)
            is the top pixel, bit 7 (MSB) is the bottom pixel.
            Only the first `width` columns are meaningful; the rest
            is zero-padding.

config.json output:
{
  "font_name": "08normal",
  "space_pixels": 2,
  "inter_char_space_pixels": 1
}
"""

import json
import os
import sys

try:
    from PIL import Image
except ImportError:
    sys.exit(
        "This script requires Pillow. Install it with:\n"
        "    pip install pillow --break-system-packages"
    )

# Parent directory to scan (contains subdirectories with .fnt files).
ROOT_DIR = "path/to/folder"


def parse_fnt(path):
    """Parse a HELEN .fnt file and return a dict with header values,
    per-character widths, and raw glyph data lines."""
    with open(path, "rb") as f:
        raw = f.read()

    lines = raw.decode("latin1").split("\r\n")
    lines = [l for l in lines if l != ""]

    header = list(map(int, lines[0].split()))
    space = header[0]
    height = header[1]
    max_width = header[2]
    inter_char_space = header[4]

    widths = list(map(int, lines[1].split()))
    glyph_lines = lines[2:]

    if len(glyph_lines) < len(widths):
        raise ValueError(
            f"Inconsistent file: {len(widths)} widths but only "
            f"{len(glyph_lines)} glyph lines."
        )

    return {
        "header": header,
        "space": space,
        "height": height,
        "max_width": max_width,
        "inter_char_space": inter_char_space,
        "widths": widths,
        "glyph_lines": glyph_lines,
    }


def decode_glyph(glyph_line, width, height, max_width):
    """Decode a glyph data line into a PIL mode '1' (bilevel) image.
    Only the first `width` columns are populated; the rest is blank."""
    columns = list(map(int, glyph_line.split()))

    im = Image.new("1", (max_width, height), 0)
    px = im.load()

    for col in range(width):
        byte = columns[col]
        for row in range(height):
            # bit 0 (LSB) = top pixel
            if (byte >> row) & 1:
                px[col, row] = 1

    return im


def extract_fnt(fnt_path, out_dir, start_code=32):
    """Extract all glyphs from a .fnt file as NNNN.BMP files in out_dir
    and write config.json. Returns the number of glyphs extracted."""
    info = parse_fnt(fnt_path)
    os.makedirs(out_dir, exist_ok=True)

    count = 0
    for i, width in enumerate(info["widths"]):
        code = start_code + i
        im = decode_glyph(
            info["glyph_lines"][i], width, info["height"], info["max_width"]
        )
        im.save(os.path.join(out_dir, f"{code:04d}.BMP"))
        count += 1

    config_path = os.path.join(out_dir, "config.json")
    if os.path.exists(config_path):
        print(f"    (config.json already exists, skipping: {config_path})")
    else:
        font_name = os.path.splitext(os.path.basename(fnt_path))[0]
        config = {
            "font_name": font_name,
            "space_pixels": info["space"],
            "inter_char_space_pixels": info["inter_char_space"],
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

    return count


def extract_directory(root_dir):
    """Walk root_dir recursively. For each .fnt file found, extract
    glyphs and config.json into the same directory. Returns a list
    of (fnt_path, glyph_count) tuples."""
    results = []

    for dirpath, _dirnames, filenames in os.walk(root_dir):
        for filename in filenames:
            if not filename.lower().endswith(".fnt"):
                continue

            fnt_path = os.path.join(dirpath, filename)

            try:
                count = extract_fnt(fnt_path, dirpath)
            except Exception as e:
                print(f"  [ERROR] {fnt_path}: {e}")
                continue

            print(f"  {fnt_path} -> {dirpath} ({count} glyphs)")
            results.append((fnt_path, count))

    return results


def main():
    if not os.path.isdir(ROOT_DIR):
        sys.exit(f"Directory not found: {ROOT_DIR}")

    print(f"Scanning for .fnt files in: {ROOT_DIR}")
    results = extract_directory(ROOT_DIR)

    if not results:
        print("No .fnt files found.")
    else:
        total = sum(c for _, c in results)
        print(f"\n{len(results)} font(s) extracted, {total} glyphs total.")


if __name__ == "__main__":
    main()
