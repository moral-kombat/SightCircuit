"""The Arcturus camera export: the helper's wire protocol as data and its strict reader.

``contract`` is the one statement of camera export v1, the packet stream
``sightcircuit-camera-helper --export-frames`` writes on its stdout;
``scripts/camera_contract_header.py`` generates ``tools/camera_export_protocol.hpp``
from it, so the helper and Python share one vocabulary. ``export_reader``
turns that stream into frames and refuses it on the first violation. Nothing
here opens a camera, links the SDK or lifts a gate.
"""
