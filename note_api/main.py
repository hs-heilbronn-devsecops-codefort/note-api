# -*- coding: utf-8 -*-
from uuid import uuid4
from typing import List, Optional
from os import getenv
from typing_extensions import Annotated

from fastapi import Depends, FastAPI
from opentelemetry import trace
from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from starlette.responses import RedirectResponse
from .backends import Backend, RedisBackend, MemoryBackend, GCSBackend
from .model import Note, CreateNoteRequest
import sys

# setting ENV variables if available
if "pytest" in sys.modules:
    print("Tracing import is not needed for pytest")
else:
    # Set up OpenTelemetry Tracer Provider
    trace.set_tracer_provider(TracerProvider())
    tracer_provider = trace.get_tracer_provider()

    # Configure GCP Trace Exporter
    cloud_trace_exporter = CloudTraceSpanExporter()
    span_processor = SimpleSpanProcessor(cloud_trace_exporter)
    tracer_provider.add_span_processor(span_processor)

app = FastAPI()

my_backend: Optional[Backend] = None


# Initialize the tracer
tracer = trace.get_tracer(__name__)

# Instrument FastAPI
FastAPIInstrumentor.instrument_app(app)

def get_backend() -> Backend:
    global my_backend  # pylint: disable=global-statement
    if my_backend is None:
        backend_type = getenv('BACKEND', 'memory')
        print(backend_type)
        if backend_type == 'redis':
            my_backend = RedisBackend()
        elif backend_type == 'gcs':
            my_backend = GCSBackend()
        else:
            my_backend = MemoryBackend()
    return my_backend


@app.get('/')
def redirect_to_notes() -> None:
    return RedirectResponse(url='/notes')


@app.get('/notes')
def get_notes(backend: Annotated[Backend, Depends(get_backend)]) -> List[Note]:
    keys = backend.keys()

    Notes = []
    for key in keys:
        Notes.append(backend.get(key))
    return Notes


@app.get('/notes/{note_id}')
def get_note(note_id: str,
             backend: Annotated[Backend, Depends(get_backend)]) -> Note:
    # Create a custom span
    with tracer.start_as_current_span("get_note_using_id") as span:
        # Add attributes to the span
        span.set_attribute("note_id", note_id)
        span.set_attribute("operation", "fetch_note")

        # Call the backend to retrieve the note
        note = backend.get(note_id)

        # Optionally, add events to the span
        span.add_event("Fetched note from backend")

        return note


@app.put('/notes/{note_id}')
def update_note(note_id: str,
                request: CreateNoteRequest,
                backend: Annotated[Backend, Depends(get_backend)]) -> None:
    backend.set(note_id, request)
    
@app.post('/notes')
def create_note(request: CreateNoteRequest,
                backend: Annotated[Backend, Depends(get_backend)]) -> str:
    with tracer.start_as_current_span("create_note") as span:
        note_id = str(uuid4())
        span.set_attribute("note_id", note_id)
        span.set_attribute("title", request.title)
        backend.set(note_id, request)
        return note_id

@app.post('/notes')
def create_note(request: CreateNoteRequest,
                backend: Annotated[Backend, Depends(get_backend)]) -> str:
    note_id = str(uuid4())
    backend.set(note_id, request)
    return note_id

# Instrument the FastAPI application
FastAPIInstrumentor.instrument_app(app)