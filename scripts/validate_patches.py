#!/usr/bin/env python3

from _mixin import list_patches


if __name__ == "__main__":
    patch_files = list_patches(root_dir='./patches')
    print(f"Validated {len(patch_files)} patch file(s).")
