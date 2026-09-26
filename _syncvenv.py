"""Re-apply this clone's mcptube sources onto the installed pipx venv copy.

Several fixes in this clone live only here and in the venv's site-packages:
mcptube is installed from PyPI, so anything not yet released upstream has to
be copied across by hand. A `pipx upgrade` (or reinstall) overwrites
site-packages and silently reverts every one of them -- with no error and no
change in behaviour until something quietly miscounts again. This puts them
back.

The clone is the source of truth. Nothing is ever copied venv -> clone.

Usage:
    _syncvenv.cmd --check      report what differs, write nothing
    _syncvenv.cmd              copy the differing files into the venv
    _syncvenv.cmd --force      copy even when the versions disagree

VERSION GUARD
The blanket copy is only safe while the clone and the installed package are
the same release: then the only differences are this clone's own patches.
After a real upgrade the installed tree is NEWER, and copying an older clone
over it would downgrade the package while masquerading as a patch re-apply.
So a version mismatch refuses by default. The fix in that case is to update
the clone to the new release first (git pull / re-fetch upstream), re-apply
the patches there, and only then sync.
"""
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).parent
CLONE_SRC = HERE / "src"
PKG = "mcptube"
DEFAULT_SITE = Path(r"C:\Users\mrsim\pipx\venvs\mcptube\Lib\site-packages")


def site_packages() -> Path:
    """Where the installed package lives. Override with MCPTUBE_VENV_SITE."""
    env = os.environ.get("MCPTUBE_VENV_SITE")
    return Path(env) if env else DEFAULT_SITE


def clone_version() -> str | None:
    """The version this clone declares in pyproject.toml."""
    pyproject = HERE / "pyproject.toml"
    if not pyproject.exists():
        return None
    in_project = False
    for line in pyproject.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("["):
            in_project = s == "[project]"
        elif in_project and s.startswith("version"):
            return s.split("=", 1)[1].strip().strip('"\'')
    return None


def installed_version() -> str | None:
    """The version of the package this interpreter imports."""
    try:
        import importlib.metadata as md
        return md.version(PKG)
    except Exception:
        return None


def normalized(path: Path) -> bytes:
    """File bytes with line endings flattened, so CRLF vs LF is not a diff."""
    return path.read_bytes().replace(b"\r\n", b"\n")


def target_newline(path: Path) -> bytes:
    """Match the convention already on disk; LF for a file that is not there.

    The clone is CRLF and site-packages is LF (pip normalizes on install).
    Writing CRLF into the venv would rewrite every line of every file --
    invisible in a diff, but it churns the tree and breaks any later
    byte-comparison against a clean reinstall.
    """
    if not path.exists():
        return b"\n"
    return b"\r\n" if b"\r\n" in path.read_bytes() else b"\n"


def pairs() -> list[tuple[Path, Path, str]]:
    """(source, target, relative path) for every .py in the clone package."""
    site = site_packages()
    out = []
    for src in sorted((CLONE_SRC / PKG).rglob("*.py")):
        rel = src.relative_to(CLONE_SRC)
        out.append((src, site / rel, rel.as_posix()))
    return out


def drop_pycache(target: Path) -> None:
    """Remove the stale .pyc beside a file we just replaced."""
    cache = target.parent / "__pycache__"
    if cache.is_dir():
        for pyc in cache.glob(f"{target.stem}.*.pyc"):
            try:
                pyc.unlink()
            except OSError:
                pass


def main() -> int:
    args = sys.argv[1:]
    check = "--check" in args
    force = "--force" in args

    site = site_packages()
    if not (site / PKG).is_dir():
        print(f"ERROR: no {PKG} package under {site}")
        return 1

    cv, iv = clone_version(), installed_version()
    print(f"clone     : {CLONE_SRC}  (version {cv})")
    print(f"installed : {site / PKG}  (version {iv})")

    if cv and iv and cv != iv and not force:
        print()
        print(f"REFUSING: clone is {cv}, installed is {iv}.")
        print("A blanket copy across releases would downgrade the package.")
        print("Update the clone to the installed release, re-apply the")
        print("patches there, then sync. --force overrides this.")
        return 2

    changed, missing = [], []
    for src, dst, rel in pairs():
        if not dst.exists():
            missing.append((src, dst, rel))
        elif normalized(src) != normalized(dst):
            changed.append((src, dst, rel))

    if not changed and not missing:
        print("\nin sync - nothing to copy")
        return 0

    for _, _, rel in changed:
        print(f"  differs : {rel}")
    for _, _, rel in missing:
        print(f"  absent  : {rel}  (not installed)")

    if check:
        print(f"\n--check: {len(changed) + len(missing)} file(s) would be "
              f"copied; nothing written")
        return 0

    for src, dst, rel in changed + missing:
        nl = target_newline(dst)
        body = src.read_bytes().replace(b"\r\n", b"\n")
        if nl == b"\r\n":
            body = body.replace(b"\n", b"\r\n")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(body)
        drop_pycache(dst)
        print(f"  copied  : {rel}")

    print(f"\n{len(changed) + len(missing)} file(s) copied into the venv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
