import os
from time import sleep

from dotenv import load_dotenv
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
)

load_dotenv("../.env")
load_dotenv()


def main():
    provider = TracerProvider(resource=Resource.create({"service.name": "Example"}))
    endpoint = (
        os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost") + "/v1/traces"
    )
    processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
    provider.add_span_processor(processor)

    # Sets the global default tracer provider
    trace.set_tracer_provider(provider)

    # Creates a tracer from the global tracer provider
    tracer = trace.get_tracer("ExampleTracer")
    with tracer.start_as_current_span("Example.run") as span:
        span.set_attribute("user.id", "user_id1")
        span.set_attribute("db.system", "postgresql")
        print("Starting")
        sleep(0.2)
        with tracer.start_as_current_span("GetName"):
            print("Reading name from database.")
            sleep(0.1)
        with tracer.start_as_current_span("GetHome"):
            print("Reading home from database.")
            sleep(0.3)
        with tracer.start_as_current_span("Result"):
            print("Printing available information")
            sleep(0.25)


if __name__ == "__main__":
    main()
