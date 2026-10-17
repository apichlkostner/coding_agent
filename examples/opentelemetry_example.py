from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
)


def main():
    provider = TracerProvider(resource=Resource.create({"service.name": "Example"}))
    # processor = BatchSpanProcessor(ConsoleSpanExporter())
    processor = BatchSpanProcessor(
        OTLPSpanExporter(endpoint="http://host.docker.internal:4318/v1/traces")
    )
    provider.add_span_processor(processor)

    # Sets the global default tracer provider
    trace.set_tracer_provider(provider)

    # Creates a tracer from the global tracer provider
    tracer = trace.get_tracer("ExampleTracer")
    with tracer.start_as_current_span("Example.run") as span:
        span.set_attribute("user.id", "user_id1")
        span.set_attribute("db.system", "postgresql")
        print("Starting")
        with tracer.start_as_current_span("GetName"):
            print("Reading name from database.")
        with tracer.start_as_current_span("GetHome"):
            print("Reading home from database.")
        with tracer.start_as_current_span("Result"):
            print("Printing available information")


if __name__ == "__main__":
    main()
