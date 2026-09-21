import glob
import json
import math
import os

import ufo2ft
import ufoLib2
from PIL import Image

# ==========================================
#               PARAMETERS
# ==========================================
# Parent folder containing one subfolder per font (e.g. Eurofonts/0505C1E1/)
ROOT_FOLDER = "path/to/folder"

# Parent font family name, shared by all subfonts
FONT_FAMILY = "Hanover POLICES"

# All compiled .otf files are written here (created automatically if missing)
EXPORT_FOLDER = "path/to/folder/_export/"

# Max height of the master font (in pixels)
MASTER_MAX_HEIGHT = 24

# config.json must define these keys; listed here only so we can validate
REQUIRED_CONFIG_KEYS = ("font_name", "space_pixels", "inter_char_space_pixels")

# ==========================================
#             BOLD (GRAS) SETTINGS
# ==========================================
# If True, a second "Bold" OTF is generated for every font folder, in addition
# to the Regular one. The Bold glyphs are built by dilating the Regular pixel
# mask (adding a ring of new "on" pixels around the existing shape), exactly
# like adding an outline of circles around the base letter.
GENERATE_BOLD_VARIANT = True

# Number of dilation passes applied to get the Bold mask. 1 pass = a 1-pixel
# halo around the shape (matches the reference "outline" mockup exactly).
# Increase this if an even heavier weight is wanted later.
BOLD_DILATION_PASSES = 1

# ==========================================
#             GRID SETTINGS
# ==========================================
CIRCLE_DIAMETER = 100  # Size of the circle itself
CIRCLE_GAP = 10  # Physical space between circles (Set to 0 to make them touch)

# The total space of one "bitmap pixel" is the circle + its gap
GRID_STEP = CIRCLE_DIAMETER + CIRCLE_GAP


def draw_circle(pen, cx, cy, d):
    """Draws a perfect circle using 4 cubic bezier curves."""
    r = d / 2.0
    kappa = 4.0 / 3.0 * (math.sqrt(2) - 1)
    h = r * kappa

    pen.moveTo((cx, cy + r))
    pen.curveTo((cx + h, cy + r), (cx + r, cy + h), (cx + r, cy))
    pen.curveTo((cx + r, cy - h), (cx + h, cy - r), (cx, cy - r))
    pen.curveTo((cx - h, cy - r), (cx - r, cy - h), (cx - r, cy))
    pen.curveTo((cx - r, cy + h), (cx - h, cy + r), (cx, cy + r))
    pen.closePath()


def load_pixel_mask(filepath):
    """Loads a BMP and returns (mask, width, height) where mask is a list of
    lists of booleans (mask[y][x] == True means an "on"/white pixel, i.e. a
    circle will be drawn there)."""
    img = Image.open(filepath).convert("1")
    width, height = img.size
    pixels = img.load()
    mask = [[pixels[x, y] > 0 for x in range(width)] for y in range(height)]
    return mask, width, height


def pad_mask(mask, width, height, pad):
    """Returns a new mask surrounded by `pad` rows/columns of empty (False)
    pixels on every side, plus the new width/height.

    This matters because a glyph's ink can touch the edge of its own bitmap
    (e.g. the loop of a '9' or the bar of a '2'/'5' starting right at
    x=0). Without this padding, dilate_mask has no array cell to grow into
    on that side, so the outline would be missing exactly where the shape
    touches the bitmap's border. Padding first guarantees there's always
    room to dilate outward on every side, border or not."""
    new_width = width + 2 * pad
    new_height = height + 2 * pad
    new_mask = [[False] * new_width for _ in range(new_height)]
    for y in range(height):
        for x in range(width):
            if mask[y][x]:
                new_mask[y + pad][x + pad] = True
    return new_mask, new_width, new_height


