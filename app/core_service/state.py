"""Lifecycle states for the Research Assistant Core Service."""

from enum import Enum


class CoreState(str, Enum):
    STARTING = "STARTING"
    LOADING = "LOADING"
    READY = "READY"
    ERROR = "ERROR"
    SHUTTING_DOWN = "SHUTTING_DOWN"
