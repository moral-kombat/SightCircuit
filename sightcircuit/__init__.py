"""SightCircuit library: frames, sources, detection, pipeline and runtime helpers.

The active commands' bodies live in ``sightcircuit.commands``; each
``scripts/<name>.py`` is a thin wrapper that imports this package from the
checkout, so no installation step is required on the headset, and an installed
package exposes them as ``sightcircuit-<name>`` console entry points.
"""
