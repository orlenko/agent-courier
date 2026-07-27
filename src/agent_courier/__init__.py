"""Private, durable messaging between terminal agents."""

from .models import Message, ModelValidationError, Peer

__all__ = ["Message", "ModelValidationError", "Peer"]
__version__ = "0.1.0"
