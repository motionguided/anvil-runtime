# This isn't really a dummy class - it's the full implementation, which is already minimal.
from contextlib import contextmanager

class DummyTraceFlags(int):
    DEFAULT = 0x00
    SAMPLED = 0x01
    @classmethod
    def get_default(cls): return cls(cls.DEFAULT)
    @property
    def sampled(self): return bool(self & DummyTraceFlags.SAMPLED)

DUMMY_TRACE_FLAGS = DummyTraceFlags()

class DummySpanContext:
    trace_id = 0
    span_id = 0
    is_remote = False
    trace_flags = DUMMY_TRACE_FLAGS
    trace_state = {}
    is_valid = False
DUMMY_SPAN_CONTEXT = DummySpanContext()

class DummySpan:
    def end(self, *a, **kw): pass
    def get_span_context(self): return DUMMY_SPAN_CONTEXT
    def set_attributes(self, *a, **kw): pass
    def set_attribute(self, *a, **kw): pass
    def add_event(self, *a, **kw): pass
    def add_link(self, *a, **kw): pass
    def update_name(self, *a, **kw): pass
    def is_recording(self): return True
    def set_status(self, *a, **kw): pass
    def record_exception(self, *a, **kw): pass

    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
DUMMY_SPAN = DummySpan()

class DummyTracer:
    def start_span(self, *args, **kwargs):
        return DUMMY_SPAN

    @contextmanager
    def start_as_current_span(self, *args, **kwargs):
        yield DUMMY_SPAN

tracer = DummyTracer()

class DummyTrace:
    def get_tracer(self, *a, **kw): return tracer
    def get_current_span(self): pass
    def set_span_in_context(self, *a, **kw): pass
    def set_tracer_provider(self, *a, **kw): pass

trace = DummyTrace()

class DummyContext:
    def create_key(self, *a, **kw): return ""
    def get_value(self, *a, **kw): pass
    def set_value(self, *a, **kw): return self
    def get_current(self): return self
    def attach(self, *a, **kw): pass
    def detach(self, *a, **kw): pass

context = DummyContext()

class DummyTracerProvider:
    def get_tracer(self, *a, **kw): return tracer

DUMMY_TRACER_PROVIDER = DummyTracerProvider()

def deserialise_parent_ctx(*args, **kw): pass
def serialise_span_ctx(*args, **kw): pass

class AnvilRpcExporter:
    def __init__(self, *a, **kw): pass
    @classmethod
    def flush_all(cls): pass

def get_tracer_provider(*a, **kw): return DUMMY_TRACER_PROVIDER
def set_internal_tracer_provider(*a, **kw): pass
def get_anvil_tracer_provider(): return DUMMY_TRACER_PROVIDER