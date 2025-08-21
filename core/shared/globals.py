from core.shared.middleware.context import g
from core.shared.components import RBroker
from core.shared.components import RCacher
from core.shared.components import RSession
from core.shared.components import Agent

broker = RBroker()
cacher = RCacher()

__all__ = ["g", "broker", "cacher", "Agent", "RSession"]
