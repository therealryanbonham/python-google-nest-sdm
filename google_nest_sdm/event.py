"""Events from pubsub subscriber."""

import datetime
import logging
from abc import ABC, abstractmethod

from .auth import AbstractAuth
from .registry import Registry
from .traits import BuildTraits, Command

EVENT_ID = "eventId"
EVENT_SESSION_ID = "eventSessionId"
TIMESTAMP = "timestamp"
RESOURCE_UPDATE = "resourceUpdate"
NAME = "name"
TRAITS = "traits"
EVENTS = "events"
RELATION_UPDATE = "relationUpdate"
TYPE = "type"
SUBJECT = "subject"
OBJECT = "object"

# Event images expire 30 seconds after the event is published
EVENT_IMAGE_EXPIRE_SECS = 30

EVENT_MAP = Registry()

_LOGGER = logging.getLogger(__name__)


class EventProcessingError(Exception):
    """Raised when there was an error handling an event."""


class ImageEventBase(ABC):
    """Base class for all image related event types."""

    def __init__(self, data: dict, timestamp: datetime.datetime):
        """Initialize EventBase."""
        self._data = data
        self._timestamp = timestamp

    @property
    def event_id(self) -> str:
        """An ID associated with the event.

        Can be used with CameraEventImageTrait to download the imaage.
        """
        return self._data[EVENT_ID]

    @property
    def event_session_id(self) -> str:
        """An ID used to associate separate messages with a single event."""
        return self._data[EVENT_SESSION_ID]

    @property
    def timestamp(self) -> datetime.datetime:
        """Timestap when the event occurred."""
        return self._timestamp

    @property
    def expires_at(self) -> datetime.datetime:
        return self._timestamp + datetime.timedelta(seconds=EVENT_IMAGE_EXPIRE_SECS)


@EVENT_MAP.register()
class CameraMotionEvent(ImageEventBase):
    """Motion has been detected by the camera."""

    NAME = "sdm.devices.events.CameraMotion.Motion"


@EVENT_MAP.register()
class CameraPersonEvent(ImageEventBase):
    """A person has been detected by the camera."""

    NAME = "sdm.devices.events.CameraPerson.Person"


@EVENT_MAP.register()
class CameraSoundEvent(ImageEventBase):
    """Sound has been detected by the camera."""

    NAME = "sdm.devices.events.CameraSound.Sound"


@EVENT_MAP.register()
class DoorbellChimeEvent(ImageEventBase):
    """The doorbell has been pressed."""

    NAME = "sdm.devices.events.DoorbellChime.Chime"


class RelationUpdate:
    """Represents a relational update for a resource."""

    def __init__(self, raw_data: dict):
        self._raw_data = raw_data

    @property
    def type(self) -> str:
        """The type of relation event 'CREATED', 'UPDATED', 'DELETED'."""
        return self._raw_data[TYPE]

    @property
    def subject(self) -> str:
        """The resource that the object is now in relation with."""
        return self._raw_data[SUBJECT]

    @property
    def object(self) -> str:
        """The resource that triggered the event."""
        return self._raw_data[OBJECT]


def BuildEvents(events: dict, event_map: dict, timestamp: datetime.datetime) -> dict:
    """Builds a trait map out of a response dict."""
    result = {}
    for (event, event_data) in events.items():
        if event not in event_map:
            continue
        cls = event_map[event]
        result[event] = cls(event_data, timestamp)
    return result


class EventMessage:
    """Event for a change in trait value or device action."""

    def __init__(self, raw_data: dict, auth: AbstractAuth):
        """Initialize an EventMessage."""
        _LOGGER.debug("EventMessage raw_data=%s", raw_data)
        self._raw_data = raw_data
        self._auth = auth

    @property
    def event_id(self) -> str:
        """A unique event identifier."""
        return self._raw_data.get(EVENT_ID, None)

    @property
    def timestamp(self) -> datetime.datetime:
        """Time when the event was published."""
        event_timestamp = self._raw_data[TIMESTAMP]
        return datetime.datetime.fromisoformat(event_timestamp.replace("Z", "+00:00"))

    @property
    def resource_update_name(self) -> str:
        """Returns the id of the device that was updated."""
        if RESOURCE_UPDATE not in self._raw_data:
            return None
        return self._raw_data[RESOURCE_UPDATE][NAME]

    @property
    def resource_update_events(self) -> dict:
        """Returns the set of events that happened."""
        if RESOURCE_UPDATE not in self._raw_data:
            return None
        events = self._raw_data[RESOURCE_UPDATE].get(EVENTS, {})
        return BuildEvents(events, EVENT_MAP, self.timestamp)

    @property
    def resource_update_traits(self) -> dict:
        """Returns the set of traits that were updated."""
        if RESOURCE_UPDATE not in self._raw_data:
            return None
        cmd = Command(self.resource_update_name, self._auth)
        events = self._raw_data[RESOURCE_UPDATE].get(TRAITS, {})
        return BuildTraits(events, cmd)

    @property
    def relation_update(self) -> RelationUpdate:
        """Represent a relational update for a resource."""
        if RELATION_UPDATE not in self._raw_data:
            return None
        return RelationUpdate(self._raw_data[RELATION_UPDATE])


class EventCallback(ABC):
    """For implementers to get notified about EventMessages."""

    @abstractmethod
    def handle_event(self, event_message: EventMessage):
        """Process an incoming EventMessage."""


class EventTypeFilterCallback(EventCallback):
    """Invokes a delegate only for events that match the trait type."""

    def __init__(self, event_name, delegate: EventCallback):
        self._event_name = event_name
        self._delegate = delegate

    def handle_event(self, event_message: EventMessage):
        """Process an incoming EventMessage."""
        events = event_message.resource_update_events
        if self._event_name in events:
            self._delegate.handle_event(event_message)


class RecentEventFilterCallback(EventCallback):
    """Invokes a delegate only for recent events."""

    def __init__(self, cutoff_timedelta: datetime.timedelta, delegate: EventCallback):
        self._cutoff_timedelta = cutoff_timedelta
        self._delegate = delegate

    def handle_event(self, event_message: EventMessage):
        """Process an incoming EventMessage."""
        if not event_message.timestamp:
            return
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        oldest_allowed = now - self._cutoff_timedelta
        if event_message.timestamp < oldest_allowed:
            _LOGGER.debug(
                "Ignoring event that is too old (%s)", event_message.timestamp
            )
            return
        self._delegate.handle_event(event_message)
