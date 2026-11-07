"""Local OCR/ASR recognition through isolated plugin processes.

Engines run in their own Python environment (numpy 2 and onnxruntime conflict
with the API environment), are called as a subprocess, and must print one
``veriscope.recognition.v1`` JSON document. This package only uses the
standard library so the API can import it lazily without engine dependencies.
"""
