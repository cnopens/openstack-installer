# Copyright 2014, 2015 Canonical, Ltd.
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
import logging
import yaml
import os
from cloudinstall.config import ConfigBase
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


class StateManager(ConfigBase):

    """ Runtime state class
    """

    def __init__(self, state_file):
        if os.path.isfile(state_file):
            config = yaml.load(utils.slurp(state_file))
        else:
            config = {}
        super().__init__(config, state_file)
