"""Path utilities for SMT-LIB benchmark files."""

import os
from pathlib import Path


def shorten(p):
    """Strip a benchmark path to the portion after incremental/ or non-incremental/."""
    for tag in ("non-incremental/", "incremental/"):
        if tag in p:
            return p.split(tag, 1)[-1]
    return p.split("/")[-1]


def extract_logic(p):
    """Extract the SMT-LIB logic name from a benchmark path."""
    for tag in ("non-incremental/", "incremental/"):
        if tag in p:
            rest = p.split(tag, 1)[-1]
            return rest.split("/")[0]
    return "unknown"


def collect_smt2_files(directories):
    """Recursively collect .smt2 files from directories, sorted by size."""
    files = []
    for d in directories:
        for root, _, names in os.walk(d):
            for name in names:
                if name.endswith(".smt2"):
                    files.append(os.path.join(root, name))
    files.sort(key=lambda p: os.path.getsize(p))
    return files


def load_file_list(path):
    """Load a list of file paths from a text file (one per line, # comments)."""
    files = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                files.append(line)
    files.sort(key=lambda p: os.path.getsize(p))
    return files
