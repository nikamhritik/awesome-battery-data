"""Create packaging resources in the caller's external build directory."""

import hashlib
import json
import shutil
import sys
from importlib.metadata import distributions
from pathlib import Path


def main():
    target = Path(sys.argv[1]).resolve()
    root = Path(__file__).resolve().parent.parent
    if target.is_relative_to(root):
        raise ValueError("Build resources must be outside the source tree")
    target.mkdir(parents=True, exist_ok=True)
    licenses = target / "licenses"
    licenses.mkdir(exist_ok=True)
    for path in (root / "desktop" / "licenses").glob("*.txt"):
        shutil.copyfile(path, licenses / path.name)
    shutil.copyfile(root / "LICENSE", licenses / "project-MIT.txt")
    shutil.copyfile(root / "desktop" / "THIRD_PARTY_NOTICES.txt", target / "THIRD_PARTY_NOTICES.txt")
    dependencies = {}
    for distribution in distributions():
        name = distribution.metadata["Name"]
        dependencies[name] = distribution.version
        for file in distribution.files or ():
            if any(word in str(file).lower() for word in ("license", "copying", "notice")):
                source = distribution.locate_file(file)
                if source.is_file():
                    destination = licenses / name / str(file)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.exists():
        shutil.copyfile(python_license, licenses / "Python-LICENSE.txt")
    import rapidocr_onnxruntime

    models = Path(rapidocr_onnxruntime.__file__).parent / "models"
    manifest = {
        "version": "1.1.0", "python": sys.version,
        "dependencies": dependencies,
        "models": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in models.glob("*.onnx")},
    }
    if len(manifest["models"]) != 3:
        raise RuntimeError("Expected three bundled Chinese OCR models")
    (target / "build-info.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Packaging resources: {target}")


if __name__ == "__main__":
    main()
