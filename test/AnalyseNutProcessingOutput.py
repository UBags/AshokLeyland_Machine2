import re
import csv

# Input and output files
input_file = 'NutProcessing1.txt'
output_file = 'NutProcessingAnalysis1.csv'
# input_file = 'TopBearing1.txt'
# output_file = 'TopBearingAnalysis1.csv'

# List to hold rows
data = []

# Current state
current_filename = None
current_detected_area = None
current_gamma = None

with open(input_file, 'r', encoding='utf-8') as f:
    mask_area = None
    original_area = None

    for line in f:
        line = line.strip()

        # Detect new image detection line: "Trials - Nut and Plate washer - XX.png: YES; ..."
        if ': YES;' in line and '.png' in line:
            # Save previous image's entries if any
            # (no need to save here since we append immediately below)

            # Extract filename (everything before ": YES;")
            filename_match = re.match(r'(.*?\.png):\s*YES;', line)
            if filename_match:
                current_filename = filename_match.group(1).strip()

            # Extract Detected Area
            area_match = re.search(r'Area=(\d+)', line)
            current_detected_area = area_match.group(1) if area_match else ''

            # Extract gamma
            gamma_match = re.search(r'gamma=([\d.]+)', line)
            current_gamma = gamma_match.group(1) if gamma_match else ''

            # Reset for new image
            mask_area = None
            original_area = None
            continue

        # Detect "actual_nut_mask area:" line
        if line.startswith('actual_nut_mask area:'):
            # Parse mask area and original area
            area_str = line.split('actual_nut_mask area:')[1].strip()
            # e.g., "2653 pixels (original: 4614)"
            mask_match = re.search(r'^(\d+)', area_str)
            orig_match = re.search(r'original:\s*(\d+)', area_str)

            mask_area = mask_match.group(1) if mask_match else ''
            original_area = orig_match.group(1) if orig_match else ''
            continue

        # Detect next line: "Mean (B - R) in actual_nut_mask:"
        if line.startswith('Mean (B - R) in actual_nut_mask:'):
            if current_filename is not None and mask_area is not None:
                mean_match = re.search(r'Mean \(B - R\) in actual_nut_mask:\s*([\d.]+)', line)
                mean_br = mean_match.group(1) if mean_match else ''

                # Append row
                data.append([
                    current_filename,
                    current_detected_area or '',
                    current_gamma or '',
                    mask_area,
                    original_area or '',
                    mean_br
                ])

                # Reset for next mask measurement
                mask_area = None
                original_area = None

# Write to CSV
headers = [
    'Filename',
    'Detected_Area',
    'Gamma',
    'actual_nut_mask_area',
    'original_area',
    'Mean_B_minus_R'
]

with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
    writer = csv.writer(csvfile)
    writer.writerow(headers)
    writer.writerows(data)

print(f"CSV written to {output_file} with {len(data)} rows (multiple measurements per image).")