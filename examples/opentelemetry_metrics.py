from dataclasses import dataclass
from time import sleep

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

metric_reader = PeriodicExportingMetricReader(
    OTLPMetricExporter(endpoint="http://host.docker.internal:4318/v1/metrics")
)
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
    work_counter.add(1, {"work.type": work_item.work_type})
    print("doing some work...")


def main():
    for i in range(10000):
        work_item = WorkItem(work_type="doing", data=i)
        do_work(work_item=work_item)
        sleep(0.1)

    provider.shutdown()
    sleep(2)


if __name__ == "__main__":
    main()
