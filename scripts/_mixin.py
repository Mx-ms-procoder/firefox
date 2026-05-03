#!/usr/bin/env python3

"""
Common functions used across the Camoufox build system.
Not meant to be called directly.
"""

import contextlib
import fnmatch
import optparse
import os
import re
import sys
import time
from dataclasses import dataclass

start_time = time.time()


@contextlib.contextmanager
def temp_cd(path):
    """Temporarily change to a different working directory"""
    _old_cwd = os.getcwd()
    abs_path = os.path.abspath(path)
    assert os.path.exists(abs_path), f'{abs_path} does not exist.'
    os.chdir(abs_path)

    try:
        yield
    finally:
        os.chdir(_old_cwd)


def get_options():
    """Get options"""
    parser = optparse.OptionParser()
    parser.add_option('--mozconfig-only', dest='mozconfig_only', default=False, action="store_true")
    parser.add_option('--validate-only', dest='validate_only', default=False, action="store_true")
    parser.add_option(
        '--feature',
        dest='features',
        action='append',
        default=[],
        help='Only include patches claimed by the named manifest feature',
    )
    parser.add_option(
        '-P', '--no-settings-pane', dest='settings_pane', default=True, action="store_false"
    )
    parser.add_option(
        '--bundle',
        dest='bundle',
        default=None,
        help='Apply only a single patch bundle (e.g. --bundle identity)',
    )
    parser.add_option(
        '--check-conflicts',
        dest='check_conflicts',
        default=False,
        action='store_true',
        help='Run conflict detection without applying patches',
    )
    return parser.parse_args()


def find_src_dir(root_dir='.', version=None, release=None):
    """Get the source directory"""
    if version and release:
        name = os.path.join(root_dir, f'camoufox-{version}-{release}')
        assert os.path.exists(name), f'{name} does not exist.'
        return name
    folders = os.listdir(root_dir)
    for folder in folders:
        if os.path.isdir(folder) and folder.startswith('camoufox-'):
            return os.path.join(root_dir, folder)
    raise FileNotFoundError('No camoufox-* folder found')


def get_moz_target(target, arch):
    """Get moz_target from target and arch"""
    if target == "linux":
        return "aarch64-unknown-linux-gnu" if arch == "arm64" else f"{arch}-pc-linux-gnu"
    if target == "windows":
        return f"{arch}-pc-mingw32"
    if target == "macos":
        return "aarch64-apple-darwin" if arch == "arm64" else f"{arch}-apple-darwin"
    raise ValueError(f"Unsupported target: {target}")


def list_files(root_dir, suffix):
    """List files in a directory"""
    for root, _, files in os.walk(root_dir):
        for file in fnmatch.filter(files, suffix):
            full_path = os.path.join(root, file)
            relative_path = os.path.relpath(full_path, root_dir)
            yield os.path.join(root_dir, relative_path).replace('\\', '/')


class PatchManifestError(ValueError):
    """Raised when the patch manifest graph is inconsistent or unsafe."""


