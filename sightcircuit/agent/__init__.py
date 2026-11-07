"""Guarded on-device agent support: a fixed task table, its runner and a tool-call guard.

See docs/agent-on-frame.md. Everything here is standard-library only, because the
runner and guard run with the headset's system ``python3`` before any project
environment exists.
"""
