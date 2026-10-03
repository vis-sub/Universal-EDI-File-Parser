"""Use the parser in-process (no service) to stream documents out of a large file.

Usage: python examples/library_stream.py path/to/file.edi
"""
import json
import sys

from ediparse import event_to_dict, stream

for event in stream(sys.argv[1]):            # constant memory: one document at a time
    if event.kind != "message":
        continue
    msg = event.message
    if msg.type == "850":                     # work with the objects directly...
        po_number = msg.segments[0].value(3)  # BEG03
        lines = [s for s in msg.segments if s.tag == "PO1"]
        print(f"PO {po_number}: {len(lines)} lines")
    else:                                     # ...or as the same JSON the service returns
        print(json.dumps(event_to_dict(event, segments=False)))
