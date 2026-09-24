"""In-memory Kafka stand-ins for the streaming tests.

FakeBroker keeps an append-only log per (topic, partition) and committed
offsets per group. FakeProducer and FakeConsumer follow the small part of
the aiokafka API the forwarder and consumer use, and can be told to fail.
"""
from __future__ import annotations

import asyncio
import zlib
from collections import defaultdict
from dataclasses import dataclass, field

from app.stream.codec import decode


@dataclass
class Record:
    topic: str
    partition: int
    offset: int
    key: bytes
    value: bytes


@dataclass(frozen=True)
class TopicPartition:
    topic: str
    partition: int


@dataclass
class FakeBroker:
    partitions: int = 3
    up: bool = True
    logs: dict = field(default_factory=lambda: defaultdict(list))
    # Every appended record in arrival order, across topics and partitions.
    arrivals: list = field(default_factory=list)
    committed: dict = field(default_factory=dict)
    # Fail the send of any record whose event_id is in this set (once each).
    fail_event_ids: set = field(default_factory=set)

    def append(self, topic: str, key: bytes, value: bytes) -> Record:
        partition = zlib.crc32(key) % self.partitions
        log = self.logs[(topic, partition)]
        record = Record(topic, partition, len(log), key, value)
        log.append(record)
        self.arrivals.append(record)
        return record

    def records(self, topic: str | None = None) -> list[Record]:
        out = [r for (t, _), log in self.logs.items() for r in log if topic is None or t == topic]
        return sorted(out, key=lambda r: (r.topic, r.partition, r.offset))

    def envelopes(self, topic: str | None = None) -> list:
        return [decode(r.value) for r in self.records(topic)]

    def arrival_kinds(self) -> list[str]:
        return [decode(r.value).kind for r in self.arrivals]


class BrokerDown(ConnectionError):
    pass


class FakeProducer:
    def __init__(self, broker: FakeBroker) -> None:
        self.broker = broker
        self.started = False
        self.starts = 0

    async def start(self) -> None:
        if not self.broker.up:
            raise BrokerDown("broker unreachable")
        self.started = True
        self.starts += 1

    async def stop(self) -> None:
        self.started = False

    async def send(self, topic: str, value: bytes, key: bytes, headers: list) -> asyncio.Future:
        future = asyncio.get_running_loop().create_future()
        event_id = dict(headers)["event_id"].decode()
        if not self.broker.up:
            future.set_exception(BrokerDown("broker went away"))
        elif event_id in self.broker.fail_event_ids:
            self.broker.fail_event_ids.discard(event_id)
            future.set_exception(BrokerDown(f"not acknowledged: {event_id}"))
        else:
            future.set_result(self.broker.append(topic, key, value))
        return future


class FakeConsumer:
    def __init__(self, broker: FakeBroker, group: str, topics: tuple[str, ...]) -> None:
        self.broker = broker
        self.group = group
        self.topics = topics
        self.positions: dict[TopicPartition, int] = {}

    async def start(self) -> None:
        if not self.broker.up:
            raise BrokerDown("broker unreachable")
        for topic in self.topics:
            for p in range(self.broker.partitions):
                tp = TopicPartition(topic, p)
                self.positions[tp] = self.broker.committed.get((self.group, tp), 0)

    async def stop(self) -> None:
        pass

    async def getmany(self, timeout_ms: int, max_records: int) -> dict:
        if not self.broker.up:
            raise BrokerDown("broker went away")
        out: dict = {}
        budget = max_records
        for tp, position in self.positions.items():
            log = self.broker.logs[(tp.topic, tp.partition)]
            batch = log[position:position + budget]
            if batch:
                out[tp] = batch
                self.positions[tp] = position + len(batch)
                budget -= len(batch)
        return out

    async def commit(self) -> None:
        for tp, position in self.positions.items():
            self.broker.committed[(self.group, tp)] = position

    def assignment(self) -> set:
        return set(self.positions)

    def highwater(self, tp: TopicPartition) -> int:
        return len(self.broker.logs[(tp.topic, tp.partition)])

    async def position(self, tp: TopicPartition) -> int:
        return self.positions[tp]


async def topics_ready(_settings: object) -> None:
    return None


class ManualClock:
    """Monotonic clock the test moves by hand; sleep() advances it."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
