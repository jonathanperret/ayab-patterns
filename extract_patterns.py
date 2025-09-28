import os
from pathlib import Path
from PIL import Image
from PIL.PngImagePlugin import PngInfo
import shutil

PATTERN_COUNT = 683
PATTERN_RECORD_SIZE = 12
VALID_HEADERS = {bytes([0x02, 0x00]), bytes([0x03, 0x00])}

def bcd_to_int(bytes):
    """Convert little-endian BCD bytes to integer.
    
    >>> bcd_to_int(bytes([0x01, 0x00]))
    1
    >>> bcd_to_int(bytes([0x19, 0x06]))
    619
    >>> bcd_to_int(bytes([0x66, 0x00]))
    66
    >>> bcd_to_int(bytes([0x00, 0x01]))
    100
    """
    result = 0
    multiplier = 1
    for b in bytes:
        low_digit = b & 0x0F
        high_digit = (b >> 4) & 0x0F
        result += (low_digit + high_digit * 10) * multiplier
        multiplier *= 100
    return result

def decode_pattern_offset(bytes):
    """Decode 3-byte pattern offset with custom byte ordering.
    
    >>> hex(decode_pattern_offset(bytes([0x05, 0x04, 0x20])))
    '0x52004'
    >>> hex(decode_pattern_offset(bytes([0x07, 0x14, 0xa7])))
    '0x7a714'
    """
    return (bytes[0] << 16) | (bytes[2] << 8) | bytes[1]

def read_pattern_index(rom_data):
    patterns = []
    index_offset = 0x50000
    
    for i in range(PATTERN_COUNT):
        record = rom_data[index_offset + i * PATTERN_RECORD_SIZE:index_offset + (i + 1) * PATTERN_RECORD_SIZE]
        if record[0:2] not in VALID_HEADERS:
            continue
            
        pattern = {
            'type': record[0],
            'number': bcd_to_int(record[2:4]),
            'width': bcd_to_int(record[4:6]),
            'height': bcd_to_int(record[6:8]),
            'offset': decode_pattern_offset(record[9:12])
        }
        patterns.append(pattern)
    
    return patterns

def decode_monochrome_bitmap(rom_data, width, height, bitmap_offset):
    """Decode a monochrome bitmap into a 2D array of 0s and 1s."""
    row_bytes = (width + 7) // 8
    pixels = []
    
    for row in range(height):
        row_pixels = []
        row_offset = bitmap_offset + row * row_bytes  # Changed: removed height-1-row
        row_data = rom_data[row_offset:row_offset + row_bytes]
        
        for byte_idx, byte in enumerate(row_data):
            for bit in range(8):
                x = byte_idx * 8 + bit
                if x < width:
                    row_pixels.append(0 if not (byte & (1 << bit)) else 1)
        pixels.append(row_pixels)
    
    return pixels

def extract_monochrome_bitmap(rom_data, pattern):
    memo_bytes = (pattern['height'] + 1) // 2
    bitmap_offset = pattern['offset'] + memo_bytes
    
    # Parse memo data
    memo_values = parse_memo_data(rom_data, pattern['offset'], pattern['height'])
    
    pixels = decode_monochrome_bitmap(rom_data, pattern['width'], pattern['height'], bitmap_offset)
    
    img = Image.new('1', (pattern['width'], pattern['height']))
    for y, row in enumerate(reversed(pixels)):
        for x, value in enumerate(row):
            img.putpixel((x, y), 1 if value == 0 else 0)
    
    # Store non-zero memo data in PNG metadata
    if any(memo_values):
        try:
            metadata = PngInfo()
            comment = 'AYAB:' + ''.join(memo_value_to_char(v) for v in memo_values)
            metadata.add_text('Comment', comment)
            img.text = {'Comment': comment}
            img.info['pnginfo'] = metadata
        except ValueError as e:
            print(f"Error processing pattern {pattern['number']}: {e}")
            raise
    
    return img

def memo_value_to_char(value):
    """Convert a memo value to its string representation.
    
    >>> memo_value_to_char(0)
    '0'
    >>> memo_value_to_char(8)
    '8'
    >>> memo_value_to_char(10)
    'N'
    >>> memo_value_to_char(11)
    'F'
    """
    if 0 <= value <= 8:
        return str(value)
    elif value == 10:
        return 'N'
    elif value == 11:
        return 'F'
    else:
        raise ValueError(f"Invalid memo value: {value}")

def parse_memo_data(rom_data, offset, height):
    """Parse Memo data bytes into a list of integers (4 bits per row).
    
    Args:
        rom_data: ROM data bytes
        offset: Starting offset of Memo data
        height: Number of rows to parse
        
    Returns:
        List of integers, one per row from bottom to top
    """
    memo_bytes = (height + 1) // 2
    memo_data = rom_data[offset:offset + memo_bytes]
    memo_values = []
    
    for row in range(height):
        memo_byte = memo_data[row // 2]
        if row % 2 == 0:
            memo_values.append(memo_byte & 0x0F)
        else:
            memo_values.append(memo_byte >> 4)
    
    return memo_values

def extract_multicolor_bitmap(rom_data, pattern):
    width = pattern['width']
    real_height = pattern['height'] // 3
    
    # Calculate bitmap offset after memo data
    memo_bytes = (pattern['height'] + 1) // 2
    bitmap_offset = pattern['offset'] + memo_bytes
    
    # Parse memo data
    memo_values = parse_memo_data(rom_data, pattern['offset'], pattern['height'])
    
    # Get the full monochrome bitmap
    mono_pixels = decode_monochrome_bitmap(rom_data, width, pattern['height'], bitmap_offset)
    
    # Create 2D array of colors (bottom to top)
    pixels = []
    for row in range(0, real_height):
        row_pixels = []
        base_row = row * 3
        memo_for_block = memo_values[base_row:base_row + 3]

        # Process each pixel in the row
        for x in range(width):
            color = 0
            for i in range(3):
                if mono_pixels[base_row + i][x]:
                    color = memo_for_block[i]
                    break
            row_pixels.append(color)
        pixels.append(row_pixels)
    
    # Convert 2D array to PIL image
    img = Image.new('P', (width, real_height))
    
    # Create fixed 4-shade grayscale palette
    palette = [
        255, 255, 255,  # White (0)
        170, 170, 170,  # Light gray (1)
        85, 85, 85,     # Dark gray (2)
        0, 0, 0,        # Black (3)
    ]
    img.putpalette(palette)
    
    # Write pixels bottom-to-top
    flattened = [color for row in reversed(pixels) for color in row]
    img.putdata(flattened)
    
    return img

def extract_pattern_bitmap(rom_data, pattern):
    if pattern['type'] == 0x02:
        return extract_monochrome_bitmap(rom_data, pattern)
    else:
        return extract_multicolor_bitmap(rom_data, pattern)

def main():
    import doctest
    doctest.testmod()
    
    rom_path = Path('kh970-rom.bin')
    out_dir = Path('StitchWorld3')
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(exist_ok=True)
    
    with open(rom_path, 'rb') as f:
        rom_data = f.read()
        
    patterns = read_pattern_index(rom_data)
    
    for pattern in patterns:
        number = pattern['number']
        img = extract_pattern_bitmap(rom_data, pattern)
        output_path = out_dir / f'{number:03d}.png'
        # Use the metadata when saving if it exists
        if 'pnginfo' in img.info:
            img.save(output_path, pnginfo=img.info['pnginfo'])
        else:
            img.save(output_path)
        print(f"Saved pattern {number} ({pattern['width']}x{pattern['height']}x{pattern['type']}) to {output_path}")

if __name__ == '__main__':
    main()
