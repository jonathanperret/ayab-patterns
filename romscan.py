from PIL import Image
import sys
import os.path

# Global ROM data
rom_data = None

def line_to_bytes(line):
    # Convert a string of '#' and '.' to bytes (8 pixels per byte, LSB first)
    bytes_data = []
    for i in range(0, len(line), 8):
        byte = 0
        for bit in range(8):
            if line[i + bit] == '#':
                # Set bit (LSB first)
                byte |= (1 << bit)
        bytes_data.append(byte)
    return bytes_data

def hamming_distance(bytes1, bytes2):
    # Count differing bits between two byte sequences
    total_bits = len(bytes1) * 8
    diff_bits = 0
    for b1, b2 in zip(bytes1, bytes2):
        xor = b1 ^ b2
        diff_bits += bin(xor).count('1')
    return diff_bits

def exact_search_in_rom(pattern, height, search_ranges, verbose=False, basename=None, width=None):
    pattern_len = len(pattern)
    prefix_len = (height + 1) // 2
    matches = []
    for start_pos, end_pos in search_ranges:
        if (prefix_len + pattern_len) > (end_pos - start_pos):
            continue
        pos = start_pos + prefix_len
        while pos < end_pos:
            pos = rom_data.find(pattern, pos, end_pos)
            if pos < 0:
                break
            matches.append(pos)
            pos += 1
    
    if not matches:
        return None
        
    # If multiple matches, report them and return special value
    if len(matches) > 1:
        print(f"Pattern {basename} ({width}x{height}) appears at multiple offsets:")
        for pos in matches[:5]:
            print(f"  {pos:06x}, offset {pos - prefix_len:06x}")
        return "ambiguous"  # Special return value for ambiguous patterns
    
    pos = matches[0]
    offset = pos - prefix_len

    if verbose:
        print(f"Found exact match at {pos:06x}, offset {offset:06x}")

    pattern_length = len(pattern)
    return (offset, pos + pattern_length, None)

