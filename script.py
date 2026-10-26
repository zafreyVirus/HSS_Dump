import os
import csv
import glob

def combine_all_columns_all_files(folder_path=None, output_filename="combined_all_columns.csv"):
    """
    Combine ALL columns from ALL data files into a single CSV
    Uses a single title file for headers and processes all data files
    Memory efficient - streams data directly to output file
    """
    # Set the folder path
    if folder_path is None:
        folder_path = "/home/f_msusa/data/dump1"

    # Ensure the folder path exists
    if not os.path.exists(folder_path):
        print(f"ERROR: Folder not found: {folder_path}")
        return

    print(f"Looking for files in: {folder_path}")
    print("="*70)

    # Find all title files
    title_files = glob.glob(os.path.join(folder_path, "*_title.txt"))

    if not title_files:
        print("No *_title.txt files found in the folder.")
        return

    print(f"Found {len(title_files)} title file(s)")

    # Get headers from the first title file
    first_title_file = title_files[0]
    print(f"Reading headers from: {os.path.basename(first_title_file)}")

    with open(first_title_file, 'r', encoding='utf-8') as f:
        header_line = f.readline().strip()
        headers = next(csv.reader([header_line]))

    headers = [h.strip() for h in headers if h.strip()]
    print(f"Headers found: {len(headers)}")
    print(f"First few headers: {headers[:10]}...")

    # Output file
    output_path = os.path.join(folder_path, output_filename)

    # Write header
    print(f"\nCreating output file: {output_filename}")
    with open(output_path, 'w', newline='', encoding='utf-8') as outfile:
        writer = csv.writer(outfile)
        writer.writerow(headers)

    total_rows = 0

    # Find ALL data files (all numbers)
    data_files = sorted(glob.glob(os.path.join(folder_path, "*_data.txt")))

    if not data_files:
        print("No data files found!")
        return

    print(f"\nFound {len(data_files)} data file(s) to process:")
    for df in data_files:
        file_size_mb = os.path.getsize(df) / (1024*1024)
        print(f"  - {os.path.basename(df)} ({file_size_mb:.2f} MB)")

    print("\n" + "="*70)

    # Process each data file
    for data_file in data_files:
        try:
            print(f"\nProcessing: {os.path.basename(data_file)}")

            # Get file size
            file_size = os.path.getsize(data_file)
            print(f"  File size: {file_size / (1024*1024):.2f} MB")

            file_rows = 0

            # Process line by line (memory efficient)
            with open(data_file, 'r', encoding='utf-8') as infile:
                reader = csv.reader(infile)

                # Open output file in append mode
                with open(output_path, 'a', newline='', encoding='utf-8') as outfile:
                    writer = csv.writer(outfile)

                    for row in reader:
                        if row:
                            # Clean values
                            values = [v.strip() for v in row]

                            # Adjust length to match headers
                            if len(values) > len(headers):
                                values = values[:len(headers)]
                            elif len(values) < len(headers):
                                values.extend([''] * (len(headers) - len(values)))

                            # Write directly to file
                            writer.writerow(values)

                            file_rows += 1
                            total_rows += 1

                            # Progress indicator every 100,000 rows
                            if file_rows % 100000 == 0:
                                print(f"  Processed {file_rows:,} rows from this file...")

            print(f"  ✅ Completed: {file_rows:,} rows from this file")

        except Exception as e:
            print(f"  ❌ Error processing {data_file}: {e}")
            continue

    print("\n" + "="*70)
    print(f"\n✅ CSV created: {output_path}")
    print(f"   Total rows: {total_rows:,}")
    print(f"   Total columns: {len(headers)}")

    # Show preview of first few rows
    print("\nPreview (first 3 rows):")
    with open(output_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        header_row = next(reader)
        print(f"Headers: {header_row[0]}, {header_row[1]}, {header_row[2]}, ...")
        for i in range(3):
            try:
                row = next(reader)
                print(f"Row {i+1}: {row[0]}, {row[1]}, {row[2]}, ...")
            except StopIteration:
                break

    return output_path

# Usage
if __name__ == "__main__":
    combine_all_columns_all_files(
        folder_path="/home/f_msusa/data/dump1",
        output_filename="combined_all_columns.csv"
    )
