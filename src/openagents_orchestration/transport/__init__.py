"""Transport layer — messaging, routing, channel policy."""

from openagents_orchestration.transport.channel_policy import (
    DEFAULT_GLOBAL_POLICY,
    DEFAULT_TEAM_POLICY,
    ChannelPolicy,
    ChannelPolicyError,
)
from openagents_orchestration.transport.matrix_transport import (
    MatrixClient,
    MatrixConfig,
    MatrixTransport,
)
from openagents_orchestration.transport.routing import (
    RouteEntry,
    RoutingTable,
    TopologyType,
)

__all__ = [
    "DEFAULT_GLOBAL_POLICY",
    "DEFAULT_TEAM_POLICY",
    "ChannelPolicy",
    "ChannelPolicyError",
    "MatrixClient",
    "MatrixConfig",
    "MatrixTransport",
    "RouteEntry",
    "RoutingTable",
    "TopologyType",
]
