"""Zips a Lambda build directory so that identical files always give a byte-identical package.
CloudFormation sees a new package only when its hash changes, so file times and directory order
stay out of the archive: an unchanged source deploys as no change at all.

Usage: zip_lambda.py <source-directory> <target-zip>
"""

import stat
import sys
import zipfile
from pathlib import Path

# The earliest time a zip entry can hold.
FIXED_TIME = (1980, 1, 1, 0, 0, 0)


def zip_directory(source: Path, target: Path) -> None:
    files = sorted(path for path in source.rglob("*") if path.is_file())
    with zipfile.ZipFile(target, "w") as package:
        for path in files:
            entry = zipfile.ZipInfo(path.relative_to(source).as_posix(), FIXED_TIME)
            entry.compress_type = zipfile.ZIP_DEFLATED
            executable = path.stat().st_mode & stat.S_IXUSR
            entry.external_attr = (stat.S_IFREG | (0o755 if executable else 0o644)) << 16
            package.writestr(entry, path.read_bytes())


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    zip_directory(Path(sys.argv[1]), Path(sys.argv[2]))
