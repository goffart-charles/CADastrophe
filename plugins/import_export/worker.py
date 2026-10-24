"""QProcess entry point: no QApplication, window or shared OCCT state."""
import json
import os
from pathlib import Path
import sys
import traceback


def main():
    # The host passes its actual package root, including for copied test plugins.
    sys.path.insert(0, sys.argv[1])
    handles = []
    if sys.platform == "win32":
        library = Path(sys.prefix) / "Library/bin"
        if library.exists():
            handles.append(os.add_dll_directory(str(library)))
    from step_io import read_step

    directory = Path(sys.argv[2])
    def progress(value, label):
        print("CADASTROPHE_STEP " + json.dumps({"value": value, "label": label}), flush=True)

    try:
        request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
        if request["mode"] == "import":
            result = read_step(request["source"], directory, progress)
        elif request["mode"] == "export":
            sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
            from import_export.exporters import default_registry, export_file
            result = export_file(default_registry(),directory,request["output"],request["objects"],
                                 request["format"],request["options"],progress)
        else:
            raise ValueError("Opération STEP inconnue.")
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        (directory / "result.json").write_text(json.dumps({"error": str(exc)}, ensure_ascii=False), encoding="utf-8")
        return 1
    (directory / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