def dilate_mask(mask, width, height):
    """Returns a new mask with one extra ring of 'on' pixels added around the
    existing shape, using 4-connectivity (up/down/left/right neighbours only,
    no diagonals). This is exactly what turns a Regular glyph into its Bold
    counterpart: every pixel touching an existing circle gets a circle too.

    Only grows pixels that are already inside the mask's array bounds -
    call pad_mask first if the shape may touch the bitmap's edge."""
    new_mask = [row[:] for row in mask]
    for y in range(height):
        for x in range(width):
            if mask[y][x]:
                continue
            touches_on_pixel = (
                (x > 0 and mask[y][x - 1])
                or (x < width - 1 and mask[y][x + 1])
                or (y > 0 and mask[y - 1][x])
                or (y < height - 1 and mask[y + 1][x])
            )
            if touches_on_pixel:
                new_mask[y][x] = True
    return new_mask


def mask_max_x(mask, width, height):
    """Returns the column index of the rightmost 'on' pixel in mask, or -1 if
    the mask is entirely empty. Used to measure a glyph's advance width from
    its ORIGINAL (undilated) shape, so Bold can reuse the exact same value
    as Regular - see build_font for why that matters."""
    max_x = -1
    for y in range(height):
        for x in range(width):
            if mask[y][x]:
                max_x = max(max_x, x)
    return max_x


def draw_glyph_from_mask(pen, mask, mask_width, mask_height, orig_height, offset=0):
    """Draws one circle per 'on' pixel in mask and returns max_x (the column
    index of the rightmost 'on' pixel, in the ORIGINAL/unpadded coordinate
    system, or -1 if the mask is empty).

    `offset` is the padding that was applied via pad_mask (0 for Regular,
    since it isn't padded). Subtracting it maps mask coordinates back to the
    original bitmap coordinate system, so Regular and Bold glyphs stay
    perfectly aligned - the Bold outline simply extends past x=0/x=width-1
    (even into negative x) instead of shifting the whole glyph.
    `orig_height` is always the *unpadded* bitmap height, so the vertical
    flip lines up with the Regular glyph too."""
    max_x = -1
    for y in range(mask_height):
        for x in range(mask_width):
            if mask[y][x]:
                orig_x = x - offset
                orig_y = y - offset
                max_x = max(max_x, orig_x)
                cx = orig_x * GRID_STEP + (CIRCLE_DIAMETER / 2.0)
                cy = (orig_height - 1 - orig_y) * GRID_STEP + (CIRCLE_DIAMETER / 2.0)
                draw_circle(pen, cx, cy, CIRCLE_DIAMETER)
    return max_x


def load_configs(folder_path):
    """Reads config.json in folder_path. Supports a single config dict
    or a list of multiple config dicts (variants)."""
    config_path = os.path.join(folder_path, "config.json")
    if not os.path.isfile(config_path):
        print(
            f"  [ALERT] No config.json found in '{folder_path}'. Skipping this folder."
        )
        return []

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"  [ALERT] Could not parse config.json in '{folder_path}': {e}")
        return []

    # Normalize data to always be a list of configs
    if isinstance(data, dict):
        # If it's a single dictionary with the required keys, wrap it in a list
        if all(key in data for key in REQUIRED_CONFIG_KEYS):
            configs = [data]
        else:
            # If it's a dict of dicts (e.g., {"var1": {...}, "var2": {...}}), extract values
            configs = list(data.values())
    elif isinstance(data, list):
        configs = data
    else:
        print(f"  [ALERT] config.json in '{folder_path}' has an invalid format.")
        return []

    # Validate each configuration entry
    valid_configs = []
    for i, config in enumerate(configs):
        missing_keys = [key for key in REQUIRED_CONFIG_KEYS if key not in config]
        if missing_keys:
            print(
                f"  [ALERT] Entry {i} in '{folder_path}/config.json' is missing key(s): {missing_keys}. Skipping this entry."
            )
            continue
        valid_configs.append(config)

    return valid_configs


