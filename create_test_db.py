#!/usr/bin/env python3
"""Create a test SQLite database from the export.xml file."""

from src.parser.parser import AppleHealthParser
import os

def main():
    xml_file = "data/export.xml"
    db_file = "data/health_data.db"
    
    # Remove existing database if it exists
    if os.path.exists(db_file):
        os.remove(db_file)
        print(f"Removed existing {db_file}")
    
    # Create the parser with no date cutoff to include all records
    from datetime import timedelta
    parser = AppleHealthParser(db_path=db_file, data_cutoff=timedelta(days=36500))  # 100 years
    print(f"Parsing {xml_file}...")
    parser.parse_file(xml_file)
    print(f"Successfully created {db_file}")

if __name__ == "__main__":
    main()