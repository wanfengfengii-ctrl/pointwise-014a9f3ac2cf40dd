from collections.abc import Mapping

from movement.arrivals import record_arrival
from movement.departures import record_departure
from movement.station_journal import StationAction


def read_action(value):
    if isinstance(value, StationAction):
        return value
    if isinstance(value, Mapping):
        try:
            return StationAction(**value)
        except TypeError as error:
            raise ValueError('invalid recording action fields') from error
    raise ValueError('an action or an action mapping is required')


def move(db, action, before_arrival=None):
    action = read_action(action)
    if action.kind == 'departure':
        return record_departure(db, action)
    if action.kind == 'arrival':
        return record_arrival(db, action, before_arrival)
    raise ValueError('unknown movement action')
