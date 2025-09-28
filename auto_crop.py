import os
import sys
import json
import cv2
import numpy as np

XMARGIN=0
YMARGIN=0

def auto_crop_image(image_path, output_root, min_area=200*200):
    filename = os.path.basename(image_path)
    name, _ = os.path.splitext(filename)

    image = cv2.imread(image_path)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    edged = cv2.Canny(gray, 30, 150)
    dilated = cv2.dilate(edged, None, iterations=2)

    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    bounding_boxes = [cv2.boundingRect(c) for c in contours]
    bounding_boxes = sorted(bounding_boxes, key=lambda b: (b[1], b[0]))

    page_output_dir = os.path.join(output_root, f"{name}_crops")
    os.makedirs(page_output_dir, exist_ok=True)

    metadata = {"image": filename, "crops": []}

    for idx, (x, y, w, h) in enumerate(bounding_boxes):
        if w * h < min_area:
            continue
        ratio = w / float(h)
        # if  ratio < 0.25 or ratio > 4.0:
        #     continue
        crop = image[max(y-YMARGIN, 0):y+h, max(x-XMARGIN, 0):x+w]
        crop_filename = f"crop_{idx+1}.png"
        crop_path = os.path.join(page_output_dir, crop_filename)
        cv2.imwrite(crop_path, crop)

        rel_path = os.path.relpath(crop_path, output_root)
        metadata["crops"].append({
            "filename": rel_path,
            "bbox": [int(x), int(y), int(w), int(h)]
        })

    return metadata

def main():
    if len(sys.argv) < 2:
        print("Usage: python auto_crop_jpegs.py image1.jpg image2.jpg ...")
        return

    output_root = "output_crops"
    os.makedirs(output_root, exist_ok=True)

    metadata_list = []

    for image_path in sys.argv[1:]:
        print(f"Processing {image_path}...")
        metadata = auto_crop_image(image_path, output_root)
        metadata_list.append(metadata)

    # Save metadata
    with open(os.path.join(output_root, "crops_metadata.json"), "w") as f:
        json.dump(metadata_list, f, indent=2)

    print("Done! Cropped images and metadata saved to:", output_root)

if __name__ == "__main__":
    main()