def _strip_wrapping_quotes(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _parse_manifest(manifest_path):
    manifest = {"name": None, "description": "", "patches": []}
    current_key = None
    with open(manifest_path, 'r', encoding='utf-8') as handle:
        for line_number, raw_line in enumerate(handle, 1):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            if stripped.startswith('name:'):
                manifest["name"] = stripped.split(':', 1)[1].strip()
                current_key = None
                continue
            if stripped.startswith('description:'):
                manifest["description"] = _strip_wrapping_quotes(
                    stripped.split(':', 1)[1].strip()
                )
                current_key = None
                continue
            if stripped == 'patches:':
                current_key = 'patches'
                continue
            if current_key == 'patches' and stripped.startswith('- '):
                manifest["patches"].append(stripped[2:].strip())
                continue
            raise PatchManifestError(
                f'Unsupported manifest syntax in {manifest_path}:{line_number}: {stripped}'
            )
    if not manifest["name"]:
        raise PatchManifestError(f'Manifest {manifest_path} does not define a name')
    return manifest


def load_patch_manifests(root_dir='../patches', manifests_dir=None):
    """Load all patch manifests and validate that every claimed patch exists."""
    manifests_root = manifests_dir or os.path.join(root_dir, 'manifests')
    if not os.path.isdir(manifests_root):
        raise PatchManifestError(f'Manifest directory not found: {manifests_root}')

    manifest_paths = sorted(
        (
            os.path.join(manifests_root, name).replace('\\', '/')
            for name in os.listdir(manifests_root)
            if name.endswith('.yaml')
        ),
        key=os.path.basename,
    )
    if not manifest_paths:
        raise PatchManifestError(f'No patch manifests found in {manifests_root}')

    manifests = []
    seen_names = set()
    for manifest_path in manifest_paths:
        manifest = _parse_manifest(manifest_path)
        if manifest["name"] in seen_names:
            raise PatchManifestError(f'Duplicate manifest name: {manifest["name"]}')
        seen_names.add(manifest["name"])
        for patch_name in manifest["patches"]:
            patch_path = os.path.join(root_dir, patch_name).replace('\\', '/')
            if not os.path.exists(patch_path):
                raise PatchManifestError(
                    f'Manifest {manifest["name"]} references missing patch {patch_name}'
                )
        manifests.append(manifest)
    return manifests


def validate_patch_file(patch_path):
    """Reject placeholder or non-portable patch stubs before they reach the build."""
    with open(patch_path, 'r', encoding='utf-8') as handle:
        contents = handle.read()
    if '@@ -XX' in contents or '+XX' in contents:
        raise PatchManifestError(
            f'Patch contains placeholder hunk markers and cannot be applied safely: {patch_path}'
        )


def list_patches(root_dir='../patches', suffix='*.patch', features=None, validate=True):
    """List all patch files claimed by manifests, preserving the legacy basename order."""
    manifest_list = load_patch_manifests(root_dir=root_dir)
    selected_features = set(features or [])
    selected_manifests = [
        manifest
        for manifest in manifest_list
        if not selected_features or manifest["name"] in selected_features
    ]
    if selected_features and len(selected_manifests) != len(selected_features):
        missing = sorted(selected_features - {manifest["name"] for manifest in selected_manifests})
        raise PatchManifestError(f'Unknown manifest feature(s): {", ".join(missing)}')

    claimed_paths = []
    for manifest in selected_manifests:
        for patch_name in manifest["patches"]:
            patch_path = os.path.join(root_dir, patch_name).replace('\\', '/')
            claimed_paths.append(patch_path)

    if len(set(claimed_paths)) != len(claimed_paths):
        duplicates = sorted(
            {
                path for path in claimed_paths
                if claimed_paths.count(path) > 1
            }
        )
        raise PatchManifestError(
            f'Duplicate patch claims detected: {", ".join(os.path.basename(path) for path in duplicates)}'
        )

    if not selected_features:
        all_patch_paths = sorted(list_files(root_dir, suffix), key=os.path.basename)
        claimed_normalized = {os.path.normpath(p) for p in claimed_paths}
        unclaimed_paths = [
            path for path in all_patch_paths
            if not is_bootstrap_patch(path) and os.path.normpath(path) not in claimed_normalized
        ]
        if unclaimed_paths:
            raise PatchManifestError(
                'Unclaimed patch files detected: '
                + ', '.join(os.path.basename(path) for path in unclaimed_paths)
            )

    if validate:
        for patch_path in claimed_paths:
            validate_patch_file(patch_path)

    return sorted(claimed_paths, key=os.path.basename)

def is_bootstrap_patch(name):
    return bool(re.match(r'\d+\-.*', os.path.basename(name)))


@dataclass
class ConflictReport:
    """Report of two manifests modifying the same Gecko source file."""
    file_path: str
    manifest_a: str
    manifest_b: str
    severity: str = "warning"  # "warning" or "error"

    def __str__(self):
        return (
            f"[{self.severity.upper()}] {self.file_path} is modified by both "
            f"'{self.manifest_a}' and '{self.manifest_b}'"
        )


def _extract_gecko_files(patch_path):
    """Extract Gecko source file paths from a patch file's diff headers."""
    files = set()
    try:
        with open(patch_path, 'r', encoding='utf-8', errors='replace') as handle:
            for line in handle:
                line = line.strip()
                if line.startswith('--- a/'):
                    files.add(line[6:])
                elif line.startswith('+++ b/'):
                    files.add(line[6:])
    except (OSError, UnicodeDecodeError):
        pass
    # Remove /dev/null entries
    files.discard('/dev/null')
    return files


def detect_conflicts(manifests, root_dir='../patches'):
    """
    Detect when two different manifests modify the same Gecko source file.

    Args:
        manifests: List of parsed manifest dicts from load_patch_manifests()
        root_dir: Root directory for patch files

    Returns:
        List of ConflictReport instances
    """
    # Build a map: gecko_file -> [(manifest_name, patch_file), ...]
    file_map = {}
    for manifest in manifests:
        for patch_name in manifest["patches"]:
            patch_path = os.path.join(root_dir, patch_name).replace('\\', '/')
            if not os.path.exists(patch_path):
                continue
            gecko_files = _extract_gecko_files(patch_path)
            for gecko_file in gecko_files:
                if gecko_file not in file_map:
                    file_map[gecko_file] = []
                file_map[gecko_file].append((manifest["name"], patch_name))

    # Find conflicts: files modified by more than one manifest
    conflicts = []
    for gecko_file, sources in file_map.items():
        manifest_names = sorted(set(entry[0] for entry in sources))
        if len(manifest_names) > 1:
            # Generate pairwise conflict reports
            for i in range(len(manifest_names)):
                for j in range(i + 1, len(manifest_names)):
                    # moz.build changes are expected to be shared — lower severity
                    severity = "warning" if gecko_file.endswith("moz.build") else "error"
                    conflicts.append(ConflictReport(
                        file_path=gecko_file,
                        manifest_a=manifest_names[i],
                        manifest_b=manifest_names[j],
                        severity=severity,
                    ))

    return sorted(conflicts, key=lambda c: (c.severity, c.file_path))


def print_conflict_report(conflicts):
    """Pretty-print conflict detection results."""
    if not conflicts:
        print("No patch conflicts detected.")
        return

    errors = [c for c in conflicts if c.severity == "error"]
    warnings = [c for c in conflicts if c.severity == "warning"]

    print(f"\n{'='*60}")
    print(f"Patch Conflict Report: {len(errors)} errors, {len(warnings)} warnings")
    print(f"{'='*60}\n")

    for conflict in conflicts:
        print(f"  {conflict}")

    if errors:
        print(f"\n  {len(errors)} hard conflict(s) detected!")
        print(f"  These manifests modify the same non-build Gecko source files.")
    print()


def script_exit(statuscode):
    """Exit the script"""
    if (time.time() - start_time) > 60:
        # print elapsed time
        elapsed = time.strftime("%H:%M:%S", time.gmtime(time.time() - start_time))
        print(f"\n\aElapsed time: {elapsed}")
        sys.stdout.flush()

    sys.exit(statuscode)


def run(cmd, exit_on_fail=True, do_print=True):
    """Run a command"""
    if not cmd:
        return
    if do_print:
        print(cmd)
        sys.stdout.flush()
    retval = os.system(cmd)
    if retval != 0 and exit_on_fail:
        print(f"fatal error: command '{cmd}' failed")
        sys.stdout.flush()
        script_exit(1)
    return retval


def patch(patchfile, reverse=False, silent=False):
    """Run a patch file"""
    if reverse:
        cmd = f"patch -p1 -R -i {patchfile}"
    else:
        cmd = f"patch -p1 -i {patchfile}"
    if silent:
        cmd += ' > /dev/null'
    else:
        print(f"\n*** -> {cmd}")
    sys.stdout.flush()
    run(cmd)


__all__ = [
    'get_moz_target',
    'load_patch_manifests',
    'list_patches',
    'patch',
    'run',
    'script_exit',
    'temp_cd',
    'get_options',
    'validate_patch_file',
    'detect_conflicts',
    'print_conflict_report',
    'ConflictReport',
]


if __name__ == '__main__':
    print('This is a module, not meant to be called directly.')
    sys.exit(1)

