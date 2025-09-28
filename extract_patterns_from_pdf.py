import json
import os
import argparse
from PIL import Image
import PyPDF2
import io
try:
    from pdf2image import convert_from_path
    PDF2IMAGE_AVAILABLE = True
except ImportError:
    PDF2IMAGE_AVAILABLE = False

def extract_image_from_pdf_page(pdf_path, page_num):
    """Extract the image from a specific page of the PDF."""
    with open(pdf_path, 'rb') as file:
        reader = PyPDF2.PdfReader(file)
        if page_num > len(reader.pages):
            raise ValueError(f"Page {page_num} does not exist in PDF")
            
        page = reader.pages[page_num - 1]  # Convert 1-based to 0-based indexing
        resources = page.get('/Resources', {})
        x_objects = resources.get('/XObject', {})
        
        # Find and extract the main image from the page
        main_image = None
        max_size = 0
        
        for obj in x_objects:
            if x_objects[obj].get('/Subtype') == '/Image':
                image = x_objects[obj]
                size = image['/Width'] * image['/Height']
                if size > max_size:
                    # Extract image parameters
                    width = image['/Width']
                    height = image['/Height']
                    color_space = image['/ColorSpace'] if '/ColorSpace' in image else None
                    filter_type = image['/Filter'] if '/Filter' in image else None
                    image_data = image.get_data()
                    
                    main_image = {
                        'width': width,
                        'height': height,
                        'color_space': color_space,
                        'filter': filter_type,
                        'data': image_data
                    }
                    max_size = size
        
        if main_image is None:
            raise ValueError(f"No image found on page {page_num}")
            
        return main_image

def extract_image_from_pdf_page_using_pdf2image(pdf_path, page_num):
    """Extract the image from a specific page of the PDF using pdf2image."""
    if not PDF2IMAGE_AVAILABLE:
        raise ImportError("pdf2image is not installed. Please install it with: pip install pdf2image")
    
    # Convert the specific page to image
    images = convert_from_path(pdf_path, first_page=page_num, last_page=page_num)
    if not images:
        raise ValueError(f"No image found on page {page_num}")
    return images[0]

def convert_pdf_image_to_pil(image_info):
    """Convert PDF image data to PIL Image."""
    width = image_info['width']
    height = image_info['height']
    data = image_info['data']
    filter_type = image_info['filter']
    
    if filter_type == '/DCTDecode':
        # DCTDecode is standard JPEG
        return Image.open(io.BytesIO(data))
    elif filter_type == '/JPXDecode':
        # JPXDecode is JPEG2000
        temp_file = 'temp_jp2.jp2'
        try:
            with open(temp_file, 'wb') as img_file:
                img_file.write(data)
            img = Image.open(temp_file)
            pil_image = img.copy()
            img.close()
            return pil_image
        finally:
            if os.path.exists(temp_file):
                os.remove(temp_file)
    else:
        # For other formats, try direct conversion
        return Image.frombytes('RGB', (width, height), data)

# Parse command line arguments
parser = argparse.ArgumentParser(description='Extract knitting patterns from a PDF file.')
parser.add_argument('pdf_path', help='Path to the PDF file containing the patterns')
parser.add_argument('json_path', help='Path to the JSON file containing pattern locations')
parser.add_argument('output_dir', help='Directory where extracted patterns will be saved')
parser.add_argument('--use-pdf2image', action='store_true', help='Use pdf2image instead of PyPDF2 for image extraction')
args = parser.parse_args()

# Create output directory if it doesn't exist
output_dir = args.output_dir
os.makedirs(output_dir, exist_ok=True)

# Read the pattern locations
with open(args.json_path, 'r') as f:
    pattern_locations = json.load(f)

# Group patterns by page to avoid converting the same page multiple times
patterns_by_page = {}
for pattern in pattern_locations:
    page = pattern['page']
    if page not in patterns_by_page:
        patterns_by_page[page] = []
    patterns_by_page[page].append(pattern)

# Convert PDF pages to images and extract patterns
print(f"Processing PDF: {args.pdf_path}")

# Process each page
all_pages = sorted(patterns_by_page.keys())
for page_num in all_pages:
    print(f"Processing page {page_num}...")
    
    # Extract the page image
    if args.use_pdf2image:
        page_image = extract_image_from_pdf_page_using_pdf2image(args.pdf_path, page_num)
    else:
        image_info = extract_image_from_pdf_page(args.pdf_path, page_num)
        page_image = convert_pdf_image_to_pil(image_info)
    print(f"Page size: {page_image.size}")
    patterns = patterns_by_page[page_num]
    
    # Extract each pattern from the page
    for pattern in patterns:
        pattern_num = pattern['pattern_number']
        
        # Get coordinates as fractions and convert to pixels
        x = pattern['x']
        y = pattern['y']
        w = pattern['w']
        h = pattern['h']
        
        # Convert fractions to pixel coordinates
        img_width, img_height = page_image.size
        x1 = int(x * img_width)
        y1 = int(y * img_height)
        width = int(w * img_width)
        height = int(h * img_height)
        
        # Calculate bottom-right coordinates
        x2 = x1 + width
        y2 = y1 + height
        crop_bbox = (x1, y1, x2, y2)
        
        # Extract and save the pattern
        pattern_image = page_image.crop(crop_bbox)
        output_path = os.path.join(output_dir, f'pattern_{pattern_num:03d}.png')
        pattern_image.save(output_path, 'PNG')
        print(f"Saved pattern {pattern_num} to {output_path}")

print("Done extracting patterns!")
