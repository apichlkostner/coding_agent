import os
from dataclasses import dataclass
import random
from time import sleep

from dotenv import load_dotenv
from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

load_dotenv("../.env")
load_dotenv()

ENDPOINT = (
    os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost") + "/v1/metrics"
)
ITERATIONS = 100

metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=ENDPOINT))
provider = MeterProvider(metric_readers=[metric_reader])

# Sets the global default meter provider
metrics.set_meter_provider(provider)

# Creates a meter from the global meter provider
meter = metrics.get_meter("my.meter.name")

work_counter = meter.create_counter(
    "work.counter", unit="1", description="Counts the amount of work done"
)


@dataclass
class WorkItem:
    work_type: str
    data: int


def do_work(work_item: WorkItem):
    # count the work being doing
    increment = random.randrange(1, 100)
    work_counter.add(increment, {"work.type": work_item.work_type})
    print("doing some work...")
    sleep(0.1)


def main():
    for i in range(ITERATIONS):
        work_item = WorkItem(work_type="doing", data=i)
        do_work(work_item=work_item)

    # Flushes pending data and stops the reader.
    provider.shutdown()


if __name__ == "__main__":
    main()
