"""Create release metadata from the final executable, never from a guessed hash."""
import hashlib
import json
from pathlib import Path
from urllib.parse import quote
from version import VERSION, REPOSITORY


def main():
    from version import APP_NAME
    exe = Path(__file__).parent / "release" / f"{APP_NAME}_v{VERSION}.exe"
    manifest = {"version": VERSION,
                "url": f"https://github.com/{REPOSITORY}/releases/download/v{VERSION}/{quote(exe.name)}",
                "sha256": hashlib.sha256(exe.read_bytes()).hexdigest(), "size": exe.stat().st_size,
                "notes": Path("RELEASE_NOTES.md").read_text(encoding="utf-8")}
    target = exe.parent / "latest.json"
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