def fuzzy_search_in_rom(pattern, height, search_ranges, verbose=False, basename=None, width=None):
    global rom_data
    pattern_len = len(pattern)
    best_pos = -1
    best_distance = float('inf')
    found_positions = []
    
    if verbose:
        print(f"\nFuzzy searching with 10% tolerance...")
        print("Search ranges:")
        for start, end in search_ranges:
            print(f"  {start:06x}-{end:06x}")
    
    # Scan all ranges
    for start_pos, end_pos in search_ranges:
        if pattern_len > end_pos - start_pos:
            continue
        for pos in range(start_pos, end_pos - pattern_len + 1):
            distance = hamming_distance(pattern, rom_data[pos:pos + pattern_len])
            max_errors = int(pattern_len * 8 * 0.1)  # 10% tolerance
            if distance <= max_errors:
                if distance < best_distance:
                    best_distance = distance
                    best_pos = pos
                found_positions.append((pos, distance))
    
    if best_pos < 0:
        return None
    
    # Count matches with the best distance
    best_matches = sum(1 for pos, dist in found_positions if dist == best_distance)
    if best_matches > 1:
        print(f"Pattern {basename} ({width}x{height}) has {best_matches} matches with {best_distance} bits different:")
        shown = 0
        for pos, dist in found_positions:
            if dist == best_distance and shown < 5:
                print(f"  {pos:06x}")
                shown += 1
        return None
    
    pos = best_pos
    if verbose:
        print(f"Found match at {pos:06x} with {best_distance} bits different")
    
    offset = pos - ((height + 1) // 2)
    pattern_length = len(pattern)
    return (offset, pos + pattern_length, best_distance)

def find_offset_references(offset, rom_path="kh970-rom.bin"):
    global rom_data
    offset_bytes = (offset & 0xffff).to_bytes(2, byteorder='little')
    refs = []
    pos = 0
    # Find up to 5 references
    for _ in range(5):
        pos = rom_data.find(offset_bytes, pos)
        if pos < 0:
            break
        refs.append(pos)
        pos += 1
    return refs

def load_patterns(image_files, verbose=False):
    patterns = []
    for image_file in image_files:
        # Open the image file
        with Image.open(image_file) as img:
            # Convert the image to grayscale
            grayscale = img.convert("L")
            # Count unique colors (before converting to monochrome)
            unique_colors = len(set(grayscale.getdata()))
            if unique_colors != 2:
                if verbose:
                    print(f"Skipping {image_file}: contains {unique_colors} colors")
                continue
            
            # Convert the image to monochrome (binary) image
            monochrome = grayscale.point(lambda x: 0 if x < 128 else 255, '1')

            # Get the pixel data
            pixels = monochrome.load()
            width, height = monochrome.size
            
            # Store original width before padding
            original_width = width

            # Calculate padding needed to reach multiple of 8
            padding = (8 - (width % 8)) % 8

            # Convert pixels to bytes
            all_bytes = []
            for y in range(height - 1, -1, -1):
                row_bits = ""
                for x in range(width):
                    row_bits += "#" if pixels[x, y] == 0 else "."
                row_bits += "." * padding
                all_bytes.extend(line_to_bytes(row_bits))
            
            # Convert all bytes to a bytestring
            byte_data = bytes(all_bytes)
            basename = os.path.splitext(os.path.basename(image_file))[0]
            patterns.append((image_file, basename, byte_data, height, original_width))
    return patterns

def search_pattern(basename, byte_data, height, width, search_ranges=None, verbose=False):
    # Search for the pattern in ROM (exact match only)
    result = exact_search_in_rom(byte_data, height, search_ranges=search_ranges, verbose=verbose, 
                         basename=basename, width=width)
    if result == "ambiguous":
        return (None, True)  # (result, is_ambiguous)
    return (result, False)

def find_gaps(results, rom_size):
    gaps = []
    last_end = 0x50000  # Start position in ROM
    
    for _, (start, end, _), _ in sorted(results, key=lambda x: x[1][0]):
        if start > last_end:
            gaps.append((last_end, start))
        last_end = end
    
    # Add final gap if needed
    if last_end < rom_size:
        gaps.append((last_end, rom_size))
    
    return gaps

def bytes_to_png(byte_data, height, width, output_path):
    # Skip ancillary data at start
    skip_bytes = (height + 1) // 2
    pattern_data = byte_data[skip_bytes:]

    # Calculate bytes per row from padded width, using pattern_data length
    bytes_per_row = len(pattern_data) // height

    # Create a new binary image with original width
    img = Image.new('1', (width, height))
    pixels = img.load()

    # Convert bytes back to pixels (LSB first)
    for y in range(height):
        row_start = y * bytes_per_row
        for x_byte in range((width + 7) // 8):
            byte = pattern_data[row_start + x_byte]
            for bit in range(8):
                x = x_byte * 8 + bit
                if x < width:  # Only set pixels up to original width
                    pixels[x, height - 1 - y] = not (byte & (1 << bit))

    # Save the image
    img.save(output_path, 'PNG')

# Main processing
if len(sys.argv) < 2:
    print("Usage: python romscan.py [-verbose] <image_file> [<image_file> ...]")
    sys.exit(1)

# Read ROM data at startup
with open("kh970-rom.bin", 'rb') as f:
    rom_data = f.read()
rom_size = len(rom_data)

verbose = "-verbose" in sys.argv
image_files = [arg for arg in sys.argv[1:] if arg != "-verbose"]

# First load all patterns
patterns = load_patterns(image_files, verbose)

# Sort patterns by decreasing size
patterns.sort(key=lambda p: len(p[2]), reverse=True)

# First calculate gaps (initially the whole search space)
gaps = [(0x50000, rom_size)]

# First pass: exact matches only
results = []
not_found = []
ambiguous = []
for image_file, basename, byte_data, height, width in patterns:
    result, is_ambiguous = search_pattern(basename, byte_data, height, width, 
                                        search_ranges=gaps, verbose=verbose)
    if result:  # if match found
        results.append((basename, result, None))  # (basename, (start, end, distance))
        # Update gaps after each match
        gaps = find_gaps(results, rom_size)
    elif is_ambiguous:
        ambiguous.append(basename)
    else:
        not_found.append((image_file, byte_data, height, width))

gaps = find_gaps(results, rom_size)

still_not_found = []

if not_found:
    print(f"\nTrying fuzzy search for {len(not_found)} remaining patterns...")

    # Second pass: try fuzzy matching in gaps for not found patterns
    for image_file, pattern, height, width in not_found:
        basename = os.path.splitext(os.path.basename(image_file))[0]
        found = False
        
        print(f"\nTrying fuzzy search for pattern: {basename}")
        result = fuzzy_search_in_rom(pattern, height, search_ranges=gaps, verbose=verbose,
                                    basename=basename, width=width)
        if result:
            print(f"\nFound {basename} at {result[0]:06x}-{result[1]:06x} with {result[2]} bits different")
            
            # Extract correct pattern from ROM using global rom_data
            correct_pattern = rom_data[result[0]:result[1]]
            bytes_to_png(correct_pattern, height, width, image_file)
            print(f"Overwrote {image_file} with correct pattern from ROM")
            
            results.append((basename, result, True))  # Mark as fuzzy match
            # Update gaps after adding new match
            gaps = find_gaps(results, rom_size)
            found = True

        if not found:
            still_not_found.append(basename)

# Sort all results (both exact and fuzzy matches) by offset
results_sorted = sorted(results, key=lambda x: x[1][0])

# Print results sorted by offset with gaps
for i, (basename, (start, end, distance), is_fuzzy) in enumerate(results_sorted):
    refs = find_offset_references(start)
    refs_str = " ".join(f"@{ref:06x}" for ref in refs) if refs else ""
    fuzzy_str = f" (fuzzy: {distance} bits)" if is_fuzzy else ""
    print(f"{start:06x}-{end:06x}: {basename}{fuzzy_str} {refs_str}")
    
    if i < len(results_sorted) - 1 and end != results_sorted[i + 1][1][0]:
        next_start = results_sorted[i + 1][1][0]
        print(f"{end:06x}-{next_start:06x}: <unknown>")

if still_not_found:
    print("\nNot found in ROM:")
    for basename in sorted(still_not_found):
        if basename not in ambiguous:  # Only show truly not found patterns
            print(f"  {basename}")

def zero_patterns_in_rom(results_sorted, output_rom="kh970-rom-zeroed.bin"):
    global rom_data
    rom_data_copy = bytearray(rom_data)
    
    # Zero out each found pattern
    for _, (start, end, _), _ in results_sorted:
        # Calculate actual ROM positions from adjusted offsets
        length = end - start
        rom_data_copy[start:start+length] = bytes(length)  # Fill with zeros
    
    # Write modified ROM
    with open(output_rom, 'wb') as f:
        f.write(rom_data_copy)

# Create zeroed ROM copy
zero_patterns_in_rom(results_sorted)
