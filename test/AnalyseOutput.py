import re
import csv

# Input and output files
input_file = 'data.txt'
output_file = 'analysis.csv'

# List to hold rows
data = []

# Variables for current entry
current_sl = None
current_section = None  # 'R' or 'RB'

r_top = None
r_nut = None
r_min = None
r_max = None
r_p01 = r_p02 = r_p05 = r_p1 = None

rb_top = None
rb_nut = None
rb_min = None
rb_max = None
rb_p01 = rb_p02 = rb_p05 = rb_p1 = None

with open(input_file, 'r', encoding='utf-8') as f:
    for line in f:
        line = line.strip()

        # Detect new processing block [n/145]
        match_sl = re.match(r'\[(\d+)/\d+\]\s+Processing:', line)
        if match_sl:
            # Save previous entry if exists
            if current_sl is not None:
                data.append([
                    current_sl,
                    r_top or '', r_nut or '',
                    r_min or '', r_max or '',
                    r_p01 or '', r_p02 or '', r_p05 or '', r_p1 or '',
                    rb_top or '', rb_nut or '',
                    rb_min or '', rb_max or '',
                    rb_p01 or '', rb_p02 or '', rb_p05 or '', rb_p1 or ''
                ])

            # Start new entry
            current_sl = match_sl.group(1)
            current_section = None
            r_top = r_nut = r_min = r_max = None
            r_p01 = r_p02 = r_p05 = r_p1 = None
            rb_top = rb_nut = rb_min = rb_max = None
            rb_p01 = rb_p02 = rb_p05 = rb_p1 = None
            continue

        # Detect section
        if line.startswith('Results (R Channel):'):
            current_section = 'R'
            continue
        if line.startswith('Results (R-B Difference):'):
            current_section = 'RB'
            continue

        # Extract Top avg and Nut avg
        if 'Top avg' in line and 'Nut avg' in line:
            if '(R+gamma)' in line:
                parts = re.split(r', Nut avg \(R\+gamma\): ', line)
                if len(parts) == 2:
                    r_top = parts[0].split(': ', 1)[1].strip()
                    r_nut = parts[1].strip()
            elif '(R-B+gamma)' in line:
                parts = re.split(r', Nut avg \(R-B\+gamma\): ', line)
                if len(parts) == 2:
                    rb_top = parts[0].split(': ', 1)[1].strip()
                    rb_nut = parts[1].strip()

        # Extract Difference range and split into min/max
        if line.startswith('Difference range:'):
            range_str = line.split(':', 1)[1].strip()  # e.g., [38.61, 100.34]
            match = re.search(r'\[([\d.]+),\s*([\d.]+)\]', range_str)
            if match:
                min_val = match.group(1)
                max_val = match.group(2)
                if current_section == 'R':
                    r_min = min_val
                    r_max = max_val
                elif current_section == 'RB':
                    rb_min = min_val
                    rb_max = max_val

        # Extract Percentiles
        if line.startswith('Percentiles:'):
            perc_str = line.split(':', 1)[1].strip()
            p01_match = re.search(r'0\.1%=([0-9.]+)', perc_str)
            p02_match = re.search(r'0\.2%=([0-9.]+)', perc_str)
            p05_match = re.search(r'0\.5%=([0-9.]+)', perc_str)
            p1_match = re.search(r'1%=([0-9.]+)', perc_str)

            if p01_match and p02_match and p05_match and p1_match:
                if current_section == 'R':
                    r_p01 = p01_match.group(1)
                    r_p02 = p02_match.group(1)
                    r_p05 = p05_match.group(1)
                    r_p1 = p1_match.group(1)
                elif current_section == 'RB':
                    rb_p01 = p01_match.group(1)
                    rb_p02 = p02_match.group(1)
                    rb_p05 = p05_match.group(1)
                    rb_p1 = p1_match.group(1)

# Don't forget the last entry
if current_sl is not None:
    data.append([
        current_sl,
        r_top or '', r_nut or '',
        r_min or '', r_max or '',
        r_p01 or '', r_p02 or '', r_p05 or '', r_p1 or '',
        rb_top or '', rb_nut or '',
        rb_min or '', rb_max or '',
        rb_p01 or '', rb_p02 or '', rb_p05 or '', rb_p1 or ''
    ])

# Write to CSV
headers = [
    'Sl No',
    'Top avg (R+gamma)', 'Nut avg (R+gamma)',
    'diff_min_R', 'diff_max_R',
    '0.1%_R', '0.2%_R', '0.5%_R', '1%_R',
    'Top avg (R-B+gamma)', 'Nut avg (R-B+gamma)',
    'diff_min_RB', 'diff_max_RB',
    '0.1%_RB', '0.2%_RB', '0.5%_RB', '1%_RB'
]

with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
    writer = csv.writer(csvfile)
    writer.writerow(headers)
    writer.writerows(data)

print(f"CSV written to {output_file} with {len(data)} rows.")