"""ediparse: read any X12, EDIFACT, TRADACOMS or HL7 v2 file without partner-specific mappings."""
from .dialect import EDIFACT, HL7, TRADACOMS, X12, Dialect, EDIDetectionError
from .model import (Document, Event, Group, Interchange, InterchangeEvent, Issue, IssueEvent, Message,
                    MessageEvent, Segment)
from .output import event_to_dict
from .parser import parse_bytes, parse_file, parse_text
from .stream import StreamParser, astream, stream

__all__ = [
    "X12", "EDIFACT", "TRADACOMS", "HL7", "Dialect", "EDIDetectionError",
    "Document", "Interchange", "Group", "Message", "Segment", "Issue",
    "Event", "MessageEvent", "InterchangeEvent", "IssueEvent", "event_to_dict",
    "parse_bytes", "parse_file", "parse_text", "stream", "astream", "StreamParser",
]
__version__ = "0.2.0"
