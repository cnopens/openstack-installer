#
# Copyright 2014 Canonical, Ltd.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from enum import Enum, IntEnum, unique
import yaml
import logging
import cloudinstall.utils as utils

log = logging.getLogger('cloudinstall.state')


class InstallState(IntEnum):
    RUNNING = 0
    NODE_WAIT = 1


@unique
class ControllerState(IntEnum):

    """Names for current screen state"""
    INSTALL_WAIT = 0
    PLACEMENT = 1
    SERVICES = 2


class CharmState(Enum):

    """ Charm relation states """
    REQUIRED = 0
    OPTIONAL = 1
    CONFLICTED = 2


class StateManager:

    """ Manage installer state """

    def __init__(self):
        self._state = {}

    def setopt(self, key, val):
        """ sets config option """
        try:
            self._state[key] = val
        except Exception as e:
            log.error("Failed to set {} in statemanager: {}".format(key, e))

    def getopt(self, key):
        if key in self._state:
            return self._state[key]
        else:
            if hasattr(self, key):
                attr = getattr(self, key)
                return attr() if callable(attr) else attr
            return False

    def save(self, path):
        utils.spew(path, yaml.safe_dump(self._state))