def build_glyph_masks(bmp_files):
    """Reads every BMP once and returns a dict:
    unicode_val -> {"mask": mask, "width": w, "height": h}
    Also returns has_space_character_bitmap (bool)."""
    space_unicode = 0x0020
    has_space_character_bitmap = False
    glyph_masks = {}

    for filepath in bmp_files:
        filename = os.path.basename(filepath)
        num_str = os.path.splitext(filename)[0]

        try:
            # Parse the filename as a standard Decimal value (Base 10) matching the PDF charts
            byte_val = int(num_str, 10)

            # Decode using Windows-1252 layout
            char = bytes([byte_val]).decode("cp1252", errors="replace")
            unicode_val = ord(char)

            # Fallback for codes unassigned in standard text tables to preserve raw indexing
            if unicode_val == 0xFFFD:
                unicode_val = byte_val

        except Exception as e:
            print(f"  [!] Skipping {filename}: {e}")
            continue

        if unicode_val == space_unicode:
            has_space_character_bitmap = True

        mask, width, height = load_pixel_mask(filepath)
        glyph_masks[unicode_val] = {"mask": mask, "width": width, "height": height}

    return glyph_masks, has_space_character_bitmap


def build_font(
    glyph_masks,
    has_space_character_bitmap,
    font_name,
    space_pixels,
    inter_char_space_pixels,
    bold=False,
):
    """Builds one UFO font (Regular or Bold) from the precomputed glyph masks
    and returns it, ready to be compiled to OTF."""
    font = ufoLib2.Font()

    # --- Naming ---------------------------------------------------------
    # familyName/styleName (nameID 16/17): the fully descriptive names shown
    # in font pickers, e.g. "Hanover RATP" / "149 Bold".
    font.info.familyName = FONT_FAMILY
    font.info.styleName = f"{font_name} Bold" if bold else font_name

    # styleMapFamilyName/styleMapStyleName (nameID 1/2): the classic
    # Regular/Bold grouping that Word/LibreOffice use to know which font to
    # switch to when the "Gras" (Bold) button is pressed. Both the Regular
    # and Bold masters of the same source font must share the same
    # styleMapFamilyName for this link to work.
    font.info.styleMapFamilyName = f"{FONT_FAMILY} {font_name}"
    font.info.styleMapStyleName = "bold" if bold else "regular"
    font.info.openTypeOS2WeightClass = 700 if bold else 400

    # Unified Master Grid calculation based on the tallest font size
    units_per_em = MASTER_MAX_HEIGHT * GRID_STEP
    font.info.unitsPerEm = units_per_em
    font.info.ascender = units_per_em
    font.info.descender = 0
    font.info.capHeight = units_per_em

    space_unicode = 0x0020

    for unicode_val, data in glyph_masks.items():
        mask, width, height = data["mask"], data["width"], data["height"]
        orig_height = height
        offset = 0

        # Measure the advance width from the ORIGINAL (undilated) shape,
        # before any Bold padding/dilation touches it. Regular and Bold both
        # use this same value below. If Bold used its own (dilated, often
        # wider) extent instead, each character would advance by a slightly
        # different amount in each font - fine for one letter, but the
        # mismatch accumulates letter after letter, so a Bold text layer
        # overlaid on a Regular layer (white text + black outline, as in the
        # Figma mockup) would drift further out of alignment with every
        # character. Sharing one width keeps both layers pixel-aligned no
        # matter how long the string is.
        base_max_x = mask_max_x(mask, width, height)

        # If the bitmap has no "on" pixel at all (entirely black), skip this
        # character completely: no glyph is created for it, instead of
        # creating an empty (invisible, zero-width) glyph.
        if base_max_x == -1:
            continue

        # For Bold, pad the mask first so the outline has room to grow even
        # where the shape already touches the bitmap's edge, then dilate it
        # to add a ring of circles around the existing shape (the "outline"
        # effect). This only affects the ink that gets drawn, not the
        # advance width computed above.
        if bold:
            offset = BOLD_DILATION_PASSES
            mask, width, height = pad_mask(mask, width, height, offset)
            for _ in range(BOLD_DILATION_PASSES):
                mask = dilate_mask(mask, width, height)

        glyph_name = f"uni{unicode_val:04X}"
        glyph = font.newGlyph(glyph_name)
        glyph.unicode = unicode_val

        pen = glyph.getPen()
        draw_glyph_from_mask(pen, mask, width, height, orig_height, offset)

        # Advance Width calculation - always based on the original shape
        # (base_max_x), identical for Regular and Bold. base_max_x can no
        # longer be -1 here since fully-empty bitmaps are skipped above.
        glyph.width = int((base_max_x + 1 + inter_char_space_pixels) * GRID_STEP)

    # Guarantee that the Space character exists even if there was no explicit bitmap file for it
    if not has_space_character_bitmap:
        space_glyph = font.newGlyph("space")
        space_glyph.unicode = space_unicode
        space_glyph.width = int(
            (space_pixels + inter_char_space_pixels * 2) * GRID_STEP
        )

    return font


