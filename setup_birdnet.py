"""
Post-install shim: makes tflite_runtime importable via ai-edge-litert.
Needed on Python 3.12+ where the standalone tflite-runtime wheel is unavailable.
Safe to run on Python 3.11 where tflite-runtime is already installed (no-op).
"""
import sys
import os

try:
    import tflite_runtime.interpreter  # already present, nothing to do
    print("tflite_runtime already available — skipping shim")
    sys.exit(0)
except ImportError:
    pass

try:
    import ai_edge_litert.interpreter  # shim source available
except ImportError:
    print("WARNING: neither tflite_runtime nor ai_edge_litert found; BirdNET will be unavailable")
    sys.exit(0)

site_pkg = next((p for p in sys.path if "site-packages" in p), None)
if not site_pkg:
    print("WARNING: could not locate site-packages; skipping shim")
    sys.exit(0)

shim_dir = os.path.join(site_pkg, "tflite_runtime")
os.makedirs(shim_dir, exist_ok=True)

with open(os.path.join(shim_dir, "__init__.py"), "w") as f:
    f.write("# tflite_runtime shim → ai_edge_litert\n")

with open(os.path.join(shim_dir, "interpreter.py"), "w") as f:
    f.write(
        "# tflite_runtime.interpreter shim → ai_edge_litert.interpreter\n"
        "from ai_edge_litert.interpreter import Interpreter, InterpreterWithCustomOps, load_delegate\n"
    )

print("tflite_runtime shim created at", shim_dir)
