from uuid import uuid4
from typing import List, Optional
from os import getenv
import logging

from fastapi import Depends, FastAPI
from starlette.responses import RedirectResponse
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from pythonjsonlogger import jsonlogger

from .backends import Backend, RedisBackend, MemoryBackend, GCSBackend
from .model import Note, CreateNoteRequest

# Initialize the FastAPI app
application = FastAPI()

# Set up OpenTelemetry tracing
otel_resource = Resource.create({"service.name": "note_service"})
otel_tracer_provider = TracerProvider(resource=otel_resource)
otel_exporter = OTLPSpanExporter(endpoint=getenv("OTLP_ENDPOINT", "https://trace.googleapis.com"))
otel_tracer_provider.add_span_processor(BatchSpanProcessor(otel_exporter))
trace.set_tracer_provider(otel_tracer_provider)

# Instrument FastAPI for observability
FastAPIInstrumentor.instrument_app(application)
LoggingInstrumentor().instrument()

# Configure JSON-based logging
stream_handler = logging.StreamHandler()
log_format = jsonlogger.JsonFormatter(
    "%(asctime)s %(levelname)s %(message)s %(otelTraceID)s %(otelSpanID)s %(otelTraceSampled)s",
    rename_fields={
        "levelname": "severity",
        "asctime": "timestamp",
        "otelTraceID": "logging.googleapis.com/trace",
        "otelSpanID": "logging.googleapis.com/spanId",
        "otelTraceSampled": "logging.googleapis.com/trace_sampled",
    },
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
stream_handler.setFormatter(log_format)
logging.basicConfig(level=logging.INFO, handlers=[stream_handler])
log = logging.getLogger("note_service")

# Define a global backend variable
backend_instance: Optional[Backend] = None

# Helper function to retrieve the backend
def retrieve_backend() -> Backend:
    global backend_instance
    if backend_instance is None:
        backend_type = getenv("BACKEND", "memory")
        log.info(f"Initializing backend: {backend_type}")
        if backend_type == "redis":
            backend_instance = RedisBackend()
        elif backend_type == "gcs":
            backend_instance = GCSBackend()
        else:
            backend_instance = MemoryBackend()
    return backend_instance

# Redirect root URL to notes endpoint
@application.get("/")
def root_redirect() -> RedirectResponse:
    return RedirectResponse(url="/notes")

# Retrieve all notes
@application.get("/notes")
def fetch_notes(backend: Backend = Depends(retrieve_backend)) -> List[Note]:
    with trace.get_tracer(__name__).start_as_current_span("fetch_all_notes") as span:
        note_keys = backend.keys()
        notes_list = [backend.get(key) for key in note_keys]
        span.set_attribute("note.total_count", len(notes_list))
        return notes_list

# Retrieve a specific note by ID
@application.get("/notes/{note_id}")
def fetch_note(note_id: str, backend: Backend = Depends(retrieve_backend)) -> Note:
    with trace.get_tracer(__name__).start_as_current_span("fetch_note_by_id") as span:
        span.set_attribute("note.identifier", note_id)
        return backend.get(note_id)

# Update a specific note
@application.put("/notes/{note_id}")
def modify_note(note_id: str, data: CreateNoteRequest, backend: Backend = Depends(retrieve_backend)) -> None:
    with trace.get_tracer(__name__).start_as_current_span("update_note_entry") as span:
        span.set_attribute("note.identifier", note_id)
        backend.set(note_id, data)

# Create a new note
@application.post("/notes")
def add_note(data: CreateNoteRequest, backend: Backend = Depends(retrieve_backend)) -> str:
    with trace.get_tracer(__name__).start_as_current_span("create_new_note") as span:
        new_note_id = str(uuid4())
        backend.set(new_note_id, data)
        span.set_attribute("note.identifier", new_note_id)
        log.info("Created a new note", extra={"note_id": new_note_id})
        return new_note_id

# Entry point for running the application
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(application, host="0.0.0.0", port=8000, reload=True)
