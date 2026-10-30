import os
import argparse


def rename_files(directory: str, prefix: str, dry_run: bool = False) -> None:
    """
    Renames all files (non-recursive, top-level only) in the given directory
    to "{prefix} - {count}{extension}", where count is sequential starting from 1
    and padded with leading zeros based on the total number of files.

    Example:
        photo.jpg  ->  Vacation - 01.jpg
        notes.txt  ->  Vacation - 02.txt
    """
    if not os.path.isdir(directory):
        print(f"Error: '{directory}' is not a valid directory.")
        return

    # Collect only files (skip subdirectories)
    files = []
    for entry in os.listdir(directory):
        if os.path.isfile(os.path.join(directory, entry)):
            files.append(entry)

    if not files:
        print("No files found in the directory.")
        return

    # Sort case-insensitively for consistent ordering
    files.sort(key=lambda x: x.lower())

    # Determine padding width (e.g., 001 for 100+ files, 01 for 10–99, none for <10)
    num_files = len(files)
    width = len(str(num_files))

    for i, filename in enumerate(files, start=1):
        old_path = os.path.join(directory, filename)

        # Preserve original extension (includes the dot if present, empty if none)
        _, ext = os.path.splitext(filename)

        # Format: "prefix - 001.ext" (with space around dash)
        new_filename = f"{prefix} - {i:0{width}d}{ext}"
        new_path = os.path.join(directory, new_filename)

        if os.path.exists(new_path):
            print(f"Warning: '{new_filename}' already exists → skipping '{filename}'")
            continue

        if dry_run:
            print(f"[Dry run] Would rename: '{filename}' → '{new_filename}'")
        else:
            try:
                os.rename(old_path, new_path)
                print(f"Renamed: '{filename}' → '{new_filename}'")
            except Exception as e:
                print(f"Error renaming '{filename}': {e}")


if __name__ == "__main__":
    rename_files("C:/RHS_Images/TopBearing", "Trials - TopBearing", dry_run=False)
