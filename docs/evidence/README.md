# Evidence records

Each numbered record preserves the original procedure, observation, provenance
and limits. The [generated status index](../status.md) keeps every historical
heading as a compatibility link. E63–E71 were reconstructed from previously
merged documentation: missing original dates, revisions and raw reports remain
unknown. The metadata category `device_report` describes the source of a
report, including failures; it does not certify success or headset acceptance.

The coordinator alone allocates evidence IDs. Add the next unused ID to
`index.json` and its Markdown file; record execution date, platform/runtime,
checkout/binary revision, procedure, result and limits. Separate offline
checks, CI, user reports, upstream claims and physical recovery. Keep private
images, recordings, calibration, credentials and downloaded models out of Git.

Update reviewed capability rows in `../status-data.json` and regenerate:

```bash
python3 scripts/generate_status.py
python3 scripts/generate_status.py --check
```

The generator is dependency-free and rejects duplicate IDs, missing records,
path traversal, unknown evidence references and lost reconstructed provenance.
It preserves all content outside the marked architecture progress block.
Generated summaries must be reviewed; changing metadata never qualifies a
hardware mapping or supplies missing device evidence.