def process_font_folder(folder_path):
    """Builds and saves the OTF(s) for all variants defined in the folder's config.json."""
    # 1. Load all configurations for this folder
    configs = load_configs(folder_path)
    if not configs:
        return

    # 2. Find BMP files
    bmp_files = glob.glob(os.path.join(folder_path, "*.[bB][mM][pP]"))
    bmp_files = [f for f in bmp_files if os.path.basename(f).upper() != "0032.BMP"]

    if not bmp_files:
        print(f"  [!] No BMP files found in '{folder_path}', skipping.")
        return

    folder_name = os.path.basename(folder_path)
    print(
        f"Found {len(configs)} variant(s) and {len(bmp_files)} bitmaps in '{folder_name}'."
    )

    # 3. Read every BMP ONCE for this folder.
    # All variants in this folder will reuse these masks, saving massive processing time!
    glyph_masks, has_space_character_bitmap = build_glyph_masks(bmp_files)
    family_nospaces = FONT_FAMILY.replace(" ", "")

    styles_to_build = [False]  # Regular always built
    if GENERATE_BOLD_VARIANT:
        styles_to_build.append(True)  # Bold

    # 4. Iterate through each variant configuration
    for config in configs:
        font_name = str(config["font_name"])
        space_pixels = config["space_pixels"]
        inter_char_space_pixels = config["inter_char_space_pixels"]

        print(f"\n  -> Processing variant: '{font_name}'")

        for bold in styles_to_build:
            font = build_font(
                glyph_masks,
                has_space_character_bitmap,
                font_name,
                space_pixels,
                inter_char_space_pixels,
                bold=bold,
            )
            suffix = "-Bold" if bold else ""
            output_filename = os.path.join(
                EXPORT_FOLDER,
                f"{family_nospaces}-{font_name.replace(' ', '')}{suffix}.otf",
            )
            print(f"     Compiling geometry to {output_filename} ...")
            otf = ufo2ft.compileOTF(font)
            otf.save(output_filename)
            print(f"     Done! Font generated: {output_filename}")


def main():
    if not os.path.isdir(ROOT_FOLDER):
        print(f"Error: Folder '{ROOT_FOLDER}' not found.")
        return

    # Each immediate subfolder of ROOT_FOLDER is treated as one font
    subfolders = [
        os.path.join(ROOT_FOLDER, d)
        for d in sorted(os.listdir(ROOT_FOLDER))
        if os.path.isdir(os.path.join(ROOT_FOLDER, d))
    ]

    if not subfolders:
        print(f"Error: No subfolders found in '{ROOT_FOLDER}'")
        return

    os.makedirs(EXPORT_FOLDER, exist_ok=True)

    print(f"Found {len(subfolders)} font folder(s) to process.\n")

    for folder in subfolders:
        process_font_folder(folder)
        print()

    print("All fonts processed.")


if __name__ == "__main__":
    main()
