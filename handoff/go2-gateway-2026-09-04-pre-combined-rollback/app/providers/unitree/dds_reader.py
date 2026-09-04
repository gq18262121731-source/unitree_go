from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class DiscoveredTopic:
    name: str
    type_name: str
    source: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class DdsReader:
    """Owns a read-only CycloneDDS participant and typed data readers.

    This class deliberately has no publisher construction or write method.
    """

    def __init__(self, network_interface: str, robot_ip: str, domain_id: int) -> None:
        self.network_interface = network_interface
        self.robot_ip = robot_ip
        self.domain_id = domain_id
        self._domain: Any = None
        self._participant: Any = None
        self._discovery_readers: list[Any] = []
        self._data_readers: list[Any] = []

    @property
    def initialized(self) -> bool:
        return self._participant is not None

    def initialize(self) -> None:
        from cyclonedds.domain import Domain, DomainParticipant
        from unitree_sdk2py.core.channel_config import ChannelConfigHasInterface

        config = ChannelConfigHasInterface.replace("$__IF_NAME__$", self.network_interface)
        config = self._replace_peer(config, self.robot_ip)
        self._domain = Domain(self.domain_id, config)
        self._participant = DomainParticipant(self.domain_id)

    def discover_topics(self, duration_seconds: float) -> list[DiscoveredTopic]:
        if self._participant is None:
            raise RuntimeError("DDS reader is not initialized")

        from cyclonedds.builtin import (
            BuiltinDataReader,
            BuiltinTopicDcpsPublication,
            BuiltinTopicDcpsSubscription,
            BuiltinTopicDcpsTopic,
        )

        builtin_topics = (
            ("topic", BuiltinTopicDcpsTopic),
            ("publication", BuiltinTopicDcpsPublication),
            ("subscription", BuiltinTopicDcpsSubscription),
        )
        readers: list[tuple[str, Any]] = []
        for source, builtin_topic in builtin_topics:
            try:
                reader = BuiltinDataReader(self._participant, builtin_topic)
            except Exception:
                continue
            readers.append((source, reader))
            self._discovery_readers.append(reader)

        discovered: dict[tuple[str, str], DiscoveredTopic] = {}
        deadline = time.monotonic() + max(duration_seconds, 0.0)
        first_pass = True
        while first_pass or time.monotonic() < deadline:
            first_pass = False
            for source, reader in readers:
                try:
                    samples = reader.read(1024)
                except Exception:
                    continue
                for sample in samples:
                    name = str(getattr(sample, "topic_name", "") or "")
                    type_name = str(getattr(sample, "type_name", "") or "")
                    if not name:
                        continue
                    key = (name, type_name)
                    previous = discovered.get(key)
                    if previous is None or previous.source != "publication":
                        discovered[key] = DiscoveredTopic(name, type_name, source)
            if time.monotonic() < deadline:
                time.sleep(min(0.1, max(deadline - time.monotonic(), 0.0)))
        return sorted(discovered.values(), key=lambda item: (item.name, item.type_name))

    def create_reader(self, topic_name: str, message_type: Any) -> Any:
        if self._participant is None:
            raise RuntimeError("DDS reader is not initialized")
        from cyclonedds.sub import DataReader
        from cyclonedds.topic import Topic

        topic = Topic(self._participant, topic_name, message_type)
        reader = DataReader(self._participant, topic)
        self._data_readers.append(reader)
        return reader

    @staticmethod
    def take(reader: Any, limit: int = 128) -> list[Any]:
        samples = reader.take(limit)
        return list(samples or [])

    def close(self) -> None:
        self._data_readers.clear()
        self._discovery_readers.clear()
        self._participant = None
        self._domain = None

    @staticmethod
    def _replace_peer(config: str, robot_ip: str) -> str:
        if not robot_ip:
            return config
        peer = f'<Peer Address="{robot_ip}"/>'
        return re.sub(r'<Peer\s+Address="[^"]+"\s*/>', peer, config, count=1)
